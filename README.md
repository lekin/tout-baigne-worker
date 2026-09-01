# tout-baigne-worker

GPU worker images for karaoke sync QA on RunPod.

## Build

Images are built natively on `linux/amd64` by GitHub Actions.

- `.github/workflows/gpu-worker-build.yml` builds and pushes to `ghcr.io/lekin/tout-baigne-worker`
- Tags: short SHA, semver, `latest`
- BuildKit layer caching is enabled (`type=gha`) so CUDA/PyTorch/Demucs/model layers reuse on rebuilds.

Manual workflow dispatch is supported.

## Deploy

```bash
export RUNPOD_API_KEY=...
export RUNPOD_ENDPOINT_ID=ylkhb72ej3hijz
python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<sha-or-version>
```

The script:
1. Updates the endpoint template to the requested image tag.
2. Scales workers to 0 and then back to 1 for a clean cold start.
3. Polls `/ping` until healthy.
4. Runs a smoke test.

## Local debugging (not the normal production path)

```bash
docker buildx build --platform linux/amd64 -f worker/Dockerfile -t tout-baigne-worker:local .
```

## Worker layout

- `worker/Dockerfile` — CUDA/PyTorch/Demucs image.
- `worker/handler.py` — FastAPI Load Balancer handler.
- `src/qa/` — audio QA modules.
- `worker/requirements.txt` — minimal runtime dependencies.
