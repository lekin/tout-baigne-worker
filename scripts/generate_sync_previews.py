#!/usr/bin/env python3
"""Generate sync-preview videos for all QA benchmark tracks."""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Add repo root to path when running from scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.airtable_client import AirtableClient
from src.config import settings
from src.karaoke_generator import KaraokeGenerator
from src.qa.benchmark import load_benchmark_labels
from src.qa.persistence import load_sync_report
from src.qa.render_qa import build_ass_from_report
from src.qa.runner import KaraokeQARunner


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w\s-]", "", name)
    return re.sub(r"\s+", "_", name).strip("_") or "track"


def main():
    parser = argparse.ArgumentParser(
        description="Render sync-preview videos from QA reports for human review."
    )
    parser.add_argument(
        "--labels",
        type=str,
        default="input/qa_benchmark_labels.yaml",
        help="Benchmark labels YAML",
    )
    parser.add_argument(
        "--record-id",
        type=str,
        default=None,
        help="Render only one record ID",
    )
    parser.add_argument(
        "--low",
        action="store_true",
        help="Low definition / fast small preview",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (default: output/qa/sync_previews)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir or (settings.get_qa_output_path() / "sync_previews"))
    output_dir.mkdir(parents=True, exist_ok=True)

    labels = load_benchmark_labels(args.labels)
    if args.record_id:
        labels = [lb for lb in labels if lb.record_id == args.record_id]

    airtable = AirtableClient()
    runner = KaraokeQARunner(verbose=False)
    generator = KaraokeGenerator()

    generated: list[str] = []
    failed: list[str] = []

    for label in labels:
        record_id = label.record_id
        print(f"\n🎬 Sync preview: {record_id}")

        # Re-use an existing QA report if available; prefer a selected
        # Phase B correction when it exists.
        report = load_sync_report(record_id)
        audio_path: str | None = None

        correction_path = (
            Path(settings.get_qa_output_path()) / "corrections" / f"{record_id}_correction.json"
        )
        if correction_path.exists():
            import json as _json

            with open(correction_path, "r", encoding="utf-8") as f:
                correction = _json.load(f)
            if correction.get("selected") and correction.get("best_attempt_id"):
                from src.qa.models import from_json, SyncReport

                for attempt in correction.get("attempts", []):
                    if attempt.get("musixmatch_track_id") == correction["best_attempt_id"]:
                        report = from_json(SyncReport, attempt["report"])
                        print(f"  Using Phase B best candidate: {correction['best_attempt_id']}")
                        break

        if not report:
            print(f"  No QA report; running Phase A for {record_id}...")
            report = runner.run(record_id)

        if report:
            record = airtable.get_record(record_id)
            audio_path = runner._download_source_audio(record)

        if not isinstance(report, dict):
            from src.qa.models import to_json

            report = to_json(report)

        from src.qa.models import from_json, SyncReport
        report = from_json(SyncReport, report)

        if not audio_path or not Path(audio_path).exists():
            print(f"  ❌ Could not get audio for {record_id}")
            failed.append(record_id)
            continue

        try:
            ass_content = build_ass_from_report(report)
        except Exception as e:
            print(f"  ❌ Could not build ASS: {e}")
            failed.append(record_id)
            continue

        safe_name = _safe_name(report.track_name or record_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(output_dir / f"{record_id}_{safe_name}_{timestamp}_sync_preview.mp4")

        ass_path = output_path.replace(".mp4", ".ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        if args.low:
            w, h, fps = 640, 360, 15
            v_bitrate, a_bitrate = "800k", "128k"
        else:
            w, h, fps = None, None, None
            v_bitrate, a_bitrate = "2000k", "192k"

        ok, res = generator.render_sync_preview(
            audio_path=audio_path,
            ass_path=ass_path,
            output_path=output_path,
            fast_mode=True,
            bg_color_hex="#000000",
            width=w,
            height=h,
            fps=fps,
            video_bitrate=v_bitrate,
            audio_bitrate=a_bitrate,
        )

        if ok:
            print(f"  ✅ {output_path}")
            generated.append(output_path)
        else:
            print(f"  ❌ Render failed: {res}")
            failed.append(record_id)

    print(f"\n=== Done ===")
    print(f"Generated: {len(generated)}")
    print(f"Failed:    {len(failed)} {failed}")
    for path in generated:
        print(path)

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
