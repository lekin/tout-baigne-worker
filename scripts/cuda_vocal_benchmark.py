#!/usr/bin/env python3
"""Benchmark PyTorch Demucs vocal separation on a CUDA worker.

Self-contained: no Airtable, no project .env. Requires torch, torchaudio,
demucs, and ffmpeg/ffprobe.
"""
import argparse
import gc
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import demucs
import torch
import torchaudio
from demucs.apply import apply_model
from demucs.pretrained import get_model


@dataclass
class Track:
    record_id: str
    track_name: str
    audio_path: str
    artist: Any = None
    source_duration_ms: Optional[float] = None


@dataclass
class BenchmarkConfig:
    label: str
    model: str
    shifts: int
    overlap: float
    split: bool
    device: str = "cuda"


@dataclass
class BenchmarkRun:
    track_id: str
    track_name: str
    config_label: str
    model: str
    overlap: float
    shifts: int
    split: bool
    device: str
    source_duration_ms: Optional[float] = None
    stem_duration_ms: Optional[float] = None
    leading_offset_ms: float = 0.0
    trailing_offset_ms: float = 0.0
    duration_delta_ms: float = 0.0
    model_load_time_s: Optional[float] = None
    separation_time_s: Optional[float] = None
    wall_time_s: Optional[float] = None
    real_time_factor: Optional[float] = None
    output_file_size_bytes: Optional[int] = None
    peak_vram_mb: Optional[float] = None
    gpu_utilization_pct: Optional[float] = None
    gpu_name: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BenchmarkManifest:
    tracks: List[Track]
    configs: List[BenchmarkConfig]
    device: str = "cuda"


def _ffprobe_duration(path: str) -> Optional[float]:
    """Return duration in milliseconds."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        data = json.loads(out)
        return float(data["format"]["duration"]) * 1000.0
    except Exception:
        return None


def _ffprobe_start_time(path: str) -> float:
    """Return start time in seconds (usually 0.0)."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        data = json.loads(out)
        return float(data["format"].get("start_time", 0.0))
    except Exception:
        return 0.0


def _nvidia_smi_utilization() -> Dict[str, Any]:
    """Return a snapshot of GPU utilization and memory from nvidia-smi."""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        parts = [p.strip() for p in out.split(",")]
        return {
            "gpu_utilization_pct": float(parts[0]) if parts[0] else None,
            "memory_used_mb": float(parts[1]) if len(parts) > 1 and parts[1] else None,
            "memory_total_mb": float(parts[2]) if len(parts) > 2 and parts[2] else None,
        }
    except Exception:
        return {}


def _apply_model_to_sources(
    model: torch.nn.Module,
    audio_path: str,
    config: BenchmarkConfig,
) -> torch.Tensor:
    """Load audio, resample, and run Demucs apply_model. Return sources tensor."""
    wav, sr = torchaudio.load(audio_path)
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    if sr != model.samplerate:
        wav = torchaudio.transforms.Resample(sr, model.samplerate)(wav)
        sr = model.samplerate

    device = torch.device(config.device)
    with torch.no_grad():
        wav_input = wav.unsqueeze(0).to(device)
        sources = apply_model(
            model,
            wav_input,
            device=device,
            shifts=config.shifts,
            split=config.split,
            overlap=config.overlap,
        )[0]
    return sources, sr


def _run_separation(
    model: torch.nn.Module,
    audio_path: str,
    output_path: str,
    config: BenchmarkConfig,
) -> None:
    """Run Demucs separation and write the vocal stem to output_path."""
    sources, sr = _apply_model_to_sources(model, audio_path, config)
    vocals = sources[3]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(output_path, vocals.cpu(), sr)


def _measure_model_load(
    model_name: str,
    device: torch.device,
    warm_audio_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Load a model and optionally run one warm-up inference (no file output)."""
    t0 = time.perf_counter()
    model = get_model(model_name)
    model.to(device)
    model.eval()
    t1 = time.perf_counter()
    load_time = t1 - t0

    warm_time: Optional[float] = None
    if warm_audio_path and os.path.exists(warm_audio_path):
        t0 = time.perf_counter()
        _apply_model_to_sources(
            model,
            warm_audio_path,
            BenchmarkConfig(label="warm", model=model_name, shifts=0, overlap=0.25, split=True, device=str(device)),
        )
        t1 = time.perf_counter()
        warm_time = t1 - t0
        torch.cuda.synchronize()
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    return {"model": model, "load_time_s": load_time, "warm_time_s": warm_time}


def _run_config_for_track(
    track: Track,
    config: BenchmarkConfig,
    model_cache: Dict[str, torch.nn.Module],
    output_dir: Path,
) -> BenchmarkRun:
    """Separate one track with one config and record everything."""
    run = BenchmarkRun(
        track_id=track.record_id,
        track_name=track.track_name,
        config_label=config.label,
        model=config.model,
        overlap=config.overlap,
        shifts=config.shifts,
        split=config.split,
        device=config.device,
    )

    if not os.path.exists(track.audio_path):
        run.error = f"Audio file not found: {track.audio_path}"
        return run

    source_duration_ms = track.source_duration_ms or _ffprobe_duration(track.audio_path)
    run.source_duration_ms = source_duration_ms

    output_path = output_dir / f"{track.record_id}_{config.label}_vocals.wav"

    model_key = f"{config.model}:{config.device}"
    if model_key not in model_cache:
        info = _measure_model_load(config.model, torch.device(config.device), warm_audio_path=track.audio_path)
        model_cache[model_key] = info["model"]
        run.model_load_time_s = info["load_time_s"] + (info["warm_time_s"] or 0.0)

    model = model_cache[model_key]

    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()

    _run_separation(model, track.audio_path, str(output_path), config)

    torch.cuda.synchronize()
    t1 = time.perf_counter()

    run.separation_time_s = t1 - t0
    run.wall_time_s = run.separation_time_s
    if run.source_duration_ms:
        run.real_time_factor = run.separation_time_s / (run.source_duration_ms / 1000.0)

    run.peak_vram_mb = torch.cuda.max_memory_allocated() / 1e6
    nvidia = _nvidia_smi_utilization()
    run.gpu_utilization_pct = nvidia.get("gpu_utilization_pct")
    run.gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None

    run.output_file_size_bytes = os.path.getsize(output_path)
    stem_duration_ms = _ffprobe_duration(str(output_path))
    run.stem_duration_ms = stem_duration_ms
    if source_duration_ms and stem_duration_ms:
        run.duration_delta_ms = stem_duration_ms - source_duration_ms
        start = _ffprobe_start_time(str(output_path))
        run.leading_offset_ms = start * 1000.0
        run.trailing_offset_ms = (source_duration_ms / 1000.0 - stem_duration_ms / 1000.0 - start) * 1000.0

    return run


def _serialize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialize(getattr(obj, k)) for k in obj.__dataclass_fields__}
    return obj


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="Path to benchmark manifest JSON.")
    parser.add_argument("--output-dir", required=True, help="Directory for stems and results.")
    parser.add_argument("--warm-up", action="store_true", default=True, help="Run a warm-up per model (default true).")
    parser.add_argument("--no-warm-up", action="store_false", dest="warm_up", help="Disable warm-up.")
    args = parser.parse_args()

    with open(args.manifest, "r", encoding="utf-8") as f:
        raw = json.load(f)

    manifest = BenchmarkManifest(
        tracks=[Track(**t) for t in raw["tracks"]],
        configs=[BenchmarkConfig(**c) for c in raw["configs"]],
        device=raw.get("device", "cuda"),
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pytorch_version": torch.__version__,
        "demucs_version": demucs.__version__,
        "cuda_available": torch.cuda.is_available(),
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
        "manifest_path": str(Path(args.manifest).resolve()),
        "output_dir": str(output_dir.resolve()),
    }

    print(f"🖥️  Worker metadata: {json.dumps(metadata, indent=2)}")
    print(f"🎤 Benchmarking {len(manifest.tracks)} track(s) with {len(manifest.configs)} config(s).")

    model_cache: Dict[str, torch.nn.Module] = {}
    results: List[BenchmarkRun] = []

    for track in manifest.tracks:
        print(f"\n[{track.record_id}] {track.track_name}")
        for config in manifest.configs:
            print(f"  → {config.label} ({config.model}, overlap={config.overlap})")
            run = _run_config_for_track(track, config, model_cache, output_dir)
            results.append(run)
            if run.error:
                print(f"     ❌ {run.error}")
            else:
                print(
                    f"     ✅ {run.separation_time_s:.2f}s "
                    f"(RTF={run.real_time_factor:.3f}, "
                    f"VRAM={run.peak_vram_mb:.0f}MB)"
                )

    metadata["end_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metadata["total_separation_time_s"] = sum(
        (r.separation_time_s or 0.0) for r in results
    )
    metadata["total_wall_time_s"] = sum(
        (r.separation_time_s or 0.0) for r in results
    )

    out_data = {
        "metadata": metadata,
        "runs": _serialize(results),
    }
    out_path = output_dir / "cuda_vocal_benchmark_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)

    print(f"\n📊 Results written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
