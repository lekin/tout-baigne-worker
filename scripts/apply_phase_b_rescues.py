#!/usr/bin/env python3
"""Apply Phase B rescues to Airtable.

For every record marked rescued in phase_b_results.jsonl, fetch the winning
Musixmatch candidate's synced lyrics and write them to the record:
- Musixmatch Track ID
- Richsync JSON (Musixmatch) and/or LRC (Musixmatch)
- Musixmatch Fetch Date

Stale higher-priority lyric fields from the previous candidate are cleared so
the new lyrics actually take precedence (build order: richsync > lrc > srt).

Usage:
    python scripts/apply_phase_b_rescues.py \
        --input output/qa/batches/remote_500_v1/phase_b/phase_b_results.jsonl \
        --dry-run
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from src.musixmatch_lyrics import MusixmatchClient

RICHSYNC_FIELD = "Richsync JSON (Musixmatch)"
LRC_FIELD = "LRC (Musixmatch)"
TRACK_ID_FIELD = "Musixmatch Track ID"
FETCH_DATE_FIELD = "Musixmatch Fetch Date"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="phase_b_results.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--record-id", default=None, help="Apply to a single record")
    args = parser.parse_args()

    rescued = []
    for line in Path(args.input).read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("rescued") and d.get("best_attempt_id"):
            rescued.append(d)
    if args.record_id:
        rescued = [d for d in rescued if d["record_id"] == args.record_id]
    if args.limit:
        rescued = rescued[: args.limit]
    print(f"{len(rescued)} rescued records to update")

    airtable = AirtableClient()
    client = MusixmatchClient(os.environ.get("MUSIXMATCH_API_KEY") or "")

    updated, failed = 0, 0
    for d in rescued:
        rid = d["record_id"]
        best_id = int(d["best_attempt_id"])
        label = f"{d.get('artist')} — {d.get('title')}"
        try:
            record = airtable.get_record(rid)
            fields = record.get("fields", {})

            update = {TRACK_ID_FIELD: best_id, FETCH_DATE_FIELD: datetime.now().isoformat()}

            richsync = None
            lrc = None
            try:
                richsync = client.get_richsync(best_id)
            except Exception:
                pass
            if not richsync:
                try:
                    lrc = client.get_subtitle(best_id)
                except Exception:
                    pass

            if richsync:
                update[RICHSYNC_FIELD] = richsync
                if fields.get(LRC_FIELD):
                    update[LRC_FIELD] = ""  # clear stale lyrics from old candidate
            elif lrc:
                update[LRC_FIELD] = lrc
                if fields.get(RICHSYNC_FIELD):
                    update[RICHSYNC_FIELD] = ""  # old richsync would take precedence
            else:
                print(f"  ✗ {label}: no lyrics fetchable for candidate {best_id}")
                failed += 1
                continue

            if args.dry_run:
                print(f"  [dry] {label}: track_id={best_id} richsync={bool(richsync)} lrc={bool(lrc)}")
            else:
                airtable.update_record(rid, update)
                print(f"  ✓ {label}: track_id={best_id} ({'richsync' if richsync else 'lrc'})")
            updated += 1
        except Exception as e:
            print(f"  ✗ {label}: {e}")
            failed += 1

    print(f"\nDone: {updated} updated, {failed} failed" + (" (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
