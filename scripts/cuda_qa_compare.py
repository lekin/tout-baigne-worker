#!/usr/bin/env python3
"""Re-run local Structural QA on CUDA-separated stems and compare to canonical."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# repo root for src imports
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from src.qa.models import SyncStatus, to_json
from src.qa.runner import KaraokeQARunner, _extract_artist
from src.qa.timeline import build_timeline_transform


def _canonical_for_record(canonical: Dict[str, Any], record_id: str) -> Optional[Dict[str, Any]]:
    for t in canonical.get("per_track", []):
        if t.get("record_id") == record_id:
            return t
    return None


def _set_vocal_info(
    runner: KaraokeQARunner,
    model: str,
    backend: str,
    separation_time_s: float,
    vocals_path: str,
) -> None:
    runner.last_vocal_info = {
        "backend": backend,
        "model": model,
        "cache_hit": False,
        "separation_time_s": separation_time_s,
        "path": vocals_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="output/qa/cuda_bench/cuda_vocal_benchmark_results.json")
    parser.add_argument("--canonical", default="output/qa/benchmarks/phase_a_20_track_report.json")
    parser.add_argument("--stems-dir", default="output/qa/cuda_bench")
    parser.add_argument("--manifest", default="output/qa/cuda_bench/manifest.json")
    parser.add_argument("--backend-label", default="demucs_cuda")
    parser.add_argument("--output", default="output/qa/cuda_bench/cuda_qa_comparison.json")
    args = parser.parse_args()

    results_path = Path(args.results)
    canonical_path = Path(args.canonical)
    stems_dir = Path(args.stems_dir)
    manifest_path = Path(args.manifest)
    output_path = Path(args.output)

    with open(results_path, "r", encoding="utf-8") as f:
        results = json.load(f)
    with open(canonical_path, "r", encoding="utf-8") as f:
        canonical = json.load(f)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    record_to_audio = {t["record_id"]: t["audio_path"] for t in manifest["tracks"]}

    airtable = AirtableClient()
    runner = KaraokeQARunner(verbose=False)

    per_track: Dict[str, Dict[str, Any]] = {}

    # Group runs by record_id
    for run in results["runs"]:
        record_id = run["track_id"]
        config_label = run["config_label"]
        if record_id not in per_track:
            per_track[record_id] = {"configs": {}}

        vocals_path = str(stems_dir / f"{record_id}_{config_label}_vocals.wav")
        if not Path(vocals_path).exists():
            print(f"  ⚠️ missing stem {vocals_path}")
            continue

        record = airtable.get_record(record_id)
        fields = record.get("fields", {})
        transform = build_timeline_transform(record)

        # Build structured lyrics once per track
        if "lyrics" not in per_track[record_id]:
            source_duration_ms = run.get("source_duration_ms")
            lyrics = runner._build_structured_lyrics(fields, transform, source_duration_ms)
            per_track[record_id]["lyrics"] = lyrics
            per_track[record_id]["record"] = record
            per_track[record_id]["transform"] = transform

        lyrics = per_track[record_id]["lyrics"]
        transform = per_track[record_id]["transform"]

        _set_vocal_info(
            runner,
            model=run["model"],
            backend=args.backend_label,
            separation_time_s=run.get("separation_time_s", 0.0),
            vocals_path=vocals_path,
        )

        report = runner.evaluate(
            record_id=record_id,
            track_name=fields.get("Name", ""),
            artist_name=_extract_artist(fields),
            musixmatch_track_id=fields.get("Musixmatch Track ID"),
            transform=transform,
            audio_path=record_to_audio[record_id],
            lyrics=lyrics,
            source_duration_ms=run.get("source_duration_ms", 0.0),
            vocals_path=vocals_path,
            save_report=False,
        )

        report_dict = to_json(report)
        per_track[record_id]["configs"][config_label] = {
            "model": run["model"],
            "overlap": run["overlap"],
            "separation_time_s": run.get("separation_time_s"),
            "wall_time_s": run.get("wall_time_s"),
            "peak_vram_mb": run.get("peak_vram_mb"),
            "status": report.status.value if isinstance(report.status, SyncStatus) else str(report.status),
            "diagnosis": report_dict.get("diagnosis"),
            "metrics": report_dict.get("metrics"),
            "gate_results": report_dict.get("gate_results"),
            "vocal_stem_duration_ms": report_dict.get("vocal_stem_duration_ms"),
            "vocal_stem_delta_ms": report_dict.get("vocal_stem_delta_ms"),
        }

    # Compare to canonical
    comparison: List[Dict[str, Any]] = []
    for record_id, data in per_track.items():
        canon = _canonical_for_record(canonical, record_id)
        track_name = data["record"]["fields"].get("Name", "")
        manual_status = canon.get("manual_status") if canon else None
        canonical_status = canon.get("predicted_status") if canon else None

        config_comparisons: Dict[str, Any] = {}
        for config_label, rep in data["configs"].items():
            cuda_status = rep["status"]
            status_match = cuda_status == canonical_status
            config_comparisons[config_label] = {
                **rep,
                "canonical_status": canonical_status,
                "status_match": status_match,
            }

        comparison.append({
            "record_id": record_id,
            "track_name": track_name,
            "manual_status": manual_status,
            "canonical_status": canonical_status,
            "canonical_vocal_backend": canon.get("vocal_backend") if canon else None,
            "canonical_vocal_model": canon.get("vocal_model") if canon else None,
            "configs": config_comparisons,
        })

    out = {
        "worker": results.get("metadata", {}),
        "tracks": comparison,
        "equivalence_summary": _summarize_equivalence(comparison),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"\nQA comparison written to {output_path}")
    print(json.dumps(out["equivalence_summary"], indent=2))
    return 0


def _summarize_equivalence(comparison: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for track in comparison:
        for config, rep in track["configs"].items():
            if config not in summary:
                summary[config] = {"total": 0, "matched": 0, "statuses": {}}
            summary[config]["total"] += 1
            if rep["status_match"]:
                summary[config]["matched"] += 1
            summary[config]["statuses"][rep["status"]] = summary[config]["statuses"].get(rep["status"], 0) + 1
    for s in summary.values():
        s["match_rate"] = s["matched"] / max(1, s["total"])
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
