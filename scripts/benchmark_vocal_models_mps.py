#!/usr/bin/env python3
"""Benchmark vocal-separation backends locally on MPS/GPU/CPU.

Usage:
    QA_AUDIO_SEPARATOR_MODEL_DIR=/tmp/audio-separator-models \
    python3 scripts/benchmark_vocal_models_mps.py \
        --record-id rec05t4vubcdahl3Q \
        --models htdemucs mdx23c melband_roformer

Models:
    htdemucs            PyTorch Demucs baseline
    mdx23c              python-audio-separator MDX23C
    melband_roformer    python-audio-separator MelBand RoFormer
    bs_roformer         python-audio-separator BS RoFormer (vocals)
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from src.qa.audio_qa_executor import AudioQARequest, run_audio_qa


def _direct_audio_url(record):
    fields = record.get("fields", {})
    for key in ("Source Audio URL", "Audio File (from Source)"):
        val = fields.get(key)
        if val:
            if isinstance(val, list):
                val = val[0]
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val
    links = fields.get("Link (from GDrive Audio files)")
    if not links:
        return None
    if isinstance(links, str):
        links = [links]
    import re
    for link in links:
        if not isinstance(link, str):
            continue
        m = re.search(r"/d/([a-zA-Z0-9_-]{10,})", link) or re.search(r"id=([a-zA-Z0-9_-]{10,})", link)
        if m:
            return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return None


def _build_lyrics(record):
    from src.qa.runner import KaraokeQARunner
    from src.qa.timeline import build_timeline_transform
    fields = record["fields"]
    transform = build_timeline_transform(record)
    runner = KaraokeQARunner(verbose=False)
    lyrics = runner._build_structured_lyrics(fields, transform, None)
    lines = []
    for ln in lyrics.lines:
        words = [
            {"text": w.text, "source_start_ms": w.source_start_ms, "source_end_ms": w.source_end_ms}
            for w in ln.words
        ]
        lines.append({
            "text": ln.text,
            "source_start_ms": ln.source_start_ms,
            "source_end_ms": ln.source_end_ms,
            "words": words,
        })
    return lines, transform


def _make_request(record, model, separator_params=None):
    audio_url = _direct_audio_url(record)
    if not audio_url:
        raise RuntimeError("No audio URL")
    lines, transform = _build_lyrics(record)
    fields = record["fields"]

    # Derive backend from alias.
    backend = None
    model_for_request = model
    if model in ("htdemucs", "hdemucs_mmi", "demucs"):
        backend = "pytorch_demucs"
        model_for_request = "htdemucs" if model == "demucs" else model

    return AudioQARequest(
        record_id=record["id"],
        audio_url=audio_url,
        audio_sha256=None,
        lyrics=lines,
        lyrics_source="lrc",
        lyrics_source_track_id=None,
        lyrics_language=None,
        transform={
            "lyrics_to_source_offset_ms": transform.lyrics_to_source_offset_ms,
            "source_to_karaoke_audio_offset_ms": 0.0,
            "karaoke_audio_to_video_offset_ms": 0.0,
        },
        source_duration_ms=None,
        separator_backend=backend,
        separator_model=model_for_request,
        separator_overlap=0.10,
        separator_shifts=0,
        separator_split=True,
        separator_params=separator_params or {},
        run_structural_qa=True,
        run_stable_ts=False,
        stem_retention="failures_only",
        return_stem=False,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-ids", default=["rec05t4vubcdahl3Q"], nargs="+")
    parser.add_argument("--models", default=["htdemucs", "mdx23c", "melband_roformer"], nargs="+")
    parser.add_argument("--output", default="output/qa/benchmarks/mps_vocal_model_benchmark.json")
    parser.add_argument("--separator-params", default=None, help="JSON dict passed as AudioQARequest.separator_params")
    args = parser.parse_args()
    separator_params = json.loads(args.separator_params) if args.separator_params else None

    # Ensure model download cache lives in a stable location for this venv.
    os.environ.setdefault("QA_AUDIO_SEPARATOR_MODEL_DIR", "/tmp/audio-separator-models")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    airtable = AirtableClient()
    results = []

    def _save() -> None:
        output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    for rid in args.record_ids:
        try:
            record = airtable.get_record(rid)
        except Exception as exc:
            print(f"\n=== {rid} === Airtable fetch failed: {exc}")
            results.append({"record_id": rid, "model": None, "error": f"airtable: {exc}"})
            _save()
            continue
        artist = record["fields"].get("Artist (string)", "")
        title = record["fields"].get("Title", "")
        print(f"\n=== {artist} — {title} ({rid}) ===")
        for model in args.models:
            print(f"\n-- Model: {model} --")
            try:
                req = _make_request(record, model, separator_params=separator_params)
                t0 = time.monotonic()
                result = run_audio_qa(req)
                elapsed = time.monotonic() - t0
            except Exception as exc:
                print(f"  error: {exc}")
                results.append({"record_id": rid, "artist": artist, "title": title, "model": model, "error": str(exc)})
                _save()
                continue

            metrics = result.metrics or {}
            line_start = metrics.get("line_start", {})
            summary = {
                "record_id": rid,
                "artist": artist,
                "title": title,
                "model": model,
                "backend": result.separator.get("backend") if result.separator else None,
                "status": result.status,
                "diagnosis": result.diagnosis.get("type") if result.diagnosis else None,
                "median_ms": line_start.get("median_error_ms"),
                "p90_ms": line_start.get("p90_error_ms"),
                "max_ms": line_start.get("max_error_ms"),
                "coverage": metrics.get("line_alignment_coverage"),
                "separation_ms": result.separator.get("separation_time_ms") if result.separator else None,
                "cache_hit": result.separator.get("cache_hit") if result.separator else None,
                "total_wall_s": round(elapsed, 2),
            }
            results.append(summary)
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            _save()

    print(f"\nSaved {len(results)} results to {output_path}")


if __name__ == "__main__":
    main()
