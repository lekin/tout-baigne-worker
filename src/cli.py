"""Command-line interface for karaoke generation using Musixmatch."""
import click
import sys
import textwrap
import time
from pathlib import Path
import tempfile
import urllib.request
from datetime import datetime
from typing import Any, List, Optional, Tuple
import os
import subprocess
import re
import unicodedata
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
import requests
import random
import shutil
import hashlib
import base64
import csv
import statistics

VIVID_COLORS = [
    '#f48fb1',
    '#ffd54f',
    '#a5d6a7',
    '#64b5f6',
    '#b39ddb',
]

# Load environment variables
load_dotenv()

from src.config import settings
from src.airtable_client import AirtableClient
from src.karaoke_generator import KaraokeGenerator
from src.render.steps import (
    download_asset,
    download_google_drive_file,
    get_audio_duration,
    get_media_duration_seconds,
    mux_replace_audio,
    synth_color_background,
    _download_to_file,
    _extract_html_hint,
    _looks_like_html,
    _normalize_google_drive_download_url,
    _save_google_drive_cache,
)
from src.musixmatch_lyrics import get_synced_lyrics, ASSKaraokeGenerator, LRCParser, RichsyncParser, SyncedLine
from src.chorus_detector import detect_chorus_start, detect_chorus_start_from_richsync
from src.playlist_generator import (
    MODELS as PLAYLIST_MODELS,
    PlaylistBlock,
    arrange_tracks,
    get_unused_tracks,
    print_playlist_summary,
    print_model_overview,
    print_genre_distribution,
    _track_label,
)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v is not None]
    return [value]


def _pick_ass_palette(fields: dict) -> Tuple[str, str, str]:
    primary_colour = "&H0000FFFF"
    secondary_colour = "&H00CCCCCC"
    outline_colour = "&HA64DFF"

    tags = " ".join(str(v) for v in _as_list(fields.get("Tags")))
    genre = " ".join(str(v) for v in _as_list(fields.get("Genre")))
    blob = f"{tags} {genre}".lower()

    if "emo" in blob:
        primary_colour = "&H00A0E87F"
        secondary_colour = "&H00FF8CB5"
        outline_colour = "&H008A2E5A"

    return primary_colour, secondary_colour, outline_colour


def _safe_unlink(path: Optional[str]) -> None:
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass


def _maybe_send_karaoke_complete_whatsapp(
    *,
    record_id: str,
    fields: dict,
    output_path: str,
    file_size_mb: float,
    upload_succeeded: Optional[bool],
) -> None:
    if not os.getenv('TWILIO_ACCOUNT_SID') or not os.getenv('TWILIO_AUTH_TOKEN') or not os.getenv('TWILIO_WHATSAPP_TO'):
        return

    track_name = str(fields.get('Name') or 'karaoke')
    artists = ", ".join(str(v) for v in _as_list(fields.get('Artist')) if str(v).strip())
    status_line = "Airtable upload: ok" if upload_succeeded else "Airtable upload: failed" if upload_succeeded is False else "Airtable upload: skipped"
    message = "\n".join([
        "✅ Karaoke finished",
        f"Track: {track_name}",
        f"Artist: {artists or 'Unknown'}",
        f"Record: {record_id}",
        f"Output: {output_path}",
        f"Size: {file_size_mb:.1f} MB",
        status_line,
    ])

    try:
        from notify import send_whatsapp
        if send_whatsapp(message):
            click.echo("✓ WhatsApp notification sent")
    except Exception as e:
        click.echo(f"⚠️  Could not send WhatsApp notification: {e}")


def _probe_video_duration_seconds(video_path: str) -> Optional[float]:
    try:
        import json

        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            video_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except Exception:
        return None


def _probe_has_audio_stream(media_path: str) -> bool:
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=index',
            '-of', 'csv=p=0',
            media_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=True,
        )
        return bool((result.stdout or '').strip())
    except Exception:
        return False


def _as_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, list) and value:
        value = value[0]
    return str(value).strip()


def _sanitize_filename(name: str, max_len: int = 180) -> str:
    s = (name or '').strip()
    s = s.replace('\x00', '').replace('\0', '')
    s = s.replace('/', '_').replace('\\', '_')
    s = re.sub(r"\s+", " ", s).strip()
    # Transliterate accented characters to ASCII equivalents (é→e, ü→u, …)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._()-&,'")
    s = ''.join((c if c in allowed else '_') for c in s)
    s = s.strip(' ._')
    if len(s) > max_len:
        s = s[:max_len].rstrip(' ._')
    return s or 'video'


def _spotify_formula_escape(value: str) -> str:
    return str(value or '').replace('\\', '\\\\').replace("'", "\\'")


def _spotify_extract_linked_ids(cell_value: Any) -> list[str]:
    if not cell_value:
        return []
    if isinstance(cell_value, list):
        out: list[str] = []
        for item in cell_value:
            if isinstance(item, str) and item.startswith('rec'):
                out.append(item)
            elif isinstance(item, dict):
                item_id = item.get('id')
                if isinstance(item_id, str) and item_id.startswith('rec'):
                    out.append(item_id)
        return out
    return []


def _spotify_iter_text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        out: list[str] = []
        for key in ('name', 'Name', 'title', 'Title', 'text', 'url', 'id'):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_spotify_iter_text_values(item))
        return out
    text = str(value).strip()
    return [text] if text else []


def _spotify_first_text(value: Any) -> str:
    values = _spotify_iter_text_values(value)
    return values[0] if values else ''


def _spotify_parse_track_uri(value: Any) -> Optional[str]:
    for raw in _spotify_iter_text_values(value):
        s = raw.strip()
        if not s:
            continue

        if s.startswith('spotify:track:'):
            parts = s.split(':')
            if len(parts) >= 3 and parts[2]:
                return f"spotify:track:{parts[2]}"

        m = re.search(r'open\.spotify\.com/track/([A-Za-z0-9]{22})', s)
        if m:
            return f"spotify:track:{m.group(1)}"

        if re.fullmatch(r'[A-Za-z0-9]{22}', s):
            return f"spotify:track:{s}"

    return None


def _spotify_extract_track_uri_from_fields(fields: dict) -> Optional[str]:
    candidate_fields = [
        'Spotify Track URI',
        'Spotify URI',
        'Spotify track URI',
        'Spotify Track URL',
        'Spotify URL',
        'Spotify Track ID',
        'Spotify ID',
        'Spotify (from Tracks)',
    ]
    for field_name in candidate_fields:
        if field_name not in fields:
            continue
        uri = _spotify_parse_track_uri(fields.get(field_name))
        if uri:
            return uri
    return None


def _spotify_extract_track_title_artist(fields: dict) -> tuple[str, str]:
    title = (
        _spotify_first_text(fields.get('Title'))
        or _spotify_first_text(fields.get('Name'))
        or _spotify_first_text(fields.get('Track name'))
        or _spotify_first_text(fields.get('Song'))
    )
    artist = (
        _spotify_first_text(fields.get('Artist (string)'))
        or _spotify_first_text(fields.get('Name (from Artist)'))
        or _spotify_first_text(fields.get('Artist'))
        or _spotify_first_text(fields.get('Artist name'))
        or _spotify_first_text(fields.get('Artists'))
    )
    return title.strip(), artist.strip()


def _spotify_normalize_match_text(s: str) -> str:
    s = (s or '').lower().strip()
    out = []
    for ch in s:
        out.append(ch if ch.isalnum() else ' ')
    return ' '.join(''.join(out).split())


def _spotify_pick_best_search_result(results: list[dict], title: str, artist: str) -> Optional[dict]:
    if not results:
        return None

    title_n = _spotify_normalize_match_text(title)
    artist_n = _spotify_normalize_match_text(artist)

    def _score(item: dict) -> tuple[int, int, int]:
        name = _spotify_normalize_match_text(str(item.get('name') or ''))
        artists = item.get('artists') or []
        artist_names = []
        if isinstance(artists, list):
            for a in artists:
                if isinstance(a, dict):
                    artist_names.append(_spotify_normalize_match_text(str(a.get('name') or '')))

        exact_title = 1 if title_n and name == title_n else 0
        contains_title = 1 if title_n and title_n in name else 0
        exact_artist = 1 if artist_n and artist_n in artist_names else 0
        popularity = int(item.get('popularity') or 0)

        return (
            -(exact_title * 4 + exact_artist * 3 + contains_title),
            -popularity,
            len(name),
        )

    return sorted(results, key=_score)[0]


class SpotifyPlaylistClient:
    def __init__(
        self,
        *,
        access_token: Optional[str],
        refresh_token: Optional[str],
        client_id: Optional[str],
        client_secret: Optional[str],
        timeout_seconds: int = 20,
    ):
        self._access_token = (access_token or '').strip() or None
        self._refresh_token = (refresh_token or '').strip() or None
        self._client_id = (client_id or '').strip() or None
        self._client_secret = (client_secret or '').strip() or None
        self._timeout_seconds = timeout_seconds

    def _refresh_access_token(self) -> str:
        if not self._refresh_token or not self._client_id or not self._client_secret:
            raise RuntimeError(
                "Missing Spotify auth. Set SPOTIFY_USER_ACCESS_TOKEN or configure "
                "SPOTIFY_REFRESH_TOKEN + SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET."
            )

        basic = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode('utf-8')).decode('utf-8')
        resp = requests.post(
            'https://accounts.spotify.com/api/token',
            headers={
                'Authorization': f'Basic {basic}',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data={
                'grant_type': 'refresh_token',
                'refresh_token': self._refresh_token,
            },
            timeout=self._timeout_seconds,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Spotify token refresh failed ({resp.status_code}): {resp.text[:500]}")

        data = resp.json()
        access_token = data.get('access_token')
        if not access_token:
            raise RuntimeError(f"Unexpected Spotify token response: {data}")

        self._access_token = str(access_token)
        new_refresh = data.get('refresh_token')
        if isinstance(new_refresh, str) and new_refresh.strip():
            self._refresh_token = new_refresh.strip()
        return self._access_token

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        return self._refresh_access_token()

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        last_error = None
        for _ in range(6):
            token = self._get_access_token()
            resp = requests.request(
                method,
                url,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                },
                params=params,
                json=json_body,
                timeout=self._timeout_seconds,
            )

            if resp.status_code == 429:
                retry_after = resp.headers.get('Retry-After')
                try:
                    wait_s = int(retry_after) if retry_after else 1
                except Exception:
                    wait_s = 1
                time.sleep(max(1, wait_s))
                continue

            if resp.status_code == 401:
                self._access_token = None
                if self._refresh_token and self._client_id and self._client_secret:
                    self._refresh_access_token()
                    continue
                raise RuntimeError(
                    "Spotify user token is unauthorized. Provide a fresh SPOTIFY_USER_ACCESS_TOKEN "
                    "with playlist-modify-public or playlist-modify-private scope."
                )

            if resp.status_code >= 400:
                last_error = f"Spotify API error {resp.status_code}: {resp.text[:500]}"
                break

            if not (resp.text or '').strip():
                return {}
            try:
                return resp.json()
            except Exception:
                return {}

        raise RuntimeError(last_error or f"Spotify request failed for {url}")

    def search_track(self, *, title: str, artist: str, limit: int = 5, market: Optional[str] = None) -> list[dict]:
        q = f"track:{title}" if title else ''
        if artist:
            q = f"{q} artist:{artist}".strip()
        params: dict[str, Any] = {
            'q': q,
            'type': 'track',
            'limit': max(1, min(int(limit), 50)),
        }
        if market:
            params['market'] = market
        data = self._request('GET', 'https://api.spotify.com/v1/search', params=params)
        items = (data.get('tracks') or {}).get('items') or []
        return [item for item in items if isinstance(item, dict)]

    def create_playlist(self, *, name: str, description: str, public: bool) -> dict:
        payload = {
            'name': name,
            'description': description,
            'public': bool(public),
        }
        return self._request('POST', 'https://api.spotify.com/v1/me/playlists', json_body=payload)

    def add_tracks(self, *, playlist_id: str, uris: list[str]) -> None:
        if not uris:
            return
        for i in range(0, len(uris), 100):
            chunk = uris[i:i + 100]
            self._request(
                'POST',
                f'https://api.spotify.com/v1/playlists/{playlist_id}/tracks',
                json_body={'uris': chunk},
            )


def _fetch_tracklist_record(
    airtable_client: AirtableClient,
    identifier: str,
    table_candidates: list[str],
) -> tuple[Optional[dict], Optional[str]]:
    ident = (identifier or '').strip()
    for table_name in [t for t in dict.fromkeys(table_candidates) if t]:
        try:
            table = airtable_client.get_table(table_name)
        except Exception:
            continue

        if ident.startswith('rec'):
            try:
                rec = table.get(ident)
                if isinstance(rec, dict):
                    return rec, table_name
            except Exception:
                pass

        safe_ident = _spotify_formula_escape(ident)
        formulas = [
            f"{{ID}} = '{safe_ident}'",
            f"{{Name}} = '{safe_ident}'",
            f"FIND('{safe_ident}', {{Name}})",
        ]
        for formula in formulas:
            try:
                rows = table.all(formula=formula, max_records=1)
            except Exception:
                continue
            if rows:
                return rows[0], table_name

    return None, None


def _guess_track_ids_from_tracklist_fields(fields: dict) -> tuple[list[str], Optional[str]]:
    best_ids: list[str] = []
    best_field = None
    for field_name, value in fields.items():
        ids = _spotify_extract_linked_ids(value)
        if len(ids) > len(best_ids):
            best_ids = ids
            best_field = str(field_name)
    return best_ids, best_field


def _extract_year(record: dict) -> Optional[int]:
    fields = record.get('fields', {}) or {}
    candidates = [
        fields.get('Year'),
        fields.get('Release year'),
        fields.get('Release Year'),
        fields.get('Year (from Tracks)'),
        fields.get('Release date'),
        fields.get('Release Date'),
    ]

    for v in candidates:
        if isinstance(v, list) and v:
            v = v[0]
        if v is None:
            continue
        try:
            if isinstance(v, (int, float)):
                y = int(v)
                if 1900 <= y <= 2100:
                    return y
            s = str(v).strip()
            m = re.search(r"(19\d{2}|20\d{2})", s)
            if m:
                y = int(m.group(1))
                if 1900 <= y <= 2100:
                    return y
        except Exception:
            continue
    return None


def _cache_video_basename(table_name: str, record: dict, record_id: str) -> str:
    fields = record.get('fields', {}) or {}
    artist = _as_text(fields.get('Artist') or fields.get('Artist name'))
    title = _as_text(fields.get('Name') or fields.get('Title') or fields.get('Track name'))

    # If the title already ends with (YYYY), do not append a second year.
    has_year_suffix = bool(re.search(r"\((19\d{2}|20\d{2})\)\s*$", title))
    if not has_year_suffix:
        y = _extract_year(record)
        if y:
            title = f"{title} ({y})" if title else f"({y})"

    if artist and title:
        return f"{artist} - {title}"
    if title:
        return title
    if artist:
        return artist
    safe_table = str(table_name).lower().replace(' ', '_').replace('/', '_')
    return f"{safe_table}_{record_id}"


def _cookies_file_contains_domain(cookies_path: str, domain_fragment: str) -> bool:
    try:
        with open(cookies_path, 'r', encoding='utf-8', errors='ignore') as f:
            data = f.read(200000)
        return domain_fragment.lower() in data.lower()
    except Exception:
        return False


def _repair_ytdlp_part_filename(target_path: str) -> None:
    try:
        if not target_path:
            return
        if os.path.exists(target_path):
            return
        weird = target_path + '.part.mp4'
        if os.path.exists(weird) and os.path.getsize(weird) > 1000:
            try:
                os.replace(weird, target_path)
            except Exception:
                try:
                    os.rename(weird, target_path)
                except Exception:
                    return
    except Exception:
        return


def _download_video_from_record(
    airtable_client: AirtableClient,
    karaoke_generator: KaraokeGenerator,
    record: dict,
    dst_path: str,
    youtube_max_height: int = 1080,
    allow_youtube: bool = True,
    yt_cookies: Optional[str] = None,
) -> bool:
    gdrive_video_url = airtable_client.get_gdrive_music_video_url(record)
    if gdrive_video_url:
        try:
            if "drive.google.com" in gdrive_video_url:
                ok = download_google_drive_file(gdrive_video_url, dst_path, timeout_seconds=180)
                if not ok:
                    _download_to_file(gdrive_video_url, dst_path, timeout_seconds=180)
            else:
                _download_to_file(gdrive_video_url, dst_path, timeout_seconds=180)
            if _looks_like_html(dst_path):
                return False
            return True
        except Exception:
            return False

    video_url = airtable_client.get_video_url(record)
    if not video_url:
        return False

    try:
        lower = str(video_url).lower()

        if 'youtube.com' in lower or 'youtu.be' in lower:
            if not allow_youtube:
                return False
            karaoke_generator.download_youtube_video(
                video_url,
                dst_path,
                max_height=youtube_max_height,
                cookies_txt_path=yt_cookies,
            )
            return os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000 and not _looks_like_html(dst_path)

        if 'drive.google.com' in lower:
            gdrive_download_url = _normalize_google_drive_download_url(str(video_url))
            download_google_drive_file(gdrive_download_url, dst_path, timeout_seconds=180)
            if os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000 and not _looks_like_html(dst_path):
                return True
            try:
                _download_to_file(gdrive_download_url, dst_path, timeout_seconds=180)
            except Exception:
                pass
            return os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000 and not _looks_like_html(dst_path)

        if (
            lower.endswith('.mp4')
            or lower.endswith('.mov')
            or lower.endswith('.mkv')
            or lower.endswith('.webm')
            or lower.endswith('.m3u8')
        ):
            _download_to_file(video_url, dst_path, timeout_seconds=180)
            return os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000 and not _looks_like_html(dst_path)

        if lower.startswith('http://') or lower.startswith('https://'):
            _download_to_file(video_url, dst_path, timeout_seconds=180)
            return os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000 and not _looks_like_html(dst_path)
    except Exception:
        return False


def _render_boomerang_excerpt(
    input_video_path: str,
    start_seconds: float,
    duration_seconds: float,
    output_path: str,
    width: int,
    height: int,
    fast: bool,
    dv: bool = False,
    mosaic: bool = False,
    glitch: bool = False,
    glitch_duration_seconds: float = 0.12,
    slowmo: bool = False,
    slowmo_factor: float = 2.0,
    slowmo_fps: int = 30,
    audio_echo: bool = False,
    audio_echo_delays_ms: str = '120|240',
    audio_echo_decays: str = '0.22|0.12',
    fade_duration: float = 0.25,
    noisy_video: bool = False,
    noisy_video_level: int = 24,
    vocals_only: bool = False,
) -> bool:
    return _render_effect_excerpt(
        input_video_path=input_video_path,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        output_path=output_path,
        width=width,
        height=height,
        fast=fast,
        mode='boomerang',
        dv=dv,
        mosaic=mosaic,
        glitch=glitch,
        glitch_duration_seconds=glitch_duration_seconds,
        slowmo=slowmo,
        slowmo_factor=slowmo_factor,
        slowmo_fps=slowmo_fps,
        audio_echo=audio_echo,
        audio_echo_delays_ms=audio_echo_delays_ms,
        audio_echo_decays=audio_echo_decays,
        fade_duration=fade_duration,
        noisy_video=noisy_video,
        noisy_video_level=noisy_video_level,
        vocals_only=vocals_only,
    )


def _render_reverse_excerpt(
    input_video_path: str,
    start_seconds: float,
    duration_seconds: float,
    output_path: str,
    width: int,
    height: int,
    fast: bool,
    dv: bool = False,
    mosaic: bool = False,
    glitch: bool = False,
    glitch_duration_seconds: float = 0.12,
    slowmo: bool = False,
    slowmo_factor: float = 2.0,
    slowmo_fps: int = 30,
    audio_echo: bool = False,
    audio_echo_delays_ms: str = '120|240',
    audio_echo_decays: str = '0.22|0.12',
    fade_duration: float = 0.25,
    noisy_video: bool = False,
    noisy_video_level: int = 24,
    vocals_only: bool = False,
) -> bool:
    return _render_effect_excerpt(
        input_video_path=input_video_path,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        output_path=output_path,
        width=width,
        height=height,
        fast=fast,
        mode='reverse',
        dv=dv,
        mosaic=mosaic,
        glitch=glitch,
        glitch_duration_seconds=glitch_duration_seconds,
        slowmo=slowmo,
        slowmo_factor=slowmo_factor,
        slowmo_fps=slowmo_fps,
        audio_echo=audio_echo,
        audio_echo_delays_ms=audio_echo_delays_ms,
        audio_echo_decays=audio_echo_decays,
        fade_duration=fade_duration,
        noisy_video=noisy_video,
        noisy_video_level=noisy_video_level,
        vocals_only=vocals_only,
    )


def _render_effect_excerpt(
    input_video_path: str,
    start_seconds: float,
    duration_seconds: float,
    output_path: str,
    width: int,
    height: int,
    fast: bool,
    mode: str,
    dv: bool,
    mosaic: bool,
    glitch: bool = False,
    glitch_duration_seconds: float = 0.12,
    slowmo: bool = False,
    slowmo_factor: float = 2.0,
    slowmo_fps: int = 30,
    audio_echo: bool = False,
    audio_echo_delays_ms: str = '120|240',
    audio_echo_decays: str = '0.22|0.12',
    fade_duration: float = 0.25,
    noisy_video: bool = False,
    noisy_video_level: int = 24,
    vocals_only: bool = False,
) -> bool:
    vf_scale_crop = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
        f"crop={width}:{height},setsar=1,format=yuv420p"
    )

    parts: list[str] = [f"[0:v]{vf_scale_crop}[base]"]
    last = 'base'

    if dv:
        parts.append(
            f"[{last}]"
            # Deliberate DV softness/compression feel: downscale then upscale
            f"scale=iw/2:ih/2:flags=bilinear,"
            f"scale=iw*2:ih*2:flags=bilinear,"
            # Punchy camcorder colors
            f"eq=contrast=1.30:brightness=0.05:saturation=0.90:gamma=0.95,"
            # Edge ringing + detail crunch
            f"unsharp=luma_msize_x=7:luma_msize_y=7:luma_amount=0.85:chroma_msize_x=5:chroma_msize_y=5:chroma_amount=0.25,"
            # Grain / sensor noise
            f"noise=alls=28:allf=t+u,"
            # Chroma bleed / misalignment
            f"chromashift=cbh=3:cbv=2:crh=-3:crv=-2,"
            # Subtle vignette
            f"vignette=0.55[dv]"
        )
        last = 'dv'

    if glitch:
        gd = max(0.0, float(glitch_duration_seconds or 0.0))
        if gd > 0.0:
            # Glitch-in effect at the beginning of a segment (acts as transition glitch)
            parts.append(
                f"[{last}]split[g0][g1];"
                f"[g0]trim=start=0:duration={gd:.6f},setpts=PTS-STARTPTS,"
                f"eq=contrast=1.55:brightness=0.08:saturation=0.25,"
                f"noise=alls=60:allf=t+u,"
                f"chromashift=cbh=10:cbv=0:crh=-10:crv=0,"
                f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=1.1[gz];"
                f"[g1]trim=start={gd:.6f},setpts=PTS-STARTPTS[gn];"
                f"[gz][gn]concat=n=2:v=1:a=0[gl]"
            )
            last = 'gl'

    if slowmo:
        try:
            sf = float(slowmo_factor)
        except Exception:
            sf = 2.0
        if sf < 1.0:
            sf = 1.0
        try:
            fps_out = int(slowmo_fps)
        except Exception:
            fps_out = 30
        if fps_out <= 0:
            fps_out = 30
        # Equivalent to your example: setpts=sf*PTS + minterpolate to fps_out
        parts.append(
            f"[{last}]setpts={sf:.6f}*PTS,"
            f"minterpolate=fps={fps_out}:mb_size=16:search_param=400:vsbmc=0:scd=none:mc_mode=aobmc:me_mode=bilat:me=ds[sm]"
        )
        last = 'sm'

    if noisy_video:
        try:
            nv = int(noisy_video_level)
        except Exception:
            nv = 24
        nv = max(4, min(80, nv))
        parts.append(f"[{last}]noise=alls={nv}:allf=t+u,eq=contrast=1.07:brightness=0.02:saturation=0.95[nv]")
        last = 'nv'

    # Ensure a stable FPS at output
    out_fps = 24 if fast else 30
    parts.append(f"[{last}]fps={out_fps}[outfps]")
    last = 'outfps'

    if mosaic:
        parts.append(
            f"[{last}]scale=iw/2:ih/2,split=4[a][b][c][d];"
            f"[a][b]hstack=inputs=2[top];"
            f"[c][d]hstack=inputs=2[bottom];"
            f"[top][bottom]vstack=inputs=2[m]"
        )
        last = 'm'

    if mode == 'boomerang':
        parts.append(f"[{last}]split[v1][v2];[v2]reverse[r];[v1][r]concat=n=2:v=1:a=0[vraw]")
    elif mode == 'reverse':
        parts.append(f"[{last}]reverse[vraw]")
    else:
        parts.append(f"[{last}]null[vraw]")

    # Audio chain (kept in sync with video mode)
    parts.append('[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0]')
    a_last = 'a0'

    if vocals_only:
        # Fast vocal-focused extraction (center information emphasis)
        parts.append(
            f"[{a_last}]pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,"
            f"highpass=f=120,lowpass=f=4200,"
            f"acompressor=threshold=-24dB:ratio=3:attack=8:release=90:makeup=6,"
            f"volume=2.8[voc]"
        )
        a_last = 'voc'

    if mode == 'boomerang':
        parts.append(f"[{a_last}]asplit[a1][a2];[a2]areverse[ar];[a1][ar]concat=n=2:v=0:a=1[araw]")
    elif mode == 'reverse':
        parts.append(f"[{a_last}]areverse[araw]")
    else:
        parts.append(f"[{a_last}]anull[araw]")

    out_duration = float(duration_seconds) * (2.0 if mode == 'boomerang' else 1.0)

    # Strong, unmistakable echo tail on the last bit of each extract.
    tail_dur = min(2.20, max(1.10, out_duration * 0.82))
    tail_start = max(0.0, out_duration - tail_dur)
    parts.append(
        f"[araw]asplit[adry0][atail0];"
        f"[adry0]volume='if(lt(t,{tail_start:.6f}),1,0.0)'[adry];"
        f"[atail0]volume='if(gte(t,{tail_start:.6f}),1,0)'[atail];"
        f"[atail]asplit=4[t0][t1][t2][t3];"
        f"[t0]volume=1.55[t0v];"
        f"[t1]adelay=220|220,volume=1.35[t1v];"
        f"[t2]adelay=440|440,volume=1.15[t2v];"
        f"[t3]adelay=660|660,volume=0.95[t3v];"
        f"[t0v][t1v][t2v][t3v]amix=inputs=4:duration=longest:normalize=0,"
        f"aecho=0.96:1.0:760:0.86,highpass=f=80,lowpass=f=4200[awet];"
        f"[adry][awet]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.93[arawfx]"
    )

    fd = max(0.0, float(fade_duration or 0.0))
    if fd > 0.0:
        fade_in_d = min(fd, max(0.03, out_duration / 2.0))
        fade_out_d = min(max(fd * 2.4, 0.35), max(0.04, out_duration * 0.85))
        fade_out_start = max(0.0, out_duration - fade_out_d)
        parts.append(f"[vraw]fade=t=in:st=0:d={fade_in_d:.6f},fade=t=out:st={fade_out_start:.6f}:d={fade_out_d:.6f}[v]")
        parts.append(f"[arawfx]afade=t=in:st=0:d={fade_in_d:.6f},afade=t=out:st={fade_out_start:.6f}:d={fade_out_d:.6f}[a]")
    else:
        parts.append('[vraw]null[v]')
        parts.append('[arawfx]anull[a]')

    filter_complex = ';'.join(parts)

    if fast:
        vcodec = ['-c:v', 'h264_videotoolbox', '-b:v', '3000k', '-r', str(out_fps)]
    else:
        vcodec = ['-c:v', 'libx264', '-crf', '23', '-preset', 'medium']

    input_duration_seconds = duration_seconds
    if slowmo:
        try:
            sf = float(slowmo_factor)
        except Exception:
            sf = 2.0
        if sf > 1.0:
            input_duration_seconds = max(0.05, float(duration_seconds) / sf)

    cmd = [
        'ffmpeg', '-y',
        '-ss', f"{start_seconds:.6f}",
        '-t', f"{input_duration_seconds:.6f}",
        '-i', input_video_path,
        '-filter_complex', filter_complex,
        '-map', '[v]',
        '-map', '[a]',
        *vcodec,
        '-c:a', 'aac',
        '-b:a', '192k',
        '-ar', '48000',
        '-ac', '2',
        '-shortest',
        '-movflags', '+faststart',
        '-pix_fmt', 'yuv420p',
        output_path,
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _write_premiere_fcp7_xml(
    segment_paths: list[str],
    output_xml_path: str,
    width: int,
    height: int,
    fps: int,
    sequence_name: str,
) -> bool:
    try:
        fps_i = max(1, int(fps))

        def _frames(seconds: float) -> int:
            return max(1, int(round(max(0.0, float(seconds)) * fps_i)))

        durations_frames: list[int] = []
        for p in segment_paths:
            d = get_media_duration_seconds(p)
            durations_frames.append(_frames(d if d else 0.0))

        total_frames = max(1, sum(durations_frames))

        xmeml = ET.Element('xmeml', {'version': '5'})
        sequence = ET.SubElement(xmeml, 'sequence')
        ET.SubElement(sequence, 'name').text = sequence_name

        seq_rate = ET.SubElement(sequence, 'rate')
        ET.SubElement(seq_rate, 'timebase').text = str(fps_i)
        ET.SubElement(seq_rate, 'ntsc').text = 'FALSE'

        ET.SubElement(sequence, 'duration').text = str(total_frames)

        media = ET.SubElement(sequence, 'media')
        video = ET.SubElement(media, 'video')
        fmt = ET.SubElement(video, 'format')
        sc = ET.SubElement(fmt, 'samplecharacteristics')
        ET.SubElement(sc, 'width').text = str(int(width))
        ET.SubElement(sc, 'height').text = str(int(height))
        ET.SubElement(sc, 'pixelaspectratio').text = 'square'
        ET.SubElement(sc, 'fielddominance').text = 'none'

        vtrack = ET.SubElement(video, 'track')
        atrack_parent = ET.SubElement(media, 'audio')
        atrack = ET.SubElement(atrack_parent, 'track')

        timeline_cursor = 0
        for i, p in enumerate(segment_paths):
            dur = durations_frames[i]
            clip_name = os.path.basename(p)
            file_id = f'file-{i+1}'
            clip_id = f'clipitem-{i+1}'
            start_f = timeline_cursor
            end_f = timeline_cursor + dur

            for trk, with_video in ((vtrack, True), (atrack, False)):
                clip = ET.SubElement(trk, 'clipitem', {'id': clip_id + ('-v' if with_video else '-a')})
                ET.SubElement(clip, 'name').text = clip_name
                clip_rate = ET.SubElement(clip, 'rate')
                ET.SubElement(clip_rate, 'timebase').text = str(fps_i)
                ET.SubElement(clip_rate, 'ntsc').text = 'FALSE'
                ET.SubElement(clip, 'duration').text = str(dur)
                ET.SubElement(clip, 'start').text = str(start_f)
                ET.SubElement(clip, 'end').text = str(end_f)
                ET.SubElement(clip, 'in').text = '0'
                ET.SubElement(clip, 'out').text = str(dur)

                file_el = ET.SubElement(clip, 'file', {'id': file_id})
                ET.SubElement(file_el, 'name').text = clip_name
                ET.SubElement(file_el, 'pathurl').text = Path(os.path.abspath(p)).as_uri()
                file_rate = ET.SubElement(file_el, 'rate')
                ET.SubElement(file_rate, 'timebase').text = str(fps_i)
                ET.SubElement(file_rate, 'ntsc').text = 'FALSE'
                ET.SubElement(file_el, 'duration').text = str(dur)

                if with_video:
                    f_media = ET.SubElement(file_el, 'media')
                    f_vid = ET.SubElement(f_media, 'video')
                    f_sc = ET.SubElement(ET.SubElement(f_vid, 'samplecharacteristics'), 'width')
                    f_sc.text = str(int(width))
                    ET.SubElement(ET.SubElement(f_vid, 'samplecharacteristics'), 'height').text = str(int(height))
                else:
                    ET.SubElement(clip, 'sourcetrack')

            timeline_cursor = end_f

        os.makedirs(os.path.dirname(output_xml_path) or '.', exist_ok=True)
        tree = ET.ElementTree(xmeml)
        try:
            ET.indent(tree, space='  ')
        except Exception:
            pass
        tree.write(output_xml_path, encoding='utf-8', xml_declaration=True)
        return True
    except Exception:
        return False


def _render_silence_segment(
    output_path: str,
    duration_seconds: float,
    width: int,
    height: int,
    fast: bool,
    fps: int = 30,
    fade_duration: float = 0.25,
) -> bool:
    d = max(0.05, float(duration_seconds))
    fd = min(max(0.0, float(fade_duration or 0.0)), max(0.02, d / 2.0))
    vfilter = f"format=yuv420p"
    afilter = "anull"
    if fd > 0.0:
        vfilter = f"{vfilter},fade=t=in:st=0:d={fd:.6f},fade=t=out:st={max(0.0, d - fd):.6f}:d={fd:.6f}"
        afilter = f"{afilter},afade=t=in:st=0:d={fd:.6f},afade=t=out:st={max(0.0, d - fd):.6f}:d={fd:.6f}"

    if fast:
        vcodec = ['-c:v', 'h264_videotoolbox', '-b:v', '2500k', '-r', str(fps)]
    else:
        vcodec = ['-c:v', 'libx264', '-crf', '23', '-preset', 'medium']

    cmd = [
        'ffmpeg', '-y',
        '-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:r={fps}:d={d:.6f}',
        '-f', 'lavfi', '-i', f'anullsrc=r=48000:cl=stereo:d={d:.6f}',
        '-filter_complex', f"[0:v]{vfilter}[v];[1:a]{afilter}[a]",
        '-map', '[v]',
        '-map', '[a]',
        *vcodec,
        '-c:a', 'aac',
        '-b:a', '192k',
        '-ar', '48000',
        '-ac', '2',
        '-shortest',
        '-movflags', '+faststart',
        '-pix_fmt', 'yuv420p',
        output_path,
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _concat_video_segments(segment_paths: list[str], output_path: str, fast: bool) -> bool:
    if not segment_paths:
        return False

    list_file = tempfile.NamedTemporaryFile(suffix='.txt', delete=False)
    try:
        with open(list_file.name, 'w', encoding='utf-8') as f:
            for p in segment_paths:
                safe = str(p).replace("'", "'\\''")
                f.write(f"file '{safe}'\n")

        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', list_file.name,
        ]

        if fast:
            cmd += ['-c:v', 'copy']
        else:
            cmd += ['-c:v', 'copy']

        cmd += ['-movflags', '+faststart', output_path]

        subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        return True
    except subprocess.CalledProcessError:
        return False
    finally:
        try:
            os.unlink(list_file.name)
        except Exception:
            pass


def _add_mysterious_background_bed(
    input_video_path: str,
    output_video_path: str,
    bed_level: float = 0.55,
) -> bool:
    level = max(0.0, min(1.0, float(bed_level)))
    if level <= 0.0:
        return False

    cmd = [
        'ffmpeg', '-y',
        '-i', input_video_path,
        '-f', 'lavfi', '-i', 'anoisesrc=color=brown:amplitude=0.25:r=48000',
        '-f', 'lavfi', '-i', 'sine=frequency=55:sample_rate=48000',
        '-f', 'lavfi', '-i', 'sine=frequency=110:sample_rate=48000',
        '-filter_complex',
        (
            f"[1:a]lowpass=f=260,highpass=f=38,volume=1.0[n];"
            f"[2:a]lowpass=f=180,volume=0.28[s1];"
            f"[3:a]lowpass=f=260,volume=0.20[s2];"
            f"[n][s1][s2]amix=inputs=3:normalize=0,volume={level:.4f}[bed];"
            f"[0:a][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
        ),
        '-map', '0:v:0',
        '-map', '[a]',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-ar', '48000',
        '-ac', '2',
        '-shortest',
        '-movflags', '+faststart',
        output_video_path,
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _parse_srt_timestamp_to_seconds(value: str) -> Optional[float]:
    try:
        hours, minutes, seconds_millis = value.strip().split(':')
        seconds, millis = seconds_millis.split(',')
        return (
            int(hours) * 3600
            + int(minutes) * 60
            + int(seconds)
            + int(millis) / 1000.0
        )
    except Exception:
        return None


def _parse_srt_synced_lines(srt_content: str) -> list[SyncedLine]:
    blocks = re.split(r'\n\s*\n', (srt_content or '').strip())
    parsed_lines: list[SyncedLine] = []

    for block in blocks:
        rows = [row.strip() for row in block.splitlines() if row.strip()]
        if not rows:
            continue

        timing_idx = 1 if rows and rows[0].isdigit() and len(rows) > 1 else 0
        if timing_idx >= len(rows):
            continue

        match = re.match(
            r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})',
            rows[timing_idx],
        )
        if not match:
            continue

        start = _parse_srt_timestamp_to_seconds(match.group(1))
        end = _parse_srt_timestamp_to_seconds(match.group(2))
        if start is None or end is None:
            continue

        text = ' '.join(part.strip() for part in rows[timing_idx + 1:] if part.strip())
        if not text:
            continue

        parsed_lines.append(
            SyncedLine(
                start=max(0.0, start),
                end=max(start + 0.01, end),
                text=text,
            )
        )

    return parsed_lines


def _apply_offset_to_synced_lines(lines: list[SyncedLine], offset_seconds: float) -> list[SyncedLine]:
    shifted_lines: list[SyncedLine] = []
    for line in lines:
        start = max(0.0, float(line.start) + float(offset_seconds))
        end = max(start + 0.01, float(line.end) + float(offset_seconds))
        shifted_lines.append(SyncedLine(start=start, end=end, text=line.text))
    return shifted_lines


def _resolve_synced_lines(
    *,
    use_richsync: bool,
    use_lrc_ass: bool,
    richsync_json: Optional[str],
    lrc_content: Optional[str],
    srt_content: Optional[str],
    lyrics_offset: float,
) -> tuple[list[SyncedLine], str]:
    if use_richsync and richsync_json:
        lines = RichsyncParser.parse(richsync_json)
        source = 'Richsync'
    elif use_lrc_ass and lrc_content:
        lines = LRCParser.parse(lrc_content)
        source = 'LRC'
    elif srt_content:
        lines = _parse_srt_synced_lines(srt_content)
        source = 'SRT'
    else:
        return [], 'None'

    return _apply_offset_to_synced_lines(lines, lyrics_offset), source


def _format_terminal_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _find_spotify_focus_line(lines: list[SyncedLine], elapsed: float) -> tuple[int, bool]:
    if not lines:
        return 0, False

    for idx, line in enumerate(lines):
        if line.start <= elapsed < line.end:
            return idx, True
        if elapsed < line.start:
            return idx, False

    return len(lines) - 1, False


def _start_audio_playback(audio_path: Optional[str]) -> tuple[Optional[subprocess.Popen], Optional[str]]:
    if not audio_path:
        return None, None

    candidates: list[tuple[str, list[str]]] = []
    if shutil.which('afplay'):
        candidates.append(('afplay', ['afplay', audio_path]))
    if shutil.which('ffplay'):
        candidates.append(('ffplay', ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', audio_path]))

    for player_name, command in candidates:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return process, player_name
        except Exception:
            continue

    return None, None


def _truncate_wrapped_lines(lines: list[str], max_lines: int, max_width: int) -> list[str]:
    wrapped = list(lines[:max_lines])
    if len(lines) > max_lines and wrapped:
        tail = wrapped[-1].rstrip()
        if len(tail) >= max_width:
            tail = tail[:max_width - 1].rstrip()
        wrapped[-1] = f"{tail}…"
    return wrapped


def _render_spotify_lyrics_view(
    *,
    synced_lines: list[SyncedLine],
    track_name: str,
    artist_name: str,
    audio_path: Optional[str],
) -> None:
    if not synced_lines:
        click.echo("❌ No synced lyric lines available for terminal view", err=True)
        return

    reset = '\033[0m'
    bold_white = '\033[1;97m'
    soft_green = '\033[38;5;151m'
    muted_green = '\033[38;5;108m'
    dim_text = '\033[2;38;5;241m'

    player_process = None
    player_name = None
    started_at = None
    total_duration = max(line.end for line in synced_lines)

    try:
        player_process, player_name = _start_audio_playback(audio_path)
        started_at = time.monotonic()
        sys.stdout.write('\033[?1049h\033[?25l')
        sys.stdout.flush()

        while True:
            elapsed = max(0.0, time.monotonic() - started_at)
            terminal_size = shutil.get_terminal_size((100, 30))
            width = max(terminal_size.columns, 60)
            height = max(terminal_size.lines, 18)
            content_width = max(28, min(width - 8, 72))
            focus_idx, is_active = _find_spotify_focus_line(synced_lines, elapsed)

            context_before = max(2, min(5, (height - 8) // 5))
            context_after = max(3, min(7, (height - 8) // 4))
            start_idx = max(0, focus_idx - context_before)
            end_idx = min(len(synced_lines), focus_idx + context_after + 1)

            title = track_name or 'Unknown title'
            artist = artist_name or 'Unknown artist'
            progress = min(1.0, elapsed / total_duration) if total_duration > 0 else 0.0
            progress_width = max(12, min(content_width, 32))
            filled = int(round(progress * progress_width))
            progress_bar = '█' * filled + '░' * max(0, progress_width - filled)
            horizontal_pad = ' ' * max(2, (width - content_width) // 2)

            rows: list[str] = [
                '\033[2J\033[H',
                f"{horizontal_pad}{soft_green}♫  {title}{reset}",
                f"{horizontal_pad}{dim_text}{artist}{reset}",
                '',
            ]

            for idx in range(start_idx, end_idx):
                line = synced_lines[idx]
                wrapped = textwrap.wrap(
                    line.text,
                    width=content_width,
                    break_long_words=False,
                    break_on_hyphens=False,
                ) or ['']

                if idx == focus_idx:
                    wrapped = _truncate_wrapped_lines(wrapped, 3, content_width)
                    style = bold_white if is_active else soft_green
                    prefix = '› '
                elif abs(idx - focus_idx) <= 2:
                    wrapped = _truncate_wrapped_lines(wrapped, 2, content_width)
                    style = soft_green
                    prefix = '  '
                else:
                    wrapped = _truncate_wrapped_lines(wrapped, 2, content_width)
                    style = muted_green
                    prefix = '  '

                for line_idx, chunk in enumerate(wrapped):
                    visible_prefix = prefix if line_idx == 0 else '  '
                    rows.append(f"{horizontal_pad}{style}{visible_prefix}{chunk}{reset}")
                rows.append('')

            while len(rows) < max(0, height - 4):
                rows.append('')

            playback_label = player_name or 'timer'
            rows.extend([
                f"{horizontal_pad}{dim_text}{progress_bar}{reset}",
                f"{horizontal_pad}{dim_text}{_format_terminal_clock(elapsed)} / {_format_terminal_clock(total_duration)}    source: {playback_label}    Ctrl+C to quit{reset}",
            ])

            sys.stdout.write('\n'.join(rows[:height]))
            sys.stdout.flush()

            if player_process is not None:
                if player_process.poll() is not None and elapsed >= max(0.0, total_duration - 0.15):
                    break
            elif elapsed >= total_duration + 0.35:
                break

            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if player_process is not None and player_process.poll() is None:
            try:
                player_process.terminate()
            except Exception:
                pass
        sys.stdout.write('\033[0m\033[?25h\033[?1049l')
        sys.stdout.flush()


def parse_mmss(timecode) -> Optional[float]:
    """Parse GKDA Insta reel timecode to seconds.

    Accepts:
    - 'MM:SS' or 'M:SS' strings
    - numeric seconds (int/float or numeric string)
    - Airtable lookup lists containing one of the above

    Special cases:
    - None or empty string -> 0.0 seconds (default start)

    Returns None only on truly invalid non-empty input.
    """
    if timecode is None:
        return 0.0

    # Handle list values from Airtable lookups
    if isinstance(timecode, list) and timecode:
        timecode = timecode[0]

    # Numeric seconds
    try:
        if isinstance(timecode, (int, float)):
            return float(timecode)
        # Sometimes Airtable sends numeric-as-string
        if isinstance(timecode, str) and timecode.replace('.', '', 1).isdigit():
            return float(timecode)
    except Exception:
        pass

    # Fallback to MM:SS parsing
    if isinstance(timecode, str):
        timecode = timecode.strip()
        if not timecode:
            # Empty string: default to 0s
            return 0.0
        value = timecode
        try:
            parts = value.split(":")
            if len(parts) != 2:
                return None
            minutes = int(parts[0])
            seconds = int(float(parts[1]))
            return float(minutes * 60 + seconds)
        except Exception:
            return None

    return None


def _get_track_duration(fields: dict) -> Optional[float]:
    """Return track duration in seconds if available in the record."""
    for key in ("Duration (Spotify)", "Track length", "Duration"):
        value = fields.get(key)
        if isinstance(value, list) and value:
            value = value[0]
        if value is not None:
            try:
                return float(value)
            except Exception:
                pass
    return None


def _get_audio_duration(audio_path: str) -> Optional[float]:
    """Return audio file duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def _parse_srt_to_synced_lines(srt_content: str) -> List[SyncedLine]:
    """Parse SRT content into SyncedLine objects for chorus detection."""
    pattern = re.compile(
        r"\d+\s*\n"
        r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n"
        r"((?:.*\n)+?)(?=\n*\n\d+\s*\n|\Z)",
        re.MULTILINE,
    )
    lines: List[SyncedLine] = []
    for m in pattern.finditer(srt_content):
        start = _srt_timestamp_to_seconds(m.group(1))
        end = _srt_timestamp_to_seconds(m.group(2))
        text = m.group(3).strip()
        if text:
            lines.append(SyncedLine(start=start, end=end, text=text))
    return lines


def _srt_timestamp_to_seconds(ts: str) -> float:
    """Convert SRT timestamp string to seconds."""
    ts = ts.strip()
    if "," in ts:
        hms, ms = ts.split(",")
    else:
        hms, ms = ts, "0"
    parts = hms.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        hours, minutes, seconds = "0", parts[0] if len(parts) == 1 else parts[-2], parts[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds) + int(ms) / 1000.0


@click.group()
def cli():
    """Karaoke Generator CLI - Generate karaoke videos using Musixmatch synced lyrics."""
    pass


@cli.command()
@click.option('--record-id', required=True, help='Airtable record ID (comma-separated for multiple)')
@click.option('--output', type=str, default=None, help='Output path for the karaoke video')
@click.option('--overlay', type=click.Path(exists=True, dir_okay=False), multiple=True, help='Path(s) to overlay effect video(s) to use (randomly chosen if multiple). If omitted, picks from overlays directory.')
@click.option('--fast', is_flag=True, help='Use fast encoding preset')
@click.option('--use-youtube', is_flag=True, help='Download video from YouTube instead of using stored file')
@click.option('--sync-preview', is_flag=True, help='Generate a super fast sync preview (audio + lyrics only; no music video, overlays, logo, intro, or upload)')
@click.option('--sync-preview-low', is_flag=True, help='Use low definition settings for sync preview (faster + smaller)')
@click.option('--lyrics-view', type=click.Choice(['off', 'spotify'], case_sensitive=False), default='off', show_default=True, help='Render synced lyrics with an alternate video layout.')
@click.option('--progressive-fill/--no-progressive-fill', default=True, show_default=True, help='Use progressive karaoke fill (\\kf) instead of instant fill (\\k).')
@click.option('--overlay-hue-deg', type=float, default=None, help='Rotate overlay hue by N degrees (-180..180).')
@click.option('--overlay-saturation', type=float, default=None, help='Scale overlay saturation (1.0 = no change).')
@click.option('--overlay-hue-random', is_flag=True, help='Randomize overlay hue per render if --overlay-hue-deg not set.')
@click.option('--remote', is_flag=True, help='Render on the remote RunPod worker instead of locally (downloads the MP4 back; no Airtable upload).')
@click.option('--remote-endpoint', type=str, default=None, help='RunPod Load Balancer endpoint ID (default: RUNPOD_ENDPOINT_ID env).')
@click.option('--encoder', type=click.Choice(['auto', 'nvenc', 'x264', 'videotoolbox'], case_sensitive=False), default='auto', show_default=True, help='Video encoder (auto = nvenc on GPU worker / videotoolbox on macOS fast / libx264 otherwise).')
@click.option('--no-upload', is_flag=True, help='Skip the Airtable upload + WhatsApp notification at the end (local renders only).')
def generate(use_youtube: bool, fast: bool, overlay, output: Optional[str], record_id: str, sync_preview: bool, sync_preview_low: bool, lyrics_view: str, progressive_fill: bool, overlay_hue_deg: Optional[float], overlay_saturation: Optional[float], overlay_hue_random: bool, remote: bool, remote_endpoint: Optional[str], encoder: str, no_upload: bool):
    """Generate a karaoke video using Musixmatch synced lyrics."""
    lyrics_view = (lyrics_view or 'off').lower()
    record_ids = [rid.strip() for rid in record_id.split(',') if rid.strip()]
    if not record_ids:
        click.echo("❌ No record IDs provided", err=True)
        return

    if len(record_ids) > 1 and output:
        click.echo("⚠️  --output is ignored when processing multiple record IDs (auto-naming used instead)")
        output = None

    total = len(record_ids)
    failed = []
    for idx, current_record_id in enumerate(record_ids, 1):
        if total > 1:
            click.echo(f"\n{'='*60}")
            click.echo(f"📦 Processing record {idx}/{total}: {current_record_id}")
            click.echo(f"{'='*60}")
        _generate_single(
            current_record_id=current_record_id,
            use_youtube=use_youtube,
            fast=fast,
            overlay=overlay,
            output=output,
            sync_preview=sync_preview,
            sync_preview_low=sync_preview_low,
            lyrics_view=lyrics_view,
            progressive_fill=progressive_fill,
            overlay_hue_deg=overlay_hue_deg,
            overlay_saturation=overlay_saturation,
            overlay_hue_random=overlay_hue_random,
            remote=remote,
            remote_endpoint=remote_endpoint,
            encoder=encoder,
            no_upload=no_upload,
            failed=failed,
        )

    if total > 1:
        click.echo(f"\n{'='*60}")
        click.echo(f"🏁 Batch complete: {total - len(failed)}/{total} succeeded")
        if failed:
            click.echo(f"❌ Failed: {', '.join(failed)}")
        click.echo(f"{'='*60}")


def _generate_single(*, current_record_id: str, use_youtube: bool, fast: bool, overlay, output: Optional[str], sync_preview: bool, sync_preview_low: bool, lyrics_view: str, progressive_fill: bool, overlay_hue_deg: Optional[float], overlay_saturation: Optional[float], overlay_hue_random: bool, remote: bool = False, remote_endpoint: Optional[str] = None, encoder: str = 'auto', no_upload: bool = False, failed: list):
    """Process a single record ID for karaoke generation."""
    record_id = current_record_id
    ass_paths_to_cleanup = []
    try:
        click.echo(f"🎬 Generating karaoke for record: {record_id}")
        click.echo(f"🎵 Using Musixmatch for professionally synced lyrics\n")
        
        # Initialize clients
        airtable_client = AirtableClient()
        karaoke_generator = KaraokeGenerator()
        
        # Get record from Airtable
        record = airtable_client.get_record(record_id)
        fields = record.get("fields", {})

        primary_colour, secondary_colour, outline_colour = _pick_ass_palette(fields)
        
        # Get lyrics offset from Airtable
        lyrics_offset = fields.get('Lyrics to singing offset (s)', 0)
        if isinstance(lyrics_offset, list) and lyrics_offset:
            lyrics_offset = float(lyrics_offset[0])
        else:
            lyrics_offset = float(lyrics_offset) if lyrics_offset else 0.0
        
        if lyrics_offset != 0:
            click.echo(f"⏱️  Lyrics offset: {lyrics_offset:+.2f}s")
        
        # Check for Richsync JSON first (word-level timing)
        richsync_json = fields.get("Richsync JSON (Musixmatch)")
        use_lrc_ass = False
        srt_content = None
        lrc_content = None
        
        if richsync_json:
            click.echo("✓ Found Richsync JSON - generating word-level karaoke!")
            ass_content = ASSKaraokeGenerator.generate_from_richsync(
                richsync_json,
                offset_seconds=lyrics_offset,
                progressive_fill=progressive_fill,
                primary_colour=primary_colour,
                secondary_colour=secondary_colour,
                outline_colour=outline_colour,
            )
            
            if ass_content:
                use_richsync = True
                click.echo(f"✅ Generated ASS with word-level timing ({len(ass_content)} chars)")
            else:
                click.echo("⚠️  Failed to generate ASS, will use SRT instead")
                use_richsync = False
        else:
            use_richsync = False
        
        # If no Richsync, check for SRT (preferred) and then LRC
        if not use_richsync:
            srt_content = fields.get("SRT (Musixmatch)")
            if not srt_content:
                lrc_content = fields.get("LRC (Musixmatch)")
                if not lrc_content:
                    click.echo("❌ No Musixmatch lyrics found in Airtable", err=True)
                    click.echo("   Run the batch fetch script first!", err=True)
                    failed.append(record_id)
                    return

                click.echo("✓ Found Musixmatch LRC")
                ass_content = ASSKaraokeGenerator.generate_from_lrc(
                    lrc_content,
                    video_width=getattr(settings, 'karaoke_video_width', 1920),
                    video_height=getattr(settings, 'karaoke_video_height', 1080),
                    font_path=getattr(settings, 'karaoke_font_path', 'legacy/karaoke/fonts/SpaceMono-Regular.ttf'),
                    offset_seconds=lyrics_offset,
                    progressive_fill=progressive_fill,
                    primary_colour=primary_colour,
                    secondary_colour=secondary_colour,
                    outline_colour=outline_colour,
                )
                if not ass_content:
                    click.echo("❌ Failed to generate ASS from LRC", err=True)
                    failed.append(record_id)
                    return
                use_lrc_ass = True
                click.echo(f"✅ Generated ASS from LRC ({len(ass_content)} chars)")
            else:
                click.echo("✓ Found Musixmatch SRT")

        synced_lines, synced_lines_source = _resolve_synced_lines(
            use_richsync=use_richsync,
            use_lrc_ass=use_lrc_ass,
            richsync_json=richsync_json,
            lrc_content=lrc_content,
            srt_content=srt_content,
            lyrics_offset=lyrics_offset,
        )
        if not synced_lines:
            click.echo("❌ Failed to prepare synced lyric lines for preview", err=True)
            failed.append(record_id)
            return
        if lyrics_view != 'off':
            click.echo(f"✓ Prepared {len(synced_lines)} synced lyric lines from {synced_lines_source}")
            if use_richsync and richsync_json:
                ass_content = ASSKaraokeGenerator.generate_from_richsync_spotify(
                    richsync_json,
                    video_width=getattr(settings, 'karaoke_video_width', 1920),
                    video_height=getattr(settings, 'karaoke_video_height', 1080),
                    font_path=getattr(settings, 'karaoke_font_path', 'legacy/karaoke/fonts/SpaceMono-Regular.ttf'),
                    offset_seconds=lyrics_offset,
                    progressive_fill=progressive_fill,
                    primary_colour=primary_colour,
                    secondary_colour=secondary_colour,
                    outline_colour=outline_colour,
                )
            else:
                ass_content = ASSKaraokeGenerator.generate_from_synced_lines_spotify(
                    synced_lines,
                    video_width=getattr(settings, 'karaoke_video_width', 1920),
                    video_height=getattr(settings, 'karaoke_video_height', 1080),
                    font_path=getattr(settings, 'karaoke_font_path', 'legacy/karaoke/fonts/SpaceMono-Regular.ttf'),
                    offset_seconds=0.0,
                    progressive_fill=progressive_fill,
                    primary_colour=primary_colour,
                    secondary_colour=secondary_colour,
                    outline_colour=outline_colour,
                )
            if not ass_content:
                click.echo("❌ Failed to generate Spotify-style ASS", err=True)
                failed.append(record_id)
                return
            click.echo("✓ Generated Spotify-style stacked lyrics ASS")
            use_custom_ass = True
        else:
            use_custom_ass = bool(use_richsync or use_lrc_ass)

        # Remote render path: send the resolved inputs to the RunPod worker
        # and download the rendered MP4 back locally.
        if remote and not sync_preview:
            _generate_remote(
                record=record,
                record_id=record_id,
                fields=fields,
                ass_content=ass_content if use_custom_ass else None,
                srt_content=None if use_custom_ass else srt_content,
                airtable_client=airtable_client,
                fast=fast,
                encoder=encoder,
                endpoint_id=remote_endpoint,
                overlay=overlay,
                overlay_hue_deg=overlay_hue_deg,
                overlay_saturation=overlay_saturation,
                overlay_hue_random=overlay_hue_random,
                output=output,
                no_upload=no_upload,
                failed=failed,
            )
            return

        # Download video file (skip entirely for sync preview mode)
        video_file_path = None

        if not sync_preview:
            if use_youtube:
                # Download from YouTube for HD quality
                video_url = airtable_client.get_video_url(record)
                if not video_url:
                    click.echo("⚠️  No YouTube URL found in Airtable; will use fallback background")
                    video_file_path = None
                else:
                    click.echo(f"📥 Downloading video from YouTube (HD quality)...")
                    temp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    video_file_path = temp_video.name

                    if not karaoke_generator.download_youtube_video(video_url, video_file_path):
                        click.echo("⚠️  Failed to download from YouTube; will use fallback background")
                        video_file_path = None
                    else:
                        click.echo(f"✓ Video downloaded from YouTube")

            else:
                # Try stored video file first
                stored_video_url = airtable_client.get_video_file_url(record)

                if stored_video_url:
                    click.echo(f"💾 Downloading video file from Airtable...")
                    try:
                        temp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                        urllib.request.urlretrieve(stored_video_url, temp_video.name)
                        video_file_path = temp_video.name
                        click.echo(f"✓ Video downloaded")
                    except Exception as e:
                        click.echo(f"⚠️  Could not download video: {e}")
                        video_file_path = None

                # Fallback: if there's no stored video file, try Google Drive music video first
                if not video_file_path:
                    gdrive_video_url = airtable_client.get_gdrive_music_video_url(record)
                    if gdrive_video_url:
                        click.echo("📥 Downloading music video from Google Drive...")
                        temp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                        video_file_path = temp_video.name
                        try:
                            if "drive.google.com" in gdrive_video_url:
                                ok = download_google_drive_file(gdrive_video_url, temp_video.name, timeout_seconds=120)
                                if not ok:
                                    _download_to_file(gdrive_video_url, temp_video.name, timeout_seconds=120)
                            else:
                                _download_to_file(gdrive_video_url, temp_video.name, timeout_seconds=120)
                            click.echo("✓ Video downloaded from Google Drive")
                        except Exception as e:
                            click.echo(f"❌ Failed to download from Google Drive: {e}")
                            video_file_path = None

                # Fallback: try YouTube even without --use-youtube
                if not video_file_path:
                    video_url = airtable_client.get_video_url(record)
                    if not video_url:
                        click.echo("ℹ️  No video URL found in Airtable")
                    else:
                        click.echo("📥 Downloading video from YouTube (fallback)...")
                        temp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                        video_file_path = temp_video.name
                        if not karaoke_generator.download_youtube_video(video_url, video_file_path):
                            click.echo("⚠️  Failed to download from YouTube; will use fallback background")
                            video_file_path = None
                        else:
                            click.echo("✓ Video downloaded from YouTube")

            if not video_file_path:
                click.echo("ℹ️  No music video available; a vivid color background will be generated")
        
        # Download original audio from Google Drive
        stored_audio_url = airtable_client.get_audio_file_url(record)
        
        if not stored_audio_url:
            click.echo(f"❌ No audio file found in Google Drive", err=True)
            failed.append(record_id)
            return
        
        click.echo(f"🎵 Downloading audio from Google Drive...")
        try:
            temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            if "drive.google.com" in stored_audio_url:
                ok = download_google_drive_file(stored_audio_url, temp_audio.name)
                if not ok:
                    # Fallback: try plain download
                    try:
                        _download_to_file(stored_audio_url, temp_audio.name)
                        _save_google_drive_cache(stored_audio_url, temp_audio.name)
                        ok = True
                    except Exception:
                        ok = False
                if not ok or _looks_like_html(temp_audio.name):
                    click.echo("❌ Downloaded audio is not a valid MP3 (Google Drive returned HTML / permission page)", err=True)
                    failed.append(record_id)
                    return
            else:
                urllib.request.urlretrieve(stored_audio_url, temp_audio.name)
            audio_file_path = temp_audio.name
            click.echo(f"✓ Audio downloaded")
        except Exception as e:
            click.echo(f"❌ Could not download audio: {e}", err=True)
            failed.append(record_id)
            return

        # Sync preview mode: render a lightweight validation video (audio + lyrics only)
        if sync_preview:
            if not output:
                track_name = fields.get('Name', 'karaoke')
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_name = str(track_name).replace('/', '_').replace(' ', '_').replace("'", "")
                output = f"output/{safe_name}_{timestamp}_sync_preview.mp4"

            os.makedirs(os.path.dirname(output) or '.', exist_ok=True)

            ass_file = output.replace('.mp4', '.ass')
            ass_file = ass_file.replace("'", "").replace('"', '').replace(',', '')
            ass_paths_to_cleanup.append(ass_file)

            if use_custom_ass:
                with open(ass_file, 'w', encoding='utf-8') as f:
                    f.write(ass_content)
            else:
                if not karaoke_generator.srt_to_ass_karaoke(
                    srt_content,
                    ass_file,
                    progressive_fill=progressive_fill,
                    primary_colour=primary_colour,
                    secondary_colour=secondary_colour,
                    outline_colour=outline_colour,
                ):
                    click.echo("❌ Failed to convert SRT to ASS", err=True)
                    failed.append(record_id)
                    return

            bg_color = random.choice(VIVID_COLORS)
            click.echo("⚡ Rendering sync preview (audio + lyrics only)...")

            w = 640 if sync_preview_low else None
            h = 360 if sync_preview_low else None
            fps = 15 if sync_preview_low else None
            v_bitrate = '800k' if sync_preview_low else '2000k'
            a_bitrate = '128k' if sync_preview_low else '192k'

            ok, res = karaoke_generator.render_sync_preview(
                audio_path=audio_file_path,
                ass_path=ass_file,
                output_path=output,
                fast_mode=True,
                bg_color_hex=bg_color,
                width=w,
                height=h,
                fps=fps,
                video_bitrate=v_bitrate,
                audio_bitrate=a_bitrate,
            )
            if not ok:
                click.echo(f"❌ Failed to render sync preview: {res}", err=True)
                failed.append(record_id)
                return

            click.echo(f"\n✅ Sync preview generated successfully!")
            click.echo(f"📹 Output: {output}")
            file_size = os.path.getsize(output) / (1024 * 1024)
            click.echo(f"📊 File size: {file_size:.1f} MB")
            return
        
        # Generate output path
        if not output:
            track_name = fields.get('Name', 'karaoke')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Sanitize track name to avoid characters that confuse ffmpeg/libass
            safe_name = str(track_name).replace('/', '_').replace(' ', '_').replace("'", "")
            output = f"output/{safe_name}_{timestamp}_karaoke.mp4"
        
        os.makedirs(os.path.dirname(output) or '.', exist_ok=True)
        
        # Generate karaoke video with subtitles
        temp_output = output.replace('.mp4', '_temp.mp4')
        
        # Generate karaoke video using the proper pipeline
        click.echo(f"🎬 Generating karaoke video...")
        # Trim first render to audio duration to avoid overlong renders
        try:
            audio_duration_for_render = get_audio_duration(audio_file_path)
        except Exception:
            audio_duration_for_render = None
        
        # Choose a vivid color to use as global tint (also used for fallback background)
        bg_color = random.choice(VIVID_COLORS)
        
        # If no video source, create a vivid faded color background matching project size
        if not video_file_path:
            w = getattr(settings, 'karaoke_video_width', 1920)
            h = getattr(settings, 'karaoke_video_height', 1080)
            color = bg_color
            tmp_bg = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
            bg_cmd = [
                'ffmpeg', '-y',
                '-f', 'lavfi',
                '-i', f"color=c={color}:s={w}x{h}:r={'24' if fast else '30'}",
                '-t', str(audio_duration_for_render or 180),
                '-pix_fmt', 'yuv420p',
                tmp_bg.name
            ]
            try:
                subprocess.run(bg_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
                video_file_path = tmp_bg.name
                click.echo(f"✓ Created color background {color}")
            except subprocess.CalledProcessError as e:
                click.echo(f"❌ Failed to create background: {e.stderr}", err=True)
                failed.append(record_id)
                return
        
        # Decide overlay tint parameters
        ov_hue = overlay_hue_deg
        ov_sat = overlay_saturation
        if ov_hue is None and overlay_hue_random:
            ov_hue = random.uniform(-180.0, 180.0)
        if ov_hue is not None and ov_sat is None:
            # Boost saturation by default when randomizing hue to ensure visible change
            ov_sat = 1.6

        if use_custom_ass:
            # Save ASS content to temp file
            ass_file = temp_output.replace('.mp4', '.ass')
            with open(ass_file, 'w', encoding='utf-8') as f:
                f.write(ass_content)
            ass_paths_to_cleanup.append(ass_file)
            if lyrics_view == 'spotify':
                click.echo("✓ Using Spotify-style stacked lyrics")
            elif use_richsync:
                click.echo(f"✓ Using word-level karaoke from Richsync")
            else:
                click.echo(f"✓ Using line-level karaoke from LRC")

        # Resolve intro clip once — it is folded into the render pass itself
        # (single encode) instead of a second full re-encode afterwards.
        resolved_intro_path = Path(settings.karaoke_intro_path)
        if not resolved_intro_path.is_absolute():
            resolved_intro_path = Path(__file__).resolve().parents[1] / resolved_intro_path
        if not resolved_intro_path.exists():
            click.echo(f"⚠️  Intro file not found at: {resolved_intro_path} (skipping intro)")
            resolved_intro_path = None

        if use_custom_ass:
            # Use karaoke generator with ASS file
            success, result = karaoke_generator.generate_karaoke(
                video_path=video_file_path,
                ass_file=ass_file,
                output_path=temp_output,
                logo_path=karaoke_generator.logo_path,
                fast_mode=fast,
                max_duration_seconds=audio_duration_for_render,
                effect_overlay_paths=list(overlay) if overlay else None,
                overlay_hue_deg=ov_hue,
                overlay_saturation=ov_sat,
                overlay_tint_color=bg_color,
                intro_path=str(resolved_intro_path) if resolved_intro_path else None,
            )
        else:
            # Use SRT with karaoke generator
            success, result = karaoke_generator.generate_karaoke(
                video_path=video_file_path,
                srt_content=srt_content,
                output_path=temp_output,
                logo_path=karaoke_generator.logo_path,
                fast_mode=fast,
                max_duration_seconds=audio_duration_for_render,
                effect_overlay_paths=list(overlay) if overlay else None,
                overlay_hue_deg=ov_hue,
                overlay_saturation=ov_sat,
                overlay_tint_color=bg_color,
                intro_path=str(resolved_intro_path) if resolved_intro_path else None,
            )
        
        if not success:
            click.echo(f"❌ Failed to generate video: {result}", err=True)
            failed.append(record_id)
            return
        
        click.echo(f"✓ Video with subtitles and branding generated")
        
        # Replace audio with original audio from GDrive
        click.echo(f"🎵 Replacing audio with original track...")
        
        audio_duration = get_audio_duration(audio_file_path)
        click.echo(f"   Audio duration: {audio_duration}s")
        
        # Get audio to video offset from Airtable
        audio_to_video_offset = airtable_client.get_audio_to_video_offset(record)
        if audio_to_video_offset != 0:
            click.echo(f"⏱️  Applying audio-to-video offset: {audio_to_video_offset:+.2f}s")

        try:
            mux_replace_audio(
                temp_output, audio_file_path, output,
                offset_s=audio_to_video_offset, fast=fast, encoder=encoder,
            )
            os.unlink(temp_output)
            click.echo(f"✓ Audio replaced")
        except subprocess.CalledProcessError as e:
            click.echo(f"❌ Failed to replace audio: {e.stderr}", err=True)
            failed.append(record_id)
            return

        # Success!
        click.echo(f"\n✅ Karaoke video generated successfully!")
        click.echo(f"📹 Output: {output}")
        file_size = os.path.getsize(output) / (1024 * 1024)
        click.echo(f"📊 File size: {file_size:.1f} MB")
        
        upload_succeeded: Optional[bool] = None

        if no_upload:
            click.echo("⏭️  --no-upload: skipping Airtable upload and notification")
        else:
            # Upload video to Airtable
            click.echo(f"\n📤 Uploading video to Airtable...")
            try:
                airtable_client.upload_video_file(record_id, output)
                upload_succeeded = True
                click.echo(f"✓ Video uploaded to Airtable")
            except Exception as e:
                upload_succeeded = False
                click.echo(f"⚠️  Could not upload video: {e}")

            _maybe_send_karaoke_complete_whatsapp(
                record_id=record_id,
                fields=fields,
                output_path=output,
                file_size_mb=file_size,
                upload_succeeded=upload_succeeded,
            )

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        import traceback
        traceback.print_exc()
        failed.append(record_id)
    finally:
        for p in ass_paths_to_cleanup:
            _safe_unlink(p)


def _generate_remote(
    *,
    record: dict,
    record_id: str,
    fields: dict,
    ass_content: Optional[str],
    srt_content: Optional[str],
    airtable_client: AirtableClient,
    fast: bool,
    encoder: str,
    endpoint_id: Optional[str],
    overlay,
    overlay_hue_deg: Optional[float],
    overlay_saturation: Optional[float],
    overlay_hue_random: bool,
    output: Optional[str],
    no_upload: bool,
    failed: list,
) -> None:
    """Render the final karaoke video on the remote RunPod worker and download
    the MP4 back locally. No Airtable upload happens on the worker."""
    import json as _json

    # Compute kill switch — same contract as @platform/compute
    # (COMPUTE_REMOTE_ENABLED=false): refuse before any remote request.
    if os.environ.get("COMPUTE_REMOTE_ENABLED", "").strip().lower() in (
        "0", "off", "false", "no", "disabled",
    ):
        click.echo(
            "❌ Remote compute disabled (COMPUTE_REMOTE_ENABLED=false) — "
            "run without --remote for local generation",
            err=True,
        )
        failed.append(record_id)
        return

    endpoint = endpoint_id or os.environ.get("RUNPOD_ENDPOINT_ID")
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not endpoint:
        click.echo("❌ --remote requires --remote-endpoint or RUNPOD_ENDPOINT_ID", err=True)
        failed.append(record_id)
        return
    if not api_key:
        click.echo("❌ --remote requires RUNPOD_API_KEY", err=True)
        failed.append(record_id)
        return

    # Resolve asset URLs only — downloads happen on the worker.
    audio_url = airtable_client.get_audio_file_url(record)
    if not audio_url:
        click.echo("❌ No audio file found in Google Drive / Airtable", err=True)
        failed.append(record_id)
        return

    video_url = airtable_client.get_video_file_url(record)
    gdrive_video_url = airtable_client.get_gdrive_music_video_url(record)
    youtube_url = airtable_client.get_video_url(record)
    audio_to_video_offset = airtable_client.get_audio_to_video_offset(record)

    overlay_name = None
    if overlay:
        overlay_name = os.path.basename(list(overlay)[0])
        click.echo(f"✨ Overlay requested: {overlay_name} (resolved in the worker image)")

    ov_hue = overlay_hue_deg
    ov_sat = overlay_saturation
    if ov_hue is None and overlay_hue_random:
        ov_hue = random.uniform(-180.0, 180.0)
    if ov_hue is not None and ov_sat is None:
        ov_sat = 1.6

    bg_color = random.choice(VIVID_COLORS)
    track_name = fields.get('Name', 'karaoke')

    payload = {
        "record_id": record_id,
        "track_name": track_name,
        "ass_content": ass_content,
        "srt_content": srt_content,
        "audio_url": audio_url,
        "video_url": video_url,
        "gdrive_video_url": gdrive_video_url,
        "youtube_url": youtube_url,
        "audio_to_video_offset_s": audio_to_video_offset,
        "fast": fast,
        "encoder": encoder,
        "overlay_name": overlay_name,
        "overlay_hue_deg": ov_hue,
        "overlay_saturation": ov_sat,
        "bg_color": bg_color,
        "apply_intro": True,
        "video_width": getattr(settings, 'karaoke_video_width', 1920),
        "video_height": getattr(settings, 'karaoke_video_height', 1080),
        "return_mode": "file_token",
    }

    if not output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = str(track_name).replace('/', '_').replace(' ', '_').replace("'", "")
        output = f"output/{safe_name}_{timestamp}_karaoke.mp4"
    os.makedirs(os.path.dirname(output) or '.', exist_ok=True)

    base_url = f"https://{endpoint}.api.runpod.ai"
    auth_headers = {"Authorization": f"Bearer {api_key}"}
    click.echo(f"🚀 Remote render via RunPod endpoint {endpoint} (encoder={encoder})...")

    try:
        start = time.monotonic()
        r = requests.post(
            f"{base_url}/render",
            headers=auth_headers,
            json={"input": payload},
            timeout=(15, 120),
        )
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code != 200 or data.get("status") != "accepted":
            detail = data.get("error") or data.get("detail") or r.text[:400]
            click.echo(f"❌ Remote render rejected (HTTP {r.status_code}): {detail}", err=True)
            failed.append(record_id)
            return

        token = data["job_token"]
        status_url = f"{base_url}/render/{token}"
        click.echo("⏳ Render job accepted; polling worker status...")

        # The LB drops long-lived requests (~3min), so the render runs async
        # on the worker and we poll until it completes.
        deadline = time.monotonic() + 45 * 60
        job: dict = {}
        while time.monotonic() < deadline:
            time.sleep(10)
            try:
                sr = requests.get(status_url, headers=auth_headers, timeout=(15, 60))
                # 500 is a terminal "failed" payload, not a transient error —
                # only ignore statuses with no usable body.
                job = sr.json() if sr.headers.get('content-type', '').startswith('application/json') else {}
            except Exception:
                continue
            if job.get("status") and job["status"] != "running":
                break
            elapsed_so_far = int(time.monotonic() - start)
            click.echo(f"   … still rendering ({elapsed_so_far}s)")

        if not job or job.get("status") == "running":
            click.echo("❌ Remote render timed out waiting for the worker", err=True)
            failed.append(record_id)
            return
        if job.get("status") != "success":
            click.echo(
                f"❌ Remote render failed: {job.get('error')} ({job.get('error_type')})",
                err=True,
            )
            failed.append(record_id)
            return

        _print_remote_render_meta(job)

        dl_url = f"{base_url}{job['download_path']}"
        click.echo("⬇️  Downloading rendered file...")
        with requests.get(dl_url, headers=auth_headers, timeout=(15, 900), stream=True) as dr:
            dr.raise_for_status()
            with open(output, 'wb') as f:
                for chunk in dr.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        f.write(chunk)
        elapsed = time.monotonic() - start

        remote_sha = job.get("sha256")
        if remote_sha:
            local_sha = hashlib.sha256()
            with open(output, 'rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    local_sha.update(chunk)
            if local_sha.hexdigest() != remote_sha:
                click.echo("⚠️  SHA256 mismatch between worker output and downloaded file", err=True)
            else:
                click.echo("✓ SHA256 verified")

        file_size = os.path.getsize(output) / (1024 * 1024)
        click.echo(f"\n✅ Remote karaoke render complete in {elapsed:.1f}s")
        click.echo(f"📹 Output: {output}")
        click.echo(f"📊 File size: {file_size:.1f} MB")

        if not no_upload:
            _maybe_send_karaoke_complete_whatsapp(
                record_id=record_id,
                fields=fields,
                output_path=output,
                file_size_mb=file_size,
                upload_succeeded=None,
            )
    except Exception as e:
        click.echo(f"❌ Remote render error: {e}", err=True)
        failed.append(record_id)


def _print_remote_render_meta(data: dict) -> None:
    if data.get('timings'):
        click.echo(f"⏱️  Worker timings: {data['timings']}")
    if data.get('encoder_used'):
        click.echo(f"🎞️  Encoder used: {data['encoder_used']}")
    if data.get('sha256'):
        click.echo(f"🔐 Worker sha256: {data['sha256']}")


@cli.command()
def config():
    """Show current configuration."""
    click.echo("📋 Current Configuration:")
    click.echo(f"   Airtable Base ID: {settings.airtable_base_id}")
    click.echo(f"   Airtable Table: {settings.airtable_table_name}")
    click.echo(f"   Video Size: {settings.karaoke_video_width}x{settings.karaoke_video_height}")
    click.echo(f"   Output Directory: {settings.output_dir}")


class PennylaneClient:
    def __init__(
        self,
        *,
        access_token: str,
        base_url: str,
        timeout_seconds: int = 20,
    ):
        self._access_token = (access_token or '').strip()
        if not self._access_token:
            raise RuntimeError('Missing Pennylane access token')
        self._base_url = (base_url or '').strip().rstrip('/')
        if not self._base_url:
            raise RuntimeError('Missing Pennylane base URL')
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()

    def _request(self, method: str, path: str, *, params: Optional[dict] = None) -> dict:
        url = f"{self._base_url}{path}"
        last_error = None
        for _ in range(6):
            resp = self._session.request(
                method,
                url,
                headers={
                    'Authorization': f'Bearer {self._access_token}',
                    'Content-Type': 'application/json',
                },
                params=params,
                timeout=self._timeout_seconds,
            )

            if resp.status_code == 429:
                retry_after = resp.headers.get('Retry-After')
                try:
                    wait_s = int(retry_after) if retry_after else 1
                except Exception:
                    wait_s = 1
                time.sleep(max(1, wait_s))
                continue

            if resp.status_code >= 400:
                last_error = f"Pennylane API error {resp.status_code}: {resp.text[:500]}"
                break

            if not (resp.text or '').strip():
                return {}
            try:
                return resp.json()
            except Exception:
                return {}

        raise RuntimeError(last_error or f"Pennylane request failed for {url}")

    def _iter_paginated(self, path: str, *, limit: int = 100) -> list[dict]:
        out: list[dict] = []
        cursor: Optional[str] = None
        offset: int = 0
        limit = max(1, min(int(limit), 100))

        for _ in range(2000):
            params: dict[str, Any] = {'limit': limit}
            if cursor:
                params['cursor'] = cursor
            elif offset > 0:
                params['offset'] = offset

            data = self._request('GET', path, params=params)
            items = (
                data.get('items')
                or data.get('invoices')
                or data.get('suppliers')
                or data.get('payments')
                or []
            )
            if not isinstance(items, list):
                items = []

            out.extend([i for i in items if isinstance(i, dict)])

            next_cursor = data.get('next_cursor')
            has_more = data.get('has_more')
            if next_cursor:
                cursor = str(next_cursor)
                continue

            if has_more is True:
                offset += limit
                continue

            if len(items) < limit:
                break
            offset += limit

        return out

    def list_suppliers(self, *, limit: int = 200) -> list[dict]:
        return self._iter_paginated('/suppliers', limit=limit)

    def list_supplier_invoices(self, *, limit: int = 200) -> list[dict]:
        return self._iter_paginated('/supplier_invoices', limit=limit)

    def list_matched_transactions(self, *, supplier_invoice_id: str, limit: int = 200) -> list[dict]:
        sid = str(supplier_invoice_id)
        return self._iter_paginated(f"/supplier_invoices/{sid}/matched_transactions", limit=limit)


def _pennylane_parse_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _pennylane_normalize_name(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())


@cli.command('pennylane-payment-speed', help='Compute payment delay (invoice issue date -> matched transaction date) for a supplier')
@click.option('--supplier-name', default='Romain Diez', show_default=True, help='Pennylane supplier name')
@click.option('--token', 'access_token', default=None, help='Pennylane access token (overrides PENNYLANE_ACCESS_TOKEN)')
@click.option('--base-url', default=None, help='Pennylane API base URL (overrides PENNYLANE_BASE_URL)')
@click.option('--csv-output', type=click.Path(dir_okay=False), default=None, help='Write results as CSV to this path')
@click.option('--limit', type=int, default=100, show_default=True, help='Page size for Pennylane list endpoints')
@click.option('--max-invoices', type=int, default=0, show_default=True, help='Max number of invoices to analyze (0 = no limit)')
def pennylane_payment_speed(
    supplier_name: str,
    access_token: Optional[str],
    base_url: Optional[str],
    csv_output: Optional[str],
    limit: int,
    max_invoices: int,
):
    try:
        token = (
            (access_token or '').strip()
            or (getattr(settings, 'pennylane_access_token', None) or '').strip()
            or (os.getenv('PENNYLANE_ACCESS_TOKEN') or '').strip()
        )
        resolved_base_url = (
            (base_url or '').strip()
            or (getattr(settings, 'pennylane_base_url', None) or '').strip()
            or (os.getenv('PENNYLANE_BASE_URL') or '').strip()
            or 'https://app.pennylane.com/api/external/v2'
        )

        client = PennylaneClient(access_token=token, base_url=resolved_base_url)

        click.echo('🔎 Fetching suppliers...')
        suppliers = client.list_suppliers(limit=limit)
        target = None
        wanted = _pennylane_normalize_name(supplier_name)
        for s in suppliers:
            if _pennylane_normalize_name(s.get('name')) == wanted:
                target = s
                break

        if not target:
            click.echo(f"❌ Supplier not found: {supplier_name}", err=True)
            if suppliers:
                sample = ', '.join(sorted({str(s.get('name') or '').strip() for s in suppliers if str(s.get('name') or '').strip()})[:20])
                if sample:
                    click.echo(f"   Suppliers (sample): {sample}", err=True)
            return

        supplier_id = str(target.get('id') or '').strip()
        if not supplier_id:
            click.echo(f"❌ Supplier has no id: {target}", err=True)
            return

        click.echo('📄 Fetching supplier invoices (this can take a while)...')
        invoices = client.list_supplier_invoices(limit=limit)
        supplier_invoices = []
        for inv in invoices:
            supplier_obj = inv.get('supplier')
            supplier_obj_id = supplier_obj.get('id') if isinstance(supplier_obj, dict) else None
            inv_supplier_id = str(inv.get('supplier_id') or supplier_obj_id or '').strip()
            if inv_supplier_id == supplier_id:
                supplier_invoices.append(inv)

        if not supplier_invoices:
            click.echo(f"⚠️  No supplier invoices found for {supplier_name} ({supplier_id})")
            return

        if max_invoices and max_invoices > 0 and len(supplier_invoices) > max_invoices:
            supplier_invoices = supplier_invoices[:max_invoices]
            click.echo(f"ℹ️  Limiting analysis to first {len(supplier_invoices)} invoices (--max-invoices)")

        rows: list[dict] = []
        for idx, inv in enumerate(supplier_invoices, 1):
            inv_id = str(inv.get('id') or '').strip()
            if not inv_id:
                continue
            issue_dt = _pennylane_parse_date(inv.get('issue_date') or inv.get('date') or inv.get('issued_at'))
            if idx == 1 or idx % 10 == 0 or idx == len(supplier_invoices):
                click.echo(f"⏳ Fetching matched transactions: {idx}/{len(supplier_invoices)}")
            txs = client.list_matched_transactions(supplier_invoice_id=inv_id, limit=limit)
            paid_dt = None
            for t in txs:
                d = _pennylane_parse_date(t.get('date') or t.get('payment_date') or t.get('created_at'))
                if d and (paid_dt is None or d > paid_dt):
                    paid_dt = d

            delay_days: Optional[int] = None
            if issue_dt and paid_dt:
                delay_days = (paid_dt.date() - issue_dt.date()).days

            rows.append({
                'invoice_id': inv_id,
                'invoice_number': str(inv.get('invoice_number') or ''),
                'label': str(inv.get('label') or ''),
                'amount': inv.get('amount'),
                'issue_date': issue_dt.date().isoformat() if issue_dt else '',
                'paid_date': paid_dt.date().isoformat() if paid_dt else '',
                'delay_days': delay_days,
                'matched_transactions': len(txs),
            })

        rows.sort(key=lambda r: (r.get('paid_date') or '9999-12-31', r.get('issue_date') or ''))

        paid_delays = [r['delay_days'] for r in rows if isinstance(r.get('delay_days'), int)]
        click.echo(f"✅ Supplier: {supplier_name} ({supplier_id})")
        click.echo(f"📄 Invoices: {len(rows)}")
        click.echo(f"💸 Paid (matched): {len(paid_delays)}")
        if paid_delays:
            click.echo(f"⏱️  Mean delay (days): {statistics.mean(paid_delays):.1f}")
            click.echo(f"⏱️  Median delay (days): {statistics.median(paid_delays):.1f}")

        for r in rows:
            delay_txt = str(r['delay_days']) if r.get('delay_days') is not None else '-'
            click.echo(
                f"- {r.get('issue_date') or '?'} -> {r.get('paid_date') or 'UNPAID'}"
                f" | {delay_txt}d"
                f" | {r.get('invoice_number') or r.get('invoice_id')}"
                f" | {r.get('label') or ''}"
            )

        if csv_output:
            with open(csv_output, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=['invoice_id', 'invoice_number', 'label', 'amount', 'issue_date', 'paid_date', 'delay_days', 'matched_transactions'],
                )
                writer.writeheader()
                for r in rows:
                    writer.writerow(r)
            click.echo(f"\n📁 CSV written: {csv_output}")

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        import traceback
        traceback.print_exc()


@cli.command('spotify-playlist')
@click.option('--tracklist-id', required=True, help='Airtable Tracklist record ID (or Name/ID value)')
@click.option('--tracklists-table', default='Tracklists', show_default=True, help='Primary Airtable table for tracklists')
@click.option('--tracks-table', default='Tracks', show_default=True, help='Airtable tracks table name')
@click.option('--track-field', default='Tracks', show_default=True, help='Tracklist linked field containing track record IDs')
@click.option('--playlist-name', default=None, help='Override Spotify playlist name')
@click.option('--description', default=None, help='Override Spotify playlist description')
@click.option('--spotify-token', default=None, help='Spotify user access token (overrides SPOTIFY_USER_ACCESS_TOKEN)')
@click.option('--public/--private', 'public_playlist', default=False, show_default=True, help='Create public or private playlist')
@click.option('--search-missing/--no-search-missing', default=True, show_default=True, help='Search Spotify when a track has no Spotify ID/URL in Airtable')
@click.option('--market', default=None, help='Spotify market code for search fallback (e.g. FR)')
@click.option('--dry-run', is_flag=True, help='Resolve tracks only, without creating a Spotify playlist')
def spotify_playlist_from_tracklist(
    tracklist_id: str,
    tracklists_table: str,
    tracks_table: str,
    track_field: str,
    playlist_name: Optional[str],
    description: Optional[str],
    spotify_token: Optional[str],
    public_playlist: bool,
    search_missing: bool,
    market: Optional[str],
    dry_run: bool,
):
    """Create a Spotify playlist from an Airtable Tracklist record."""
    try:
        airtable_client = AirtableClient()

        env_access_token = (spotify_token or os.getenv('SPOTIFY_USER_ACCESS_TOKEN') or '').strip() or None
        env_refresh_token = (os.getenv('SPOTIFY_REFRESH_TOKEN') or '').strip() or None
        env_client_id = (settings.spotify_client_id or os.getenv('SPOTIFY_CLIENT_ID') or '').strip() or None
        env_client_secret = (settings.spotify_client_secret or os.getenv('SPOTIFY_CLIENT_SECRET') or '').strip() or None

        has_user_auth = bool(env_access_token) or bool(env_refresh_token and env_client_id and env_client_secret)
        if not has_user_auth and not dry_run:
            click.echo(
                "❌ Missing Spotify user auth. Spotify playlist creation requires SPOTIFY_USER_ACCESS_TOKEN "
                "or SPOTIFY_REFRESH_TOKEN + SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET.",
                err=True,
            )
            return
        if not has_user_auth and dry_run and search_missing:
            click.echo("ℹ️  No Spotify user auth configured; disabling search fallback in dry-run mode.")
            search_missing = False

        candidates = [tracklists_table, 'Tracklists', 'Playlists (from events)', 'Playlists']
        tracklist_record, resolved_table_name = _fetch_tracklist_record(
            airtable_client,
            tracklist_id,
            candidates,
        )
        if not tracklist_record:
            click.echo(
                f"❌ Tracklist introuvable: {tracklist_id} (tables testées: {', '.join([c for c in dict.fromkeys(candidates) if c])})",
                err=True,
            )
            return

        tracklist_fields = tracklist_record.get('fields', {}) or {}
        tracklist_name = (
            _spotify_first_text(tracklist_fields.get('Name'))
            or _spotify_first_text(tracklist_fields.get('Event type'))
            or tracklist_record.get('id')
            or 'Tracklist'
        )
        click.echo(f"✅ Tracklist trouvée: {tracklist_name} ({resolved_table_name})")

        track_ids = _spotify_extract_linked_ids(tracklist_fields.get(track_field))
        if not track_ids:
            guessed_ids, guessed_field = _guess_track_ids_from_tracklist_fields(tracklist_fields)
            if guessed_ids:
                track_ids = guessed_ids
                click.echo(f"ℹ️  Champ '{track_field}' vide, utilisation auto du champ '{guessed_field}'")

        if not track_ids:
            click.echo(
                f"❌ Aucun track lié trouvé dans '{track_field}'.",
                err=True,
            )
            return

        click.echo(f"🎵 {len(track_ids)} tracks linked found")
        tracks_table_api = airtable_client.get_table(tracks_table)

        spotify_client: Optional[SpotifyPlaylistClient] = None

        def _get_spotify_client() -> SpotifyPlaylistClient:
            nonlocal spotify_client
            if spotify_client is not None:
                return spotify_client

            spotify_client = SpotifyPlaylistClient(
                access_token=env_access_token,
                refresh_token=env_refresh_token,
                client_id=env_client_id,
                client_secret=env_client_secret,
            )
            return spotify_client

        resolved_uris: list[str] = []
        unresolved_tracks: list[str] = []

        for idx, track_id in enumerate(track_ids, 1):
            try:
                track_record = tracks_table_api.get(track_id)
            except Exception as e:
                unresolved_tracks.append(f"{track_id} (Airtable read error: {e})")
                continue

            track_fields = track_record.get('fields', {}) or {}
            title, artist = _spotify_extract_track_title_artist(track_fields)
            track_label = f"{artist} - {title}".strip(' -') or track_id

            uri = _spotify_extract_track_uri_from_fields(track_fields)
            if not uri and search_missing and title:
                try:
                    results = _get_spotify_client().search_track(
                        title=title,
                        artist=artist,
                        limit=5,
                        market=market,
                    )
                    picked = _spotify_pick_best_search_result(results, title, artist)
                    uri = _spotify_parse_track_uri((picked or {}).get('uri'))
                except Exception as e:
                    click.echo(f"⚠️  Spotify search failed for '{track_label}': {e}")

            if uri:
                resolved_uris.append(uri)
                click.echo(f"[{idx}/{len(track_ids)}] ✓ {track_label}")
            else:
                unresolved_tracks.append(track_label)
                click.echo(f"[{idx}/{len(track_ids)}] ✗ {track_label}")

        unique_uris = list(dict.fromkeys(resolved_uris))
        click.echo(f"\n📊 Match summary: {len(unique_uris)}/{len(track_ids)} tracks resolved")
        if unresolved_tracks:
            click.echo(f"   Unresolved: {len(unresolved_tracks)}")

        if not unique_uris:
            click.echo("❌ No Spotify tracks resolved, aborting.", err=True)
            return

        default_description = (
            f"Generated from Airtable {resolved_table_name or 'Tracklists'} "
            f"({tracklist_record.get('id', tracklist_id)}) on {datetime.now().strftime('%Y-%m-%d')}"
        )
        final_playlist_name = (playlist_name or tracklist_name or 'Tracklist').strip()
        final_description = (description or default_description).strip()

        if dry_run:
            click.echo("\n🧪 Dry run enabled, no playlist created.")
            click.echo(f"   Playlist name: {final_playlist_name}")
            click.echo(f"   Tracks to add: {len(unique_uris)}")
            return

        spotify_api = _get_spotify_client()
        click.echo(f"🧾 Creating playlist: {final_playlist_name}")

        playlist = spotify_api.create_playlist(
            name=final_playlist_name,
            description=final_description,
            public=public_playlist,
        )
        playlist_id = str(playlist.get('id') or '').strip()
        if not playlist_id:
            click.echo(f"❌ Failed to create playlist: {playlist}", err=True)
            return

        spotify_api.add_tracks(playlist_id=playlist_id, uris=unique_uris)
        playlist_url = ((playlist.get('external_urls') or {}).get('spotify'))

        click.echo(f"\n✅ Playlist created with {len(unique_uris)} tracks")
        click.echo(f"   Playlist ID: {playlist_id}")
        if playlist_url:
            click.echo(f"   URL: {playlist_url}")
        if unresolved_tracks:
            click.echo(f"   Unresolved tracks: {len(unresolved_tracks)}")

    except Exception as e:
        click.echo(f"❌ Error creating Spotify playlist: {e}", err=True)
        import traceback
        traceback.print_exc()


@cli.command('spotify-playlist-query')
@click.option('--table', 'tracks_table', default='Tracks', show_default=True, help='Airtable table to query')
@click.option('--event-type', 'event_types', multiple=True, help='Filter by Event type (repeatable)')
@click.option('--genre', 'genres', multiple=True, help='Filter by Genre (repeatable)')
@click.option('--decade', 'decades', multiple=True, help='Filter by Decade (repeatable)')
@click.option('--empty-field', 'empty_fields', multiple=True, help='Require this field to be empty (repeatable)')
@click.option('--non-empty-field', 'non_empty_fields', multiple=True, help='Require this field to be non-empty (repeatable)')
@click.option('--contains', 'contains_filters', multiple=True, help='Field contains text, format: "Field=value" (repeatable)')
@click.option('--formula', 'raw_formulas', multiple=True, help='Raw Airtable formula fragment to AND-combine (repeatable)')
@click.option('--playlist-name', default=None, help='Override Spotify playlist name')
@click.option('--description', default=None, help='Override Spotify playlist description')
@click.option('--spotify-token', default=None, help='Spotify user access token (overrides env)')
@click.option('--public/--private', 'public_playlist', default=False, show_default=True, help='Create public or private playlist')
@click.option('--search-missing/--no-search-missing', default=True, show_default=True, help='Search Spotify when a track has no Spotify ID/URL')
@click.option('--market', default=None, help='Spotify market code for search fallback (e.g. FR)')
@click.option('--dry-run', is_flag=True, help='Resolve tracks only, do not create playlist')
@click.option('--sort-field', default=None, help='Airtable field used to sort matched tracks')
@click.option('--sort-direction', type=click.Choice(['asc', 'desc'], case_sensitive=False), default='asc', show_default=True, help='Sort direction for --sort-field')
@click.option('--max-records', type=int, default=None, help='Max number of Airtable records to fetch')
def spotify_playlist_from_query(
    tracks_table: str,
    event_types: tuple,
    genres: tuple,
    decades: tuple,
    empty_fields: tuple,
    non_empty_fields: tuple,
    contains_filters: tuple,
    raw_formulas: tuple,
    playlist_name: Optional[str],
    description: Optional[str],
    spotify_token: Optional[str],
    public_playlist: bool,
    search_missing: bool,
    market: Optional[str],
    dry_run: bool,
    sort_field: Optional[str],
    sort_direction: str,
    max_records: Optional[int],
):
    """Create a Spotify playlist from Airtable tracks matching flexible filter criteria.

    \b
    Examples:
      # Tracks for "Le Grand Karaoké de l'Amour" with no GDrive audio
      python -m src.cli spotify-playlist-query \\
        --event-type "Le Grand Karaoké de l'Amour" \\
        --empty-field "GDrive Audio files" \\
        --dry-run

      # Combine genre + decade
      python -m src.cli spotify-playlist-query \\
        --genre "Pop" --decade "2000s" \\
        --playlist-name "Pop 2000s"
    """
    try:
        # ----- build Airtable formula -----
        formulas: list[str] = []

        if event_types:
            conds = [
                f"FIND('{_spotify_formula_escape(et)}', ARRAYJOIN({{Event type}}))"
                for et in event_types
            ]
            formulas.append(f"OR({', '.join(conds)})" if len(conds) > 1 else conds[0])

        if genres:
            conds = [
                f"FIND('{_spotify_formula_escape(g)}', {{Genre}})"
                for g in genres
            ]
            formulas.append(f"OR({', '.join(conds)})" if len(conds) > 1 else conds[0])

        if decades:
            conds = [
                f"FIND('{_spotify_formula_escape(d)}', ARRAYJOIN({{Decade}}))"
                for d in decades
            ]
            formulas.append(f"OR({', '.join(conds)})" if len(conds) > 1 else conds[0])

        for field_name in empty_fields:
            formulas.append(f"OR({{{field_name}}} = '', {{{field_name}}} = BLANK())")

        for field_name in non_empty_fields:
            formulas.append(f"AND({{{field_name}}} != '', {{{field_name}}} != BLANK())")

        for cf in contains_filters:
            if '=' not in cf:
                click.echo(f"⚠️  Invalid --contains format (expected 'Field=value'): {cf}", err=True)
                continue
            field_name, _, value = cf.partition('=')
            formulas.append(f"FIND('{_spotify_formula_escape(value)}', {{{field_name}}})")

        for raw in raw_formulas:
            formulas.append(raw)

        if not formulas:
            click.echo("❌ No filter criteria provided. Use --event-type, --genre, --decade, --empty-field, --non-empty-field, or --formula.", err=True)
            return

        if len(formulas) == 1:
            formula = formulas[0]
        else:
            formula = f"AND({', '.join(formulas)})"

        click.echo(f"🔎 Airtable formula: {formula}")

        # ----- fetch tracks -----
        airtable_client = AirtableClient()
        table_api = airtable_client.get_table(tracks_table)
        kwargs: dict[str, Any] = {'formula': formula}
        sort_field_name = (sort_field or '').strip()
        sort_direction_value = (sort_direction or 'asc').strip().lower()
        if sort_field_name:
            kwargs['sort'] = [f"-{sort_field_name}"] if sort_direction_value == 'desc' else [sort_field_name]
            click.echo(f"↕️  Airtable sort: {sort_field_name} ({sort_direction_value})")
        if max_records:
            kwargs['max_records'] = max_records
        records = table_api.all(**kwargs)

        if not records:
            click.echo("⚠️  No tracks matched the filter criteria.")
            return

        click.echo(f"🎵 {len(records)} tracks matched")

        # ----- Spotify auth -----
        env_access_token = (spotify_token or os.getenv('SPOTIFY_USER_ACCESS_TOKEN') or '').strip() or None
        env_refresh_token = (os.getenv('SPOTIFY_REFRESH_TOKEN') or '').strip() or None
        env_client_id = (settings.spotify_client_id or os.getenv('SPOTIFY_CLIENT_ID') or '').strip() or None
        env_client_secret = (settings.spotify_client_secret or os.getenv('SPOTIFY_CLIENT_SECRET') or '').strip() or None

        has_user_auth = bool(env_access_token) or bool(env_refresh_token and env_client_id and env_client_secret)
        if not has_user_auth and not dry_run:
            click.echo(
                "❌ Missing Spotify user auth. Set SPOTIFY_USER_ACCESS_TOKEN "
                "or SPOTIFY_REFRESH_TOKEN + SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET.",
                err=True,
            )
            return
        if not has_user_auth and dry_run and search_missing:
            click.echo("ℹ️  No Spotify auth; disabling search fallback in dry-run mode.")
            search_missing = False

        spotify_client: Optional[SpotifyPlaylistClient] = None

        def _get_spotify():
            nonlocal spotify_client
            if spotify_client is not None:
                return spotify_client
            spotify_client = SpotifyPlaylistClient(
                access_token=env_access_token,
                refresh_token=env_refresh_token,
                client_id=env_client_id,
                client_secret=env_client_secret,
            )
            return spotify_client

        # ----- resolve Spotify URIs -----
        resolved_uris: list[str] = []
        unresolved_tracks: list[str] = []

        for idx, record in enumerate(records, 1):
            track_fields = record.get('fields', {}) or {}
            title, artist = _spotify_extract_track_title_artist(track_fields)
            track_label = f"{artist} - {title}".strip(' -') or record.get('id', '?')

            uri = _spotify_extract_track_uri_from_fields(track_fields)
            if not uri and search_missing and title:
                try:
                    results = _get_spotify().search_track(
                        title=title, artist=artist, limit=5, market=market,
                    )
                    picked = _spotify_pick_best_search_result(results, title, artist)
                    uri = _spotify_parse_track_uri((picked or {}).get('uri'))
                except Exception as e:
                    click.echo(f"⚠️  Spotify search failed for '{track_label}': {e}")

            if uri:
                resolved_uris.append(uri)
                click.echo(f"[{idx}/{len(records)}] ✓ {track_label}")
            else:
                unresolved_tracks.append(track_label)
                click.echo(f"[{idx}/{len(records)}] ✗ {track_label}")

        unique_uris = list(dict.fromkeys(resolved_uris))
        click.echo(f"\n📊 Match summary: {len(unique_uris)}/{len(records)} tracks resolved")
        if unresolved_tracks:
            click.echo(f"   Unresolved: {len(unresolved_tracks)}")
            for t in unresolved_tracks:
                click.echo(f"     - {t}")

        if not unique_uris:
            click.echo("❌ No Spotify tracks resolved, aborting.", err=True)
            return

        # ----- build playlist name -----
        default_name_parts = []
        if event_types:
            default_name_parts.append(' / '.join(event_types))
        if genres:
            default_name_parts.append(' / '.join(genres))
        if decades:
            default_name_parts.append(' / '.join(decades))
        if empty_fields:
            default_name_parts.append('no ' + ', '.join(empty_fields))
        default_name = ' — '.join(default_name_parts) if default_name_parts else 'Airtable Query'

        default_description = (
            f"Generated from Airtable {tracks_table} query on {datetime.now().strftime('%Y-%m-%d')}"
        )
        final_playlist_name = (playlist_name or default_name).strip()
        final_description = (description or default_description).strip()

        if dry_run:
            click.echo(f"\n🧪 Dry run — no playlist created.")
            click.echo(f"   Playlist name: {final_playlist_name}")
            click.echo(f"   Tracks to add: {len(unique_uris)}")
            return

        spotify_api = _get_spotify()
        click.echo(f"🧾 Creating playlist: {final_playlist_name}")

        playlist = spotify_api.create_playlist(
            name=final_playlist_name,
            description=final_description,
            public=public_playlist,
        )
        playlist_id = str(playlist.get('id') or '').strip()
        if not playlist_id:
            click.echo(f"❌ Failed to create playlist: {playlist}", err=True)
            return

        spotify_api.add_tracks(playlist_id=playlist_id, uris=unique_uris)
        playlist_url = ((playlist.get('external_urls') or {}).get('spotify'))

        click.echo(f"\n✅ Playlist created with {len(unique_uris)} tracks")
        click.echo(f"   Playlist ID: {playlist_id}")
        if playlist_url:
            click.echo(f"   URL: {playlist_url}")
        if unresolved_tracks:
            click.echo(f"   Unresolved tracks: {len(unresolved_tracks)}")

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        import traceback
        traceback.print_exc()


@cli.command()
@click.option('--record-id', required=True, help='Airtable record ID')
@click.option('--output', type=str, default=None, help='Output path for the Instagram reel video')
@click.option('--duration', type=float, default=30.0, show_default=True, help='Duration of the reel in seconds')
@click.option('--background', type=str, default=None, help='Path or URL to a background image (default: gkda-instagram-reel-template.jpg)')
@click.option('--debug-box', is_flag=True, help='Overlay a debug rectangle showing the subtitle region')
@click.option('--fast', is_flag=True, help='Use fast encoding preset')
@click.option('--progressive-fill/--no-progressive-fill', default=True, show_default=True, help='Use progressive karaoke fill (\\kf) instead of instant fill (\\k).')
@click.option('--lyrics-source', type=click.Choice(['auto', 'richsync', 'srt', 'lrc']), default='auto', show_default=True, help='Force the Musixmatch lyrics source (auto = prefer Richsync, then SRT, then LRC)')
def reel(fast: bool, debug_box: bool, background: Optional[str], duration: float, output: Optional[str], record_id: str, progressive_fill: bool, lyrics_source: str):
    """Generate an Instagram Reel using the static GKDA template and Musixmatch lyrics."""
    ass_paths_to_cleanup = []
    try:
        click.echo(f"🎬 Generating Instagram Reel for record: {record_id}")

        airtable_client = AirtableClient()
        karaoke_generator = KaraokeGenerator(video_width=1080, video_height=1920)

        record = airtable_client.get_record(record_id)
        fields = record.get("fields", {})

        primary_colour, secondary_colour, outline_colour = _pick_ass_palette(fields)

        # Determine initial reel start time: use GKDA field, otherwise detect chorus.
        reel_timecode = fields.get("GKDA - Insta reel timecode")
        click.echo(f"ℹ️  Raw 'GKDA - Insta reel timecode' value: {reel_timecode!r}")
        start_seconds: Optional[float] = None
        if reel_timecode:
            start_seconds = parse_mmss(reel_timecode)

        if duration <= 0:
            click.echo("❌ Duration must be > 0 seconds", err=True)
            return

        # We need a track duration to detect/plan the chorus window. Try the record
        # first, then fall back to ffprobing the downloaded audio.
        track_duration = _get_track_duration(fields)

        # Prefer Richsync JSON, fallback to SRT then LRC (Musixmatch)
        richsync_json = fields.get("Richsync JSON (Musixmatch)")
        srt_content = fields.get("SRT (Musixmatch)")
        lrc_content = fields.get("LRC (Musixmatch)")
        if lyrics_source == 'richsync':
            srt_content = None
            lrc_content = None
        elif lyrics_source == 'srt':
            richsync_json = None
            lrc_content = None
        elif lyrics_source == 'lrc':
            richsync_json = None
            srt_content = None

        if start_seconds is None:
            click.echo("🎵 No GKDA timecode, attempting chorus detection...")
            if richsync_json:
                start_seconds = detect_chorus_start_from_richsync(
                    richsync_json,
                    audio_duration=track_duration,
                    excerpt_duration=duration,
                )
                click.echo(f"⏱️  Chorus detected at {start_seconds:.2f}s")
            elif lrc_content:
                synced_lines = LRCParser.parse(lrc_content)
                start_seconds = detect_chorus_start(
                    synced_lines,
                    audio_duration=track_duration,
                    excerpt_duration=duration,
                )
                click.echo(f"⏱️  Chorus detected at {start_seconds:.2f}s")
            elif srt_content:
                # SRT fallback: parse SRT blocks and detect.
                synced_lines = _parse_srt_to_synced_lines(srt_content)
                start_seconds = detect_chorus_start(
                    synced_lines,
                    audio_duration=track_duration,
                    excerpt_duration=duration,
                )
                click.echo(f"⏱️  Chorus detected at {start_seconds:.2f}s")
            else:
                click.echo("❌ No Musixmatch lyrics found for chorus detection", err=True)
                return

        # Reel window starts at chosen timecode / chorus and runs for the target duration.
        window_start = start_seconds
        window_end = start_seconds + duration

        click.echo(f"⏱️  Reel window: start={window_start:.2f}s, duration={duration:.1f}s")

        # Lyrics offset (same logic as generate)
        lyrics_offset = fields.get('Lyrics to singing offset (s)', 0)
        if isinstance(lyrics_offset, list) and lyrics_offset:
            lyrics_offset = float(lyrics_offset[0])
        else:
            lyrics_offset = float(lyrics_offset) if lyrics_offset else 0.0

        if lyrics_offset != 0:
            click.echo(f"⏱️  Lyrics offset: {lyrics_offset:+.2f}s")

        ass_content: Optional[str] = None

        if richsync_json:
            click.echo("✓ Found Richsync JSON - generating word-level karaoke for reel!")
            import json

            try:
                data = json.loads(richsync_json)
            except Exception as e:
                click.echo(f"⚠️  Failed to parse Richsync JSON, falling back to SRT: {e}")
                data = None

            if isinstance(data, list):
                trimmed_entries = []

                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    ts = float(entry.get('ts', 0))
                    te = float(entry.get('te', ts + 3))
                    # Keep entries that overlap the reel window
                    if te <= window_start or ts >= window_end:
                        continue
                    # Shift the whole window to t=0 WITHOUT clamping ts/te:
                    # word offsets inside `l` stay relative to the line's ts,
                    # so clamping ts would desync the boundary line. Negative
                    # start times are fine — the line is simply already in
                    # progress when the clip begins.
                    shifted = dict(entry)
                    shifted['ts'] = ts - window_start
                    shifted['te'] = te - window_start
                    trimmed_entries.append(shifted)

                if not trimmed_entries:
                    click.echo("⚠️  No Richsync entries in the requested reel window, falling back to SRT")
                else:
                    trimmed_json = json.dumps(trimmed_entries)
                    # Use reel-specific ASS generator (fixed region, simple fade-in)
                    ass_content = ASSKaraokeGenerator.generate_from_richsync_reel(
                        trimmed_json,
                        video_width=1080,
                        video_height=1920,
                        font_path=karaoke_generator.font_path,
                        offset_seconds=lyrics_offset,
                        progressive_fill=progressive_fill,
                        primary_colour=primary_colour,
                        secondary_colour=secondary_colour,
                        outline_colour=outline_colour,
                    )

        if not ass_content:
            # Fallback: use SRT (Musixmatch) or LRC (Musixmatch)
            if srt_content:
                click.echo("✓ Using Musixmatch SRT for reel")
            else:
                if not lrc_content:
                    click.echo("❌ No Musixmatch lyrics (Richsync, SRT, or LRC) found in Airtable", err=True)
                    return

                click.echo("✓ Using Musixmatch LRC for reel")
                synced_lines = LRCParser.parse(lrc_content)
                trimmed_lines: List[SyncedLine] = []
                for ln in synced_lines:
                    start_rel = float(ln.start) - window_start
                    end_rel = float(ln.end) - window_start
                    if end_rel <= 0 or start_rel >= duration:
                        continue
                    if end_rel <= start_rel:
                        continue
                    trimmed_lines.append(SyncedLine(start=start_rel, end=end_rel, text=ln.text))

                if not trimmed_lines:
                    click.echo("❌ No LRC lines overlap the requested reel window", err=True)
                    return

                ass_content = ASSKaraokeGenerator.generate_from_synced_lines_reel(
                    trimmed_lines,
                    video_width=1080,
                    video_height=1920,
                    font_path=karaoke_generator.font_path,
                    offset_seconds=lyrics_offset,
                    progressive_fill=progressive_fill,
                    primary_colour=primary_colour,
                    secondary_colour=secondary_colour,
                    outline_colour=outline_colour,
                )
                if not ass_content:
                    click.echo("❌ Failed to generate ASS from LRC for reel", err=True)
                    return

            # If LRC already produced ASS, write it directly and skip the SRT pipeline.
            if not srt_content:
                with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as temp_ass:
                    ass_path = temp_ass.name
                    temp_ass.write(ass_content.encode('utf-8'))
                ass_paths_to_cleanup.append(ass_path)
            else:
                # Apply lyrics offset
                if lyrics_offset != 0:
                    from src.karaoke_generator import KaraokeGenerator as KG
                    srt_content = KG.adjust_srt_timing(srt_content, lyrics_offset)

                # Shift window so reel starts at 0 and trim to target duration
                from src.karaoke_generator import KaraokeGenerator as KG
                srt_content = KG.adjust_srt_timing(srt_content, -start_seconds)

                # Manually trim SRT lines outside [0, duration]
                import re
                lines = srt_content.strip().split('\n')
                new_blocks = []
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if not line.isdigit():
                        i += 1
                        continue
                    idx = line
                    if i + 1 >= len(lines):
                        break
                    time_line = lines[i + 1].strip()
                    if ' --> ' not in time_line:
                        i += 1
                        continue
                    start_str, end_str = time_line.split(' --> ')
                    start_sec = KaraokeGenerator.parse_srt_timestamp(start_str)
                    end_sec = KaraokeGenerator.parse_srt_timestamp(end_str)
                    i += 2
                    text_lines = []
                    while i < len(lines) and lines[i].strip():
                        text_lines.append(lines[i])
                        i += 1
                    i += 1  # skip blank

                    if end_sec <= 0 or start_sec >= duration:
                        continue

                    new_blocks.append((start_sec, end_sec, text_lines))

                # Rebuild SRT with normalized indices
                out_lines = []
                for idx, (start_sec, end_sec, text_lines) in enumerate(new_blocks, start=1):
                    start_ts = KaraokeGenerator._seconds_to_srt_timestamp(start_sec)
                    end_ts = KaraokeGenerator._seconds_to_srt_timestamp(end_sec)
                    out_lines.append(str(idx))
                    out_lines.append(f"{start_ts} --> {end_ts}")
                    out_lines.extend(text_lines)
                    out_lines.append("")

                trimmed_srt = "\n".join(out_lines)

                # Convert trimmed SRT to ASS using portrait resolution
                with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as temp_ass:
                    ass_path = temp_ass.name
                ass_paths_to_cleanup.append(ass_path)
                # Use a dedicated KaraokeGenerator with portrait dimensions
                reel_generator = KaraokeGenerator(video_width=1080, video_height=1920)
                if not reel_generator.srt_to_ass_karaoke(
                    trimmed_srt,
                    ass_path,
                    progressive_fill=progressive_fill,
                    primary_colour=primary_colour,
                    secondary_colour=secondary_colour,
                    outline_colour=outline_colour,
                ):
                    click.echo("❌ Failed to convert SRT to ASS for reel", err=True)
                    return
        else:
            # Save ASS content from Richsync to a temp file
            with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as temp_ass:
                ass_path = temp_ass.name
                temp_ass.write(ass_content.encode('utf-8'))
            ass_paths_to_cleanup.append(ass_path)

        # Download original audio from Google Drive
        stored_audio_url = airtable_client.get_audio_file_url(record)
        if not stored_audio_url:
            click.echo("❌ No audio file found in Google Drive", err=True)
            return

        click.echo("🎵 Downloading audio from Google Drive...")
        try:
            temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            if "drive.google.com" in stored_audio_url:
                ok = download_google_drive_file(stored_audio_url, temp_audio.name)
                if not ok:
                    _download_to_file(stored_audio_url, temp_audio.name)
                    _save_google_drive_cache(stored_audio_url, temp_audio.name)
            else:
                _download_to_file(stored_audio_url, temp_audio.name)
            audio_file_path = temp_audio.name
            click.echo("✓ Audio downloaded")
        except Exception as e:
            click.echo(f"❌ Could not download audio: {e}", err=True)
            return

        # Use ffprobe to get the real audio duration and clamp the start time.
        audio_duration = _get_audio_duration(audio_file_path) or track_duration
        if audio_duration:
            max_start = max(0.0, audio_duration - duration)
            if start_seconds > max_start:
                click.echo(f"⚠️  Chorus start {start_seconds:.2f}s is too late, clamping to {max_start:.2f}s")
                start_seconds = max_start
                window_start = start_seconds
                window_end = start_seconds + duration

        # Trim audio to the reel window
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_clip:
            audio_clip_path = temp_clip.name

        cmd_trim = [
            'ffmpeg', '-y',
            '-ss', str(start_seconds),
            '-t', str(duration),
            '-i', audio_file_path,
            '-c:a', 'copy',
            audio_clip_path,
        ]
        try:
            subprocess.run(cmd_trim, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
            click.echo("✓ Audio trimmed for reel window")
        except subprocess.CalledProcessError as e:
            click.echo(f"❌ Failed to trim audio: {e.stderr}", err=True)
            return

        # Generate output path
        if not output:
            track_name = fields.get('Name', 'karaoke')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = str(track_name).replace('/', '_').replace(' ', '_')
            output = f"output/{safe_name}_{timestamp}_reel.mp4"

        os.makedirs(os.path.dirname(output) or '.', exist_ok=True)

        # Determine background image
        if background:
            # Accept URL or local path. If URL, download to a temp file.
            if background.startswith("http://") or background.startswith("https://"):
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_bg:
                    _download_to_file(background, temp_bg.name)
                    template_path = Path(temp_bg.name)
            else:
                template_path = Path(background)
                if not template_path.exists():
                    click.echo(f"❌ Background image not found at {template_path}", err=True)
                    return
        else:
            project_root = Path(__file__).resolve().parents[1]
            template_path = project_root / 'gkda-instagram-reel-template.jpg'
            if not template_path.exists():
                click.echo(f"❌ Template image not found at {template_path}", err=True)
                return

        # Escape ASS path for subtitles filter
        ass_path_escaped = (ass_path
            .replace('\\', '\\\\')
            .replace(':', '\\:')
            .replace("'", "\\'")
            .replace(',', '\\,')
            .replace('[', '\\[')
            .replace(']', '\\]')
        )

        # Build video filter chain: scale -> setsar -> optional debug box -> subtitles
        vf_filters = [
            "scale=1080:1920",
            "setsar=1",
        ]
        if debug_box:
            # Draw a semi-transparent rectangle where subtitles should appear
            vf_filters.append("drawbox=x=70:y=560:w=946:h=220:color=white@0.1:t=2")
        vf_filters.append(f"subtitles={ass_path_escaped}")
        vf_chain = ",".join(vf_filters)

        # Encoding preset / quality
        if fast:
            preset = 'ultrafast'
            crf = '28'
            audio_bitrate = '192k'
            click.echo("⚡ Using FAST mode (ultrafast preset, lower quality)")
        else:
            preset = 'medium'
            crf = '23'
            audio_bitrate = '320k'

        # Build ffmpeg command: loop template image, overlay ASS subtitles, add trimmed audio
        cmd = [
            'ffmpeg', '-y',
            '-loop', '1',
            '-i', str(template_path),
            '-i', audio_clip_path,
            '-vf', vf_chain,
            '-c:v', 'libx264', '-preset', preset, '-crf', crf,
            '-c:a', 'aac', '-b:a', audio_bitrate,
            '-shortest',
            '-t', str(duration),
            '-pix_fmt', 'yuv420p',
            output,
        ]

        click.echo("🎬 Rendering reel video with FFmpeg...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        except subprocess.CalledProcessError as e:
            click.echo(f"❌ Failed to render reel video: {e.stderr}", err=True)
            return

        click.echo(f"\n✅ Instagram Reel generated successfully!")
        click.echo(f"📹 Output: {output}")
        file_size = os.path.getsize(output) / (1024 * 1024)
        click.echo(f"📊 File size: {file_size:.1f} MB")

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        import traceback
        traceback.print_exc()


@cli.command()
@click.option('--limit', type=int, default=5, show_default=True, help='Number of records to process')
@click.option('--fast', is_flag=True, help='Use fast encoding preset')
@click.option('--overlay', type=click.Path(exists=True, dir_okay=False), multiple=True, help='Path(s) to overlay effect video(s) to use (randomly chosen if multiple). If omitted, picks from overlays directory.')
@click.option('--overlay-hue-deg', type=float, default=None, help='Rotate overlay hue by N degrees (-180..180).')
@click.option('--overlay-saturation', type=float, default=None, help='Scale overlay saturation (1.0 = no change).')
@click.option('--overlay-hue-random', is_flag=True, help='Randomize overlay hue per track if --overlay-hue-deg not set.')
@click.option('--progressive-fill/--no-progressive-fill', default=True, show_default=True, help='Use progressive karaoke fill (\\kf) instead of instant fill (\\k).')
@click.option('--include-lrc', is_flag=True, help='Include tracks that have LRC (Musixmatch) even if Richsync JSON is missing.')
@click.option('--from', 'start', type=int, default=0, show_default=True, help='Start index (0-based) into the filtered result set')
@click.option('--yt-cookies', type=click.Path(exists=True, dir_okay=False), default=None, help='Path to a Netscape cookies.txt file for YouTube (helps avoid 403)')
@click.option('--allow-youtube', is_flag=True, help='Allow downloading from YouTube when no GDrive video is available (may fail with 403)')
@click.option('--alternate/--no-alternate', default=True, show_default=True)
@click.option('--max-sources', type=int, default=None, help='Max number of unique video sources to use (omit to use all)')
@click.option('--max-records', type=int, default=None, help='Max number of Airtable records to fetch per table (omit to fetch all)')
@click.option('--min-rating', type=float, default=None, help='Only keep records with {Rating} >= this value')
@click.option('--video-cache-dir', type=str, default='output/video_cache', show_default=True, help='Directory where source videos are cached (not deleted between runs)')
@click.option('--output', type=str, default=None, help='Output path for the loop video')
@click.option('--fast', is_flag=True, help='Use fast encoding preset')
@click.option('--no-boomerang', is_flag=True, help='Disable boomerang effect')
@click.option('--dry-run', is_flag=True, help='List matching Airtable records and exit (no downloads, no ffmpeg)')
@click.option('--debug', is_flag=True, help='Print extra debug info')
def batch(limit: int, fast: bool, overlay, overlay_hue_deg: Optional[float], overlay_saturation: Optional[float], overlay_hue_random: bool, progressive_fill: bool, include_lrc: bool, start: int, yt_cookies: Optional[str], allow_youtube: bool, alternate: bool, max_sources: Optional[int], max_records: Optional[int], min_rating: Optional[float], video_cache_dir: str, output: Optional[str], no_boomerang: bool, dry_run: bool, debug: bool):
    """Batch-generate karaoke videos for Tracks where Banger is True and Richsync JSON is present."""
    ass_paths_to_cleanup = []
    try:
        airtable_client = AirtableClient()
        karaoke_generator = KaraokeGenerator()

        # Airtable formula: Banger is checked AND Richsync JSON (Musixmatch) is not blank
        # Airtable doesn't support IS_BLANK; use LEN(field) > 0 to check non-empty
        if include_lrc:
            formula = "AND({Banger}, OR(LEN({Richsync JSON (Musixmatch)}) > 0, LEN({LRC (Musixmatch)}) > 0))"
        else:
            formula = "AND({Banger}, LEN({Richsync JSON (Musixmatch)}) > 0)"
        # Guard and compute fetch window
        start = max(0, int(start))
        total_to_fetch = start + int(limit)
        click.echo(f"🔎 Fetching matching records from Airtable (start={start}, limit={limit})...")
        all_records = airtable_client.table.all(formula=formula, max_records=total_to_fetch)
        records = all_records[start:start + limit]
        click.echo(f"✓ Retrieved {len(records)} record(s) in this page (total fetched: {len(all_records)})")

        # Use shared vivid color palette for fallbacks
        last_bg_color = None

        for idx, record in enumerate(records, start=1):
            fields = record.get("fields", {})
            rec_id = record.get("id")
            name = fields.get("Name", f"track_{rec_id}")
            click.echo(f"\n[{idx}/{len(records)}] 🎬 Processing: {name} ({rec_id})")

            primary_colour, secondary_colour, outline_colour = _pick_ass_palette(fields)

            # Lyrics offset
            lyrics_offset = fields.get('Lyrics to singing offset (s)', 0)
            try:
                if isinstance(lyrics_offset, list) and lyrics_offset:
                    lyrics_offset = float(lyrics_offset[0])
                else:
                    lyrics_offset = float(lyrics_offset) if lyrics_offset else 0.0
            except Exception:
                lyrics_offset = 0.0

            # Build ASS from Richsync JSON (or LRC if enabled)
            richsync_json = fields.get("Richsync JSON (Musixmatch)")
            if richsync_json:
                ass_content = ASSKaraokeGenerator.generate_from_richsync(
                    richsync_json,
                    video_width=getattr(settings, 'karaoke_video_width', 1920),
                    video_height=getattr(settings, 'karaoke_video_height', 1080),
                    font_path=getattr(settings, 'karaoke_font_path', 'legacy/karaoke/fonts/SpaceMono-Regular.ttf'),
                    offset_seconds=lyrics_offset,
                    progressive_fill=progressive_fill,
                    primary_colour=primary_colour,
                    secondary_colour=secondary_colour,
                    outline_colour=outline_colour,
                )
            else:
                lrc_content = fields.get("LRC (Musixmatch)")
                ass_content = ASSKaraokeGenerator.generate_from_lrc(
                    lrc_content,
                    video_width=getattr(settings, 'karaoke_video_width', 1920),
                    video_height=getattr(settings, 'karaoke_video_height', 1080),
                    font_path=getattr(settings, 'karaoke_font_path', 'legacy/karaoke/fonts/SpaceMono-Regular.ttf'),
                    offset_seconds=lyrics_offset,
                    progressive_fill=progressive_fill,
                    primary_colour=primary_colour,
                    secondary_colour=secondary_colour,
                    outline_colour=outline_colour,
                )
            if not ass_content:
                click.echo("❌ Failed to build ASS from richsync, skipping", err=True)
                continue

            # Download audio (Google Drive preferred)
            audio_url = airtable_client.get_audio_file_url(record)
            if not audio_url:
                click.echo("❌ No audio file URL in Airtable, skipping", err=True)
                continue
            temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            try:
                if "drive.google.com" in audio_url:
                    ok = download_google_drive_file(audio_url, temp_audio.name)
                    if not ok:
                        _download_to_file(audio_url, temp_audio.name)
                        _save_google_drive_cache(audio_url, temp_audio.name)
                else:
                    _download_to_file(audio_url, temp_audio.name)
                audio_file_path = temp_audio.name
                click.echo("✓ Audio downloaded")
            except Exception as e:
                click.echo(f"❌ Audio download failed: {e}", err=True)
                continue

            # Output paths
            safe_name = str(name).replace('/', '_').replace(' ', '_').replace("'", "")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = f"output/{safe_name}_{timestamp}_karaoke.mp4"
            os.makedirs(os.path.dirname(output) or '.', exist_ok=True)
            temp_output = output.replace('.mp4', '_temp.mp4')

            # Determine video source
            video_file_path = None
            stored_video_url = airtable_client.get_video_file_url(record)
            if stored_video_url:
                try:
                    tmpv = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    _download_to_file(stored_video_url, tmpv.name)
                    video_file_path = tmpv.name
                    click.echo("✓ Using stored video file")
                except Exception as e:
                    click.echo(f"⚠️  Could not download stored video: {e}")
                    video_file_path = None

            if not video_file_path:
                video_url = airtable_client.get_video_url(record)
                if video_url:
                    try:
                        tmpv = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                        if karaoke_generator.download_youtube_video(video_url, tmpv.name, cookies_txt_path=yt_cookies):
                            video_file_path = tmpv.name
                            click.echo("✓ Downloaded YouTube video")
                    except Exception as e:
                        click.echo(f"⚠️  Could not download YouTube video: {e}")

            # If no music video, create a vivid color background
            audio_duration_for_render = get_audio_duration(audio_file_path)
            # Choose a vivid color to use as global tint (also used for fallback background)
            palette = VIVID_COLORS
            choices = [c for c in palette if c != last_bg_color] or palette
            bg_color = random.choice(choices)
            last_bg_color = bg_color
            if not video_file_path:
                w = getattr(settings, 'karaoke_video_width', 1920)
                h = getattr(settings, 'karaoke_video_height', 1080)
                color = bg_color
                tmp_bg = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
                bg_cmd = [
                    'ffmpeg', '-y',
                    '-f', 'lavfi',
                    '-i', f"color=c={color}:s={w}x{h}:r={'24' if fast else '30'}",
                    '-t', str(audio_duration_for_render or 180),
                    '-pix_fmt', 'yuv420p',
                    tmp_bg.name
                ]
                try:
                    subprocess.run(bg_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
                    video_file_path = tmp_bg.name
                    click.echo(f"✓ Created color background {color}")
                except subprocess.CalledProcessError as e:
                    click.echo(f"❌ Failed to create background: {e.stderr}", err=True)
                    continue

            # Write ASS to temp file
            ass_file = temp_output.replace('.mp4', '.ass')
            with open(ass_file, 'w', encoding='utf-8') as f:
                f.write(ass_content)
            ass_paths_to_cleanup.append(ass_file)

            # Render first pass (subtitles + overlay)
            click.echo("🎬 Rendering video with subtitles and overlay...")
            # Decide overlay tint parameters per record
            ov_hue = overlay_hue_deg if overlay_hue_deg is not None else (random.uniform(-180.0, 180.0) if overlay_hue_random else None)
            ov_sat = overlay_saturation if overlay_saturation is not None else (1.6 if ov_hue is not None else None)
            if ov_hue is not None:
                click.echo(f"🎨 Overlay tint: hue={ov_hue:.1f}°, sat={ov_sat}")

            batch_intro_path = Path(__file__).resolve().parents[1] / 'gkda-yellow.mp4'
            success, result = karaoke_generator.generate_karaoke(
                video_path=video_file_path,
                ass_file=ass_file,
                output_path=temp_output,
                logo_path=karaoke_generator.logo_path,
                fast_mode=fast,
                max_duration_seconds=audio_duration_for_render,
                effect_overlay_paths=list(overlay) if overlay else None,
                overlay_hue_deg=ov_hue,
                overlay_saturation=ov_sat,
                overlay_tint_color=bg_color,
                intro_path=str(batch_intro_path) if batch_intro_path.exists() else None,
            )
            if not success:
                click.echo(f"❌ Render failed: {result}", err=True)
                continue
            click.echo("✓ Video with subtitles generated")

            # Replace audio with original track (copy video when possible)
            click.echo("🎵 Replacing audio with original track...")
            cmd = ['ffmpeg', '-y', '-i', temp_output, '-i', audio_file_path,
                   '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', ('192k' if fast else '320k'), '-movflags', '+faststart']
            if audio_duration_for_render:
                cmd.extend(['-t', str(audio_duration_for_render)])
            else:
                cmd.extend(['-shortest'])
            cmd.append(output)

            try:
                subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
                os.unlink(temp_output)
                click.echo("✓ Audio replaced")
            except subprocess.CalledProcessError as e:
                click.echo(f"❌ Failed to replace audio: {e.stderr}", err=True)
                continue

            click.echo(f"✅ Done: {output}")

        click.echo("\n🎉 Batch complete")

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        import traceback
        traceback.print_exc()
    finally:
        for p in ass_paths_to_cleanup:
            _safe_unlink(p)


@cli.command()
@click.option('--tag', type=str, default='Tektonik', show_default=True)
@click.option('--event-type', 'event_types', type=str, multiple=True, help='Filter sources by Airtable Event type (can be provided multiple times). If set, tag-based filtering is not used.')
@click.option('--total-duration', type=float, default=180.0, show_default=True)
@click.option('--clip-min', type=float, default=1.25, show_default=True)
@click.option('--clip-max', type=float, default=10.0, show_default=True)
@click.option('--boomerang-prob', type=float, default=0.25, show_default=True)
@click.option('--reverse-prob', type=float, default=0.15, show_default=True)
@click.option('--dv-prob', type=float, default=0.10, show_default=True)
@click.option('--mosaic-prob', type=float, default=0.08, show_default=True)
@click.option('--glitch-prob', type=float, default=0.20, show_default=True)
@click.option('--glitch-dur', type=float, default=0.12, show_default=True)
@click.option('--glitch-every-cut/--no-glitch-every-cut', default=False, show_default=True)
@click.option('--slowmo-prob', type=float, default=0.0, show_default=True)
@click.option('--slowmo-factor', type=float, default=2.0, show_default=True)
@click.option('--slowmo-fps', type=int, default=30, show_default=True)
@click.option('--alternate/--no-alternate', default=True, show_default=True)
@click.option('--max-sources', type=int, default=None, help='Max number of unique video sources to use (omit to use all)')
@click.option('--max-records', type=int, default=None, help='Max number of Airtable records to fetch per table (omit to fetch all)')
@click.option('--min-rating', type=float, default=None, help='Only keep records with {Rating} >= this value')
@click.option('--video-cache-dir', type=str, default='output/video_cache', show_default=True, help='Directory where source videos are cached (not deleted between runs)')
@click.option('--yt-cookies', type=click.Path(exists=True, dir_okay=False), default=None, help='Path to a Netscape cookies.txt file for YouTube (helps avoid 403)')
@click.option('--allow-youtube/--no-allow-youtube', default=True, show_default=True, help='Allow downloading from YouTube when no GDrive video is available (may fail with 403)')
@click.option('--output', type=str, default=None, help='Output path for the loop video')
@click.option('--fast', is_flag=True, help='Use fast encoding preset')
@click.option('--no-boomerang', is_flag=True, help='Disable boomerang effect')
@click.option('--video-type', type=str, default='Official music video', show_default=True, help='Only keep records where {Type} matches this value (set to empty string to disable filter)')
@click.option('--audio-echo/--no-audio-echo', default=True, show_default=True, help='Apply subtle delay/echo to each excerpt audio')
@click.option('--audio-echo-delays-ms', type=str, default='120|240', show_default=True, help='aecho delays in ms (e.g. 120|240)')
@click.option('--audio-echo-decays', type=str, default='0.22|0.12', show_default=True, help='aecho decays (e.g. 0.22|0.12)')
@click.option('--silence-min', type=float, default=0.0, show_default=True, help='Insert silence/black gap min duration in seconds between extracts')
@click.option('--silence-max', type=float, default=0.0, show_default=True, help='Insert silence/black gap max duration in seconds between extracts')
@click.option('--fade-dur', type=float, default=0.45, show_default=True, help='Fade in/out duration in seconds for each extract and silence segment')
@click.option('--noisy-video/--no-noisy-video', default=True, show_default=True, help='Add visible noisy/grainy texture to each extract')
@click.option('--noisy-video-level', type=int, default=34, show_default=True, help='Noise intensity (4-80)')
@click.option('--vocals-only/--no-vocals-only', default=False, show_default=True, help='Use vocal-focused audio extraction instead of full mix')
@click.option('--mysterious-bed/--no-mysterious-bed', default=True, show_default=True, help='Add a subtle mysterious background bed across the full intro')
@click.option('--mysterious-bed-level', type=float, default=0.55, show_default=True, help='Background bed level (0.0-1.0)')
@click.option('--premiere-xml-output', type=str, default=None, help='Write a Premiere-importable FCP7 XML timeline for the generated segments')
@click.option('--workdir', type=str, default=None, help='Optional working directory for rendered segment files')
@click.option('--keep-workdir/--no-keep-workdir', default=False, show_default=True, help='Keep working directory with rendered segment files')
@click.option('--dry-run', is_flag=True, help='List matching Airtable records and exit (no downloads, no ffmpeg)')
@click.option('--debug', is_flag=True, help='Print extra debug info')
def loop(
    tag: str,
    event_types: tuple[str, ...],
    total_duration: float,
    clip_min: float,
    clip_max: float,
    boomerang_prob: float,
    reverse_prob: float,
    dv_prob: float,
    mosaic_prob: float,
    glitch_prob: float,
    glitch_dur: float,
    glitch_every_cut: bool,
    slowmo_prob: float,
    slowmo_factor: float,
    slowmo_fps: int,
    alternate: bool,
    max_sources: Optional[int],
    max_records: Optional[int],
    min_rating: Optional[float],
    video_cache_dir: str,
    yt_cookies: Optional[str],
    allow_youtube: bool,
    output: Optional[str],
    fast: bool,
    no_boomerang: bool,
    video_type: str,
    audio_echo: bool,
    audio_echo_delays_ms: str,
    audio_echo_decays: str,
    silence_min: float,
    silence_max: float,
    fade_dur: float,
    noisy_video: bool,
    noisy_video_level: int,
    vocals_only: bool,
    mysterious_bed: bool,
    mysterious_bed_level: float,
    premiere_xml_output: Optional[str],
    workdir: Optional[str],
    keep_workdir: bool,
    dry_run: bool,
    debug: bool,
):
    try:
        tag = (tag or '').strip()
        event_types_list = [s.strip() for s in (event_types or ()) if isinstance(s, str) and s.strip()]
        if not tag and not event_types_list:
            click.echo('❌ Provide either --tag or --event-type', err=True)
            return

        total_duration = float(total_duration)
        if total_duration <= 0:
            click.echo('❌ total-duration must be > 0', err=True)
            return

        clip_max = float(clip_max)
        if clip_max <= 0:
            click.echo('❌ clip-max must be > 0', err=True)
            return

        clip_min = float(clip_min)
        if clip_min <= 0:
            click.echo('❌ clip-min must be > 0', err=True)
            return
        if clip_min > clip_max:
            click.echo('❌ clip-min must be <= clip-max', err=True)
            return

        boomerang_prob = float(boomerang_prob)
        reverse_prob = float(reverse_prob)
        dv_prob = float(dv_prob)
        mosaic_prob = float(mosaic_prob)
        glitch_prob = float(glitch_prob)
        glitch_dur = float(glitch_dur)
        slowmo_prob = float(slowmo_prob)
        slowmo_factor = float(slowmo_factor)
        slowmo_fps = int(slowmo_fps)
        if boomerang_prob < 0 or reverse_prob < 0:
            click.echo('❌ probabilities must be >= 0', err=True)
            return
        if dv_prob < 0 or mosaic_prob < 0:
            click.echo('❌ probabilities must be >= 0', err=True)
            return
        if glitch_prob < 0:
            click.echo('❌ probabilities must be >= 0', err=True)
            return
        if glitch_dur < 0:
            click.echo('❌ glitch-dur must be >= 0', err=True)
            return
        if slowmo_prob < 0:
            click.echo('❌ probabilities must be >= 0', err=True)
            return
        if slowmo_factor < 1.0:
            click.echo('❌ slowmo-factor must be >= 1.0', err=True)
            return
        if slowmo_fps <= 0:
            click.echo('❌ slowmo-fps must be > 0', err=True)
            return
        silence_min = float(silence_min)
        silence_max = float(silence_max)
        if silence_min < 0 or silence_max < 0:
            click.echo('❌ silence-min/silence-max must be >= 0', err=True)
            return
        if silence_min > silence_max:
            click.echo('❌ silence-min must be <= silence-max', err=True)
            return
        fade_dur = max(0.0, float(fade_dur))
        noisy_video_level = max(4, min(80, int(noisy_video_level)))
        mysterious_bed_level = max(0.0, min(1.0, float(mysterious_bed_level)))

        if max_sources is not None:
            max_sources = int(max_sources)
            if max_sources <= 0:
                click.echo('❌ max-sources must be > 0', err=True)
                return

        width, height = 1080, 720
        if no_boomerang:
            boomerang_prob = 0.0

        if event_types_list:
            width, height = 1920, 1080
            boomerang_prob = 0.0
            reverse_prob = 0.0
            dv_prob = 0.0
            mosaic_prob = 0.0
            glitch_prob = 0.0
            glitch_every_cut = False
            glitch_dur = 0.0
            slowmo_prob = 0.0

        if not output:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            name_for_output = (event_types_list[0] if event_types_list else tag) or 'loop'
            safe_name = str(name_for_output).lower().replace(' ', '_').replace('/', '_').replace("'", '')
            output = f"output/{safe_name}_{timestamp}_loop.mp4"

        if premiere_xml_output and not keep_workdir:
            keep_workdir = True

        os.makedirs(os.path.dirname(output) or '.', exist_ok=True)

        video_cache_dir = (video_cache_dir or '').strip() or 'output/video_cache'
        os.makedirs(video_cache_dir, exist_ok=True)

        if yt_cookies:
            has_youtube = _cookies_file_contains_domain(yt_cookies, 'youtube.com')
            has_google = _cookies_file_contains_domain(yt_cookies, 'google.com')
            if has_youtube and not has_google:
                click.echo(
                    "⚠️  Your cookies file contains youtube.com cookies but no google.com cookies. "
                    "This often still results in YouTube HTTP 403. Re-export cookies including google.com/accounts.google.com."
                )

        if fast:
            click.echo('⚡ FAST MODE: hardware encoder, lower bitrate (3000k), 24fps, draft quality')

        click.echo('📡 Connecting to Airtable...')
        airtable_client = AirtableClient()
        karaoke_generator = KaraokeGenerator(video_width=width, video_height=height)

        if event_types_list:
            click.echo(f'🔍 Fetching records for event type(s): {", ".join(event_types_list)}...')
            mv_table_candidates = ['Music videos', 'Music Videos', 'Music video', 'Music Video']
            music_videos = []
            used_table = None
            for tname in mv_table_candidates:
                recs = airtable_client.get_records_by_filters_for_table(
                    tname,
                    event_types=event_types_list,
                    event_types_field_name="Event type (from Tracks)",
                    min_rating=min_rating,
                    max_records=max_records,
                )
                if not recs:
                    recs = airtable_client.get_records_by_filters_for_table(
                        tname,
                        event_types=event_types_list,
                        event_types_field_name="Event type",
                        min_rating=min_rating,
                        max_records=max_records,
                    )

                if recs:
                    music_videos = recs
                    used_table = tname
                    break
                if used_table is None:
                    used_table = tname

            click.echo(f'📋 Fetched {len(music_videos)} records from \'{used_table}\'')
            # Filter by Type field if requested
            video_type_filter = (video_type or '').strip()
            if video_type_filter:
                def _type_matches(record, expected):
                    val = (record.get('fields', {}) or {}).get('Type')
                    if val is None:
                        return False
                    if isinstance(val, list):
                        return expected in val
                    return str(val) == expected

                before_type = len(music_videos)
                if debug:
                    for r in music_videos:
                        t = (r.get('fields', {}) or {}).get('Type')
                        if not _type_matches(r, video_type_filter):
                            rid = r.get('id', '?')
                            name = (r.get('fields', {}) or {}).get('Name') or (r.get('fields', {}) or {}).get('Title') or '?'
                            click.echo(f'  🏷️  excluded: {rid} {name} Type={t!r}')
                music_videos = [
                    r for r in music_videos
                    if _type_matches(r, video_type_filter)
                ]
                click.echo(f'🏷️  Type filter: {before_type} → {len(music_videos)} (Type = "{video_type_filter}")')
            # Only keep records that have a GDrive video linked
            click.echo('🔗 Filtering records with GDrive video link...')
            before_filter = len(music_videos)
            music_videos = [
                r for r in music_videos
                if airtable_client.get_gdrive_music_video_url(r)
            ]
            click.echo(f"🔎 Airtable (event type): {before_filter} {used_table}, {len(music_videos)} with GDrive video")
        else:
            click.echo(f'🔍 Fetching records for tag: {tag}...')
            tracks = airtable_client.get_records_by_tag('Tracks', tag, max_records=max_records)
            themed = airtable_client.get_records_by_tag('Themed footage', tag, max_records=max_records)
            click.echo(f"🔎 Airtable (tag): {len(tracks)} Tracks, {len(themed)} Themed footage")

        if dry_run:
            click.echo("\n🔍 Dry run - matching records:")
            if event_types_list:
                table_iter = ((used_table or 'Music videos', music_videos),)
            else:
                table_iter = (("Tracks", tracks), ("Themed footage", themed))

            for table_name, recs in table_iter:
                shown = 0
                for r in recs:
                    fields = r.get('fields', {}) or {}
                    name = fields.get('Name') or fields.get('Title') or fields.get('Track name') or '<no name>'
                    artist = fields.get('Artist') or fields.get('Artist name')
                    rating = fields.get('Rating')
                    rid = r.get('id')
                    if artist:
                        if rating is not None:
                            click.echo(f"  - {table_name}: {artist} - {name} (rating={rating}) ({rid})")
                        else:
                            click.echo(f"  - {table_name}: {artist} - {name} ({rid})")
                    else:
                        if rating is not None:
                            click.echo(f"  - {table_name}: {name} (rating={rating}) ({rid})")
                        else:
                            click.echo(f"  - {table_name}: {name} ({rid})")
                    shown += 1
                    if shown >= 10:
                        break
            return

        if event_types_list:
            mv_shuf = list(music_videos)
            random.shuffle(mv_shuf)
            candidates: list[tuple[str, dict]] = []
            candidate_limit = (max_sources * 3) if max_sources is not None else None
            while mv_shuf and (candidate_limit is None or len(candidates) < candidate_limit):
                candidates.append((used_table or 'Music videos', mv_shuf.pop()))
        else:
            tracks_shuf = list(tracks)
            themed_shuf = list(themed)
            random.shuffle(tracks_shuf)
            random.shuffle(themed_shuf)

            candidates = []
            prefer_tracks_first = random.random() < 0.5
            candidate_limit = (max_sources * 3) if max_sources is not None else None
            while (tracks_shuf or themed_shuf) and (candidate_limit is None or len(candidates) < candidate_limit):
                if prefer_tracks_first:
                    if tracks_shuf:
                        candidates.append(('Tracks', tracks_shuf.pop()))
                    if themed_shuf:
                        candidates.append(('Themed footage', themed_shuf.pop()))
                else:
                    if themed_shuf:
                        candidates.append(('Themed footage', themed_shuf.pop()))
                    if tracks_shuf:
                        candidates.append(('Tracks', tracks_shuf.pop()))

        if not candidates:
            if event_types_list:
                click.echo(f"❌ No records found with Event type matching {event_types_list}", err=True)
            else:
                click.echo(f"❌ No records found with Tags containing '{tag}'", err=True)
            return

        click.echo(f'🎲 {len(candidates)} candidates shuffled, resolving video sources...')
        if workdir:
            temp_root = os.path.abspath(workdir)
            os.makedirs(temp_root, exist_ok=True)
            keep_workdir = True
        elif premiere_xml_output:
            temp_root = os.path.abspath(
                os.path.join(
                    os.path.dirname(output) or '.',
                    f"{Path(output).stem}_segments",
                )
            )
            os.makedirs(temp_root, exist_ok=True)
            keep_workdir = True
        else:
            temp_root = tempfile.mkdtemp(prefix='video_loop_')
        sources: list[tuple[str, dict, str, float]] = []
        record_video_cache: dict[str, str] = {}

        try:
            if event_types_list:
                resolved_by_table = {used_table or 'Music videos': 0}
            else:
                resolved_by_table: dict[str, int] = {'Tracks': 0, 'Themed footage': 0}
            n_candidates = len(candidates)
            n_cached = 0
            n_downloaded = 0
            n_failed = 0
            for ci, (table_name, record) in enumerate(candidates):
                if max_sources is not None and len(sources) >= max_sources:
                    break

                record_id = str(record.get('id') or '')
                if not record_id:
                    continue

                fields = record.get('fields', {}) or {}
                title = fields.get('Name') or fields.get('Title') or fields.get('Track name') or '<no name>'
                artist = fields.get('Artist') or fields.get('Artist name')
                label = f"{artist} - {title}" if artist else str(title)

                if debug:
                    url_dbg = airtable_client.get_gdrive_music_video_url(record) or airtable_client.get_video_url(record)
                    if url_dbg:
                        click.echo(f"🔗 {table_name}:{record_id} url={url_dbg}")
                    else:
                        click.echo(f"🔗 {table_name}:{record_id} url=<none>")

                # Progress line every 10 candidates (or every 1 in debug)
                if ci % (1 if debug else 10) == 0:
                    click.echo(f'  [{ci+1}/{n_candidates}] resolving... ({len(sources)} ok, {n_cached} cached, {n_downloaded} downloaded, {n_failed} failed)')

                cache_key = f"{table_name}:{record_id}"
                if cache_key in record_video_cache and os.path.exists(record_video_cache[cache_key]):
                    vpath = record_video_cache[cache_key]
                else:
                    vpath = None
                    gdrive_video_url = airtable_client.get_gdrive_music_video_url(record)

                    cache_base = _sanitize_filename(_cache_video_basename(table_name, record, record_id))
                    primary_cache_path = os.path.join(video_cache_dir, f"{cache_base}.mp4")
                    record_cache_path = os.path.join(video_cache_dir, f"{cache_base}__{record_id}.mp4")
                    gdrive_cache_path = os.path.join(video_cache_dir, f"{cache_base}__{record_id}__gdrive.mp4")

                    if gdrive_video_url:
                        cache_candidates = [
                            gdrive_cache_path,
                            record_cache_path,
                            primary_cache_path,
                        ]
                        for p in cache_candidates:
                            if os.path.exists(p) and os.path.getsize(p) > 1000 and not _looks_like_html(p):
                                click.echo(f"💾 Cache hit (GDrive): {table_name}:{record_id} {label} -> {p}")
                                vpath = p
                                n_cached += 1
                                break

                        if not vpath:
                            final_path = gdrive_cache_path
                            tmp_path = final_path + ".tmp.mp4"
                            try:
                                if os.path.exists(tmp_path):
                                    os.unlink(tmp_path)
                            except Exception:
                                pass

                            click.echo(f"⬇️  Downloading (GDrive, cached): {table_name}:{record_id} {label} url={gdrive_video_url} -> {final_path}")
                            try:
                                if "drive.google.com" in gdrive_video_url:
                                    ok = download_google_drive_file(gdrive_video_url, tmp_path, timeout_seconds=180)
                                    if not ok:
                                        _download_to_file(gdrive_video_url, tmp_path, timeout_seconds=180)
                                else:
                                    _download_to_file(gdrive_video_url, tmp_path, timeout_seconds=180)

                                if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000 and not _looks_like_html(tmp_path):
                                    try:
                                        os.replace(tmp_path, final_path)
                                    except Exception:
                                        try:
                                            os.rename(tmp_path, final_path)
                                        except Exception:
                                            pass

                                if os.path.exists(final_path) and os.path.getsize(final_path) > 1000 and not _looks_like_html(final_path):
                                    vpath = final_path
                                    n_downloaded += 1
                            except Exception:
                                pass

                    if not vpath:
                        # YouTube / other: download to video_cache (persisted)
                        _repair_ytdlp_part_filename(primary_cache_path)
                        _repair_ytdlp_part_filename(record_cache_path)

                        cache_path = record_cache_path if os.path.exists(record_cache_path) else primary_cache_path

                        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000 and not _looks_like_html(cache_path):
                            click.echo(f"💾 Cache hit (YT): {table_name}:{record_id} {label} -> {cache_path}")
                            vpath = cache_path
                            n_cached += 1
                        else:
                            final_path = primary_cache_path
                            if os.path.exists(primary_cache_path):
                                final_path = record_cache_path

                            tmp_path = final_path + ".tmp.mp4"
                            try:
                                if os.path.exists(tmp_path):
                                    os.unlink(tmp_path)
                            except Exception:
                                pass

                            video_url = airtable_client.get_video_url(record)
                            if video_url:
                                click.echo(f"⬇️  Downloading (YT, cached): {table_name}:{record_id} {label} url={video_url} -> {final_path}")
                            else:
                                click.echo(f"⬇️  No video URL: {table_name}:{record_id} {label}")

                            if video_url:
                                ok = _download_video_from_record(
                                    airtable_client,
                                    karaoke_generator,
                                    record,
                                    tmp_path,
                                    yt_cookies=yt_cookies,
                                    allow_youtube=allow_youtube,
                                )
                                if ok and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000 and not _looks_like_html(tmp_path):
                                    try:
                                        os.replace(tmp_path, final_path)
                                    except Exception:
                                        try:
                                            os.rename(tmp_path, final_path)
                                        except Exception:
                                            pass

                            if os.path.exists(final_path) and os.path.getsize(final_path) > 1000 and not _looks_like_html(final_path):
                                vpath = final_path
                                n_downloaded += 1

                    if not vpath or not os.path.exists(vpath) or os.path.getsize(vpath) < 1000 or _looks_like_html(vpath):
                        n_failed += 1
                        if debug:
                            try:
                                sz = os.path.getsize(vpath) if vpath and os.path.exists(vpath) else 0
                            except Exception:
                                sz = 0
                            click.echo(f"⚠️  Download failed/too small {table_name}:{record_id} size={sz}B")
                            if vpath and os.path.exists(vpath):
                                hint = _extract_html_hint(vpath)
                                if hint:
                                    click.echo(f"   ↳ HTML hint: {hint}")
                        try:
                            if vpath and os.path.exists(vpath):
                                os.unlink(vpath)
                        except Exception:
                            pass
                        continue
                    record_video_cache[cache_key] = vpath

                vdur = _probe_video_duration_seconds(vpath)
                if not vdur or vdur <= 2.0:
                    if debug:
                        try:
                            sz = os.path.getsize(vpath) if os.path.exists(vpath) else 0
                        except Exception:
                            sz = 0
                        click.echo(f"⚠️  Skipping {table_name}:{record_id} (duration={vdur}, size={sz}B)")
                    continue

                if not _probe_has_audio_stream(vpath):
                    if debug:
                        click.echo(f"⚠️  Skipping {table_name}:{record_id} (no audio stream)")
                    continue

                sources.append((table_name, record, vpath, float(vdur)))
                resolved_by_table[table_name] = resolved_by_table.get(table_name, 0) + 1

            click.echo(
                f"🎞️  Sources resolved: {sum(resolved_by_table.values())} total "
                f"({n_cached} from cache, {n_downloaded} freshly downloaded, {n_failed} failed)"
            )

            if not sources:
                click.echo('❌ Could not resolve any playable video sources (GDrive/YT)', err=True)
                return

            segment_paths: list[str] = []
            elapsed = 0.0
            idx = 0
            last_record_id: Optional[str] = None
            last_table: Optional[str] = None
            used_source_ids: set[str] = set()

            progress_every = 1 if debug else 5
            click.echo(
                f"🧩 Rendering segments... target={total_duration:.1f}s "
                f"(clip {clip_min:.2f}s–{clip_max:.2f}s, effects: boomerang={boomerang_prob:.2f}, reverse={reverse_prob:.2f}, glitch={glitch_prob:.2f})"
            )

            while elapsed + 0.25 < total_duration:
                remaining = total_duration - elapsed
                final_max = min(clip_max, remaining)
                final_min = min(clip_min, final_max)
                if final_max <= 0.25:
                    break

                r = random.random() ** 2
                final_dur = final_min + (final_max - final_min) * r

                eligible = list(sources)
                if last_record_id:
                    filtered = [s for s in eligible if str(s[1].get('id') or '') != last_record_id]
                    if filtered:
                        eligible = filtered
                if alternate and last_table:
                    alt = [s for s in eligible if s[0] != last_table]
                    if alt:
                        eligible = alt

                if event_types_list:
                    eligible = [s for s in eligible if str((s[1].get('id') or '')) not in used_source_ids]
                    if not eligible:
                        break
                    table_name, record, vpath, vdur = random.choice(eligible)
                else:
                    table_name, record, vpath, vdur = random.choice(eligible)
                record_id = str(record.get('id') or '')

                r_fields = record.get('fields', {}) or {}
                r_title = r_fields.get('Name') or r_fields.get('Title') or r_fields.get('Track name') or '<no name>'
                r_artist = r_fields.get('Artist') or r_fields.get('Artist name')
                r_label = f"{r_artist} - {r_title}" if r_artist else str(r_title)

                use_boomerang = (boomerang_prob > 0) and (random.random() < boomerang_prob)
                use_reverse = (not use_boomerang) and (reverse_prob > 0) and (random.random() < reverse_prob)

                # Visual effects (independent)
                use_dv = (dv_prob > 0) and (random.random() < dv_prob)
                use_mosaic = (mosaic_prob > 0) and (random.random() < mosaic_prob)
                # Transition glitch: apply on the first frames of a segment (skip first segment)
                if glitch_every_cut:
                    use_glitch = idx > 0 and (glitch_dur > 0)
                else:
                    use_glitch = (idx > 0) and (glitch_prob > 0) and (random.random() < glitch_prob)
                use_slowmo = (slowmo_prob > 0) and (random.random() < slowmo_prob)

                excerpt_dur = final_dur / 2.0 if use_boomerang else final_dur
                if excerpt_dur <= 0.25:
                    break

                # Skip first/last 15s of each source to avoid intro/outro text
                margin = 15.0
                safe_start = min(margin, vdur * 0.25)
                safe_end = min(margin, vdur * 0.25)
                usable_start = safe_start
                usable_end = max(0.0, vdur - safe_end)
                if usable_end - usable_start >= excerpt_dur:
                    if event_types_list:
                        # Chorus-biased region: favor center of track over intros/outros.
                        span = max(0.0, usable_end - usable_start)
                        chorus_start = usable_start + 0.25 * span
                        chorus_end = usable_start + 0.75 * span
                        max_start = chorus_end - excerpt_dur
                        if max_start > chorus_start:
                            start = random.uniform(chorus_start, max_start)
                        else:
                            max_start = usable_end - excerpt_dur
                            start = random.uniform(usable_start, max_start) if max_start > usable_start else usable_start
                    else:
                        max_start = usable_end - excerpt_dur
                        start = random.uniform(usable_start, max_start) if max_start > usable_start else usable_start
                else:
                    # Clip too short for margins, use full range
                    max_start = max(0.0, vdur - excerpt_dur - 0.1)
                    start = random.uniform(0.0, max_start) if max_start > 0 else 0.0

                if idx % progress_every == 0 or idx == 0:
                    fx_parts = []
                    if use_boomerang:
                        fx_parts.append('boomerang')
                    elif use_reverse:
                        fx_parts.append('reverse')
                    if use_dv:
                        fx_parts.append('dv')
                    if use_mosaic:
                        fx_parts.append('mosaic')
                    if use_glitch:
                        fx_parts.append('glitch')
                    if use_slowmo:
                        fx_parts.append(f"slowmo×{slowmo_factor:g}")
                    if noisy_video:
                        fx_parts.append(f"noise{noisy_video_level}")
                    fx = '+'.join(fx_parts) if fx_parts else 'normal'

                    pct = (elapsed / total_duration * 100.0) if total_duration > 0 else 0.0
                    click.echo(
                        f"[{idx:04d}] ⏱️  {elapsed:6.1f}/{total_duration:.1f}s ({pct:5.1f}%) "
                        f"dur≈{final_dur:.2f}s start={start:.1f}s fx={fx} src={table_name}:{record_id} {r_label}"
                    )

                seg_out = os.path.join(temp_root, f"seg_{idx:04d}.mp4")

                if use_boomerang:
                    ok = _render_boomerang_excerpt(
                        input_video_path=vpath,
                        start_seconds=start,
                        duration_seconds=excerpt_dur,
                        output_path=seg_out,
                        width=width,
                        height=height,
                        fast=fast,
                        dv=use_dv,
                        mosaic=use_mosaic,
                        glitch=use_glitch,
                        glitch_duration_seconds=glitch_dur,
                        slowmo=use_slowmo,
                        slowmo_factor=slowmo_factor,
                        slowmo_fps=slowmo_fps,
                        audio_echo=audio_echo,
                        audio_echo_delays_ms=audio_echo_delays_ms,
                        audio_echo_decays=audio_echo_decays,
                        fade_duration=fade_dur,
                        noisy_video=noisy_video,
                        noisy_video_level=noisy_video_level,
                        vocals_only=vocals_only,
                    )
                    seg_dur = excerpt_dur * 2.0
                elif use_reverse:
                    ok = _render_reverse_excerpt(
                        input_video_path=vpath,
                        start_seconds=start,
                        duration_seconds=excerpt_dur,
                        output_path=seg_out,
                        width=width,
                        height=height,
                        fast=fast,
                        dv=use_dv,
                        mosaic=use_mosaic,
                        glitch=use_glitch,
                        glitch_duration_seconds=glitch_dur,
                        slowmo=use_slowmo,
                        slowmo_factor=slowmo_factor,
                        slowmo_fps=slowmo_fps,
                        audio_echo=audio_echo,
                        audio_echo_delays_ms=audio_echo_delays_ms,
                        audio_echo_decays=audio_echo_decays,
                        fade_duration=fade_dur,
                        noisy_video=noisy_video,
                        noisy_video_level=noisy_video_level,
                        vocals_only=vocals_only,
                    )
                    seg_dur = excerpt_dur
                else:
                    ok = _render_effect_excerpt(
                        input_video_path=vpath,
                        start_seconds=start,
                        duration_seconds=excerpt_dur,
                        output_path=seg_out,
                        width=width,
                        height=height,
                        fast=fast,
                        mode='normal',
                        dv=use_dv,
                        mosaic=use_mosaic,
                        glitch=use_glitch,
                        glitch_duration_seconds=glitch_dur,
                        slowmo=use_slowmo,
                        slowmo_factor=slowmo_factor,
                        slowmo_fps=slowmo_fps,
                        audio_echo=audio_echo,
                        audio_echo_delays_ms=audio_echo_delays_ms,
                        audio_echo_decays=audio_echo_decays,
                        fade_duration=fade_dur,
                        noisy_video=noisy_video,
                        noisy_video_level=noisy_video_level,
                    )
                    seg_dur = excerpt_dur

                if not ok or not os.path.exists(seg_out) or os.path.getsize(seg_out) < 1000:
                    if debug:
                        try:
                            sz = os.path.getsize(seg_out) if os.path.exists(seg_out) else 0
                        except Exception:
                            sz = 0
                        click.echo(f"⚠️  Segment render failed idx={idx} size={sz}B src={table_name}:{record_id}")
                    try:
                        if os.path.exists(seg_out):
                            os.unlink(seg_out)
                    except Exception:
                        pass
                    idx += 1
                    continue

                segment_paths.append(seg_out)
                elapsed += seg_dur
                idx += 1
                last_record_id = record_id
                last_table = table_name
                used_source_ids.add(record_id)

                # Optional silence/black gap between excerpts.
                if silence_max > 0 and elapsed + 0.25 < total_duration:
                    if event_types_list:
                        remaining_sources = [
                            s for s in sources
                            if str((s[1].get('id') or '')) not in used_source_ids
                        ]
                        if not remaining_sources:
                            continue

                    gap_remaining = total_duration - elapsed
                    if gap_remaining > 0.05:
                        gap_min = min(silence_min, gap_remaining)
                        gap_max = min(silence_max, gap_remaining)
                        if gap_max >= gap_min and gap_max > 0:
                            gap_dur = random.uniform(gap_min, gap_max) if gap_max > gap_min else gap_max
                            gap_out = os.path.join(temp_root, f"seg_{idx:04d}_gap.mp4")
                            ok_gap = _render_silence_segment(
                                output_path=gap_out,
                                duration_seconds=gap_dur,
                                width=width,
                                height=height,
                                fast=fast,
                                fps=30,
                                fade_duration=fade_dur,
                            )
                            if ok_gap and os.path.exists(gap_out) and os.path.getsize(gap_out) > 1000:
                                segment_paths.append(gap_out)
                                elapsed += gap_dur
                                idx += 1

            if not segment_paths:
                click.echo('❌ No segments could be rendered', err=True)
                return

            click.echo(f"🔗 Concatenating {len(segment_paths)} segments...")
            ok = _concat_video_segments(segment_paths, output, fast=fast)
            if not ok:
                click.echo('❌ Failed to concat segments', err=True)
                return

            if mysterious_bed:
                bed_output = output + '.bed.mp4'
                click.echo('🫧 Adding mysterious background bed...')
                if _add_mysterious_background_bed(output, bed_output, bed_level=mysterious_bed_level):
                    try:
                        os.replace(bed_output, output)
                    except Exception:
                        pass
                else:
                    click.echo('⚠️  Could not apply mysterious bed, keeping dry mix')

            if premiere_xml_output:
                timeline_fps = 24 if fast else 30
                xml_ok = _write_premiere_fcp7_xml(
                    segment_paths=segment_paths,
                    output_xml_path=premiere_xml_output,
                    width=width,
                    height=height,
                    fps=timeline_fps,
                    sequence_name=Path(output).stem,
                )
                if xml_ok:
                    click.echo(f"🧩 Premiere XML written: {premiere_xml_output}")
                else:
                    click.echo('⚠️  Failed to write Premiere XML timeline')

            click.echo(f"\n✅ Loop generated successfully!")
            click.echo(f"📹 Output: {output}")
            try:
                file_size = os.path.getsize(output) / (1024 * 1024)
                click.echo(f"📊 File size: {file_size:.1f} MB")
            except Exception:
                pass
        finally:
            if keep_workdir:
                click.echo(f"📁 Kept workdir: {temp_root}")
            else:
                try:
                    shutil.rmtree(temp_root)
                except Exception:
                    pass

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        import traceback
        traceback.print_exc()


@cli.command('generate-playlist')
@click.option('--decade', 'decades', multiple=True, help='Filter tracks by Decade (repeatable, e.g. "2010s")')
@click.option('--event-type', 'event_types', multiple=True, help='Filter tracks by Event type (repeatable)')
@click.option('--genre', 'genres', multiple=True, help='Filter tracks by Genre (repeatable)')
@click.option('--tag', 'tags', multiple=True, help='Filter tracks by Tag (repeatable)')
@click.option('--table', 'tracks_table', default='Tracks', show_default=True, help='Airtable tracks table')
@click.option('--model', 'model_name', default=None, help='Energy model to use (e.g. "2010s"). Defaults to auto-detect from --decade.')
@click.option('--name', 'playlist_name', default=None, help='Playlist name (default: auto-generated)')
@click.option('--notes', default=None, help='Notes to attach to the playlist record')
@click.option('--event-id', default=None, help='Airtable Event record ID to link the playlist to')
@click.option('--shuffle/--no-shuffle', default=True, show_default=True, help='Shuffle tracks within each block')
@click.option('--seed', type=int, default=None, help='Random seed for reproducible playlists')
@click.option('--show-model', is_flag=True, help='Show the energy model overview and exit')
@click.option('--show-genres', is_flag=True, help='Show available genre distribution and exit')
@click.option('--dry-run', is_flag=True, help='Preview the playlist without creating it in Airtable')
@click.option('--max-records', type=int, default=None, help='Max Airtable records to fetch')
@click.option('--target-hours', type=float, default=6.0, show_default=True, help='Target set duration in hours (fills extended blocks until reached)')
@click.option('--late-track', 'late_tracks', multiple=True, help='Track title keyword to place later in the night (repeatable). Default includes "Get Lucky"')
@click.option('--late-track-ratio', type=float, default=0.60, show_default=True, help='Minimum playlist position ratio for late tracks (0.0 to 1.0)')
def generate_playlist_cmd(
    decades: tuple,
    event_types: tuple,
    genres: tuple,
    tags: tuple,
    tracks_table: str,
    model_name: Optional[str],
    playlist_name: Optional[str],
    notes: Optional[str],
    event_id: Optional[str],
    shuffle: bool,
    seed: Optional[int],
    show_model: bool,
    show_genres: bool,
    dry_run: bool,
    max_records: Optional[int],
    target_hours: float,
    late_tracks: tuple,
    late_track_ratio: float,
):
    """Generate a playlist from Airtable tracks using an energy model.

    Arranges tracks into blocks (Warmup, Dance, Hip-hop, Pop, Breathing,
    Peak, Wind Down, Closer) following DJ set energy guidelines inspired
    by the "We Are The 90's" structure.

    Creates a record in the "Playlists (from events)" Airtable table.

    \b
    Examples:
      # Preview a 2010s playlist
      python -m src.cli generate-playlist --decade 2010s --dry-run

      # Show the energy model for 2010s
      python -m src.cli generate-playlist --model 2010s --show-model

      # Generate and save to Airtable
      python -m src.cli generate-playlist --decade 2010s --name "We Are The 2010's"

      # Filter by specific genres
      python -m src.cli generate-playlist --decade 2010s --genre Pop --genre Dance --dry-run
    """
    try:
        # ── Resolve model ──
        resolved_model_name = model_name
        if not resolved_model_name and decades:
            for d in decades:
                d_clean = d.strip().lower().replace("'", "")
                if d_clean in PLAYLIST_MODELS:
                    resolved_model_name = d_clean
                    break
        if not resolved_model_name:
            resolved_model_name = "2010s"

        model = PLAYLIST_MODELS.get(resolved_model_name)
        if not model:
            available = ", ".join(PLAYLIST_MODELS.keys())
            click.echo(f"❌ Modèle inconnu: {resolved_model_name} (disponibles: {available})", err=True)
            return

        click.echo(f"🎛️  Modèle: {resolved_model_name}")

        if show_model:
            print_model_overview(model)
            return

        # ── Build Airtable formula ──
        formulas: list[str] = []

        if decades:
            conds = [
                f"FIND('{_spotify_formula_escape(d)}', ARRAYJOIN({{Decade}}))"
                for d in decades
            ]
            formulas.append(f"OR({', '.join(conds)})" if len(conds) > 1 else conds[0])

        if event_types:
            conds = [
                f"FIND('{_spotify_formula_escape(et)}', ARRAYJOIN({{Event type}}))"
                for et in event_types
            ]
            formulas.append(f"OR({', '.join(conds)})" if len(conds) > 1 else conds[0])

        if genres:
            conds = [
                f"FIND('{_spotify_formula_escape(g)}', {{Genre}})"
                for g in genres
            ]
            formulas.append(f"OR({', '.join(conds)})" if len(conds) > 1 else conds[0])

        if tags:
            conds = [
                f"FIND('{_spotify_formula_escape(t)}', {{Tags}})"
                for t in tags
            ]
            formulas.append(f"OR({', '.join(conds)})" if len(conds) > 1 else conds[0])

        if not formulas:
            click.echo("❌ Aucun filtre fourni. Utilise --decade, --event-type, --genre, ou --tag.", err=True)
            return

        formula = f"AND({', '.join(formulas)})" if len(formulas) > 1 else formulas[0]
        click.echo(f"🔎 Formule: {formula}")

        # ── Fetch tracks ──
        airtable_client = AirtableClient()
        table_api = airtable_client.get_table(tracks_table)
        kwargs: dict[str, Any] = {"formula": formula}
        if max_records:
            kwargs["max_records"] = max_records
        records = table_api.all(**kwargs)

        if not records:
            click.echo("⚠️  Aucun track trouvé avec ces filtres.")
            return

        click.echo(f"🎵 {len(records)} tracks trouvés")

        if show_genres:
            print_genre_distribution(records)
            return

        # ── Arrange tracks ──
        playlist = arrange_tracks(
            records,
            model,
            shuffle_within_blocks=shuffle,
            seed=seed,
            target_duration_seconds=max(0.0, float(target_hours or 0.0)) * 3600.0,
            late_track_keywords=tuple(late_tracks) if late_tracks else ("get lucky",),
            late_track_min_ratio=float(late_track_ratio),
        )

        if not playlist:
            click.echo("⚠️  Aucun track n'a pu être placé dans le modèle.")
            return

        # ── Display ──
        print_playlist_summary(playlist, model)

        unused = get_unused_tracks(records, playlist)
        if unused:
            click.echo(f"\n  ⚠️  {len(unused)} tracks non placés :")
            for r in unused[:10]:
                fields = r.get("fields", {}) or {}
                click.echo(f"     - {_track_label(fields)}")
            if len(unused) > 10:
                click.echo(f"     ... et {len(unused) - 10} de plus")

        if dry_run:
            click.echo("\n🧪 Dry run — playlist non créée dans Airtable.")
            return

        # ── Create playlist in Airtable ──
        playlist_table_name = "Playlists (from events)"
        playlist_table = airtable_client.get_table(playlist_table_name)

        track_ids = [rid for rid, _, _ in playlist]

        # Build default name
        if not playlist_name:
            parts = []
            if decades:
                parts.append(" / ".join(decades))
            if event_types:
                parts.append(" / ".join(event_types))
            playlist_name = " — ".join(parts) if parts else f"Playlist {resolved_model_name}"

        # Name is a computed field — we write to Notes and link Tracks/Event
        create_fields: dict[str, Any] = {
            "Tracks": track_ids,
        }

        # Build notes content (includes playlist name + block structure)
        if not notes:
            block_lines = []
            current_block = None
            idx = 0
            for rid, record, block_name in playlist:
                if block_name != current_block:
                    current_block = block_name
                    block_lines.append(f"\n▸ {block_name}")
                idx += 1
                fields = record.get("fields", {}) or {}
                block_lines.append(f"  {idx}. {_track_label(fields)}")
            notes = f"Playlist: {playlist_name}\nModèle: {resolved_model_name}\n{''.join(block_lines)}"
        create_fields["Notes"] = notes

        if decades:
            create_fields["Decades"] = list(decades)

        if event_id:
            create_fields["Event"] = [event_id]

        click.echo(f"\n📝 Création de la playlist: {playlist_name}")
        click.echo(f"   {len(track_ids)} tracks dans '{playlist_table_name}'")

        result = playlist_table.create(create_fields)
        new_id = result.get("id", "?")
        click.echo(f"\n✅ Playlist créée !")
        click.echo(f"   Record ID: {new_id}")
        click.echo(f"   Nom: {playlist_name}")
        click.echo(f"   Tracks: {len(track_ids)}")

    except Exception as e:
        click.echo(f"❌ Erreur: {e}", err=True)
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    cli()
