# Project notes

## @platform/discovery (SEO + GEO for public sites)

- Package lives in `~/Code/platform/packages/discovery` (separate `platform` repo, npm workspaces). Ships TS source, consumed via `"@platform/discovery": "file:../../platform/packages/discovery"` + `transpilePackages` in `www/next.config.ts` (with `turbopack.root` at `~/Code`).
- Generic entities only — no TBP domains/brands/facts in the package. All TBP knowledge lives in `www/lib/discovery/` (`entities.ts` = pure adapter, `data.ts` = fetching). `TBP_ORGANIZATION` is declared once; relations flow site → brand → org → event.
- `entity.id` is stable and URL-independent (caller `id` or name slug); JSON-LD `@id` is `${url}#${kind}` per render.
- Event pages: `/events/<title>-<date>-<venue>-<postId>`; the trailing `rec…` post id resolves the page even if title/date/venue change (`eventPostId`). Past events keep their page (`mapPostToPublicEvent(record, {includePast:true})`, status `completed`); a post only resolves on a site that published it (`Website` field check).
- Commands: `npm run discovery:audit` (tsx, loads `.env.local`, audits all 4 hosts live), `npx vitest run lib/discovery` (TBP answer-engine QA), `npm run typecheck` in `platform/packages/discovery` (package tests use fictional fixtures).
- Facts shown in `EntityFacts` come from `geo.representation` — the same canonical data that feeds JSON-LD. `auditVisibleFacts` flags facts missing from rendered HTML.

## @platform/notifications (operational alerts)

- Package lives in `~/Code/platform/packages/notifications`. Operational escalation layer only (failures, stale data, SLA breaches, blockers, approvals) — never marketing/campaigns. Producers call `service.request({source, category, severity, recipient(s), title, message, actionUrl, deduplicationKey})` and nothing else.
- Canonical tables (admin migration 041; generic DDL in package `migrations/`): `notifications` + `notification_recipients` (per-recipient `channel_plan`, `read_at`) + `notification_deliveries` (attempts/backoff) + `notification_dedup` (ledger, `occurrences` counts suppressed repeats). Dedup claim and delivery claim are atomic RPCs (`request_notification`, `claim_due_notification_deliveries` — SKIP LOCKED); the dedup window is anchored at first claim so a continuously failing process re-alerts on a bounded cadence.
- Delivery pump = the admin scheduler (`Notifications dispatch` every 1m → `dispatchPending`); retries are `next_attempt_at` re-queues, not an internal loop. V1 channel: `in_app` (`InAppChannelAdapter`); new channels implement `NotificationChannelAdapter` and plug into `channel_plan` fallback.
- TBP wiring in `admin/lib/notifications/`: `service.ts` (service-role repo + health/audit sinks), `recipients.ts` (`role:admin` → memberships of the `type='admin'` org — same rule as `is_tbp_admin()`), `queries.ts` (caller-uid-scoped reads). UI: `components/app/notifications-bell.tsx` in the nav, actions in `app/actions/notifications.ts`.
- First vertical slice: `reportSyncFailure` in `admin/lib/scheduler.ts` escalates every scheduled-task failure (dedup key `scheduler-failure:<component>`, actionUrl `/platform`).
- Lifecycle events are audit actions `notification.requested/.suppressed/.sent/.delivered/.failed/.read`; dispatch reports `tbp.notifications.dispatch` to component_health.
- Commands: `npx vitest run` + `npm run typecheck` in the package; live smoke `npx tsx --env-file=.env.local --tsconfig tsconfig.json scripts/smoke-notifications.ts` (Node 22+).

## @platform/artifacts (TBP Partner Packs)

- Generic portable-artifact capability lives in `~/Code/platform/packages/artifacts` (model, section registry, renderer, publication/access). TBP Partner Packs are the first consumer — all TBP knowledge stays in `admin/lib/artifacts/`.
- TBP wiring: `lib/artifacts/service.ts` (`artifactsService()` — service-role repo, health `tbp.artifacts.*`, audit `artifact.*`), `packs.ts` (the domain adapter: `PACK_TEMPLATES` + `buildPackDraft(event, templateId)` — the only place Event/Venue/Staff → sections mapping lives), `queries.ts` (per-event list via `context->>'eventId'`).
- Tables `artifacts`/`artifact_versions`/`artifact_publications` (migration `072_artifacts.sql`, generic DDL in package `migrations/`). RLS: admin select only — public hosted access is service-role + hashed token; raw tokens are never stored (SHA-256), shown once at publish, `token_hint` identifies the live link.
- Hosted view: `app/a/[token]/route.ts` → `service.renderPublicArtifact(token)` (no-store, noindex, 410 for revoked/expired, non-enumerating errors). `proxy.ts` matcher excludes `a/` — no auth.
- Admin flow: event page → "Partner packs" (`/events/[id]/packs`) → `/packs/new?template=` → `PackEditor` (include/exclude sections, JSON payload overrides, live preview via the same `renderArtifactDocument`, expiry select, publish/rotate/revoke/duplicate/Download-HTML). Actions in `app/actions/artifacts.ts` — all `requireAdmin()`; the envelope (context, issuer) is re-derived server-side from canonical data.
- Republish keeps the same URL (new version snapshot behind the active publication); `rotatePackLink` mints a fresh link and kills the old one. Templates: `tbp-dj-pack`, `tbp-venue-pack`, `tbp-production-pack`.
- Commands: `npx vitest run lib/artifacts` (adapter tests), live smoke `npx tsx --env-file=.env.local --tsconfig tsconfig.json scripts/smoke-artifacts.ts` (Node 22+). Package: `npx vitest run` + `npm run typecheck` in `platform/packages/artifacts`.

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
- New `audio_separator` backend (python-audio-separator / `audio-separator==0.47.0`) exposes a common interface for MDX/MDXC/MelBand RoFormer/BS RoFormer. Use `separator_backend="audio_separator"` and `separator_model="<filename or alias>"`. Friendly aliases: `mdx23c`, `melband_roformer`, `bs_roformer`, `kuielab`. Any `.ckpt`/`.onnx`/`.pth` filename is routed to this backend.
- `AudioQARequest.separator_model` now defaults to `kuielab_a_vocals.onnx` (audio_separator). It is ~4–6× faster than htdemucs on M3 and achieves parity (SYNC_VERIFIED/good) on all 5 tested tracks. `MDX23C` and `MelBandRoformerSYHFTV3Epsilon` are much slower on MPS and do not beat htdemucs. `batch_size>1` for MDX models does not help.
- `audio_separator` architecture-specific params can be passed via `AudioQARequest.separator_params` (e.g. `{"mdx": {"batch_size": 2, ...}}`) and are included in the vocal stem cache key.
- The local `.venv` runs torch 2.8; `audio-separator` (via `onnx2torch-py313`) needs `torch>=2.13`, so `qa_vocal_separator` still defaults to `demucs` for the `KaraokeQARunner` path. Use `/tmp/venv_pas` or a torch≥2.13 env for `run_audio_qa` with `audio_separator`.
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

## Remote karaoke render (definitive renders on RunPod)

`generate --remote` renders the definitive karaoke MP4 on a RunPod worker and downloads it locally — **no Airtable upload, no Airtable credentials sent to the worker**. Sync previews stay local.

### Endpoints

- **CPU render (recommended)**: `5mdcsyq1hm8ryx` — Serverless CPU `cpu3c` compute-optimized, 16 vCPU / 32 GB, LOAD_BALANCER, same worker image. libx264 with 16 cores beats the GPU worker's weak CPU.
- **GPU (audio QA + optional render)**: `ylkhb72ej3hijz` — RTX 4090, `ADA_24`. NVENC does **not** work on RunPod serverless (`OpenEncodeSessionEx: unsupported device` even with `NVIDIA_DRIVER_CAPABILITIES=all` + `video` in driver_caps — platform limitation). `encoder=auto` falls back to libx264, but the GPU worker's CPU is ~6× slower than cpu3c on this pipeline.

### Usage

```bash
export RUNPOD_API_KEY=...   # ~/.runpod/config.toml
python -m src.cli generate --record-id <id> --remote \
  --remote-endpoint 5mdcsyq1hm8ryx --encoder x264 \
  --overlay overlays/optical2_1080p30.mp4 --no-upload --output out.mp4
```

- `--remote` → `_generate_remote` in `src/cli.py`; payload = record id, track name, ASS/SRT, audio/video/GDrive/YouTube URLs, offsets, encoder, overlay name (resolved in-image), dimensions. Nothing secret.
- `--encoder auto|nvenc|x264|videotoolbox` (auto = nvenc if usable else x264; videotoolbox = local macOS fast mode).
- `--no-upload` also skips Airtable upload + WhatsApp for local renders.

### Async flow (required)

The LB drops HTTP requests after ~3 min and renders take 1–15 min, so `POST /render` with `return_mode=file_token` returns a `job_token` immediately; the render runs in a thread. Client polls `GET /render/{token}` (HTTP 500 carries the failed payload — read it) then downloads `GET /files/{token}` (files live 60 min). Worker-side timings are in the result JSON (`timings` dict, `encoder_used`, `sha256`).

### Performance (record rec1XbI4BcQmkxnhy, 124s 1080p, overlay optical2)

| Target | Wall | Worker render | Encoder |
| --- | --- | --- | --- |
| Local M3 (old two-pass) | 13m07s | — | libx264 |
| Local M3 (single-pass) | 6m40s | — | libx264 |
| **Remote cpu3c 16 vCPU** | **81s** | 60s | libx264 |
| Remote GPU RTX 4090 | fail | nvenc blocked | — |

Remote-vs-local SSIM: 0.979 (same ASS/fonts/overlay → visually identical).

### Gotchas discovered

- The render pipeline is **CPU-bound** (libass + overlay blend + eq); the GPU only accelerates the encoder. Cost: cpu3c ≈ $0.036/vCPU/h → ~$0.013 per 124s render.
- Intro is folded into the render `filter_complex` (`overlay=enable='between(t,0,4)'`) — a second full re-encode pass was eliminated (~2× faster). `apply_intro_overlay` remains in `src/render/steps.py` but is no longer used.
- ASS `Fontname` historically held a `.ttf` path that libass can't resolve (silent fallback). `normalize_ass_font_path` rewrites it to the family name + `fontsdir=` points at the font dir; the image bakes all SpaceMono variants.
- `.dockerignore` must not exclude `overlays/`, `*.mp4`, `*.png`, `*.ttf` — the assets are `git add -f`'d and copied into the image.
- `audio-separator` needs `numpy>=2` + `audioread` + CPU `onnxruntime==1.23.2` (last with py3.10 wheels; onnxruntime-gpu is incompatible with numpy 2 on the cuDNN8 base).
- `/capabilities` reports encoders, `nvenc_probe` (real 1s encode test), fonts, overlays, `cpu_count`.

### Remote compute kill switch (@platform/compute policy)

`COMPUTE_REMOTE_ENABLED=false` (or `0|off|no|disabled`) refuses `--remote` before any RunPod request — same contract as the platform package. `@platform/compute` (`~/Code/platform/packages/compute`) owns the generic policy: execution modes `local|remote|auto`, per-workload defaults (`media.ffmpeg.render`→remote, `music.track.analyze`→local, `video.analyze`→auto), env pins `COMPUTE_DEFAULT_MODE`, `COMPUTE_MODE_<TYPE>`, `COMPUTE_PROVIDER_<TYPE>`, `COMPUTE_MAX_COST_USD`, runtime `ComputeRuntimeConfig` toggle, and a persisted `routing` record on every execution (requestedMode/resolvedMode/provider/reason) so RunPod usage is auditable. DJOS exposes a minimal control surface at `GET|POST /api/compute` (Bearer auth): remoteEnabled toggle, defaultMode, per-workload mode, usage (running count, compute ms, estimated spend).
