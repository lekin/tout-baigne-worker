#!/usr/bin/env python3
"""Add new tracks to the QA benchmark by pulling from Airtable."""
import argparse
import os
import random
import sys
from pathlib import Path

# Add repo root to path when running from scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml

from src.airtable_client import AirtableClient


def _load_existing_ids(label_files: list[str]) -> set[str]:
    ids: set[str] = set()
    for path in label_files:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for t in data.get("tracks", []):
            ids.add(t["record_id"])
    return ids


def _formula(only_with_lyrics: bool = True) -> str:
    conditions = [
        "{Musixmatch Track ID} != BLANK()",
        "{Count GDrive Audio files} > 0",
    ]
    if only_with_lyrics:
        conditions.append(
            "OR({Has LRC} = TRUE(), {Has Richtext} = TRUE(), {LRC (Musixmatch)} != BLANK(), {Richsync JSON (Musixmatch)} != BLANK())"
        )
    return f"AND({', '.join(conditions)})"


def main():
    parser = argparse.ArgumentParser(
        description="Pull new karaoke tracks from Airtable into a benchmark labels file."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=20,
        help="Number of new tracks to add",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="input/qa_benchmark_labels_new.yaml",
        help="Output labels YAML",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for shuffling",
    )
    parser.add_argument(
        "--existing",
        nargs="+",
        default=["input/qa_benchmark_labels.yaml"],
        help="Existing label files to exclude",
    )
    args = parser.parse_args()

    existing_ids = _load_existing_ids(args.existing)
    client = AirtableClient()
    formula = _formula()
    records = client.table.all(formula=formula, max_records=1000)

    new_records = [r for r in records if r["id"] not in existing_ids]

    if not new_records:
        print("No new tracks found.")
        return 0

    random.seed(args.seed)
    random.shuffle(new_records)
    selected = new_records[: args.count]

    tracks = []
    for r in selected:
        fields = r.get("fields", {})
        tracks.append(
            {
                "record_id": r["id"],
                "manual_status": "unknown",
                "human_severity": "unknown",
                "track_name": fields.get("Name"),
                "artist_name": fields.get("Artist (string)") or fields.get("Artist"),
            }
        )

    data = {"tracks": tracks}
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

    print(f"Selected {len(tracks)} new tracks -> {args.output}")
    for t in tracks:
        print(f"  {t['record_id']} | {t.get('artist_name')} - {t.get('track_name')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
