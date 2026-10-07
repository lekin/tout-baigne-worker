#!/usr/bin/env python3

import argparse
import base64
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from pyairtable import Api


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _normalize(s: str) -> str:
    s = (s or "").lower().strip()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def extract_track_info(fields: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    track_name = (
        fields.get("Title")
        or fields.get("Track")
        or fields.get("Track name")
        or fields.get("Song")
    )

    artist_name = (
        fields.get("Name (from Artist)")
        or fields.get("Artist")
        or fields.get("Artist name")
        or fields.get("Artists")
    )

    if isinstance(track_name, list):
        track_name = track_name[0] if track_name else None
    if isinstance(artist_name, list):
        artist_name = artist_name[0] if artist_name else None

    if isinstance(track_name, str):
        track_name = track_name.strip() or None
    if isinstance(artist_name, str):
        artist_name = artist_name.strip() or None

    return track_name, artist_name


class SpotifyClient:
    def __init__(self, client_id: str, client_secret: str, timeout_seconds: int = 20):
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout_seconds = timeout_seconds
        self._access_token: Optional[str] = None
        self._access_token_expires_at: float = 0

    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < (self._access_token_expires_at - 30):
            return self._access_token

        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode("utf-8")).decode(
            "utf-8"
        )
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "client_credentials"},
            timeout=self._timeout_seconds,
        )
        r.raise_for_status()
        data = r.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not token or not expires_in:
            raise RuntimeError(f"Unexpected token response: {data}")

        self._access_token = token
        self._access_token_expires_at = now + float(expires_in)
        return token

    def _request(self, method: str, url: str, *, params: Optional[dict] = None) -> dict:
        token = self._get_access_token()

        for _ in range(5):
            r = requests.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=self._timeout_seconds,
            )

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    sleep_s = int(retry_after) if retry_after else 1
                except Exception:
                    sleep_s = 1
                time.sleep(max(1, sleep_s))
                continue

            if r.status_code == 401:
                self._access_token = None
                token = self._get_access_token()
                continue

            r.raise_for_status()
            return r.json()

        raise RuntimeError(f"Spotify rate limited too many times calling {url}")

    def search_track(self, title: str, artist: str, limit: int = 5, market: Optional[str] = None) -> List[dict]:
        q = f"track:{title} artist:{artist}"
        params: dict = {"q": q, "type": "track", "limit": limit}
        if market:
            params["market"] = market
        data = self._request("GET", "https://api.spotify.com/v1/search", params=params)
        tracks = (data.get("tracks") or {}).get("items") or []
        return [t for t in tracks if isinstance(t, dict)]

    def get_track(self, track_id: str, market: Optional[str] = None) -> dict:
        params: dict = {}
        if market:
            params["market"] = market
        return self._request("GET", f"https://api.spotify.com/v1/tracks/{track_id}", params=params)


def pick_best_search_result(results: List[dict], title: str, artist: str) -> Optional[dict]:
    if not results:
        return None

    title_n = _normalize(title)
    artist_n = _normalize(artist)

    def score(t: dict) -> Tuple[int, int]:
        name = _normalize(str(t.get("name") or ""))
        artists = t.get("artists") or []
        primary_artist = ""
        if artists and isinstance(artists, list) and isinstance(artists[0], dict):
            primary_artist = _normalize(str(artists[0].get("name") or ""))

        exact_title = 1 if name == title_n else 0
        exact_artist = 1 if primary_artist == artist_n else 0
        return (-(exact_title + exact_artist), len(name))

    return sorted(results, key=score)[0]


def build_year_filter_formula(year_field: str, year_start: int, year_end: int) -> str:
    year_field_ref = "{" + str(year_field) + "}"
    return f"AND({year_field_ref} >= {int(year_start)}, {year_field_ref} <= {int(year_end)})"


def _extract_non_writable_field_name(err: Exception) -> Optional[str]:
    s = str(err)
    patterns = [
        r'Field "([^"]+)" cannot accept a value because the field is computed',
        r"Field '([^']+)' cannot accept a value because the field is computed",
        r'Field "([^"]+)" cannot accept a value because the field is a lookup',
        r"Field '([^']+)' cannot accept a value because the field is a lookup",
        r'Unknown field name: "([^"]+)"',
        r"Unknown field name: '([^']+)'",
    ]
    for p in patterns:
        m = re.search(p, s)
        if m:
            return m.group(1)
    return None


def _update_record_safe(
    table, record_id: str, fields: Dict[str, Any], disabled_fields: Optional[set[str]] = None
) -> bool:
    pending = dict(fields)
    if disabled_fields:
        for f in list(pending.keys()):
            if f in disabled_fields:
                del pending[f]
    for _ in range(10):
        if not pending:
            return False
        try:
            table.update(record_id, pending)
            return True
        except Exception as e:
            non_writable = _extract_non_writable_field_name(e)
            if non_writable and non_writable in pending:
                del pending[non_writable]
                if disabled_fields is not None:
                    disabled_fields.add(non_writable)
                continue
            raise
    return False


def _flush_updates_safe(table, updates: List[Dict[str, Any]], disabled_fields: set[str]) -> int:
    if not updates:
        return 0

    try:
        table.batch_update(updates)
        return len(updates)
    except Exception:
        updated = 0
        for item in updates:
            record_id = item.get("id")
            fields = item.get("fields")
            if not record_id or not isinstance(fields, dict):
                continue
            try:
                if _update_record_safe(table, record_id, fields, disabled_fields=disabled_fields):
                    updated += 1
            except Exception as e:
                print(f"   ❌ Error: {e}")
        return updated


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default=os.getenv("AIRTABLE_TABLE_NAME", "Tracks"))
    parser.add_argument("--year-field", default="Year (manual)")
    parser.add_argument("--year-start", type=int, default=2010)
    parser.add_argument("--year-end", type=int, default=2019)
    parser.add_argument("--filter-formula", default=None)

    parser.add_argument("--rank-only", action="store_true")
    parser.add_argument("--top", type=int, default=50)

    parser.add_argument("--spotify-field-id", default="Spotify Track ID")
    parser.add_argument("--spotify-field-url", default="Spotify Track URL")
    parser.add_argument("--spotify-field-popularity", default="Spotify Popularity")
    parser.add_argument("--spotify-field-fetch-date", default="Spotify Popularity Fetch Date")

    parser.add_argument("--market", default=None)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--sleep-ms", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    airtable_api_key = os.getenv("AIRTABLE_API_KEY")
    airtable_base_id = os.getenv("AIRTABLE_BASE_ID")
    if not airtable_api_key or not airtable_base_id:
        raise RuntimeError("AIRTABLE_API_KEY and AIRTABLE_BASE_ID must be set")

    spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
    spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not spotify_client_id or not spotify_client_secret:
        raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set")

    api = Api(airtable_api_key)
    table = api.table(airtable_base_id, args.table)

    formula = args.filter_formula
    if formula is None:
        formula = build_year_filter_formula(args.year_field, args.year_start, args.year_end)

    print(f"📋 Airtable table={args.table}")
    print(f"🔎 Airtable formula={formula}")
    print(
        "✍️  Airtable fields: "
        f"popularity={args.spotify_field_popularity}, "
        f"id={args.spotify_field_id}, "
        f"url={args.spotify_field_url}, "
        f"fetch_date={args.spotify_field_fetch_date}"
    )
    if args.rank_only:
        print("🧾 Mode: rank-only (no Airtable updates)")

    spotify = SpotifyClient(spotify_client_id, spotify_client_secret)

    records = table.all(formula=formula, page_size=args.page_size)
    print(f"✅ Found {len(records)} record(s) to process")

    updates_batch: List[Dict[str, Any]] = []
    updated_count = 0
    skipped_count = 0
    failed_count = 0
    disabled_fields: set[str] = set()
    ranking: List[Dict[str, Any]] = []

    for idx, rec in enumerate(records, 1):
        record_id = rec.get("id")
        fields = rec.get("fields") or {}
        if not record_id or not isinstance(fields, dict):
            skipped_count += 1
            continue

        title, artist = extract_track_info(fields)
        if not title or not artist:
            print(f"[{idx}/{len(records)}] ⚠️  Missing title/artist for {record_id}")
            skipped_count += 1
            continue

        try:
            print(f"[{idx}/{len(records)}] 🎵 {artist} - {title}")
            results = spotify.search_track(title, artist, limit=5, market=args.market)
            picked = pick_best_search_result(results, title, artist)
            if not picked:
                print("   ⚠️  No Spotify match")
                skipped_count += 1
                continue

            track_id = picked.get("id")
            if not track_id:
                print("   ⚠️  Spotify match missing id")
                skipped_count += 1
                continue

            full = spotify.get_track(track_id, market=args.market)
            popularity = full.get("popularity")
            url = ((full.get("external_urls") or {}).get("spotify"))

            ranking.append(
                {
                    "artist": artist,
                    "title": title,
                    "spotify_id": track_id,
                    "spotify_url": url,
                    "popularity": popularity,
                }
            )

            update_fields: Dict[str, Any] = {}
            update_fields[args.spotify_field_id] = track_id
            if url:
                update_fields[args.spotify_field_url] = url
            if popularity is not None:
                update_fields[args.spotify_field_popularity] = int(popularity)
            update_fields[args.spotify_field_fetch_date] = datetime.now().isoformat()

            if disabled_fields:
                for f in list(update_fields.keys()):
                    if f in disabled_fields:
                        del update_fields[f]

            if args.rank_only:
                pass
            elif args.dry_run:
                print(f"   🧪 Dry run -> {update_fields}")
            else:
                updates_batch.append({"id": record_id, "fields": update_fields})

            if len(updates_batch) >= 10 and (not args.dry_run) and (not args.rank_only):
                updated_count += _flush_updates_safe(table, updates_batch, disabled_fields)
                updates_batch = []

        except Exception as e:
            print(f"   ❌ Error: {e}")
            failed_count += 1

        time.sleep(max(0, args.sleep_ms) / 1000.0)

    if updates_batch and (not args.dry_run) and (not args.rank_only):
        updated_count += _flush_updates_safe(table, updates_batch, disabled_fields)

    if args.rank_only:
        def _pop_key(item: Dict[str, Any]) -> Tuple[int, int]:
            p = item.get("popularity")
            if p is None:
                return (1, 0)
            try:
                return (0, -int(p))
            except Exception:
                return (1, 0)

        ranking_sorted = sorted(ranking, key=_pop_key)
        if args.top and args.top > 0:
            ranking_sorted = ranking_sorted[: int(args.top)]

        print("\n🏆 Spotify popularity ranking")
        for i, item in enumerate(ranking_sorted, 1):
            p = item.get("popularity")
            artist = item.get("artist")
            title = item.get("title")
            url = item.get("spotify_url")
            p_str = "?" if p is None else str(p)
            if url:
                print(f"{i:>3}. {p_str:>3} | {artist} - {title} | {url}")
            else:
                print(f"{i:>3}. {p_str:>3} | {artist} - {title}")

    print("\n📊 Summary")
    print(f"   Updated: {updated_count}")
    print(f"   Skipped: {skipped_count}")
    print(f"   Failed: {failed_count}")
    if disabled_fields:
        disabled_sorted = ", ".join(sorted(disabled_fields))
        print(f"   Disabled fields: {disabled_sorted}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
