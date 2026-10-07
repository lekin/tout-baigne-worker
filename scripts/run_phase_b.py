#!/usr/bin/env python3
"""Phase B: try alternate Musixmatch candidates for a failed sync QA track."""
import argparse
import os
import sys
from pathlib import Path

# Add repo root to path when running from scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.airtable_client import AirtableClient
from src.qa.correction import MusixmatchFallbackResolver
from src.qa.models import TimelineTransform
from src.qa.runner import KaraokeQARunner


def _load_candidate_file(path: str, source: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _eval_manual_candidate(
    runner: KaraokeQARunner,
    record_id: str,
    fields: dict,
    audio_path: str,
    candidate_source: str,
    candidate_content: str,
    candidate_track_id: str,
) -> int:
    """Test a single manually supplied lyric candidate end-to-end."""
    source_duration_ms = runner._media_duration_ms(audio_path)

    transform = TimelineTransform(
        lyrics_to_source_offset_ms=0.0,
        source_to_karaoke_audio_offset_ms=0.0,
        karaoke_audio_to_video_offset_ms=0.0,
    )

    lyrics = runner.build_structured_lyrics_from_source(
        richsync_json=candidate_content if candidate_source == "richsync" else None,
        lrc_content=candidate_content if candidate_source == "lrc" else None,
        srt_content=candidate_content if candidate_source == "srt" else None,
        track_id=candidate_track_id,
        transform=transform,
        source_duration_ms=source_duration_ms,
    )

    report = runner.evaluate(
        record_id=record_id,
        track_name=str(fields.get("Name", "")),
        artist_name=fields.get("Artist (string)", ""),
        musixmatch_track_id=candidate_track_id,
        transform=transform,
        audio_path=audio_path,
        lyrics=lyrics,
        source_duration_ms=source_duration_ms,
        save_report=False,
    )

    print(f"\n=== Manual candidate result for {record_id} ===")
    print(f"Track ID: {candidate_track_id}")
    print(f"Source:   {candidate_source}")
    print(f"Status:   {report.status.value} | {report.diagnosis.type.value}")
    print(
        f"  median={report.metrics.line_start.median_error_ms:.0f}ms "
        f"p90={report.metrics.line_start.p90_error_ms:.0f}ms "
        f"max={report.metrics.line_start.max_error_ms:.0f}ms "
        f"cov={report.metrics.line_alignment_coverage:.2%} "
        f"unresolved={report.metrics.largest_unresolved_lyric_region_ms:.0f}ms"
    )
    return 0 if report.status.value == "SYNC_VERIFIED" else 1


def main():
    parser = argparse.ArgumentParser(
        description="Resolve a sync failure by evaluating alternate Musixmatch candidates."
    )
    parser.add_argument("--record-id", required=True, help="Airtable record ID")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument(
        "--candidate-file",
        type=str,
        default=None,
        help="Path to a local richsync/lrc/srt file to test as a candidate",
    )
    parser.add_argument(
        "--candidate-source",
        type=str,
        choices=["richsync", "lrc", "srt"],
        default="lrc",
        help="Format of the local candidate file",
    )
    parser.add_argument(
        "--candidate-track-id",
        type=str,
        default="manual",
        help="Track ID label for the local candidate",
    )
    args = parser.parse_args()

    airtable = AirtableClient()
    record = airtable.get_record(args.record_id)
    fields = record.get("fields", {})

    runner = KaraokeQARunner(verbose=args.verbose)

    if args.candidate_file:
        audio_path = runner._download_source_audio(record)
        if not audio_path:
            print("❌ Could not download source audio.")
            return 1
        content = _load_candidate_file(args.candidate_file, args.candidate_source)
        return _eval_manual_candidate(
            runner,
            args.record_id,
            fields,
            audio_path,
            args.candidate_source,
            content,
            args.candidate_track_id,
        )

    # Run Phase A first so we have the original report to compare against.
    original_report = runner.run(args.record_id)
    if original_report.status.value == "SYNC_VERIFIED":
        print(f"✅ {args.record_id} already passed Phase A; no fallback needed.")
        return 0

    audio_path = runner._download_source_audio(record)
    if not audio_path:
        print("❌ Could not download source audio.")
        return 1

    source_duration_ms = runner._media_duration_ms(audio_path)
    resolver = MusixmatchFallbackResolver(runner)
    result = resolver.resolve(args.record_id, fields, audio_path, source_duration_ms)

    if result.error:
        print(f"❌ Phase B failed: {result.error}")
        return 1

    if not result.attempts:
        print("⚠️  No alternate candidates evaluated.")
        return 0

    print(f"\n=== Phase B result for {args.record_id} ===")
    print(f"Evaluated {len(result.attempts)} alternate candidate(s)")
    if result.best_attempt:
        best = result.best_attempt
        print(
            f"Best candidate: {best.musixmatch_track_id} "
            f"({best.track_name} - {best.artist_name})"
        )
        print(
            f"  Status: {best.report.status.value} | "
            f"median={best.report.metrics.line_start.median_error_ms:.0f}ms "
            f"p90={best.report.metrics.line_start.p90_error_ms:.0f}ms "
            f"cov={best.report.metrics.line_alignment_coverage:.2%}"
        )
        print(f"  Selected: {result.selected}")
    else:
        print("No improvement found.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
