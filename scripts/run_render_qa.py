#!/usr/bin/env python3
"""Render QA: verify that the generated ASS for a track matches the predicted sync."""
import argparse
import os
import sys
from pathlib import Path

# Add repo root to path when running from scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.qa.persistence import load_sync_report
from src.qa.render_qa import run_render_qa


def main():
    parser = argparse.ArgumentParser(
        description="Verify that the ASS renderer reproduces the predicted sync."
    )
    parser.add_argument("--record-id", required=True, help="Airtable record ID")
    parser.add_argument(
        "--tolerance-ms",
        type=float,
        default=150.0,
        help="Max allowed difference between predicted and ASS start times",
    )
    parser.add_argument(
        "--offset-seconds",
        type=float,
        default=0.0,
        help="Lyrics-to-audio offset used by the renderer",
    )
    parser.add_argument(
        "--save-ass",
        action="store_true",
        help="Write the generated ASS to output/qa/<record_id>_render_qa.ass",
    )
    args = parser.parse_args()

    report = load_sync_report(args.record_id)
    if not report:
        print(f"❌ No sync report found for {args.record_id}")
        return 1

    result = run_render_qa(
        report,
        offset_seconds=args.offset_seconds,
        timing_tolerance_ms=args.tolerance_ms,
    )

    if args.save_ass:
        from src.config import settings

        ass_path = Path(settings.get_qa_output_path()) / f"{args.record_id}_render_qa.ass"
        # Regenerate ASS content to save.
        from src.qa.render_qa import build_ass_from_report

        ass_content = build_ass_from_report(
            report, offset_seconds=args.offset_seconds
        )
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)
        result.ass_path = str(ass_path)

    print(f"🎬 Render QA for {args.record_id} — {report.track_name}")
    print(f"  Verified: {result.verified}")
    print(f"  Total predicted lines: {result.total_lines}")
    print(f"  Matched lines: {result.matched_lines}")
    print(f"  Unmatched lines: {result.unmatched_lines}")
    print(f"  Max timing error: {result.max_timing_error_ms:.0f} ms")
    print(f"  Mean timing error: {result.mean_timing_error_ms:.0f} ms")
    if result.ass_path:
        print(f"  ASS saved: {result.ass_path}")
    if result.errors:
        print("\n  Errors:")
        for e in result.errors[:20]:
            print(f"    - {e}")

    return 0 if result.verified else 1


if __name__ == "__main__":
    sys.exit(main())
