#!/usr/bin/env python3
"""Download all YouTube clips where GDrive video is missing in the Music videos table.

Usage:
    python -m src.download_missing_clips --yt-cookies cookies.txt
    python -m src.download_missing_clips --yt-cookies cookies.txt --max-records 10 --dry-run
    python -m src.download_missing_clips --yt-cookies cookies.txt --event-type Mariage
"""
import os
import re
import sys
import shutil
import subprocess
import argparse
import unicodedata
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

from src.config import settings
from src.airtable_client import AirtableClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list) and value:
        value = value[0]
    return str(value).strip()


def _sanitize_filename(name: str, max_len: int = 180) -> str:
    s = (name or "").strip()
    s = s.replace("\x00", "").replace("\0", "")
    s = s.replace("/", "_").replace("\\", "_")
    s = re.sub(r"\s+", " ", s).strip()
    # Transliterate accented characters to ASCII equivalents (é→e, ü→u, …)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._()-&,'"
    )
    s = "".join((c if c in allowed else "_") for c in s)
    s = s.strip(" ._")
    if len(s) > max_len:
        s = s[: max_len].rstrip(" ._")
    return s or "video"


def _extract_year(record: dict) -> Optional[int]:
    fields = record.get("fields", {}) or {}
    candidates = [
        fields.get("Year"),
        fields.get("Release year"),
        fields.get("Release Year"),
        fields.get("Year (from Tracks)"),
        fields.get("Release date"),
        fields.get("Release Date"),
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


def _cache_video_basename(record: dict, record_id: str) -> str:
    fields = record.get("fields", {}) or {}
    artist = _as_text(fields.get("Artist") or fields.get("Artist name"))
    title = _as_text(
        fields.get("Name") or fields.get("Title") or fields.get("Track name")
    )
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
    return f"music_videos_{record_id}"


def _looks_like_html(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(2048).lstrip()
        return head.lower().startswith(b"<!doctype html") or head.lower().startswith(
            b"<html"
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# YouTube download (yt-dlp CLI with cookies, impersonate, node EJS)
# ---------------------------------------------------------------------------

GDRIVE_FIELD_NAMES = [
    "GDrive videos",
    "GDrive video",
    "GDrive",
    "Google Drive videos",
    "Google Drive video",
    "Drive videos",
    "Drive video",
]


def _record_has_gdrive(record: dict) -> bool:
    """Return True if the record already has a non-empty GDrive video field."""
    fields = record.get("fields", {}) or {}
    for key in GDRIVE_FIELD_NAMES:
        val = fields.get(key)
        if val:
            if isinstance(val, list) and len(val) > 0:
                return True
            if isinstance(val, str) and val.strip():
                return True
    return False


def _get_youtube_url(record: dict) -> Optional[str]:
    """Extract a YouTube URL from the record."""
    fields = record.get("fields", {}) or {}
    candidates = [
        fields.get("Video URL"),
        fields.get("Video"),
        fields.get("YouTube URL"),
        fields.get("Youtube URL"),
        fields.get("YouTube"),
        fields.get("YT URL"),
        fields.get("YT"),
        fields.get("YouTube URL (from Tracks)"),
        fields.get("Open music video URL"),
    ]
    for v in candidates:
        if isinstance(v, list) and v:
            v = v[0]
        if isinstance(v, dict):
            v = v.get("url")
        if not isinstance(v, str) or not v.strip():
            continue
        lower = v.lower()
        if "youtube.com" in lower or "youtu.be" in lower:
            return v.strip()
    return None


def download_youtube_video(
    url: str,
    output_path: str,
    cookies_txt_path: str,
    max_height: int = 1080,
) -> bool:
    """Download a YouTube video using yt-dlp CLI with cookies + impersonate."""
    mh = max_height

    def _valid_file() -> bool:
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000

    def _cleanup_empty() -> None:
        for p in [output_path, output_path + ".part.mp4", output_path + ".part"]:
            try:
                if os.path.exists(p) and os.path.getsize(p) <= 1000:
                    os.unlink(p)
            except Exception:
                pass

    def _finalize() -> bool:
        if _valid_file():
            return True
        for alt in [output_path + ".part.mp4", output_path + ".part"]:
            try:
                if os.path.exists(alt) and os.path.getsize(alt) > 1000:
                    os.replace(alt, output_path)
                    return _valid_file()
            except Exception:
                continue
        return _valid_file()

    node_path = shutil.which("node")
    if not node_path:
        print("  ⚠️  node not found in PATH – EJS challenges will fail")

    fmt = (
        f"bestvideo[height<={mh}][vcodec^=avc][ext=mp4]"
        f"+bestaudio[ext=m4a]"
        f"/bestvideo[height<={mh}][ext=mp4]+bestaudio"
        f"/best[height<={mh}][ext=mp4]"
        f"/best[ext=mp4]"
        f"/best"
    )

    if output_path.lower().endswith(".mp4"):
        outtmpl = output_path[:-4] + ".%(ext)s"
    else:
        outtmpl = output_path + ".%(ext)s"

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--force-overwrites",
        "--merge-output-format",
        "mp4",
        "--output",
        outtmpl,
        "--cookies",
        cookies_txt_path,
        "--impersonate",
        "chrome",
    ]

    if node_path:
        cmd += ["--no-js-runtimes", "--js-runtimes", "node", "--remote-components", "ejs:github"]

    cmd += [
        "--format-sort",
        f"res:{mh},ext:mp4:m4a",
        "-f",
        fmt,
        url,
    ]

    _cleanup_empty()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print("  " + line.rstrip())
        rc = proc.wait()
        if rc == 0 and _finalize():
            sz = os.path.getsize(output_path)
            print(f"  ✓ Downloaded {sz / (1024 * 1024):.1f} MB")
            return True
        _finalize()
        if _valid_file():
            sz = os.path.getsize(output_path)
            print(f"  ✓ Downloaded {sz / (1024 * 1024):.1f} MB (rc={rc})")
            return True
        return False
    except Exception as e:
        print(f"  ✗ Exception: {e}")
        return False
    finally:
        _cleanup_empty()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download YouTube clips for Music videos records missing a GDrive video."
    )
    parser.add_argument(
        "--record-id",
        default=None,
        help="Airtable record id (rec...) to download only this Music videos record",
    )
    parser.add_argument(
        "--yt-cookies",
        required=True,
        help="Path to a Netscape cookies.txt file for YouTube authentication",
    )
    parser.add_argument(
        "--video-cache-dir",
        default="output/video_cache",
        help="Directory to save downloaded videos (default: output/video_cache)",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=1080,
        help="Max video height in pixels (default: 1080)",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Limit number of Airtable records fetched (default: all)",
    )
    parser.add_argument(
        "--event-type",
        action="append",
        default=None,
        help="Filter by Event type (can be repeated). If omitted, fetch all records.",
    )
    parser.add_argument(
        "--min-rating",
        type=float,
        default=None,
        help="Only keep records with Rating >= this value",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List records that would be downloaded without actually downloading",
    )
    args = parser.parse_args()

    cookies_path = args.yt_cookies
    if not os.path.exists(cookies_path):
        print(f"✗ Cookies file not found: {cookies_path}")
        sys.exit(1)

    os.makedirs(args.video_cache_dir, exist_ok=True)

    print("Connecting to Airtable...")
    airtable = AirtableClient()

    # Find the Music videos table
    mv_table_names = ["Music videos", "Music Videos", "Music video", "Music Video"]
    records = []
    used_table = None

    if args.record_id:
        for tname in mv_table_names:
            try:
                table = airtable.get_table(tname)
                rec = table.get(args.record_id)
                if rec:
                    records = [rec]
                    used_table = tname
                    break
            except Exception:
                continue

        if not records:
            print(f"✗ Record not found in Music videos table(s): {args.record_id}")
            sys.exit(1)

    if not records:
        for tname in mv_table_names:
            try:
                if args.event_type:
                    recs = airtable.get_records_by_filters_for_table(
                        tname,
                        event_types=args.event_type,
                        event_types_field_name="Event type (from Tracks)",
                        min_rating=args.min_rating,
                        max_records=args.max_records,
                    )
                    if not recs:
                        recs = airtable.get_records_by_filters_for_table(
                            tname,
                            event_types=args.event_type,
                            event_types_field_name="Event type",
                            min_rating=args.min_rating,
                            max_records=args.max_records,
                        )
                else:
                    table = airtable.get_table(tname)
                    recs = table.all(max_records=args.max_records)
                if recs:
                    records = recs
                    used_table = tname
                    break
            except Exception:
                continue

    if not records:
        print("✗ No records found in Music videos table.")
        sys.exit(1)

    print(f"📋 Fetched {len(records)} records from '{used_table}'")

    # Filter: keep only records WITHOUT GDrive video AND WITH a YouTube URL
    to_download = []
    skipped_has_gdrive = 0
    skipped_no_yt = 0

    for rec in records:
        record_id = rec.get("id", "")
        fields = rec.get("fields", {}) or {}
        artist = _as_text(fields.get("Artist") or fields.get("Artist name"))
        title = _as_text(fields.get("Name") or fields.get("Title") or fields.get("Track name"))
        label = f"{artist} - {title}" if artist else title

        if _record_has_gdrive(rec) and not args.record_id:
            skipped_has_gdrive += 1
            continue

        yt_url = _get_youtube_url(rec)
        if not yt_url:
            skipped_no_yt += 1
            continue

        to_download.append((record_id, rec, label, yt_url))

    print(f"\n📊 Summary:")
    print(f"   Total records:          {len(records)}")
    print(f"   Already have GDrive:    {skipped_has_gdrive}")
    print(f"   No YouTube URL:         {skipped_no_yt}")
    print(f"   To download:            {len(to_download)}")

    if not to_download:
        print("\n✅ Nothing to download – all clips have GDrive videos or no YT URL.")
        return

    if args.dry_run:
        print(f"\n🔍 Dry run – would download {len(to_download)} clips:")
        for i, (rid, rec, label, yt_url) in enumerate(to_download, 1):
            print(f"  {i:3d}. {label}  →  {yt_url}")
        return

    print(f"\n🚀 Downloading {len(to_download)} clips...\n")

    succeeded = 0
    failed = 0
    already_cached = 0

    for i, (record_id, rec, label, yt_url) in enumerate(to_download, 1):
        cache_base = _sanitize_filename(_cache_video_basename(rec, record_id))
        final_path = os.path.join(args.video_cache_dir, f"{cache_base}.mp4")

        # Check if already cached
        if os.path.exists(final_path) and os.path.getsize(final_path) > 1000 and not _looks_like_html(final_path):
            sz = os.path.getsize(final_path)
            print(f"[{i}/{len(to_download)}] 💾 Cached: {label} ({sz / (1024*1024):.1f} MB)")
            already_cached += 1
            succeeded += 1
            continue

        print(f"[{i}/{len(to_download)}] ⬇️  {label}")
        print(f"  URL: {yt_url}")
        print(f"  -> {final_path}")

        tmp_path = final_path + ".tmp.mp4"
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass

        ok = download_youtube_video(
            yt_url, tmp_path, cookies_path, max_height=args.max_height
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
            succeeded += 1
        else:
            failed += 1
            print(f"  ✗ Failed to download: {label}")
            # Clean up bad files
            for p in [final_path, tmp_path]:
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass

        print()

    print(f"\n{'='*60}")
    print(f"✅ Done!")
    print(f"   Succeeded:      {succeeded} ({already_cached} from cache)")
    print(f"   Failed:         {failed}")
    print(f"   Cache dir:      {args.video_cache_dir}")


if __name__ == "__main__":
    main()
