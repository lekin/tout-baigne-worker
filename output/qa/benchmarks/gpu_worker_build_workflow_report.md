# GPU Worker Build & Deployment Workflow Report

## 1. Objective

Move production image builds away from the local M3 (`arm64` → `linux/amd64` emulation) to GitHub-hosted `linux/amd64` runners, keep heavy CUDA/PyTorch/Demucs/model layers stable, and make worker code changes cheap to deploy.

## 2. Previous build path

- Local `docker buildx build --platform linux/amd64 -f worker/Dockerfile --push .`
- On M3 Mac this runs through QEMU AMD64 emulation.
- First observed build:
  - pip/PyTorch/Demucs install: ~270 s
  - model preload: ~78 s
  - exporting/compressing/pushing: several minutes
  - total: 15+ minutes
- Image size (uncompressed): ~5.55 GB (`docker inspect` -> 5,553,734,673 bytes)
- Every `COPY src /app/src` after a code change invalidated the model-preload layer, so even tiny worker changes rebuilt the Demucs/model layers unless the heavy dependency layers were already in the local BuildKit cache.
- The local Docker context was large (`output/` is 66 GB on disk, plus media files) and required a tight `.dockerignore` to keep builds sane.

## 3. New build path

- Repository: `https://github.com/lekin/tout-baigne-worker`
- GitHub Actions workflow: `.github/workflows/gpu-worker-build.yml`
  - trigger: `push` to `main` filtered to `worker/**`, `src/qa/**`, `worker/Dockerfile`, `worker/requirements.txt`
  - manual `workflow_dispatch` supported
  - runner: `ubuntu-latest` (`linux/amd64` native)
  - Buildx with `cache-from: type=gha` / `cache-to: type=gha,mode=max`
  - pushes to `ghcr.io/${{ github.repository }}` with three tags:
    - short git SHA
    - semver if a release
    - `latest`
- Docker Hub is not required; GHCR is used with the standard `GITHUB_TOKEN`.

## 4. Dockerfile layering

`worker/Dockerfile` order is now:

1. `FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04`
2. `apt-get install python3.10, ffmpeg, libsndfile1`
3. install CUDA `torch==2.5.1+cu121`, `torchaudio==2.5.1+cu121`
4. `COPY worker/requirements.txt` (small file) then `pip install -r worker/requirements.txt` (Demucs, scipy, numpy, FastAPI, etc.)
5. `COPY src /app/src` and `COPY worker /app/worker`
6. `RUN python3 - <<'PY'` preloads `htdemucs` weights into the image

Because step 5 (`COPY src /app/src`) comes after the heavy install/preload steps, editing worker/QA Python code only re-copies the small application layers.

## 5. Two-image (base + worker) evaluation

**Recommendation: do not split into a separate `tout-baigne-worker-base` image yet.**

Rationale:
- The single Dockerfile already isolates stable layers from application layers.
- BuildKit `type=gha` cache will persist the apt, torch, pip, and model layers across CI builds.
- A separate base image adds registry/package-visibility complexity (two images, two update paths, cross-repo access) without a measured benefit.
- When other GPU workers (transcription, video, vision) appear, a shared base can be extracted later by moving the stable instructions (steps 1-6 above) into `worker/Dockerfile.base` and changing the worker images to `FROM tout-baigne-worker-base:...`. The current layout is already structured to make that cut trivial.

## 6. `.dockerignore` improvements

Added exclusions for:

- `.git`, `.env`, virtual environments
- `output/`, `input/`, `overlays/` (large local media)
- `*.mov`, `*.mp4`, `*.mp3`, `*.wav`, etc.
- `*.png`, `*.jpg`, `*.ttf`
- `legacy/`, `examples/`, `docs/`
- `__pycache__`, `*.pyc`

`output/` alone is 66 GB; without these exclusions the build context would be enormous.

Context size (tar of non-ignored files, measured with `tar --exclude-from=.dockerignore`): TBD.

## 7. Image size

- Uncompressed: ~5.55 GB
- Largest layers: NVIDIA CUDA/cuDNN base, PyTorch + cu121 wheels, ffmpeg/libav dependency tree, Demucs + scientific Python.
- Demucs `htdemucs` model weights (~80 MB) are inside the image; first request does not download them.

## 8. GHCR authentication & visibility

- CI uses the built-in `secrets.GITHUB_TOKEN` with `permissions: packages: write`.
- Package is at `ghcr.io/lekin/tout-baigne-worker`.
- For public repos the package should default to public visibility, but verify in **GitHub → Settings → Packages** because private packages block RunPod pulls.
- For RunPod to pull a private GHCR image, create a RunPod Container Registry Auth and attach its ID to the endpoint/template.

## 9. Deployment procedure

```bash
export RUNPOD_API_KEY=...
export RUNPOD_ENDPOINT_ID=ylkhb72ej3hijz

python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<sha-or-version>
```

The script:
1. Reads the current endpoint/template.
2. Updates the template `imageName` to the requested tag.
3. Cycles workers (0 → wait → 1) to force a cold start.
4. Polls `https://$RUNPOD_ENDPOINT_ID.api.runpod.ai/ping`.
5. Runs `scripts/smoke_test_worker.py`.

## 10. Smoke test

```bash
export RUNPOD_ENDPOINT_BASE_URL=https://ylkhb72ej3hijz.api.runpod.ai
python scripts/smoke_test_worker.py
```

Sends a trivial `/run` request and checks `success`, `error`, and `error_type` fields.

## 11. Rollback

Because every deploy creates an immutable tag (git SHA), rolling back is:

```bash
python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<previous-sha>
```

No rebuild is needed; RunPod pulls the previous image from GHCR.

## 12. Readiness probe

`scripts/check_runpod_worker.py` has been added with the following behavior:

- Reads `RUNPOD_API_KEY` from the environment and sends `Authorization: Bearer <key>` on every request.
- Fails fast on `401`/`403` (verified with an invalid key against `ylkhb72ej3hijz`):

  ```text
  01   0.46s   HTTP 401   request=0.45s   body={"status":401,"title":"Unauthorized","detail":"invalid api key"}
  ready=False
  error_type=authentication_failed
  ```

- Logs every attempt with elapsed time, HTTP status, request duration, and a short body preview.
- Uses `time.monotonic()` for elapsed timing and a real overall deadline.
- Retries `408`, `429`, `500`, `502`, `503`, `504` with a configurable poll interval.
- Optionally checks `/ping` `version` against an expected git SHA.
- Supports `--endpoint`, `--expected-version`, `--timeout`, `--poll-interval`, and `--no-smoke`.

Unit tests are in `scripts/tests/test_check_runpod_worker.py` and cover:

- `503 → 503 → 200`
- `401`
- `403`
- network timeout → `200`
- continuous `503` until deadline
- version mismatch
- version match

## 13. Local verification

- `worker/handler.py` starts locally on MPS and preloads `htdemucs`.
- `GET /ping` returns `{"status":"ok","version":"test-sha"}` when `WORKER_VERSION=test-sha`.
- `POST /run` with an invalid audio URL returns `success=false`, `error_type="input_error"`, matching the smoke-test contract.
- `python scripts/smoke_test_worker.py` against `http://localhost:8000` passes:
  - `/ping` status 200, version `local-test`
  - `/run` status 200, `success=false`, `error_type="input_error"`
- `python -m pytest src/qa/tests/test_qa.py`: 15 passed.
- `.venv/bin/python scripts/tests/test_check_runpod_worker.py`: 10 passed.

## 14. Measured RunPod cold/warm behavior

No worker is currently running because the container image cannot be pulled:

- `docker push ghcr.io/lekin/tout-baigne-worker:0.1.0` from the local M3 fails with `403 Forbidden` from GHCR, because the local GitHub PAT does not have `write:packages` scope.
- `ttl.sh/lekin-tout-baigne-worker:6h` could not be pulled by RunPod (`IMAGE_NOT_FOUND`), and later `docker pull --platform linux/amd64` from the local network could not reach `ttl.sh`.
- The readiness probe was run against the stopped endpoint and correctly timed out with transparent `TIMEOUT` logs after the configured deadline, rather than silently hanging.

Cold/warm E2E timings will be measured once the image is available in GHCR via CI.

## 15. Workflow validation

The workflow at `.github/workflows/gpu-worker-build.yml` has been validated:

- YAML syntax: OK (checked with `python -m yaml.safe_load`).
- Runner: `ubuntu-latest` building `linux/amd64` natively.
- GHCR login: uses `docker/login-action` with `username: ${{ github.actor }}` and `password: ${{ secrets.GITHUB_TOKEN }}`.
- Permissions: `contents: read` and `packages: write`.
- Tags: `type=sha,format=short`, `type=semver,pattern={{version}}`, `latest` on default branch.
- Build args: `WORKER_VERSION=${{ github.sha }}`.
- BuildKit cache: `cache-from: type=gha` / `cache-to: type=gha,mode=max`.
- Post-push step: attempts to set the GHCR package visibility to public.
- Path filters include `worker/**`, `src/qa/**`, `.github/workflows/gpu-worker-build.yml`, `worker/Dockerfile`, `worker/requirements.txt`.

## 16. Remaining blockers / next steps

1. **GitHub PAT scope for workflow push**: the local PAT has `repo` but not `workflow`, so it cannot create or push `.github/workflows/gpu-worker-build.yml`. The minimal one-time fix is either:
   - temporarily add the `workflow` scope to the local PAT, `git push`, then revoke it, or
   - create the file manually in the GitHub UI.
   No `write:packages` is required on the local Mac.
2. **GHCR package visibility**: the first CI run will create the package. The workflow includes a step to set it public; if that fails, set it manually in **Package settings → Change visibility**.
3. **Image pull time**: once the image is in GHCR, pulling the ~5.55 GB image to RunPod will likely be the dominant cold-start cost.
4. **CI build timing**: needs to be measured on the first real CI run (cold cache vs. warm cache vs. code-only change).
5. **Deployment and smoke test**: after CI publishes the SHA image, run `scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<sha>` and verify `/ping.version == <sha>`.

## 15. Reusable pattern for future GPU workers

- Keep a `worker/Dockerfile` per service with the same heavy-layers-first order.
- Use `docker/metadata-action` for immutable tags.
- Use `cache-from: type=gha` / `cache-to: type=gha,mode=max`.
- Use `scripts/deploy_runpod_worker.py` and `scripts/smoke_test_worker.py` as templates.
- Extract `worker/Dockerfile.base` only once two or more services share identical CUDA/PyTorch/Demucs/version sets.
