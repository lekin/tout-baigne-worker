"""Generate 30s Instagram reels for every background of a given event and upload them to Airtable.

Usage:
    .venv/bin/python scripts/reel_for_event.py --event-id recXXXXXXXXXXXXX

Optional:
    --track-id   Explicit Airtable track record ID (otherwise picks the first
                 track from the event type with audio + synced lyrics).
    --template-id  Visual assets template record ID (defaults to the
                   'Instagram reel background' template for Le Grand Karaoké).
    --fast       Use fast encoding preset.
    --no-verify-lyrics  Skip structural QA verification of the lyrics source
                        (default: verify each source against the actual audio
                        and pick the first verified one, preferring Richsync).
"""

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _first_text(value: Any) -> Optional[str]:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("id", "")).strip() or None
    return None


def _attachment_url(value: Any, allowed_types: tuple = ("image/",)) -> Optional[str]:
    if isinstance(value, list) and value:
        item = value[0]
        if isinstance(item, dict):
            if any(str(item.get("type", "")).startswith(t) for t in allowed_types):
                return item.get("url")
    return None


def _download(url: str, dest: Path) -> bool:
    try:
        urllib.request.urlretrieve(url, dest)
        return True
    except Exception as e:
        print(f"⚠️  Could not download {url}: {e}")
        return False


def _load_shortlist(path: str) -> List[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tracks = [t["track_id"] for t in data.get("top_tracks", [])]
        print(f"🎯 Loaded shortlist: {len(tracks)} tracks from {path}")
        return tracks
    except Exception as e:
        print(f"⚠️  Could not load shortlist {path}: {e}", file=sys.stderr)
        return []


def _track_has_lyrics_and_audio(client, record: Dict[str, Any]) -> bool:
    fields = record.get("fields", {})
    return bool(
        (fields.get("Richsync JSON (Musixmatch)")
         or fields.get("SRT (Musixmatch)")
         or fields.get("LRC (Musixmatch)"))
        and client.get_audio_file_url(record)
    )


def _find_eligible_tracks(client, track_ids: List[str]) -> List[str]:
    """Return all track IDs that have both synced lyrics and an audio file."""
    eligible: List[str] = []
    for tid in track_ids:
        try:
            tr = client.table.get(tid)
        except Exception:
            continue
        if _track_has_lyrics_and_audio(client, tr):
            eligible.append(tid)
    return eligible


def _get_airtable_shortlist(client, field_name: str = "GKDA shortlist") -> List[str]:
    """Try to load the shortlist from a checkbox field on the Tracks table."""
    try:
        records = client.table.all(formula=f"{{{field_name}}}")
        if records:
            print(f"🎯 Using Airtable shortlist field '{field_name}' ({len(records)} tracks)")
            return [r["id"] for r in records]
    except Exception as e:
        print(f"⚠️  Airtable shortlist field '{field_name}' not available: {e}")
    return []


def _get_decade_bangers(client, event_type: Dict[str, Any]) -> List[str]:
    """Pool = Banger tracks from the decades linked on the Event type.

    Event type → 'Decades (table)' links → each Decade record's 'Bangers'
    track links. Chronologic/Chrono Reverse link every decade (all bangers);
    La Bug de l'An 2000 → 2000s only, We Are The 90's → 1990s, etc.
    """
    et_fields = event_type.get("fields", {})
    et_name = et_fields.get("Name", "?")
    decade_ids = et_fields.get("Decades (table)") or []
    if not decade_ids:
        return []

    # Resolve decade names ("2000s") — Tracks.{Decade (string)} uses them.
    # (Linked-record ids can't be matched via FIND/ARRAYJOIN in a filter.)
    decade_names: List[str] = []
    decades_table = client.get_table("Decades")
    for did in decade_ids:
        try:
            decade_names.append(decades_table.get(did).get("fields", {}).get("Name", did))
        except Exception:
            decade_names.append(did)
    decade_names = [n for n in decade_names if not n.startswith("rec")]
    if not decade_names:
        return []

    # One batch query: banger tracks of the event type's decades.
    conds = ", ".join(
        f"{{Decade (string)}}='{n.replace(chr(39), '')}'" for n in decade_names
    )
    formula = f"AND({{Banger}}=1, OR({conds}))"
    try:
        records = client.table.all(formula=formula)
    except Exception as e:
        print(f"⚠️  Bangers query failed for {et_name}: {e}")
        return []
    eligible = [r["id"] for r in records if _track_has_lyrics_and_audio(client, r)]
    print(
        f"🎯 Bangers pool for {et_name} ({', '.join(decade_names)}): "
        f"{len(eligible)}/{len(records)} tracks with lyrics+audio"
    )
    return eligible



def _pick_verified_lyrics_source(client, runner, record: Dict[str, Any]) -> Optional[str]:
    """Return 'richsync' when the word-level Musixmatch Richsync structurally
    verifies against the record's own audio. None otherwise — line-level-only
    sources (SRT/LRC) lose the word-by-word fill, so the track is skipped."""
    from src.qa.models import SyncStatus
    from src.qa.timeline import build_timeline_transform

    fields = record.get("fields", {})
    richsync = fields.get("Richsync JSON (Musixmatch)")
    if not richsync:
        return None
    sources = [("richsync", richsync, None, None)]

    transform = build_timeline_transform(record)
    audio_path = runner._download_source_audio(record)
    if not audio_path:
        print("⚠️  QA: could not download audio — cannot verify lyrics sources")
        return None
    duration_ms = runner._media_duration_ms(audio_path)
    vocals_path = runner._get_or_separate_vocals(audio_path)
    if not vocals_path:
        print("⚠️  QA: vocal separation failed — cannot verify lyrics sources")
        return None

    for name, rich, srt, lrc in sources:
        lyrics = runner.build_structured_lyrics_from_source(
            rich, lrc, srt, fields.get("Musixmatch Track ID"), transform, duration_ms)
        report = runner.evaluate(
            record_id=record["id"], track_name=fields.get("Name", ""),
            artist_name="", musixmatch_track_id=fields.get("Musixmatch Track ID"),
            transform=transform, audio_path=audio_path, lyrics=lyrics,
            source_duration_ms=duration_ms, vocals_path=vocals_path, save_report=False)
        ok = report.status == SyncStatus.SYNC_VERIFIED
        print(f"  🔍 QA {name}: {report.status.value if hasattr(report.status, 'value') else report.status}")
        if ok:
            return name
    return None


FONTS_DIR = PROJECT_ROOT / "legacy" / "karaoke" / "fonts"


def _escape_ass_text(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").strip()


def _wrap_title(title: str, max_chars: int = 20) -> str:
    """Wrap a song title into ASS \\N lines, balanced-ish."""
    words = title.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return "\\N".join(_escape_ass_text(l) for l in lines)


def _display_title(fields: Dict[str, Any]) -> str:
    """Track name minus the trailing '(year)' — 'Diam's - La boulette (2006)'
    → 'Diam's - La boulette'."""
    import re
    return re.sub(r"\s*\(\d{4}\)\s*$", "", fields.get("Name", "")).strip()


def _render_cover(bg_url: str, title: str, out_path: Path,
                  palette=("&H0000FFFF", "&H00CCCCCC", "&HA64DFF")) -> bool:
    """Render a 1080x1920 cover: event visual + song title in karaoke typography.

    Same treatment as the lyric overlays — SpaceMono bold, primary/outline
    palette — so the title card matches the reel's look. Title stays on one
    line: font size shrinks to fit ~920px of usable width.
    """
    primary, _secondary, outline = palette
    font_name = "SpaceMono"
    # SpaceMono bold ≈ 0.62em advance width → fit title in one line.
    font_size = min(72, int(1480 / max(len(title), 1)))
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cover,{font_name},{font_size},{primary},{primary},{outline},&H64000000,1,0,0,0,100,100,0,0,1,3,0,5,80,80,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:59:59.99,Cover,,0,0,0,,{{\\an5\\pos(540,670)}}{_escape_ass_text(title)}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False) as f:
        f.write(ass)
        ass_path = f.name
    try:
        ass_esc = (ass_path.replace("\\", "\\\\").replace(":", "\\:")
                   .replace("'", "\\'").replace(",", "\\,"))
        fonts_esc = str(FONTS_DIR).replace(":", "\\:")
        vf = f"scale=1080:1920,setsar=1,subtitles={ass_esc}:fontsdir={fonts_esc}"
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", bg_url,
               "-vf", vf, "-frames:v", "1", "-q:v", "3", str(out_path)]
        subprocess.run(cmd, capture_output=True, check=True)
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        print(f"⚠️  Cover render failed: {e}")
        return False
    finally:
        try:
            os.unlink(ass_path)
        except OSError:
            pass


def _attachment_ids(client, record_id: str) -> List[str]:
    rec = client.get_table("Social media assets").get(record_id)
    return [a["id"] for a in rec.get("fields", {}).get("Generated asset", []) if a.get("id")]


def _upload_generated_asset(client, record_id: str, files: List[tuple],
                            drop_types: tuple = ()) -> bool:
    """Upload files into 'Generated asset'. The upload endpoint APPENDS to
    the field, so we PATCH the final attachment list afterwards.
    `drop_types`: mime prefixes of pre-existing attachments to discard
    (e.g. ("image/",) to replace a stale cover)."""
    keep: List[str] = []
    if drop_types:
        rec = client.get_table("Social media assets").get(record_id)
        keep = [a["id"] for a in rec.get("fields", {}).get("Generated asset", [])
                if a.get("id") and not str(a.get("type", "")).startswith(drop_types)]
    for path, ctype in files:
        if not client.upload_attachment_to_record(
            table_name="Social media assets",
            record_id=record_id,
            field_name="Generated asset",
            file_path=str(path),
            content_type=ctype,
        ):
            return False
    if drop_types:
        # Re-read the field (now containing kept + appended attachments) and
        # drop the stale ones by rewriting the exact desired list.
        ids = keep + [i for i in _attachment_ids(client, record_id) if i not in keep]
        client.get_table("Social media assets").update(
            record_id, {"Generated asset": [{"id": i} for i in ids]})
    return True


def _find_background_assets(client, event_id: str, asset_type: str = "🖼️ Instagram reel background") -> List[Dict[str, Any]]:
    # Use the event ID lookup and the template type lookup to find the
    # generated background image assets for this event.
    safe_type = asset_type.replace("'", "\\'")
    formula = f"AND({{ID (from Event)}}='{event_id}', {{Type (from Template)}}='{safe_type}')"
    return client.get_table("Social media assets").all(formula=formula)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--track-id", action="append", help="One or more track record IDs. Can be passed multiple times. If fewer tracks than backgrounds, they cycle.")
    parser.add_argument("--shortlist", type=str, default=None, help="Path to a JSON shortlist (e.g. output/gkda_top_tracks.json). If passed, uses top tracks from that file.")
    parser.add_argument("--template-id", default=None)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=None, help="Max number of reels to generate (defaults to all backgrounds)")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--no-verify-lyrics", action="store_true",
                        help="Skip structural QA of lyrics sources (not recommended)")
    parser.add_argument("--covers-only", nargs="+", action="append",
                        metavar="ARG",
                        help="Only (re)generate+attach a cover on an existing video asset record: "
                             "--covers-only ASSET_ID TITLE [BG_ASSET_ID]. Repeatable.")
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))
    from src.airtable_client import AirtableClient

    client = AirtableClient()

    # --covers-only: attach a title-card cover to existing video asset records.
    if args.covers_only:
        output_dir = PROJECT_ROOT / "output" / "reels" / args.event_id
        output_dir.mkdir(parents=True, exist_ok=True)
        assets_table = client.get_table("Social media assets")
        # Background lookup: name → image URL from the image-only bg records.
        bg_by_name: Dict[str, str] = {}
        for a in _find_background_assets(client, args.event_id):
            atts = a.get("fields", {}).get("Generated asset") or []
            if any(str(x.get("type", "")).startswith("video/") for x in atts if isinstance(x, dict)):
                continue  # generated video records aren't backgrounds
            url = _attachment_url(atts)
            if url:
                bg_by_name[a.get("fields", {}).get("Background layer", "")] = url
        for entry in args.covers_only:
            if len(entry) not in (2, 3):
                print(f"⚠️  --covers-only expects ASSET_ID TITLE [BG_ASSET_ID], got: {entry}")
                continue
            asset_id, title = entry[0], entry[1]
            explicit_bg = entry[2] if len(entry) == 3 else None
            rec = assets_table.get(asset_id)
            fields = rec.get("fields", {})
            bg_url = None
            if explicit_bg:
                bg_rec = assets_table.get(explicit_bg)
                bg_url = _attachment_url(bg_rec.get("fields", {}).get("Generated asset"))
            else:
                # Reels uploaded by this script embed the bg asset id in the
                # mp4 filename: reel_<bgAssetId>.mp4.
                import re as _re
                for a in fields.get("Generated asset", []):
                    m = _re.search(r"reel_(rec[A-Za-z0-9]+)", a.get("filename", ""))
                    if m:
                        bg_rec = assets_table.get(m.group(1))
                        bg_url = _attachment_url(bg_rec.get("fields", {}).get("Generated asset"))
                        break
            if not bg_url:
                bg_url = bg_by_name.get(fields.get("Background layer", ""))
            if not bg_url:
                print(f"⚠️  No background found for {asset_id}, pass BG_ASSET_ID explicitly")
                continue
            cover = output_dir / f"cover_{asset_id}.jpg"
            if not _render_cover(bg_url, title, cover):
                continue
            if _upload_generated_asset(client, asset_id, [(cover, "image/jpeg")],
                                       drop_types=("image/",)):
                print(f"✅ Cover attached to {asset_id} ({title})")
        print("\n✅ Done")
        return

    event = client.get_table("Events").get(args.event_id)
    event_fields = event.get("fields", {})
    event_name = event_fields.get("Name") or args.event_id
    print(f"🎉 Event: {event_name}")

    event_type_id = _first_text(event_fields.get("Type"))
    if not event_type_id:
        print("❌ Event has no event type", file=sys.stderr)
        sys.exit(1)

    # Resolve the pool of tracks for the reel(s).
    if args.track_id:
        track_pool = args.track_id
    elif args.shortlist:
        track_pool = _load_shortlist(args.shortlist)
        if not track_pool:
            print("❌ Shortlist is empty or could not be loaded", file=sys.stderr)
            sys.exit(1)
        random.shuffle(track_pool)
    else:
        event_type = client.get_table("Event types").get(event_type_id)
        et_name = event_type.get("fields", {}).get("Name", "")
        et_fields = event_type.get("fields", {})

        track_pool: List[str] = []

        # GKDA: dedicated shortlist (checkbox field is GKDA-specific — it must
        # not leak into other event types' pools).
        if et_name == "Le Grand Karaoké de l'Amour":
            track_pool = _get_airtable_shortlist(client)
            if not track_pool:
                default_shortlist = PROJECT_ROOT / "output" / "gkda_top_tracks.json"
                if default_shortlist.exists():
                    track_pool = _load_shortlist(str(default_shortlist))

        # Default: Banger tracks from the event type's linked decades
        # (all decades for Chronologic/Reverse, decade-bound otherwise).
        if not track_pool:
            track_pool = _find_eligible_tracks(client, _get_decade_bangers(client, event_type))

        # Fallback to the Event type's linked Tracks field
        if not track_pool:
            track_ids = et_fields.get("Tracks", [])
            if not track_ids:
                print("❌ Event type has no linked Tracks", file=sys.stderr)
                sys.exit(1)
            track_pool = _find_eligible_tracks(client, track_ids)
            if not track_pool:
                print("❌ No track with both lyrics and audio found", file=sys.stderr)
                sys.exit(1)
        random.shuffle(track_pool)

    bg_assets = _find_background_assets(client, args.event_id)
    # Keep pure image backgrounds only — generated video records (mp4, or
    # mp4+cover jpg) must not be reused as backgrounds.
    def _is_plain_image_asset(a: Dict[str, Any]) -> bool:
        atts = a.get("fields", {}).get("Generated asset") or []
        has_image = any(str(x.get("type", "")).startswith("image/") for x in atts if isinstance(x, dict))
        has_video = any(str(x.get("type", "")).startswith("video/") for x in atts if isinstance(x, dict))
        return has_image and not has_video
    bg_assets = [a for a in bg_assets if _is_plain_image_asset(a)]
    if args.limit:
        random.shuffle(bg_assets)
        bg_assets = bg_assets[: args.limit]

    if not bg_assets:
        print("❌ No background image assets found for this event", file=sys.stderr)
        sys.exit(1)

    # Derive the Airtable template from the existing background assets so that the
    # new video records are linked to the same template and get the right Name.
    template_info = None
    if args.template_id:
        try:
            t = client.get_table("Visual assets templates").get(args.template_id)
            template_info = {"id": t["id"], "name": t["fields"].get("Name", "")}
        except Exception as e:
            print(f"❌ Could not load template {args.template_id}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        first_template_id = (bg_assets[0].get("fields", {}).get("Template") or [None])[0]
        if first_template_id:
            try:
                t = client.get_table("Visual assets templates").get(first_template_id)
                template_info = {"id": t["id"], "name": t["fields"].get("Name", "")}
            except Exception:
                pass
    if not template_info:
        print("❌ Could not determine Visual assets template from background assets", file=sys.stderr)
        sys.exit(1)

    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)

    output_dir = PROJECT_ROOT / "output" / "reels" / args.event_id
    output_dir.mkdir(parents=True, exist_ok=True)

    qa_runner = None
    if not args.no_verify_lyrics:
        from src.qa.runner import KaraokeQARunner
        qa_runner = KaraokeQARunner(verbose=False)
    verified_source_cache: Dict[str, Optional[str]] = {}

    for i, asset in enumerate(bg_assets, start=1):
        fields = asset.get("fields", {})
        bg_name = fields.get("Background layer", f"bg_{i}")
        bg_url = _attachment_url(fields.get("Generated asset"))
        if not bg_url:
            print(f"⚠️  No background image URL for {bg_name}, skipping")
            continue

        track_id = track_pool[i % len(track_pool)]
        track_record = client.table.get(track_id)
        track_name = track_record.get("fields", {}).get("Name", track_id)
        print(f"\n[{i}/{len(bg_assets)}] Background: {bg_name}  →  Track: {track_name}")

        lyrics_source: Optional[str] = None
        if qa_runner is not None:
            if track_id not in verified_source_cache:
                try:
                    verified_source_cache[track_id] = _pick_verified_lyrics_source(
                        client, qa_runner, track_record)
                except Exception as e:
                    print(f"⚠️  QA verification failed for {track_name}: {e}")
                    verified_source_cache[track_id] = None
            lyrics_source = verified_source_cache[track_id]
            if not lyrics_source:
                print(f"❌ No lyrics source verified against the audio for {track_name}, skipping")
                continue
            print(f"  ✅ Using verified lyrics source: {lyrics_source}")

        # Embed the source background asset id in the filename — the durable
        # link back to the visual (needed to rebuild a matching cover later).
        safe_name = asset["id"]
        mp4_path = output_dir / f"reel_{safe_name}.mp4"

        # Generate the reel
        cmd = [
            str(python), "-m", "src.cli", "reel",
            "--record-id", track_id,
            "--duration", str(args.duration),
            "--background", bg_url,
            "--output", str(mp4_path),
        ]
        if args.fast:
            cmd.append("--fast")
        if lyrics_source:
            cmd.extend(["--lyrics-source", lyrics_source])

        result = subprocess.run(cmd, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f"❌ Failed to generate reel for {bg_name}", file=sys.stderr)
            continue

        if not mp4_path.exists():
            print(f"❌ Expected output not found: {mp4_path}", file=sys.stderr)
            continue

        # Create Social media asset record for the video
        fields = {
            "Event": [args.event_id],
            "Template": [template_info["id"]],
        }
        try:
            record = client.get_table("Social media assets").create({**fields, "Background layer": bg_name})
        except Exception as e:
            if "Background layer" in str(e):
                record = client.get_table("Social media assets").create(fields)
            else:
                raise
        record_id = record["id"]
        print(f"📝 Created Social media asset: {record_id}")

        # Cover: same background + song title in karaoke typography — shown by
        # Instagram before the reel plays (spec coverRef).
        cover_path = output_dir / f"cover_{safe_name}.jpg"
        try:
            from src.cli import _pick_ass_palette
            palette = _pick_ass_palette(track_record.get("fields", {}))
        except Exception:
            palette = ("&H0000FFFF", "&H00CCCCCC", "&HA64DFF")
        uploads: List[tuple] = [(mp4_path, "video/mp4")]
        if _render_cover(bg_url, _display_title(track_record.get("fields", {})), cover_path, palette):
            uploads.append((cover_path, "image/jpeg"))

        # Upload video + cover to Generated asset
        success = _upload_generated_asset(client, record_id, uploads)
        if not success:
            print(f"❌ Failed to upload {mp4_path} to Airtable", file=sys.stderr)

    print("\n✅ Done")


if __name__ == "__main__":
    main()
