# Project notes

## Phase A karaoke sync QA

### Useful commands

- `python -m pytest src/qa/tests/test_qa.py` — unit tests for the QA core.
- `python scripts/run_karaoke_benchmark.py --labels <yaml>` — run the Phase A benchmark.
- `python -m src.cli generate --record-id <id> --sync-preview` — generate a fast sync preview for visual checks.

### Key QA files

- `src/qa/runner.py` — end-to-end QA pipeline.
- `src/qa/structural.py` — primary structural timing analyzer (RMS/onset matching).
- `src/qa/audio_sync.py` — stable-ts aligner (kept available but not default).
- `src/qa/separator.py` — vocal-separation backend abstraction (CPU/MPS/MLX/FFmpeg).
- `scripts/separate_mlx.py` — helper used by the MLX backend in an isolated venv.
- `src/qa/scoring.py` — sync metrics and duration mismatch logic.
- `src/qa/diagnosis.py` — diagnosis and final status.
- `src/qa/gates.py` — hard gates.
- `src/qa/benchmark.py` — benchmark runner and report generation.
- `scripts/run_karaoke_benchmark.py` — CLI to run the benchmark.

### Known pitfalls

- Source lyric timestamps in `LyricLine`/`LyricWord` are already on the source audio timeline (transform applied during build); do not re-apply `apply_transform_to_source_ms` in scoring.
- `StableTsAligner` used to cache one model per class, ignoring `model_name`; this is now fixed to cache per `model_name:device:compute_type`.
- Duration mismatch uses `max(0, predicted_lyrics_duration - audio_duration)` so long instrumental outros are not flagged.
- Status `SYNC_VERIFIED` now also requires the diagnosis to be `good`, not just passing numeric gates.
- MPS PyTorch is not viable on macOS 14.5 (PyTorch `Conv1d` fails with `Output channels > 65536`); `PYTORCH_ENABLE_MPS_FALLBACK=1` does not fix it.
- `StableTsAligner` is not reliable as the primary oracle for sung music. It can produce `global_offset` on lyric-version mismatches and `local_mismatch` even with correct lyrics. Keep `StructuralAnalyzer` as the primary aligner.
- Vocal separation now uses `src/qa/separator.py`. Recommended config: `qa_vocal_separator = auto`, `qa_vocal_model = hdemucs_mmi`. Valid values: `demucs`/`demucs_cpu`/`demucs_mps`/`mlx`/`ffmpeg`/`auto`.
- Vocal cache keys are now content-addressed by backend, model, package version, and settings; existing CPU `htdemucs` stems remain valid via a legacy-key fallback.

### Active plan

- `/Users/lekin/.devin/plans/plan-f0b136639b635cea.md`

## Remote GPU Audio QA (RunPod Load Balancer)

### Working endpoint

- Endpoint ID: `ylkhb72ej3hijz`
- Name: `audio-qa-lb`
- Type: `LOAD_BALANCER` (RunPod routes HTTP directly to the worker)
- URL: `https://ylkhb72ej3hijz.api.runpod.ai`
- Image: `ghcr.io/lekin/tout-baigne-worker:<tag>` (custom CUDA/PyTorch/Demucs image built in CI) for the new workflow
- GPU pool: `ADA_24` (NVIDIA GeForce RTX 4090)
- Port: `8000/http` with `PORT=8000`, `PORT_HEALTH=8000`, `HEALTH_CHECK_PATH=/ping`
- Startup args: `python3 -u /app/worker/handler.py`
- Scaling: `REQUEST_COUNT` with `requestCount: 1`
- `flashboot`: `FLASHBOOT`
- `workersMin`: `0`
- `workersMax`: `2`
- `idleTimeout`: `300`
- Notes: with `workersMin=0` and Flashboot, the API may report idle workers in the warm pool, but `running` workers go to 0 when no requests are in flight. Set `workersMax` to the intended concurrency (2 for the batch runner).

### Build & deploy workflow

- CI: `.github/workflows/gpu-worker-build.yml` in `lekin/tout-baigne-worker`
  - Builds `linux/amd64` natively on a GitHub-hosted runner (no local M3/QEMU)
  - Publishes to `ghcr.io/lekin/tout-baigne-worker` using `secrets.GITHUB_TOKEN`
  - Tags: short git SHA (deploy source of truth), semver, `latest`
  - BuildKit `type=gha` cache for CUDA/PyTorch/Demucs/model layers
  - Includes a step to set the GHCR package visibility to public after the first push
- One-time `workflow`-scope push is already done; the workflow now lives in the remote repo. The local PAT does **not** need `write:packages`.
- Deploy: `python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<tag>`
  - Requires `RUNPOD_API_KEY` and `RUNPOD_ENDPOINT_ID`
  - Updates the endpoint template, cycles workers, polls `/ping`, verifies `version == <sha>`, and runs `scripts/smoke_test_worker.py`
  - Defaults to 300s overall timeout and 180s per request to tolerate image-pull cold starts
- Rollback: `python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<previous-sha>` (no rebuild)
- Smoke test: `python scripts/smoke_test_worker.py` (needs `RUNPOD_ENDPOINT_BASE_URL`)

### Important deployment notes

- The worker image is fully baked: no `apt install`, `pip install`, or model download at boot.
- The Demucs `htdemucs` model is preloaded inside the image at `/app/.torch_home` (`TORCH_HOME`).
- The handler is a FastAPI app (`worker/handler.py`) with `/ping` and `/run`.
- The `/run` endpoint receives `{"input": <AudioQARequest>}` and returns an `AudioQAResult` flat dict.
- GHCR packages are public by default for public repos, but verify package visibility in GitHub settings.

### Load Balancer requests require Bearer auth

All Load Balancer requests (`/ping` and `/run`) must include:

```http
Authorization: Bearer <RUNPOD_API_KEY>
```

The key is not passed in query strings or logged.

### Readiness probe

```bash
export RUNPOD_API_KEY=...
export RUNPOD_ENDPOINT_ID=ylkhb72ej3hijz

python scripts/check_runpod_worker.py --endpoint $RUNPOD_ENDPOINT_ID --timeout 120
```

Local test against `http://localhost:8000`:

```bash
RUNPOD_API_KEY=dummy \
  python scripts/check_runpod_worker.py \
  --endpoint local \
  --base-url http://localhost:8000 \
  --expected-version local-test \
  --no-smoke
```

The probe:
- sends `Authorization: Bearer ...` on every request
- logs every attempt, status, and response-body prefix
- fails fast on `401`/`403`
- uses a real overall deadline with `time.monotonic()`
- optionally checks `/ping` `version` against an expected git SHA

### Deploy

```bash
export RUNPOD_API_KEY=...
export RUNPOD_ENDPOINT_ID=ylkhb72ej3hijz

python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<tag>
```

### Client usage

```bash
# Load Balancer endpoint
python scripts/runpod_remote_qa_client.py --record-id <id> --endpoint-id ylkhb72ej3hijz --lb --audio-url <public-mp3-url>
```

For records without a direct `Source Audio URL`, use `--audio-url` to pass a public URL (e.g. a GDrive direct link or a temporary tunnel).

### Status

- 2026-09-01: E2E succeeded on `Khaled - Aïcha` and `Willy Denzey - Le mur du son` using Load Balancer endpoint `ylkhb72ej3hijz` (RTX 4090, Demucs 4.0.1, ~20–30 s wall time).
- 2026-09-01: `worker/handler.py` now returns `{"status":"ok","version":..., "gpu": ...}` on `/ping`; `scripts/check_runpod_worker.py` performs authenticated, bounded readiness probes.
- 2026-09-01: CI/GitHub-Actions/RunPod loop complete: workflow pushed, multiple native `linux/amd64` builds published to GHCR, deployed, version-verified, smoke QA passed, rollback verified, scale-to-zero confirmed.
- 2026-09-01: Dockerfile cache fixed — `ARG WORKER_VERSION` moved to final layer and Demucs model preload moved before `COPY src/worker`, giving sub-3-minute code-only CI builds.
