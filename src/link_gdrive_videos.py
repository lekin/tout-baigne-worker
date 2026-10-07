#!/usr/bin/env python3
"""Link unlinked GDrive videos to Music videos records in Airtable.

The "GDrive videos" table is a Google Drive-synced table with filenames like
"Artist - Title (Year).mp4". The "Music videos" table has a Name field like
"Artist - Title (Year)" and a linked-record field "GDrive videos".

This script:
  1. Fetches all GDrive videos records (with their Name / Music video fields)
  2. Fetches all Music videos records missing a GDrive videos link
  3. Matches them by normalised name
  4. Updates the Music videos "GDrive videos" field with the linked record ID

Usage:
    python -m src.link_gdrive_videos --dry-run
    python -m src.link_gdrive_videos
    python -m src.link_gdrive_videos --verbose
"""
import argparse
import os
import re
import sys
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

from src.config import settings
from src.airtable_client import AirtableClient


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

def _normalise(name: str) -> str:
    """Normalise a name for fuzzy matching.

    - Strip file extension (.mp4, .mkv, …)
    - Transliterate accented characters (é→e)
    - Lowercase
    - Collapse whitespace / punctuation differences
    """
    s = (name or "").strip()
    # Strip common video extensions
    s = re.sub(r"\.(mp4|mkv|mov|webm|avi|m4v)$", "", s, flags=re.IGNORECASE)
    # Transliterate accents
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    # Lowercase
    s = s.lower()
    # Normalise punctuation & whitespace
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Link unlinked GDrive videos to Music videos records in Airtable."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be linked without making changes",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show extra debug info (unmatched records, etc.)",
    )
    args = parser.parse_args()

    print("Connecting to Airtable...")
    airtable = AirtableClient()

    # ------------------------------------------------------------------
    # 1. Fetch GDrive videos
    # ------------------------------------------------------------------
    gd_table = airtable.get_table("GDrive videos")
    all_gd: List[Dict[str, Any]] = gd_table.all()
    print(f"📁 GDrive videos: {len(all_gd)} records")

    # Build lookup: normalised name → list of (record_id, original_name)
    gd_by_name: Dict[str, List[Tuple[str, str]]] = {}
    for rec in all_gd:
        rid = rec.get("id", "")
        fields = rec.get("fields", {}) or {}
        raw_name = fields.get("Name") or ""
        if not raw_name or not rid:
            continue
        key = _normalise(raw_name)
        if key:
            gd_by_name.setdefault(key, []).append((rid, raw_name))

    # ------------------------------------------------------------------
    # 2. Fetch Music videos missing a GDrive link
    # ------------------------------------------------------------------
    mv_table_name = "Music videos"
    mv_table = airtable.get_table(mv_table_name)
    all_mv: List[Dict[str, Any]] = mv_table.all()
    print(f"🎬 Music videos: {len(all_mv)} records")

    missing: List[Dict[str, Any]] = []
    already_linked = 0
    for rec in all_mv:
        fields = rec.get("fields", {}) or {}
        gd_link = fields.get("GDrive videos")
        if gd_link:
            already_linked += 1
        else:
            missing.append(rec)

    print(f"   Already linked: {already_linked}")
    print(f"   Missing GDrive: {len(missing)}")

    # ------------------------------------------------------------------
    # 3. Match
    # ------------------------------------------------------------------
    matched: List[Tuple[Dict[str, Any], str, str, str]] = []  # (mv_rec, mv_name, gd_id, gd_name)
    unmatched: List[Tuple[str, str]] = []  # (mv_id, mv_name)

    for rec in missing:
        rid = rec.get("id", "")
        fields = rec.get("fields", {}) or {}
        mv_name = fields.get("Name") or ""
        if not mv_name:
            continue

        key = _normalise(mv_name)
        candidates = gd_by_name.get(key)
        if candidates:
            gd_id, gd_name = candidates[0]
            matched.append((rec, mv_name, gd_id, gd_name))
        else:
            unmatched.append((rid, mv_name))

    print(f"\n📊 Matching results:")
    print(f"   Matched:   {len(matched)}")
    print(f"   Unmatched: {len(unmatched)}")

    if not matched:
        print("\n✅ Nothing to link.")
        if args.verbose and unmatched:
            print(f"\n🔍 Unmatched Music videos ({len(unmatched)}):")
            for rid, name in sorted(unmatched, key=lambda x: x[1]):
                print(f"   {rid}: {name}")
        return

    # ------------------------------------------------------------------
    # 4. Preview / Apply
    # ------------------------------------------------------------------
    if args.dry_run:
        print(f"\n🔍 Dry run — would link {len(matched)} records:")
        for mv_rec, mv_name, gd_id, gd_name in sorted(matched, key=lambda x: x[1]):
            print(f"   {mv_name}")
            print(f"     → GDrive: {gd_name} ({gd_id})")
        if args.verbose and unmatched:
            print(f"\n❌ Unmatched Music videos ({len(unmatched)}):")
            for rid, name in sorted(unmatched, key=lambda x: x[1]):
                print(f"   {rid}: {name}")
        return

    print(f"\n🚀 Linking {len(matched)} records...\n")
    succeeded = 0
    failed = 0

    for i, (mv_rec, mv_name, gd_id, gd_name) in enumerate(matched, 1):
        mv_id = mv_rec.get("id", "")
        try:
            mv_table.update(mv_id, {"GDrive videos": [gd_id]})
            print(f"  [{i}/{len(matched)}] ✅ {mv_name} → {gd_name}")
            succeeded += 1
        except Exception as e:
            print(f"  [{i}/{len(matched)}] ❌ {mv_name}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"✅ Done!")
    print(f"   Linked:  {succeeded}")
    print(f"   Failed:  {failed}")

    if args.verbose and unmatched:
        print(f"\n❌ Unmatched Music videos ({len(unmatched)}):")
        for rid, name in sorted(unmatched, key=lambda x: x[1]):
            print(f"   {rid}: {name}")


if __name__ == "__main__":
    main()
