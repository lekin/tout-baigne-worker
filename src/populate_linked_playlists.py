#!/usr/bin/env python3
"""Populate Tracks -> Linked Playlists from Airtable playlist criteria.

This script ports the Airtable scripting-block logic into a Python CLI and adds
an optional filter to update only playlists matching a specific Event Type
criteria value (e.g. "Get Lucky!").
"""

from __future__ import annotations

import argparse
from typing import Any

from dotenv import load_dotenv

from src.airtable_client import AirtableClient


load_dotenv()


TRACKS_TABLE = "Tracks"
PLAYLISTS_TABLE_CANDIDATES = ["Playlists", "Playlists (from events)"]


def _extract_names(cell_value: Any) -> list[str]:
    if not cell_value:
        return []
    if isinstance(cell_value, str):
        return [cell_value]
    if isinstance(cell_value, dict):
        name = cell_value.get("name")
        return [str(name)] if name else []
    if isinstance(cell_value, list):
        out: list[str] = []
        for item in cell_value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                name = item.get("name")
                if name:
                    out.append(str(name))
        return out
    return []


def _get_field(fields: dict[str, Any], candidates: list[str], default: Any = None) -> Any:
    for key in candidates:
        if key in fields:
            return fields.get(key)
    return default


def _extract_linked_ids(cell_value: Any) -> list[str]:
    if not cell_value:
        return []
    if isinstance(cell_value, list):
        ids: list[str] = []
        for item in cell_value:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
        return ids
    return []


def _is_playlist_element(cell_value: Any) -> bool:
    if cell_value in (None, "", []):
        # Some bases don't use "Element type". In that case, don't exclude.
        return True
    names = [n.strip().lower() for n in _extract_names(cell_value) if n and n.strip()]
    return any("playlist" in n for n in names)


def _matches_playlist_event_type(playlist_fields: dict[str, Any], wanted: str | None) -> bool:
    if not wanted:
        return True
    playlist_event_type = str(
        _get_field(
            playlist_fields,
            [
                "Event Type Criteria (string)",
                "Event type Criteria (string)",
                "Event type criteria (string)",
                "Event Type Criteria",
                "Event type Criteria",
            ],
            "",
        )
        or ""
    )
    return wanted.lower() in playlist_event_type.lower()


def _is_active_playlist(playlist_fields: dict[str, Any]) -> bool:
    # Airtable checkbox field semantics (API): checked => True; unchecked => empty/omitted.
    # We intentionally use the exact "Active" field only.
    return playlist_fields.get("Active") is True


def _track_matches_playlist(track_fields: dict[str, Any], playlist_fields: dict[str, Any]) -> bool:
    track_event_type = str(
        _get_field(
            track_fields,
            ["Event type (string)", "Event Type (string)", "Event type", "Event Type"],
            "",
        )
        or ""
    )
    track_decade = str(
        _get_field(track_fields, ["Decade (string)", "Decade string", "Decade"], "") or ""
    )
    track_rating = track_fields.get("Rating") or 0

    track_genres_set = set(_extract_names(track_fields.get("Genre")))
    track_tags_set = set(_extract_names(track_fields.get("Tags")))

    # 1) EVENT TYPE (substring)
    playlist_event_type = str(
        _get_field(
            playlist_fields,
            [
                "Event Type Criteria (string)",
                "Event type Criteria (string)",
                "Event type criteria (string)",
                "Event Type Criteria",
                "Event type Criteria",
            ],
            "",
        )
        or ""
    )
    if playlist_event_type and playlist_event_type.lower() not in track_event_type.lower():
        return False

    # 2) DECADE (exact match)
    playlist_decade = str(
        _get_field(
            playlist_fields,
            ["Decade Criteria (string)", "Decade criteria (string)", "Decade Criteria"],
            "",
        )
        or ""
    )
    if playlist_decade and track_decade != playlist_decade:
        return False

    # 3) GENRE (all requested genres must be present)
    playlist_genres = set(
        _extract_names(
            _get_field(playlist_fields, ["Genre Criteria", "Genres Criteria", "Genre criteria"], [])
        )
    )
    if playlist_genres and not playlist_genres.issubset(track_genres_set):
        return False

    # 4) MIN RATING
    min_rating = playlist_fields.get("Minimum Rating")
    if min_rating not in (None, ""):
        try:
            if float(track_rating) < float(min_rating):
                return False
        except Exception:
            return False

    # 5) TAGS (any overlap)
    playlist_tags = set(
        _extract_names(
            _get_field(playlist_fields, ["Tags Criteria", "Tag Criteria", "Tags criteria"], [])
        )
    )
    if playlist_tags and playlist_tags.isdisjoint(track_tags_set):
        return False

    return True


def _chunked(items: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def _track_label(track_fields: dict[str, Any], track_id: str) -> str:
    artist = str(_get_field(track_fields, ["Artist", "Artist name"], "") or "").strip()
    title = str(_get_field(track_fields, ["Name", "Title", "Track name"], "") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    if title:
        return title
    if artist:
        return artist
    return track_id


def _playlist_label(playlist: dict[str, Any]) -> str:
    pf = playlist.get("fields", {}) or {}
    pid = str(playlist.get("id") or "")
    name = str(pf.get("Name") or pid)
    active_raw = pf.get("Active")
    return f"{pid} | {name} | Active={active_raw!r}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Populate Tracks -> Linked Playlists from playlist criteria "
            "with optional playlist Event Type filter."
        )
    )
    parser.add_argument(
        "--playlists-table",
        type=str,
        default=None,
        help='Override playlists table name (default: auto-detect between "Playlists" and "Playlists (from events)").',
    )
    parser.add_argument(
        "--playlist-event-type",
        type=str,
        default=None,
        help='Only update playlists whose "Event Type Criteria (string)" contains this value (e.g. "Get Lucky!").',
    )
    parser.add_argument(
        "--excluded-tag",
        action="append",
        default=None,
        help="Exclude tracks that have any of these tags (repeatable). Default: OFF",
    )
    parser.add_argument(
        "--include-linked",
        action="store_true",
        help="Also process tracks already linked to playlists (default: only unlinked tracks).",
    )
    parser.add_argument(
        "--track-limit",
        type=int,
        default=None,
        help="Max number of tracks to process after filtering.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=200,
        help="Tracks per processing chunk (default: 200).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print updates without writing to Airtable.",
    )
    parser.add_argument(
        "--show-candidates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print the list of track candidates that would be updated (default: enabled). Use --no-show-candidates to disable.",
    )
    parser.add_argument(
        "--max-candidates-print",
        type=int,
        default=200,
        help="Maximum number of candidate lines to print when --show-candidates is set (default: 200).",
    )
    parser.add_argument(
        "--append-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Only add missing Linked Playlists and never remove existing ones.",
    )
    parser.add_argument(
        "--include-inactive-playlists",
        action="store_true",
        help="Include playlists where Active is not truthy (default: only Active playlists are processed).",
    )
    args = parser.parse_args()

    excluded_tags = set(args.excluded_tag or ["OFF"])
    chunk_size = max(1, int(args.chunk_size))

    client = AirtableClient()
    tracks_table = client.get_table(TRACKS_TABLE)

    playlists_table_name: str | None = None
    all_playlists: list[dict[str, Any]] = []
    playlist_candidates = [args.playlists_table] if args.playlists_table else PLAYLISTS_TABLE_CANDIDATES
    for table_name in playlist_candidates:
        if not table_name:
            continue
        try:
            table = client.get_table(table_name)
            rows = table.all()
            playlists_table_name = table_name
            all_playlists = rows
            break
        except Exception:
            continue

    if playlists_table_name is None:
        print("Could not fetch playlists table. Tried:", ", ".join(playlist_candidates))
        return

    print(f"Using playlists table: {playlists_table_name} ({len(all_playlists)} records)")

    all_tracks = tracks_table.all()
    print(f"Total tracks in table: {len(all_tracks)}")

    if args.include_linked:
        candidate_tracks = list(all_tracks)
    else:
        candidate_tracks = [
            t
            for t in all_tracks
            if len(
                _extract_linked_ids((t.get("fields", {}) or {}).get("Linked Playlists"))
            )
            == 0
        ]
    print(f"{len(candidate_tracks)} tracks selected before tag exclusion.")

    tracks_to_process: list[dict[str, Any]] = []
    for t in candidate_tracks:
        fields = t.get("fields", {}) or {}
        tags = set(_extract_names(fields.get("Tags")))
        if tags.intersection(excluded_tags):
            continue
        tracks_to_process.append(t)

    if args.track_limit is not None:
        tracks_to_process = tracks_to_process[: max(0, int(args.track_limit))]

    print(
        f"{len(tracks_to_process)} tracks remain after exclusions"
        f" (excluded tags: {', '.join(sorted(excluded_tags))})."
    )

    filtered_playlists = []
    inactive_skipped = 0
    for p in all_playlists:
        pf = p.get("fields", {}) or {}
        if not args.include_inactive_playlists and not _is_active_playlist(pf):
            inactive_skipped += 1
            continue
        if not _is_playlist_element(pf.get("Element type")):
            continue
        if not _matches_playlist_event_type(pf, args.playlist_event_type):
            continue
        filtered_playlists.append(p)

    print(
        f"Found {len(filtered_playlists)} playlist(s) where Element type = Playlist"
        f" and event type filter = {args.playlist_event_type!r}."
    )
    if not args.include_inactive_playlists:
        print(f"Skipped {inactive_skipped} inactive playlist(s) (Active field filter).")
    if filtered_playlists:
        print("Selected playlists:")
        for p in filtered_playlists:
            print(f"  - {_playlist_label(p)}")

    if args.playlist_event_type and not filtered_playlists:
        preview = []
        for p in all_playlists[:10]:
            pf = p.get("fields", {}) or {}
            preview.append(
                {
                    "id": p.get("id"),
                    "name": pf.get("Name"),
                    "event_type_criteria": _get_field(
                        pf,
                        [
                            "Event Type Criteria (string)",
                            "Event type Criteria (string)",
                            "Event type criteria (string)",
                            "Event Type Criteria",
                            "Event type Criteria",
                        ],
                        None,
                    ),
                }
            )
        print("No playlists matched the event type filter. Sample playlists:")
        for item in preview:
            print(
                f"  - {item['id']} | {item['name']} | EventTypeCriteria={item['event_type_criteria']!r}"
            )

    if not filtered_playlists:
        print("No playlists to process. Exiting.")
        return

    target_playlist_ids = {p["id"] for p in filtered_playlists}
    track_chunks = _chunked(tracks_to_process, chunk_size)
    print(f"Split into {len(track_chunks)} chunk(s). Each up to {chunk_size} tracks.")

    total_updates = 0
    candidate_lines: list[str] = []
    playlist_name_by_id = {
        str(p.get("id")): str((p.get("fields", {}) or {}).get("Name") or p.get("id") or "")
        for p in filtered_playlists
    }
    for chunk_idx, chunk in enumerate(track_chunks, start=1):
        print(
            f"\n=== Processing chunk {chunk_idx} of {len(track_chunks)} [{len(chunk)} tracks] ==="
        )
        updates: list[dict[str, Any]] = []

        for track in chunk:
            track_id = track["id"]
            tf = track.get("fields", {}) or {}

            qualifying_playlist_ids: list[str] = []
            for playlist in filtered_playlists:
                pf = playlist.get("fields", {}) or {}
                if _track_matches_playlist(tf, pf):
                    qualifying_playlist_ids.append(playlist["id"])

            existing_ids = set(_extract_linked_ids(tf.get("Linked Playlists")))
            if args.append_only:
                # Append-only mode: never remove any existing links.
                merged_ids = list(existing_ids) + qualifying_playlist_ids
            else:
                # Preserve links outside the target playlist subset; replace links within subset.
                preserved_ids = [pid for pid in existing_ids if pid not in target_playlist_ids]
                merged_ids = preserved_ids + qualifying_playlist_ids
            merged_ids_unique = list(dict.fromkeys(merged_ids))

            if set(merged_ids_unique) == existing_ids:
                continue

            if args.dry_run and args.show_candidates:
                label = _track_label(tf, track_id)
                matched_names = [playlist_name_by_id.get(pid, pid) for pid in qualifying_playlist_ids]
                if matched_names:
                    candidate_lines.append(
                        f"  - {track_id} | {label} | matched playlists: {', '.join(matched_names)}"
                    )
                else:
                    candidate_lines.append(f"  - {track_id} | {label} | matched playlists: <none>")

            updates.append(
                {
                    "id": track_id,
                    "fields": {
                        # pyairtable expects linked-record cell values as a list of
                        # record ID strings (not {"id": ...} objects).
                        "Linked Playlists": merged_ids_unique
                    },
                }
            )

        if args.dry_run:
            print(f"Chunk {chunk_idx}: would update {len(updates)} track(s).")
            total_updates += len(updates)
            continue

        i = 0
        while i < len(updates):
            batch = updates[i : i + 50]
            tracks_table.batch_update(batch)
            i += 50

        print(
            f"Chunk {chunk_idx}: processed {len(chunk)} tracks, updated {len(updates)} track(s)."
        )
        total_updates += len(updates)

    if args.dry_run:
        if args.show_candidates:
            max_print = max(0, int(args.max_candidates_print))
            print("\nDry-run candidate tracks:")
            for line in candidate_lines[:max_print]:
                print(line)
            if len(candidate_lines) > max_print:
                print(f"  ... ({len(candidate_lines) - max_print} more not shown)")
        print(f"\nDry run complete. Would update {total_updates} track(s).")
    else:
        print(f"\nAll chunks processed. Updated {total_updates} track(s).")


if __name__ == "__main__":
    main()
