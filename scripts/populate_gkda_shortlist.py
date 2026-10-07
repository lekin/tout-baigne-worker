"""Populate the 'GKDA shortlist' checkbox field on the Tracks table.

Run this after creating the 'GKDA shortlist' checkbox field in Airtable:
    .venv/bin/python scripts/populate_gkda_shortlist.py

It reads output/gkda_top_tracks.json and checks the box for the top tracks.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.airtable_client import AirtableClient

FIELD_NAME = "GKDA shortlist"


def main():
    shortlist_path = PROJECT_ROOT / "output" / "gkda_top_tracks.json"
    if not shortlist_path.exists():
        print(f"❌ Shortlist file not found: {shortlist_path}")
        print("   Run .venv/bin/python scripts/top_karaoke_tracks.py first")
        sys.exit(1)

    with open(shortlist_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    track_ids = [t["track_id"] for t in data.get("top_tracks", [])]
    if not track_ids:
        print("❌ No tracks in shortlist")
        sys.exit(1)

    print(f"📝 Marking {len(track_ids)} tracks with '{FIELD_NAME}' = True")

    client = AirtableClient()
    for tid in track_ids:
        try:
            client.table.update(tid, {FIELD_NAME: True})
            print(f"✅ {tid}")
        except Exception as e:
            print(f"❌ {tid}: {e}")
            print(f"   Make sure the '{FIELD_NAME}' checkbox field exists on the Tracks table.")
            sys.exit(1)

    print(f"\n✅ Done — {len(track_ids)} tracks marked")


if __name__ == "__main__":
    main()
