# tout-baigne-worker

GPU worker images for karaoke sync QA on RunPod.

## Build

Images are built natively on `linux/amd64` by GitHub Actions. The local Mac does **not** need `write:packages`.

- `.github/workflows/gpu-worker-build.yml` builds and pushes to `ghcr.io/lekin/tout-baigne-worker`
- Uses `secrets.GITHUB_TOKEN` with `permissions: packages: write` for GHCR login and push
- Tags: short git SHA (deploy source of truth), semver, `latest`
- BuildKit layer caching is enabled (`type=gha`) so CUDA/PyTorch/Demucs/model layers reuse on rebuilds
- After the first push, CI attempts to set the package visibility to public

**One-time setup:** the workflow file is in the local tree at `.github/workflows/gpu-worker-build.yml` but cannot be pushed by the current PAT because it lacks the `workflow` scope. Choose one:
1. Temporarily add the `workflow` scope to your local PAT, run `git push`, then revoke the scope.
2. Create `.github/workflows/gpu-worker-build.yml` in the GitHub UI using the file content from the working tree.

After the workflow exists remotely, a qualifying push or `workflow_dispatch` triggers the build.

Manual workflow dispatch is supported.

### Local worker smoke test

```bash
# Starts worker/handler.py on port 8000 and runs scripts/smoke_test_worker.py
scripts/test_worker_local.sh

# Or manually:
scripts/run_worker_local.sh
# in another shell:
RUNPOD_API_KEY=dummy RUNPOD_ENDPOINT_BASE_URL=http://localhost:8000 \
  python scripts/smoke_test_worker.py
```

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
