#!/usr/bin/env python3
"""Local sanity test for src.qa.audio_qa_executor."""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from src.qa.audio_qa_executor import AudioQARequest, run_audio_qa
from src.qa.runner import KaraokeQARunner
from src.qa.timeline import build_timeline_transform


def main() -> int:
    record_id = sys.argv[1] if len(sys.argv) > 1 else "recFzqntCCL6Kq42t"
    record = AirtableClient().get_record(record_id)
    runner = KaraokeQARunner(verbose=False)
    fields = record["fields"]
    transform = build_timeline_transform(record)
    audio_path = "output/qa/cache/audio/36f13975160cad466606e497d782cda7.mp3"
    source_duration_ms = float(runner._media_duration_ms(audio_path) or 0)
    lyrics = runner._build_structured_lyrics(fields, transform, source_duration_ms)

    lines = []
    for ln in lyrics.lines:
        words = [
            {"text": w.text, "source_start_ms": w.source_start_ms, "source_end_ms": w.source_end_ms}
            for w in ln.words
        ]
        lines.append(
            {
                "text": ln.text,
                "source_start_ms": ln.source_start_ms,
                "source_end_ms": ln.source_end_ms,
                "words": words,
            }
        )

    req = AudioQARequest(
        record_id=record_id,
        audio_url="file://" + os.path.abspath(audio_path),
        audio_sha256=None,
        lyrics=lines,
        lyrics_source=lyrics.source,
        lyrics_source_track_id=lyrics.source_track_id,
        lyrics_language=lyrics.language,
        transform={"lyrics_to_source_offset_ms": transform.lyrics_to_source_offset_ms},
        source_duration_ms=source_duration_ms,
        separator_model="htdemucs",
        separator_overlap=0.10,
        separator_shifts=0,
        stem_retention="failures_only",
    )

    result = run_audio_qa(req, work_dir="output/qa/worker_test")
    print("status:", result.status)
    print("error:", result.error)
    if result.metrics:
        print(json.dumps(result.metrics, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
