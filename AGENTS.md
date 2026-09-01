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
- Type: `LOAD_BALANCER` (RunPod routes HTTP directly to the worker, which is more reliable than the managed queue worker with this image)
- URL: `https://ylkhb72ej3hijz.api.runpod.ai`
- Image: `mimbos/demucs-gpu:latest` (CUDA 12.1, public Docker Hub image, no custom `ENTRYPOINT`, so `args` can be a `bash -c "..."` command)
- GPU pool: `ADA_24` (NVIDIA GeForce RTX 4090)
- Port: `8000/http` with `PORT=8000`, `PORT_HEALTH=8000`, `HEALTH_CHECK_PATH=/ping`

### Important deployment notes

- RunPod v2 with `ENTRYPOINT ["/bin/bash", "--login", "-c"]` (e.g. `maxhollmann/demucs`) splits the `args` string on spaces and uses the first token as the `-c` command. To run a multi-word shell command, use an image with no `ENTRYPOINT` and wrap the whole command in `bash -c "..."`.
- The worker runs a one-shot install at first boot (git clone, pip install) because the container disk is not persisted across workers. For faster cold starts, use a network volume to cache `/workspace/tout-baigne` and `~/.cache/pip`.
- The worker's `/run` endpoint receives a JSON body `{"input": <AudioQARequest>}`. It returns the `AudioQAResult` as a flat dict (not nested under `output`).

### curl example

```bash
export RUNPOD_API_KEY=...
curl -X POST \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d @/tmp/qa_request_wrapped.json \
  --max-time 300 \
  https://ylkhb72ej3hijz.api.runpod.ai/run
```

### Client usage

```bash
# Queue endpoint (legacy)
python scripts/runpod_remote_qa_client.py --record-id <id> --endpoint-id <queue-id>

# Load Balancer endpoint
python scripts/runpod_remote_qa_client.py --record-id <id> --endpoint-id ylkhb72ej3hijz --lb --audio-url <public-mp3-url>
```

For records without a direct `Source Audio URL`, use `--audio-url` to pass a public URL (e.g. a GDrive direct link or a temporary tunnel).

### Status

- 2026-09-01: E2E succeeded on `Khaled - Aïcha` and `Willy Denzey - Le mur du son` using Load Balancer endpoint `ylkhb72ej3hijz` (RTX 4090, Demucs 4.0.1, ~20–30 s wall time).
