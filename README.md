# tout-baigne-worker

GPU worker images for karaoke sync QA on RunPod.

## Build

Images are built natively on `linux/amd64` by GitHub Actions. The local Mac does **not** need `write:packages`.

- `.github/workflows/gpu-worker-build.yml` builds and pushes to `ghcr.io/lekin/tout-baigne-worker`
- Uses `secrets.GITHUB_TOKEN` with `permissions: packages: write` for GHCR login and push
- Tags: short git SHA (deploy source of truth), semver, `latest`
- BuildKit layer caching is enabled (`type=gha`) so CUDA/PyTorch/Demucs/model layers reuse on rebuilds
- After the first push, CI attempts to set the package visibility to public

The workflow file is already pushed to the remote repository. Future changes to `worker/**`, `src/qa/**`, `worker/Dockerfile`, `worker/requirements.txt`, or `.github/workflows/gpu-worker-build.yml` will trigger a build automatically. You can also trigger a manual build with `workflow_dispatch`.

The local PAT does **not** need `write:packages`; GHCR push uses the repository `GITHUB_TOKEN`.

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
3. Polls `/ping` until healthy and verifies the `/ping.version` field matches the git SHA.
4. Runs a smoke test.

Rollback is just re-running the deploy with a previous SHA:

```bash
python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<previous-sha>
```

No rebuild is required.

## Steady-state production configuration

- Endpoint: `ylkhb72ej3hijz` (`audio-qa-lb`)
- Image: `ghcr.io/lekin/tout-baigne-worker:<sha>` (currently `dfc4d29`)
- GPU pool: `ADA_24` (RTX 4090)
- `flashboot`: `FLASHBOOT`
- `workersMin`: `0`
- `workersMax`: `2`
- `idleTimeout`: `300`
- `scaling`: `REQUEST_COUNT` with `requestCount: 1`

With this setup the endpoint keeps a small **idle** warm pool (flashboot) so first requests start quickly; active workers scale down to zero when idle.

## Local debugging (not the normal production path)

```bash
docker buildx build --platform linux/amd64 -f worker/Dockerfile -t tout-baigne-worker:local .
```

## Worker layout

- `worker/Dockerfile` — CUDA/PyTorch/Demucs image.
- `worker/handler.py` — FastAPI Load Balancer handler.
- `src/qa/` — audio QA modules.
- `worker/requirements.txt` — minimal runtime dependencies.
