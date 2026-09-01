# RunPod Cold-Start Diagnosis

## 1. Endpoint and image under test

- Endpoint ID: `ylkhb72ej3hijz`
- Endpoint name: `audio-qa-lb`
- Type: `LOAD_BALANCER`
- Image: `ghcr.io/lekin/tout-baigne-worker:dfc4d29`
- Expected version: `dfc4d29019fcc8ca863dfef0d489fbd61b85ac45`
- GPU pool: `ADA_24` (NVIDIA GeForce RTX 4090)
- Disk: 50 GB
- Ports: `80/http`, `8000/http`
- Env: `PORT=8000`, `PORT_HEALTH=8000`, `HEALTH_CHECK_PATH=/ping`
- Startup: `python3 -u /app/worker/handler.py`
- Scaling: `REQUEST_COUNT=1`

## 2. Instrumentation changes

Updated:

- `scripts/check_runpod_worker.py`
  - Default per-request timeout: **30 s**
  - Default global timeout: **300 s**
  - Default poll interval: **3 s**
- `scripts/deploy_runpod_worker.py`
  - Worker-state polling via RunPod v2 API during deploy
  - 20 s control-plane propagation wait
  - 30 s per-request probe timeout
  - 300 s global deadline
  - Explicit failure classification based on worker inventory

## 3. Test 1: `/ping` as readiness probe with `workersMin=0 max=1`

Timeline (from deploy script):

- `t=0.00s` — deploy started
- `t=1.32s` — template updated to `dfc4d29`
- `t=1.93s` — workers scaled to `min=0 max=0`
- `t=31.93s` — scaled to `min=0 max=1`
- `t=52.63s` — propagation wait complete; start `/ping` probes
- `t=52.63s ... 383.24s` — 10 consecutive `/ping` attempts, each 30 s, all TIMEOUT
- `t=383.24s` — deploy fails with `worker_never_allocated`

Worker state timeline (RunPod v2 API):

```text
t=0.59s  total=0 throttled=0 running=0 initializing=0 idle=0 unhealthy=0
...
t=327.60s total=0 throttled=0 running=0 initializing=0 idle=0 unhealthy=0
```

**Result: with `workersMin=0` and `/ping` requests, no worker was ever allocated.**

## 4. Test 2: `/run` as allocation trigger

After Test 1, a `POST /run` smoke payload was sent with a 300 s timeout.

- Request submitted: `t=0`
- Worker `plmv7hn2v592fp` appeared in `INITIALIZING`: ~seconds after request
- Worker `RUNNING`: after ~3.5 minutes
- First `GET /ping` response (200): additional ~30 s

Wait, the `/run` request itself timed out at 300 s, but by that time the worker had initialized and was running. A subsequent `GET /ping` returned 200 in ~27 s.

## 5. Worker container logs

RunPod container logs for worker `plmv7hn2v592fp`:

| Timestamp | Event | Elapsed since start |
| --- | --- | --- |
| 23:09:20 | `loading container image from cache` | 0 s |
| 23:13:30 | `Loaded image: ghcr.io/lekin/tout-baigne-worker:dfc4d29` | **4 m 10 s** |
| 23:13:38 | `Status: Image is up to date ... worker is ready` | 4 m 18 s |
| 23:14:09 | `create container ... start container` | 4 m 49 s |
| 23:14:29 | `Started server process [20]` | 5 m 9 s |
| 23:14:29 | `Preloading Demucs model: htdemucs` | 5 m 9 s |
| 23:14:33 | `Preloaded htdemucs; Application startup complete; Uvicorn running` | 5 m 13 s |
| 23:14:33 | `GET /ping 200 OK` | 5 m 13 s |
| 23:14:52 | `POST /run 200 OK` (smoke missing lyrics) | 5 m 32 s |

## 6. Cold-start component breakdown

| Phase | Time | Dominant activity |
| --- | --- | --- |
| Control-plane / request propagation | < 2 s | template update + scale |
| Image load from cache | ~4 m 10 s | Loading the 5.25 GB (compressed) / ~15 GB (uncompressed) image into the container runtime |
| Container create + start | ~31 s | Runtime container setup |
| Application boot (CUDA/FastAPI/Demucs preload) | ~25 s | Python imports, model preload in memory |
| Health request response | < 1 s (warm) | Uvicorn ready |

**Root cause: the image load from the container cache is the dominant cold-start cost (~250 s).** The actual application boot is only ~25 s once the container starts. Pulling the image from GHCR itself is not the bottleneck on this run because the digest was already cached on the RunPod host ("Image is up to date" in < 10 s), but loading the uncompressed layers into containerd takes several minutes.

## 7. `/ping` vs `/run` behavior

With `workersMin=0` and `REQUEST_COUNT=1`:

- `GET /ping` does **not** allocate a worker (Test 1).
- `POST /run` (a real job request) **does** allocate a worker (Test 2).

This means a deployment readiness probe that only hits `/ping` can time out completely with `workersMin=0`. The worker needs an actual job request to trigger scale-up.

## 8. Capacity / throttling

At no point did RunPod report `throttled > 0` or `unhealthy > 0`. The worker eventually started and did not crash. Therefore GPU capacity/scheduling is **not** the root cause; the delay is local image/container runtime loading.

## 9. Mitigation: Flashboot + `workersMin=1`

The endpoint was updated by the user to:

- `flashboot: "FLASHBOOT"`
- `workers: { min: 1, max: 1 }`

With this setting, RunPod keeps one worker ready. The `workersMin=1` policy, combined with Flashboot, means the worker is pre-allocated and does not need to cold-start on each request.

Smoke test after enabling Flashboot:

```text
/ping 200 version=dfc4d29...
/run 200 success=false error_type=input_error (expected)
Smoke test passed.
```

## 10. Recommendation

**Status: SAFE TO RUN 100-TRACK BATCH**, with the endpoint in `flashboot` + `workersMin=1`.

Conditions:

- Keep `workersMin=1 max=1` and Flashboot enabled during the batch.
- The first track will still encounter a ~5 min image load if the worker has not been pre-warmed; after that, tracks will be warm.
- Do not return to `workersMin=0` before the batch, or the first job will hang waiting for a `/ping`-only probe.
- Future (after this experiment) optional optimization: investigate a smaller base image or RunPod-cached base to reduce the image-load time. Do **not** implement image changes during this task.
