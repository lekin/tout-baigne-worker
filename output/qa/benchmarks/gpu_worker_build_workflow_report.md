# GPU Worker Build & Deployment Workflow Report

## 1. Objective

Move production image builds away from the local M3 (`arm64` → `linux/amd64` emulation) to GitHub-hosted `linux/amd64` runners, keep heavy CUDA/PyTorch/Demucs/model layers stable, and make worker code changes cheap to deploy.

## 2. Final steady-state workflow

```text
edit worker code
↓
local tests
↓
git push
↓
GitHub Actions native linux/amd64 build
↓
GHCR immutable SHA image
↓
python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<sha>
↓
authenticated /ping
↓
version == <sha>
↓
smoke QA
↓
endpoint scales back to zero when idle
```

The local Mac no longer builds or pushes production CUDA images.

## 3. Repositories and assets

- Main repository: `https://github.com/lekin/tout-baigne-worker`
- Workflow: `.github/workflows/gpu-worker-build.yml`
- Worker image: `ghcr.io/lekin/tout-baigne-worker`
- RunPod endpoint: `ylkhb72ej3hijz` (`audio-qa-lb`, `LOAD_BALANCER`)
- Template: `unqjky9vdn`
- Deployment script: `scripts/deploy_runpod_worker.py`
- Readiness probe: `scripts/check_runpod_worker.py`
- Smoke test: `scripts/smoke_test_worker.py`

## 4. CI workflow configuration

- Trigger: `push` to `main` filtered to `worker/**`, `src/qa/**`, `.github/workflows/gpu-worker-build.yml`, `worker/Dockerfile`, `worker/requirements.txt`.
- Manual `workflow_dispatch` supported.
- Runner: `ubuntu-latest` building `linux/amd64` natively.
- No QEMU step.
- Docker Buildx with `cache-from: type=gha` and `cache-to: type=gha,mode=max`.
- GHCR login uses `docker/login-action` with `secrets.GITHUB_TOKEN` and `permissions: contents: read, packages: write`.
- Tags: short git SHA, semver (if release), `latest` (default branch).
- Build arg: `WORKER_VERSION=${{ github.sha }}`.
- Post-push step attempts to set the GHCR package visibility to public.

## 5. First CI build (cold cache)

- Workflow ID: `347844004`
- Run ID: `33554006202`
- Head SHA: `554871c38fbea1e7fe85c8139c5d576d4c99fd5f`
- Short SHA: `554871c`
- Started: `2026-09-01T20:13:04Z`
- Completed: `2026-09-01T20:25:33Z`
- Total workflow duration: **749 s (12 m 29 s)**
- `Build and push image` step: **718 s (11 m 58 s)**
- Platform: `linux/amd64`
- No QEMU.
- Cache behavior: cold (no prior `type=gha` cache); heavy layers were built/pushed.

## 6. GHCR image publication

- Image: `ghcr.io/lekin/tout-baigne-worker:554871c`
- Convenience tags: `latest`
- Manifest digest: `sha256:e900eb6c88d55ddeb98f58de42a38bcaba39ffde962006e78426f680bd57cac2`
- Compressed manifest size: **~5.25 GB**
- Architecture: `amd64`
- Package visibility: public (verified by anonymous `docker pull` after `docker logout ghcr.io`)

## 7. First deployment and cold start

- Command:
  ```bash
  python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:554871c
  ```
- Template image updated to `ghcr.io/lekin/tout-baigne-worker:554871c`.
- Workers cycled to 0, then to 1.
- Cold readiness latency: **55.85 s** (from scale-to-1 to first `HTTP 200 /ping`).
- `/ping` version: `554871c38fbea1e7fe85c8139c5d576d4c99fd5f` (matches expected SHA).
- Smoke test: **0.66 s**, returned `error_type="input_error"`, `error="missing lyrics"` (expected infrastructure smoke).

Endpoint configuration preserved:

- GPU pool: `ADA_24` (NVIDIA GeForce RTX 4090)
- Workers min/max: `0/1`
- Idle timeout: 300 s
- Execution timeout: 600000 ms
- Ports: `80/http`, `8000/http`
- Env: `PORT=8000`, `PORT_HEALTH=8000`, `HEALTH_CHECK_PATH=/ping`
- Startup args: `python3 -u /app/worker/handler.py`

## 8. Warm behavior

After the first successful `/ping`, the worker was warm.

| Metric | Value |
| --- | --- |
| Warm `/ping` | 0.41 s |
| Smoke `/run` (3 runs) | 0.73, 0.55, 0.56 s; **median 0.56 s** |
| First real QA `/run` (27 s sample) | **3.10 s** |
| Warm QA `/run` (3 runs) | 1.55, 1.65, 1.62 s; **median 1.62 s** |

QA payload used a public 27 s MP3 and a one-line dummy lyric. The worker downloaded the audio, ran Demucs, and returned a `SYNC_FAILED`/`global_offset` diagnosis with high confidence; the infrastructure path was healthy.

## 9. Scale-to-zero

After testing, the endpoint was scaled to `min=0 max=0`. `list-endpoint-workers` confirmed zero workers after a short reconcile. The endpoint can return to zero and will cold-start on the next request.

## 10. Second code-only CI build (BuildKit cache reuse)

Trigger: commit touching `worker/` code only.

(Results will be appended after the second CI run completes.)

## 11. Second deployment

(Results will be appended after the second SHA is deployed.)

## 12. Rollback

Rollback procedure:

```bash
python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<previous-sha>
```

No rebuild required. RunPod pulls the previous image from GHCR and the readiness probe verifies `/ping.version` matches the previous SHA.

## 13. Security and credentials

- The local GitHub PAT was temporarily granted `workflow` scope only for the one-time workflow-file push. No `write:packages` scope was added locally.
- GHCR publication uses the repository's `GITHUB_TOKEN` (`packages: write`).
- `RUNPOD_API_KEY` is read from the environment and never logged.
- The temporary `workflow` scope can be removed from the local PAT now that the workflow file is in the remote repository.

## 14. Reusable pattern for future GPU workers

- Keep a `worker/Dockerfile` per service with heavy layers first (CUDA base, apt, PyTorch, pip, Demucs, model preload) and application `COPY` at the end.
- Use `docker/metadata-action` for immutable SHA tags.
- Use `cache-from: type=gha` / `cache-to: type=gha,mode=max`.
- Use `scripts/deploy_runpod_worker.py` and `scripts/smoke_test_worker.py` for deploy/verify.
- Extract a `worker/Dockerfile.base` only once two or more services share identical CUDA/PyTorch/Demucs/version sets.

## 15. Remaining work

1. Complete the second code-only CI build and record cache-hit timing.
2. Deploy the second SHA and verify `/ping.version`.
3. Perform a rollback test to a previously published SHA.
4. Remove the temporary `workflow` scope from the local PAT.
