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

## 5. Dockerfile layering (final)

`worker/Dockerfile` now keeps heavy layers stable and only changes the final metadata layer per commit:

1. `FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04`
2. `ENV` for `PYTHONUNBUFFERED`, `PIP_NO_CACHE_DIR`, `TORCH_HOME`, `QA_CACHE_DIR`, `QA_WORK_DIR`, `PRELOAD_DEMUCS_MODELS`
3. `RUN apt-get install python3.10 ffmpeg libsndfile1 ...`
4. `RUN update-alternatives` for python
5. `WORKDIR /app`
6. `RUN pip install PyTorch+cu121 torchaudio+cu121`
7. `COPY worker/requirements.txt` + `RUN pip install -r ...` (Demucs + scientific Python)
8. `RUN python3 - <<'PY'` — **pre-download Demucs `htdemucs` model**
9. `COPY src /app/src`
10. `COPY worker /app/worker`
11. `ARG WORKER_VERSION` / `ENV WORKER_VERSION` — **injected at the very end**
12. `EXPOSE 8000` / `CMD`

Lessons learned and applied:

- `ARG WORKER_VERSION` at the top invalidated every subsequent layer on every commit → moved to the final step.
- The model preload ran *after* `COPY src /app/src` and `COPY worker /app/worker`, so every code change forced a ~10-minute model re-download → preloaded *before* copying application code.
- The preload no longer imports `src.qa.separator`; it only calls `demucs.pretrained.get_model`, keeping the model layer fully stable.

## 6. CI build timing summary

| Run | Head SHA | Build & push | Workflow total | Notes |
| --- | --- | --- | --- | --- |
| 1 | `554871c` | 11 m 58 s | 12 m 29 s | Cold cache, no prior `type=gha` build. |
| 2 | `0e52d49` | 7 m 23 s | 7 m 38 s | Partial cache; `ARG WORKER_VERSION` still at top, heavy layers rebuilt. |
| 3 | `2fb3d75` | 8 m 40 s | 9 m 3 s | First Dockerfile arg move, but cache chain changed, heavy layers rebuilt. |
| 4 | `0d65533` | 11 m 8 s | 11 m 48 s | Model still re-downloaded because preload ran after `COPY`. |
| 5 | `4615377` | 4 m 24 s | 4 m 52 s | Model preload moved before `COPY`; heavy layers cache hit, only final `ENV` rebuilt. |
| 6 | `dfc4d29` | 2 m 54 s | 3 m 47 s | True code-only build; apt/PyTorch/pip/model layers fully CACHED; only `COPY src`, `COPY worker`, and `ENV WORKER_VERSION` changed. |

## 7. GHCR image publication

- Package: `ghcr.io/lekin/tout-baigne-worker`
- Tags observed: `554871c`, `0e52d49`, `2fb3d75`, `4615377`, `0d65533`, `dfc4d29`, `latest`
- Current `latest` manifest list digest: `sha256:15b9828def369aabdcdd4b0190e00f41f501bce11bb6cbbc794e741dfa1090d1`
- Architecture: `linux/amd64`
- Compressed size: ~5.25 GB
- Visibility: public (verified by anonymous `docker pull`)

## 8. RunPod deployment and timing summary

| Image | SHA prefix | Cold start | `/ping` version | Smoke | QA first | QA warm median |
| --- | --- | --- | --- | --- | --- | --- |
| `554871c` | `554871c...` | 55.85 s | match | 0.66 s | 3.10 s | 1.62 s |
| `0e52d49` | `0e52d49...` | ~110 s | match | 0.76 s | — | — |
| `2fb3d75` | `2fb3d75...` | 90.46 s (probe timed out on first try, 60s request too short) | match | 0.76 s | — | — |
| `4615377` | `4615377...` | 123.47 s | match | 0.72 s | — | — |
| `dfc4d29` | `dfc4d29...` | 135.09 s | match | 0.71 s | 3.21 s | 1.70 s |
| rollback `554871c` | `554871c...` | 154.91 s | match | 0.80 s | — | — |

Cold start includes RunPod pulling the ~5.25 GB image. The `type=gha` cache in CI is now healthy, but each new image still needs to be pulled to RunPod unless the host already has the same layer stack. This is a RunPod image-cache concern, not a CI build concern.

Endpoint preserved across all deploys:

- GPU: NVIDIA GeForce RTX 4090 (`ADA_24`)
- Workers: min `0`, max `1`, idle timeout `300` s
- Ports: `80/http`, `8000/http`
- Env: `PORT=8000`, `PORT_HEALTH=8000`, `HEALTH_CHECK_PATH=/ping`
- Startup args: `python3 -u /app/worker/handler.py`

## 9. Smoke and real QA

- Smoke: `POST /run` with empty lyrics returns `success=false`, `error_type="input_error"`, `error="missing lyrics"`. This is the expected contract smoke and runs in ~0.7 s.
- Real QA: used a public 27 s MP3 (`https://files.edge.network/misc/mp3/file_example_MP3_700KB.mp3`) and one-line dummy lyrics. The worker returned `SYNC_FAILED` with a high-confidence `global_offset` diagnosis, confirming the full `audio download → Demucs → structural QA → scoring` path.

## 10. Rollback

```bash
python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:554871c
```

No rebuild. RunPod pulls the previous immutable SHA, the template is updated, and the readiness probe verifies `/ping.version == 554871c...`. Verified successfully.

## 11. Scale-to-zero

After benchmarking, the endpoint was scaled to `min=0 max=0`. `list-endpoint-workers` confirmed 0 workers. No billable worker remained active.

## 12. Security and credentials

- The local GitHub PAT was temporarily granted `workflow` scope only for the one-time push of `.github/workflows/gpu-worker-build.yml`. No `write:packages` scope was added locally.
- GHCR publication uses the repository `GITHUB_TOKEN` with `packages: write`.
- `RUNPOD_API_KEY` is read from the environment and never logged.
- The temporary `workflow` scope can now be removed.

## 13. Local tests

- `python -m pytest src/qa/tests/test_qa.py` — 15 passed.
- `python -m pytest scripts/tests/test_check_runpod_worker.py` — 10 passed.

## 14. Remaining operational notes

- The `type=gha` cache is now correctly configured and gives sub-3-minute code-only builds.
- RunPod cold start is dominated by pulling the 5.25 GB image. Options to reduce further:
  - Use a smaller CUDA base or freeze a worker base image in RunPod.
  - Split into a `tout-baigne-worker-base` image with CUDA/PyTorch/Demucs/model and a thin worker image `FROM` that base; RunPod will only pull the thin worker layer.
  - Use a network volume with the model and application pre-populated.
- The current setup is production-usable and fully automated from `git push` to version-verified deployment.
