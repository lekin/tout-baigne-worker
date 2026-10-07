#!/usr/bin/env python3
"""Backfill Brevo email-campaign statistics into the Airtable `Newsletter` table.

For every Newsletter record that has a `Campaign ID`, fetch the campaign's
`statistics.globalStats` from the Brevo API (GET /v3/emailCampaigns/{id}) and
write the matching stats fields.

Field mapping (verified against previously filled records, e.g. campaign 19):

    Airtable field                 Brevo globalStats key / formula
    ----------------------------   -----------------------------------------
    Delivered                      delivered
    Opened                         uniqueViews
    Open Rate                      uniqueViews / delivered
    Clics uniques                  uniqueClicks
    Click-Through Rate (CTR)       uniqueClicks / delivered
    Unsubscribes                   unsubscriptions
    Unsubscribe Rate               unsubscriptions / delivered
    Soft bounces                   softBounces
    Soft Bounces Rate              softBounces / sent
    Hard bounces                   hardBounces
    Hard Bounces Rate              hardBounces / sent
    Spam Complaints                complaints
    Spam Complaint Rate            complaints / delivered

`sent` = globalStats.sent (falls back to delivered + softBounces + hardBounces).
Percent fields are stored as fractions rounded to 4 decimals (0.2807 = 28.07%).

NOTE: `globalStats` is only populated on the LIST endpoint
(GET /v3/emailCampaigns?statistics=globalStats) — the single-campaign
endpoint returns it zeroed for sent campaigns.

Usage:
    python scripts/backfill_brevo_newsletter_stats.py              # fill missing stats
    python scripts/backfill_brevo_newsletter_stats.py --all        # refresh every record with a Campaign ID
    python scripts/backfill_brevo_newsletter_stats.py --validate   # recompute & diff already-filled records
    python scripts/backfill_brevo_newsletter_stats.py --dry-run    # print planned writes, touch nothing
"""
import argparse
import os
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The Brevo account allow-lists the IPv4 address only; prefer IPv4 for API calls.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_preferred_getaddrinfo(host, port, family=0, *args, **kwargs):
    if host == "api.brevo.com":
        family = socket.AF_INET
    return _orig_getaddrinfo(host, port, family, *args, **kwargs)


socket.getaddrinfo = _ipv4_preferred_getaddrinfo

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient

BREVO_API_BASE = "https://api.brevo.com/v3"
NEWSLETTER_TABLE = "Newsletter"

STATS_FIELDS = [
    "Delivered",
    "Opened",
    "Open Rate",
    "Clics uniques",
    "Click-Through Rate (CTR)",
    "Unsubscribes",
    "Unsubscribe Rate",
    "Soft bounces",
    "Soft Bounces Rate",
    "Hard bounces",
    "Hard Bounces Rate",
    "Spam Complaints",
    "Spam Complaint Rate",
]


def _rate(numerator: float, denominator: float) -> Optional[float]:
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def fetch_all_global_stats(api_key: str) -> Dict[str, Dict[str, Any]]:
    """Return {campaign_id: globalStats} for every campaign.

    `globalStats` is only populated on the list endpoint, so we page through
    GET /v3/emailCampaigns?statistics=globalStats once instead of querying
    each campaign individually.
    """
    stats_by_id: Dict[str, Dict[str, Any]] = {}
    offset = 0
    while True:
        resp = requests.get(
            f"{BREVO_API_BASE}/emailCampaigns",
            headers={"api-key": api_key, "Accept": "application/json"},
            params={"statistics": "globalStats", "limit": 100, "offset": offset},
            timeout=30,
        )
        if resp.status_code in (401, 403):
            raise RuntimeError(f"Brevo auth failed ({resp.status_code}): {resp.text[:300]}")
        resp.raise_for_status()
        campaigns = resp.json().get("campaigns", [])
        for camp in campaigns:
            global_stats = (camp.get("statistics") or {}).get("globalStats")
            if global_stats:
                stats_by_id[str(camp["id"])] = global_stats
        if len(campaigns) < 100:
            break
        offset += 100
    return stats_by_id


def stats_to_fields(stats: Dict[str, Any]) -> Dict[str, Any]:
    delivered = stats.get("delivered") or 0
    soft = stats.get("softBounces") or 0
    hard = stats.get("hardBounces") or 0
    sent = stats.get("sent") or (delivered + soft + hard)
    unique_views = stats.get("uniqueViews") or 0
    unique_clicks = stats.get("uniqueClicks") or 0
    unsubs = stats.get("unsubscriptions") or 0
    complaints = stats.get("complaints") or 0

    fields: Dict[str, Any] = {
        "Delivered": delivered,
        "Opened": unique_views,
        "Clics uniques": unique_clicks,
        "Unsubscribes": unsubs,
        "Soft bounces": soft,
        "Hard bounces": hard,
        "Spam Complaints": complaints,
    }
    rates = {
        "Open Rate": _rate(unique_views, delivered),
        "Click-Through Rate (CTR)": _rate(unique_clicks, delivered),
        "Unsubscribe Rate": _rate(unsubs, delivered),
        "Soft Bounces Rate": _rate(soft, sent),
        "Hard Bounces Rate": _rate(hard, sent),
        "Spam Complaint Rate": _rate(complaints, delivered),
    }
    fields.update({k: v for k, v in rates.items() if v is not None})
    return fields


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="Refresh all records with a Campaign ID (default: only records missing Delivered)")
    parser.add_argument("--validate", action="store_true", help="Recompute stats for already-filled records and print diffs (no writes)")
    parser.add_argument("--dry-run", action="store_true", help="Print planned updates without writing to Airtable")
    parser.add_argument("--table", default=NEWSLETTER_TABLE, help="Airtable table name")
    args = parser.parse_args()

    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key:
        print("❌ BREVO_API_KEY is not set (add it to .env)")
        return 1

    table = AirtableClient().get_table(args.table)
    records = table.all()
    with_campaign = [r for r in records if r.get("fields", {}).get("Campaign ID")]
    print(f"📋 {len(records)} Newsletter records, {len(with_campaign)} with a Campaign ID")

    stats_by_id = fetch_all_global_stats(api_key)
    print(f"📊 {len(stats_by_id)} Brevo campaigns with statistics")

    updates = []
    skipped = 0
    mismatches = 0
    for rec in with_campaign:
        fields = rec["fields"]
        campaign_id = str(fields["Campaign ID"]).strip()
        name = fields.get("Name") or fields.get("Subject") or "(no name)"
        already_filled = fields.get("Delivered") is not None

        if args.validate:
            if not already_filled:
                continue
        elif already_filled and not args.all:
            skipped += 1
            continue

        print(f"→ {name!r} (campaign {campaign_id})")
        stats = stats_by_id.get(campaign_id)
        if stats is None:
            print(f"   ⚠️  Brevo campaign {campaign_id} not found or has no statistics yet")
            continue
        new_fields = stats_to_fields(stats)

        if args.validate:
            diffs = [
                f"     {k}: stored={fields.get(k)!r} computed={v!r}"
                for k, v in new_fields.items()
                if fields.get(k) is not None and fields.get(k) != v
            ]
            if diffs:
                mismatches += 1
                print("\n".join(diffs))
            continue

        if args.dry_run:
            print("     would write:", {k: new_fields[k] for k in STATS_FIELDS if k in new_fields})
            continue
        updates.append({"id": rec["id"], "fields": new_fields})

    if args.validate:
        print(f"\n✅ validation done — {mismatches} record(s) with mismatched stats")
        return 0

    if args.dry_run:
        print(f"\n(dry-run) {skipped} already-filled record(s) skipped")
        return 0

    for i in range(0, len(updates), 10):
        table.batch_update(updates[i : i + 10])
        print(f"   wrote {min(i + 10, len(updates))}/{len(updates)}")

    print(f"\n✅ updated {len(updates)} record(s), skipped {skipped} already-filled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
