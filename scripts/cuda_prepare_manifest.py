#!/usr/bin/env python3
"""Download Airtable source audio for CUDA benchmark and emit a manifest."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make sure the repo root (containing src/) is on the path when this script is
# invoked directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from src.qa.cache import cached_audio_path, hash_audio_file


def _download_streaming_url(url: str, dest: str, timeout: int = 300) -> None:
    import requests
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)


def _media_duration_ms(path: str) -> Optional[float]:
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
        return float(json.loads(out)["format"]["duration"]) * 1000.0
    except Exception:
        return None


def _download_record_audio(airtable: AirtableClient, record_id: str) -> Optional[Dict[str, Any]]:
    record = airtable.get_record(record_id)
    fields = record.get("fields", {})
    track_name = fields.get("Name", "")
    artist = fields.get("Artist", "")
    url = airtable.get_audio_file_url(record) or airtable.get_video_url(record)
    if not url:
        print(f"  ❌ no audio URL for {record_id}")
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        print(f"  → downloading {record_id}: {track_name}")
        _download_streaming_url(url, tmp_path)
        cache_key = hash_audio_file(tmp_path)
        ext = Path(url).suffix or ".mp3"
        if ext not in (".mp3", ".wav", ".m4a", ".flac"):
            ext = ".mp3"
        dest = cached_audio_path(cache_key, ext)
        os.replace(tmp_path, dest)
        duration_ms = _media_duration_ms(str(dest))
        print(f"  ✅ cached to {dest} ({duration_ms / 1000.0:.2f}s)")
        return {
            "record_id": record_id,
            "track_name": track_name,
            "artist": artist,
            "audio_path": str(dest),
            "source_duration_ms": duration_ms,
        }
    except Exception as e:
        print(f"  ❌ error downloading {record_id}: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return None


CONFIGS = [
    {"label": "htdemucs_o25", "model": "htdemucs", "shifts": 0, "overlap": 0.25, "split": True},
    {"label": "htdemucs_o10", "model": "htdemucs", "shifts": 0, "overlap": 0.10, "split": True},
    {"label": "hdemucs_mmi_o25", "model": "hdemucs_mmi", "shifts": 0, "overlap": 0.25, "split": True},
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-ids", required=True, help="Comma-separated record IDs")
    parser.add_argument("--output-dir", default="output/qa/cuda_bench", help="Manifest and audio copy dir")
    args = parser.parse_args()

    record_ids = [r.strip() for r in args.record_ids.split(",") if r.strip()]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    airtable = AirtableClient()
    tracks: List[Dict[str, Any]] = []
    for record_id in record_ids:
        info = _download_record_audio(airtable, record_id)
        if info:
            tracks.append(info)

    manifest = {
        "device": "cuda",
        "tracks": tracks,
        "configs": CONFIGS,
    }
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n📄 Manifest written to {manifest_path}")
    print(f"Tracks: {len(tracks)} / {len(record_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
