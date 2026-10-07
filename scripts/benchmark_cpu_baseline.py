#!/usr/bin/env python3
"""Benchmark CPU Demucs vocal separation + Stable-ts QA baseline for Sia - Chandelier."""
import sys
import time
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import torch
import torchaudio

from demucs.pretrained import get_model
from demucs.apply import apply_model

from src.airtable_client import AirtableClient
from src.qa.audio_sync import StableTsAligner
from src.qa.runner import KaraokeQARunner
from src.qa.cache import hash_audio_file, vocals_cache_key, cached_vocals_path
from src.qa.timeline import build_timeline_transform

RECORD_ID = "rec1Lg9F36BYbHMH1"


def main():
    print("=== CPU Demucs baseline for Sia - Chandelier ===")
    record = AirtableClient().get_record(RECORD_ID)
    fields = record["fields"]
    audio_path = KaraokeQARunner(verbose=False)._download_source_audio(record)
    if not audio_path:
        print("Could not download source audio")
        return 1

    import json
    source_duration_ms = float(json.loads(__import__("subprocess").run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
        capture_output=True, text=True, check=True
    ).stdout)["format"]["duration"]) * 1000.0

    print(f"Source audio: {audio_path}")
    print(f"Source duration: {source_duration_ms/1000:.2f}s")

    # Load and resample
    wav, sr = torchaudio.load(audio_path)
    model = get_model("htdemucs")
    model.to("cpu")
    model.eval()
    if sr != model.samplerate:
        resampler = torchaudio.transforms.Resample(sr, model.samplerate)
        wav = resampler(wav)
        sr = model.samplerate

    # Timing includes model load? No, model loaded above. This is pure separation.
    print("Running apply_model on CPU (shifts=0, split=True, overlap=0.25)...")
    t0 = time.perf_counter()
    with torch.no_grad():
        sources = apply_model(model, wav.unsqueeze(0).to("cpu"), device="cpu", shifts=0)[0]
    t1 = time.perf_counter()
    separation_s = t1 - t0
    print(f"Separation wall time: {separation_s:.2f}s")

    vocals = sources[3]
    vocals_path = cached_vocals_path(vocals_cache_key(audio_path, separator="demucs"))
    Path(vocals_path).parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(vocals_path, vocals.cpu(), sr)
    print(f"Vocals saved: {vocals_path}")

    # Measure stem duration and offsets
    stem_info = json.loads(__import__("subprocess").run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", vocals_path],
        capture_output=True, text=True, check=True
    ).stdout)["format"]
    stem_duration_ms = float(stem_info["duration"]) * 1000.0
    print(f"Stem duration: {stem_duration_ms/1000:.2f}s")
    print(f"Duration delta: {stem_duration_ms - source_duration_ms:.2f}ms")

    # Build lyrics and transform
    runner = KaraokeQARunner(aligner=StableTsAligner(), verbose=False)
    transform = build_timeline_transform(record)
    lyrics = runner._build_structured_lyrics(fields, transform, source_duration_ms)

    def _extract_artist(fields):
        for key in ["Artist (string)", "Artist", "Name (from Artist)", "Artist name", "Artists"]:
            v = fields.get(key)
            if v:
                if isinstance(v, list):
                    return str(v[0]) if v else ""
                return str(v)
        return ""

    report = runner.evaluate(
        record_id=RECORD_ID,
        track_name=fields.get("Name", ""),
        artist_name=_extract_artist(fields),
        musixmatch_track_id=fields.get("Musixmatch Track ID"),
        transform=transform,
        audio_path=audio_path,
        lyrics=lyrics,
        source_duration_ms=source_duration_ms,
        vocals_path=vocals_path,
        save_report=False,
    )

    print("\n=== QA result ===")
    print(f"Status: {report.status.value}")
    print(f"Diagnosis: {report.diagnosis.type.value}")
    print(f"Median line error: {report.metrics.line_start.median_error_ms} ms")
    print(f"P90 line error: {report.metrics.line_start.p90_error_ms} ms")
    print(f"Max line error: {report.metrics.line_start.max_error_ms} ms")
    print(f"Line coverage: {report.metrics.line_alignment_coverage:.4f}")
    print(f"Word coverage: {report.metrics.word.coverage:.4f}")
    print(f"Largest unresolved: {report.metrics.largest_unresolved_lyric_region_ms:.2f} ms")
    print(f"Drift slope: {report.metrics.drift_slope:.6f}")

    result = {
        "backend": "cpu_demucs_direct",
        "model": "htdemucs",
        "device": "cpu",
        "shifts": 0,
        "overlap": 0.25,
        "separation_time_s": separation_s,
        "source_duration_ms": source_duration_ms,
        "stem_duration_ms": stem_duration_ms,
        "duration_delta_ms": stem_duration_ms - source_duration_ms,
        "qa_status": report.status.value,
        "qa_diagnosis": report.diagnosis.type.value,
        "qa_median_ms": report.metrics.line_start.median_error_ms,
        "qa_p90_ms": report.metrics.line_start.p90_error_ms,
        "qa_max_ms": report.metrics.line_start.max_error_ms,
        "qa_line_coverage": report.metrics.line_alignment_coverage,
        "qa_word_coverage": report.metrics.word.coverage,
        "qa_unresolved_ms": report.metrics.largest_unresolved_lyric_region_ms,
        "qa_drift_slope": report.metrics.drift_slope,
    }
    out_path = Path("output/qa/benchmarks/vocal_backend_cpu_baseline.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved result to {out_path}")
    return 0


if __name__ == "__main__":
    import json
    import subprocess
    sys.exit(main())
