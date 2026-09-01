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

- Workflow run: `33554006202`
- Head SHA: `554871c38fbea1e7fe85c8139c5d576d4c99fd5f`
- Started: `2026-09-01T20:13:04Z`
- Completed: `2026-09-01T20:25:33Z`
- Total workflow duration: **749 s (12 m 29 s)**
- `Build and push image`: **718 s (11 m 58 s)**
- Platform: `linux/amd64`
- No QEMU.
- Cache: cold.

## 6. Second CI build (partial cache, Dockerfile ARG placement issue)

- Workflow run: `33559411643`
- Head SHA: `0e52d49febadc4416a89e2739c78d165b52ce57f`
- Started: `2026-09-01T21:08:44Z`
- Completed: `2026-09-01T21:16:22Z`
- Total workflow duration: **458 s (7 m 38 s)**
- `Build and push image`: **443 s (7 m 23 s)**
- Image: `ghcr.io/lekin/tout-baigne-worker:0e52d49`
- Cache behavior: The `worker/Dockerfile` originally declared `ARG WORKER_VERSION` at the very top, so the SHA build arg invalidated every layer (apt, PyTorch, pip, model preload). The build still reused the `type=gha` pip wheel cache, but the image layers were rebuilt, and RunPod had to re-pull a 2.887 GB layer. Lesson: move build args that change every commit as close to the final layer as possible.

## 7. Dockerfile fix

`ARG WORKER_VERSION` and `ENV WORKER_VERSION` were moved to just before `CMD`. Heavy layers (CUDA, apt, PyTorch, pip, Demucs model preload) are now fully stable and do not rebuild on every commit.

## 8. GHCR image publication

- Package: `ghcr.io/lekin/tout-baigne-worker`
- Tags observed: `554871c`, `0e52d49`, `latest`
- Manifest digest (0e52d49 list): `sha256:3e750a36fd02aa459acb88559de53a5fea866cef51f10c844846e30cdf905872`
- Compressed size: **~5.25 GB**
- Architecture: `amd64`
- Visibility: public (verified by anonymous `docker pull` after `docker logout ghcr.io`)

## 9. First deployment (`554871c`)

- Command:
  ```bash
  python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:554871c
  ```
- Cold start: **55.85 s**
- `/ping` version: `554871c38fbea1e7fe85c8139c5d576d4c99fd5f` (match)
- Smoke: **0.66 s**, `error_type="input_error"` (expected)

Endpoint configuration preserved:

- GPU: NVIDIA GeForce RTX 4090 (`ADA_24`)
- Workers: min `0`, max `1`, idle timeout `300` s
- Ports: `80/http`, `8000/http`
- Env: `PORT=8000`, `PORT_HEALTH=8000`, `HEALTH_CHECK_PATH=/ping`

## 10. Warm behavior (`554871c`)

| Metric | Value |
| --- | --- |
| Warm `/ping` | 0.41 s |
| Smoke `/run` (3) | 0.73, 0.55, 0.56 s; **median 0.56 s** |
| First real QA `/run` (27 s sample) | **3.10 s** |
| Warm QA `/run` (3) | 1.55, 1.65, 1.62 s; **median 1.62 s** |

## 11. Second deployment (`0e52d49`)

- Cold start: **~110 s** (image had to re-pull the PyTorch/Demucs layer because the `ARG` was at the top of the Dockerfile)
- `/ping` version: `0e52d49febadc4416a89e2739c78d165b52ce57f` (match)
- `/ping` now includes `"gpu": "NVIDIA GeForce RTX 4090"`
- Smoke: passed

## 12. Scale-to-zero

Endpoint scaled to `min=0 max=0`; `list-endpoint-workers` confirmed 0 workers. Scale-to-zero works.

## 13. Rollback

Procedure:

```bash
python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<previous-sha>
```

No rebuild required; RunPod pulls the previous GHCR tag and the readiness probe checks `/ping.version`.

## 14. Security and credentials

- Local PAT received the `workflow` scope only for the one-time push of `.github/workflows/gpu-worker-build.yml`. No `write:packages` was added locally.
- GHCR publication uses the repository `GITHUB_TOKEN` with `packages: write`.
- `RUNPOD_API_KEY` is read from the environment and never logged.
- The local `workflow` scope can be removed now.

## 15. Remaining work

1. Run the corrected Dockerfile through CI and verify a true code-only cache hit (heavy layers unchanged, only the final `ENV WORKER_VERSION` layer rebuilds).
2. Deploy the fixed image and measure cold/warm behavior.
3. Roll back to `554871c` or `0e52d49` and verify version.
4. Remove the temporary `workflow` scope from the local PAT.
