"""Build a shortlist of the most frequent tracks in Le Grand Karaoké de l'Amour
playlists, filtered to tracks usable for 15s reels (audio + synced lyrics).

Usage:
    .venv/bin/python scripts/top_karaoke_tracks.py [--limit 30] [--output output/gkda_top_tracks.json]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.airtable_client import AirtableClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-type-name", default="Le Grand Karaoké de l'Amour")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--output", default="output/gkda_top_tracks.json")
    parser.add_argument("--require-reel-ready", action="store_true", default=True)
    args = parser.parse_args()

    client = AirtableClient()

    # 1. Resolve event type record
    event_type = None
    for et in client.get_table("Event types").all():
        if et["fields"].get("Name") == args.event_type_name:
            event_type = et
            break
    if not event_type:
        print(f"❌ Event type '{args.event_type_name}' not found", file=sys.stderr)
        sys.exit(1)
    et_id = event_type["id"]
    print(f"🎤 Event type: {args.event_type_name} ({et_id})")

    # 2. Load all playlists for this event type and aggregate track occurrences
    all_playlists = client.get_table("Playlists (from events)").all()
    event_playlists = [p for p in all_playlists if et_id in (p["fields"].get("Event type") or [])]
    print(f"📀 Playlists found: {len(event_playlists)}")

    counts = Counter()
    track_info: dict[str, dict] = {}
    playlist_names: list[str] = []

    for p in event_playlists:
        fields = p["fields"]
        playlist_names.append(fields.get("Name", p["id"]))
        track_ids = fields.get("Tracks", [])
        titles = fields.get("Title (from Tracks)", []) or []
        names = fields.get("Name (from Tracks)", []) or []

        for i, tid in enumerate(track_ids):
            counts[tid] += 1
            # Prefer Name (from Tracks) over Title (from Tracks) if available
            label = (
                (names[i] if i < len(names) else None)
                or (titles[i] if i < len(titles) else None)
                or tid
            )
            if tid not in track_info:
                track_info[tid] = {"id": tid, "label": label}

    print(f"🎵 Unique tracks across playlists: {len(counts)}")
    print(f"🎵 Total track slots: {sum(counts.values())}")

    # 3. (Optionally) filter to reel-ready tracks: has audio + synced lyrics
    if args.require_reel_ready:
        print("🔍 Checking audio + lyrics for each unique track...")
        reel_ready: list[str] = []
        total = len(track_info)
        for n, (tid, info) in enumerate(track_info.items(), start=1):
            try:
                tr = client.table.get(tid)
            except Exception:
                continue
            fields = tr.get("fields", {})
            has_lyrics = bool(
                fields.get("Richsync JSON (Musixmatch)")
                or fields.get("SRT (Musixmatch)")
                or fields.get("LRC (Musixmatch)")
            )
            has_audio = bool(client.get_audio_file_url(tr))
            if has_lyrics and has_audio:
                reel_ready.append(tid)
                if n % 10 == 0 or n == total:
                    print(f"  {n}/{total} checked, {len(reel_ready)} reel-ready so far", end="\r")
        print()
        print(f"✅ Reel-ready tracks: {len(reel_ready)}")

        # Keep only reel-ready tracks in the counter
        filtered_counts = Counter({tid: c for tid, c in counts.items() if tid in set(reel_ready)})
    else:
        filtered_counts = counts

    # 4. Sort and take top N
    top = filtered_counts.most_common(args.limit)

    results = []
    for rank, (tid, c) in enumerate(top, start=1):
        info = track_info[tid]
        results.append({
            "rank": rank,
            "track_id": tid,
            "name": info["label"],
            "playlist_count": c,
        })

    # 5. Save JSON
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "event_type": args.event_type_name,
                "event_type_id": et_id,
                "playlists": playlist_names,
                "limit": args.limit,
                "total_unique_tracks": len(counts),
                "reel_ready_count": len(filtered_counts) if args.require_reel_ready else None,
                "top_tracks": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n⭐ Top {len(results)} tracks:")
    for r in results:
        print(f"  {r['rank']:2}. {r['name']} ({r['playlist_count']})")

    print(f"\n💾 Saved to {output_path}")


if __name__ == "__main__":
    main()
