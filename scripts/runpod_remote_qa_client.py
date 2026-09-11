#!/usr/bin/env python3
"""Send a single track through the RunPod Serverless Audio QA endpoint."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from src.qa.runner import KaraokeQARunner
from src.qa.timeline import build_timeline_transform


def _build_request(record: Dict[str, Any], qa: Optional[Dict[str, Any]] = None, audio_url_override: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Build an AudioQARequest payload from an Airtable record."""
    fields = record.get("fields", {})
    record_id = record.get("id")
    audio_url = audio_url_override
    if not audio_url:
        audio_url = fields.get("Audio File (from Source)")
        if not audio_url:
            # fallback: try direct URL field
            audio_url = fields.get("Source Audio URL")
        if isinstance(audio_url, list):
            audio_url = audio_url[0]
    if not audio_url:
        return None

    # Get source audio cached locally for hash, if available.
    from src.qa.cache import hash_audio_file
    from src.qa.runner import _download_streaming_url

    cache_dir = Path("output/qa/cache/audio")
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_path = cache_dir / f"{record_id}.mp3"
    if not local_path.exists():
        _download_streaming_url(audio_url, str(local_path))
    audio_sha256 = hash_audio_file(str(local_path))

    # Build lyrics and transform using the existing Karaoke QA logic.
    runner = KaraokeQARunner(verbose=False)
    transform = build_timeline_transform(record)
    source_duration_ms = float(runner._media_duration_ms(str(local_path)) or 0)
    lyrics = runner._build_structured_lyrics(fields, transform, source_duration_ms)

    # Convert dataclass lyrics to serializable list.
    lines = []
    for ln in lyrics.lines:
        words = []
        for w in ln.words:
            words.append({
                "text": w.text,
                "source_start_ms": w.source_start_ms,
                "source_end_ms": w.source_end_ms,
            })
        lines.append({
            "text": ln.text,
            "source_start_ms": ln.source_start_ms,
            "source_end_ms": ln.source_end_ms,
            "words": words,
        })

    return {
        "record_id": record_id,
        "audio_url": audio_url,
        "audio_sha256": audio_sha256,
        "lyrics": lines,
        "lyrics_source": lyrics.source,
        "lyrics_source_track_id": lyrics.source_track_id,
        "lyrics_language": lyrics.language,
        "transform": {
            "lyrics_to_source_offset_ms": transform.lyrics_to_source_offset_ms,
            "source_to_karaoke_audio_offset_ms": transform.source_to_karaoke_audio_offset_ms,
            "karaoke_audio_to_video_offset_ms": transform.karaoke_audio_to_video_offset_ms,
        },
        "source_duration_ms": source_duration_ms,
        "separator_model": (qa or {}).get("model", "kuielab_a_vocals.onnx"),
        "separator_overlap": (qa or {}).get("overlap", 0.10),
        "separator_shifts": (qa or {}).get("shifts", 0),
        "separator_split": (qa or {}).get("split", True),
        "run_structural_qa": True,
        "run_stable_ts": False,
        "stem_retention": "failures_only",
        "return_stem": False,
    }


def send_request(endpoint_id: str, payload: Dict[str, Any], api_key: Optional[str] = None, load_balancer: bool = False) -> Dict[str, Any]:
    key = api_key or os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise RuntimeError("RUNPOD_API_KEY not set")
    if load_balancer:
        url = f"https://{endpoint_id}.api.runpod.ai/run"
    else:
        url = f"https://api.runpod.ai/v2/{endpoint_id}/runsync"
    start = time.time()
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"input": payload},
        timeout=600,
    )
    r.raise_for_status()
    end = time.time()
    data = r.json()
    data["_client_wall_time_s"] = round(end - start, 3)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--lb", action="store_true", help="Use a Load Balancer endpoint (default is queue/runsync)")
    parser.add_argument("--audio-url", default=None, help="Override the source audio URL used by the worker")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    airtable = AirtableClient()
    record = airtable.get_record(args.record_id)
    payload = _build_request(record, audio_url_override=args.audio_url)
    if not payload:
        print("Could not build request: missing audio URL")
        return 1

    result = send_request(args.endpoint_id, payload, load_balancer=args.lb)
    print(json.dumps(result, indent=2))
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
