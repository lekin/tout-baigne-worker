#!/usr/bin/env python3
"""
Batch fetch Musixmatch synced lyrics for all tracks in Airtable database.
Saves LRC, Richsync JSON, Track ID, and Fetch Date to Airtable.
"""

import os
import sys
import time
from datetime import datetime
from typing import Dict, Optional, Tuple, List
import re
import subprocess
import tempfile
import json
import requests
from dotenv import load_dotenv
from pyairtable import Api

# Add src to path if running from root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from musixmatch_lyrics import MusixmatchClient, MusixmatchTrack


def _normalize_google_drive_download_url(url: str) -> str:
    try:
        if not url:
            return url
        if 'drive.google.com' not in url:
            return url
        if 'uc?export=download' in url and 'id=' in url:
            return url
        if '/file/d/' in url:
            file_id = url.split('/file/d/')[1].split('/')[0]
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        if 'id=' in url:
            file_id = url.split('id=')[1].split('&')[0]
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        return url
    except Exception:
        return url


def _download_to_file(url: str, dst_path: str, timeout_seconds: int = 60) -> None:
    with requests.get(url, stream=True, allow_redirects=True, timeout=timeout_seconds) as r:
        r.raise_for_status()
        with open(dst_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)


def download_google_drive_file(url: str, dst_path: str, timeout_seconds: int = 60) -> bool:
    try:
        import re
        from urllib.parse import unquote

        url = _normalize_google_drive_download_url(str(url))
        session = requests.Session()
        with session.get(url, stream=True, allow_redirects=True, timeout=timeout_seconds) as r:
            r.raise_for_status()

            content_type = (r.headers.get('content-type') or '').lower()
            if 'text/html' not in content_type:
                with open(dst_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
                return True

            html = r.text

            form_action: Optional[str] = None
            form_method = 'get'
            form_payload: dict[str, str] = {}
            form_match = re.search(r'(<form\b[^>]*>.*?</form>)', html, flags=re.IGNORECASE | re.DOTALL)
            if form_match:
                form_block = form_match.group(1)
                m_action = re.search(r"action\s*=\s*(['\"])(.*?)\1", form_block, flags=re.IGNORECASE)
                if m_action:
                    form_action = m_action.group(2)
                m_method = re.search(r"method\s*=\s*(['\"])?(get|post)\1?", form_block, flags=re.IGNORECASE)
                if m_method:
                    form_method = (m_method.group(2) or 'get').lower()

                for input_tag in re.findall(r"<input\b[^>]*>", form_block, flags=re.IGNORECASE):
                    m_name = re.search(r"name\s*=\s*(['\"])([^'\"]+)\1", input_tag, flags=re.IGNORECASE)
                    if not m_name:
                        continue
                    name = m_name.group(2)
                    m_value = re.search(r"value\s*=\s*(['\"])([^'\"]*)\1", input_tag, flags=re.IGNORECASE)
                    value = m_value.group(2) if m_value else ''
                    form_payload[name] = value

            if form_action:
                action_url = form_action
                action_url = action_url.replace('\\u003d', '=').replace('\\u0026', '&')
                action_url = action_url.replace('&amp;', '&')
                action_url = unquote(action_url)
                if action_url.startswith('/'):
                    action_url = 'https://drive.google.com' + action_url

                try:
                    req = session.post if form_method == 'post' else session.get
                    with req(action_url, data=form_payload if form_method == 'post' else None, params=form_payload if form_method != 'post' else None, stream=True, allow_redirects=True, timeout=timeout_seconds) as rp:
                        rp.raise_for_status()
                        ctp = (rp.headers.get('content-type') or '').lower()
                        if 'text/html' not in ctp:
                            with open(dst_path, 'wb') as f:
                                for chunk in rp.iter_content(chunk_size=1024 * 256):
                                    if chunk:
                                        f.write(chunk)
                            return True
                except Exception:
                    pass

        return False
    except Exception:
        return False


def _get_media_duration_seconds(media_path: str) -> Optional[float]:
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            media_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except Exception:
        return None


def get_audio_url_from_record(fields: Dict) -> Optional[str]:
    """Extract direct-download URL for the Google Drive MP3 (preferred) or attachment URL."""
    gdrive_link = fields.get('Link (from GDrive Audio files)')
    if isinstance(gdrive_link, list) and gdrive_link:
        gdrive_link = gdrive_link[0]
    if isinstance(gdrive_link, str) and gdrive_link:
        return _normalize_google_drive_download_url(gdrive_link)

    audio_field = fields.get('Audio file') or fields.get('Audio File')
    if isinstance(audio_field, list) and audio_field:
        first = audio_field[0]
        if isinstance(first, dict):
            url = first.get('url')
            if isinstance(url, str) and url:
                return url
    return None


def probe_audio_duration_seconds_from_record(fields: Dict) -> Optional[int]:
    url = get_audio_url_from_record(fields)
    if not url:
        return None

    tmp = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        ok = False
        if 'drive.google.com' in url:
            ok = download_google_drive_file(url, tmp_path, timeout_seconds=180)
            if not ok:
                _download_to_file(url, tmp_path, timeout_seconds=180)
        else:
            _download_to_file(url, tmp_path, timeout_seconds=180)

        dur = _get_media_duration_seconds(tmp_path)
        if dur is None:
            return None
        return int(dur)
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def get_all_tracks_from_airtable() -> List[Dict]:
    """
    Fetch all tracks from Airtable.
    
    Returns:
        List of track records with id and fields
    """
    api_key = os.getenv('AIRTABLE_API_KEY')
    base_id = os.getenv('AIRTABLE_BASE_ID')
    table_name = os.getenv('AIRTABLE_TABLE_NAME', 'Tracks')
    
    if not api_key or not base_id:
        raise Exception("AIRTABLE_API_KEY and AIRTABLE_BASE_ID must be set in .env")
    
    print(f"📋 Fetching all tracks from Airtable table '{table_name}'...")
    api = Api(api_key)
    table = api.table(base_id, table_name)
    
    # Fetch all records
    records = table.all()
    print(f"✅ Found {len(records)} tracks in database")
    
    return records


def get_single_track_from_airtable(record_id: str) -> List[Dict]:
    api_key = os.getenv('AIRTABLE_API_KEY')
    base_id = os.getenv('AIRTABLE_BASE_ID')
    table_name = os.getenv('AIRTABLE_TABLE_NAME', 'Tracks')

    if not api_key or not base_id:
        raise Exception("AIRTABLE_API_KEY and AIRTABLE_BASE_ID must be set in .env")

    api = Api(api_key)
    table = api.table(base_id, table_name)
    record = table.get(record_id)
    return [record]


def extract_track_info(fields: Dict) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[int]]:
    """
    Extract track information from Airtable fields.
    
    Args:
        fields: Airtable record fields
        
    Returns:
        Tuple of (track_name, artist_name, album_name, duration)
    """
    # Extract track name - try multiple field name variations
    track_name = (
        fields.get('Title') or
        fields.get('Track') or 
        fields.get('Track name') or
        fields.get('Song')
    )
    
    # Extract artist name
    artist_name = (
        fields.get('Name (from Artist)') or
        fields.get('Artist') or
        fields.get('Artist name') or
        fields.get('Artists')
    )
    
    # Extract album name
    album_name = (
        fields.get('Album') or
        fields.get('Album name') or
        fields.get('Spotify release name')
    )
    
    # Extract duration (in seconds) - prioritize GDrive duration for accurate matching
    duration = (
        fields.get('Duration (GDrive)') or
        fields.get('Duration (Spotify)') or
        fields.get('Duration (from GDrive Audio files)') or
        fields.get('Duration') or
        fields.get('Track duration')
    )
    
    # Handle list fields (from lookups)
    if isinstance(track_name, list):
        track_name = track_name[0] if track_name else None
    if isinstance(artist_name, list):
        artist_name = artist_name[0] if artist_name else None
    if isinstance(album_name, list):
        album_name = album_name[0] if album_name else None
    if isinstance(duration, list):
        duration = duration[0] if duration else None
    
    # Convert duration to int if it's a string
    if duration and isinstance(duration, str):
        try:
            duration = int(float(duration))
        except (ValueError, TypeError):
            duration = None
    elif duration and isinstance(duration, (int, float)):
        duration = int(duration)
    
    return track_name, artist_name, album_name, duration


def fetch_musixmatch_data(
    client: MusixmatchClient,
    track_name: str,
    artist_name: str,
    album_name: Optional[str] = None,
    target_duration: Optional[int] = None,
    pick: Optional[int] = None,
    musixmatch_track_id: Optional[int] = None,
    prefer_richsync_selection: bool = False
) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Fetch LRC and Richsync data from Musixmatch.
    
    Args:
        client: MusixmatchClient instance
        track_name: Song title
        artist_name: Artist name
        album_name: Album name (optional)
        target_duration: Target duration in seconds (optional)
        
    Returns:
        Tuple of (lrc_content, richsync_content, track_id)
    """
    try:
        # Search for track
        tracks = client.search_track(track_name, artist_name, album_name)

        # 2-pass search: album filtering can be overly restrictive and return only a Live/odd version
        if album_name and len(tracks) <= 1:
            more = client.search_track(track_name, artist_name, None)
            if more:
                existing = {int(t.track_id) for t in tracks}
                tracks.extend([t for t in more if int(t.track_id) not in existing])

        # Fallback: matcher.track.get often returns the canonical track (studio) even when search is weird
        matcher_track = client.matcher_track_get(track_name, artist_name, None)
        if matcher_track:
            existing = {int(t.track_id) for t in tracks}
            if int(matcher_track.track_id) not in existing:
                tracks.insert(0, matcher_track)
        
        if not tracks:
            print("   ⚠️  No tracks found")
            return None, None, None
        
        # Hydrate durations: Musixmatch search may return track_length=0
        if target_duration:
            try:
                client.hydrate_track_lengths(tracks)
            except Exception:
                pass
        else:
            try:
                client.hydrate_track_lengths(tracks, limit=5)
            except Exception:
                pass

        # Show all results (useful for --pick)
        print("   📋 Musixmatch results:")
        for i, t in enumerate(tracks, 1):
            flags = []
            if t.has_richsync:
                flags.append('richsync')
            if t.has_subtitles:
                flags.append('subtitles')
            flag_str = ','.join(flags) if flags else 'no_sync'
            dur_str = f"{t.track_length}s" if (t.track_length and int(t.track_length) > 0) else "?"
            print(f"     {i}. id={t.track_id} | {t.artist_name} - {t.track_name} ({dur_str}) [{flag_str}]")

        best_match: Optional[MusixmatchTrack] = None

        if pick is not None:
            idx = int(pick) - 1
            if idx < 0 or idx >= len(tracks):
                print(f"   ❌ Invalid --pick {pick} (must be 1..{len(tracks)})")
                return None, None, None
            best_match = tracks[idx]

        if best_match is None and musixmatch_track_id is not None:
            for t in tracks:
                if int(t.track_id) == int(musixmatch_track_id):
                    best_match = t
                    break
            if best_match is None:
                print(f"   ❌ Musixmatch track id {musixmatch_track_id} not found in search results")
                return None, None, None

        if best_match is None and target_duration:
            synced_tracks = [t for t in tracks if (t.has_subtitles or t.has_richsync)]
            if prefer_richsync_selection:
                richsync_tracks = [t for t in tracks if t.has_richsync]
                if richsync_tracks:
                    synced_tracks = richsync_tracks
            if synced_tracks:
                known_duration_tracks = [t for t in synced_tracks if (t.track_length and int(t.track_length) > 0)]

                def _norm(s: str) -> str:
                    s = (s or '').lower()
                    s = re.sub(r"\(.*?\)", " ", s)
                    s = re.sub(r"[^a-z0-9]+", " ", s)
                    return re.sub(r"\s+", " ", s).strip()

                def _score(t: MusixmatchTrack) -> Tuple[int, int, int]:
                    name_n = _norm(t.track_name)
                    q_n = _norm(track_name)
                    bad = 0
                    if 'live' in name_n:
                        bad += 2
                    if 'remaster' in name_n or 'remastered' in name_n:
                        bad += 1
                    if 'demo' in name_n:
                        bad += 1
                    exact = 1 if name_n == q_n else 0
                    prefix = 1 if name_n.startswith(q_n) else 0
                    has_sync = 1 if (t.has_richsync or t.has_subtitles) else 0
                    return (bad, -has_sync, -(exact + prefix))

                if known_duration_tracks:
                    best_match = min(known_duration_tracks, key=lambda t: abs(t.track_length - target_duration))
                    duration_diff = abs(best_match.track_length - target_duration)
                    print(f"   🎯 Best match: {best_match.track_name} (duration diff: {duration_diff}s)")
                else:
                    best_match = sorted(synced_tracks, key=_score)[0]
                    print(f"   🎯 Best match (no durations available): {best_match.track_name}")
            else:
                best_match = tracks[0]

        if best_match is None:
            best_match = tracks[0]
        
        print(f"   ✅ Using: {best_match.artist_name} - {best_match.track_name}")

        desired_len = best_match.track_length if (best_match.track_length and int(best_match.track_length) > 0) else target_duration
        
        # Fetch LRC (subtitle)
        lrc_content = None
        if best_match.has_subtitles:
            lrc_content = client.get_subtitle(
                best_match.track_id, 
                'lrc', 
                desired_len
            )
            if lrc_content:
                print(f"   ✅ Retrieved LRC ({len(lrc_content)} chars)")
        
        # Fetch Richsync
        richsync_content = None
        if best_match.has_richsync:
            richsync_content = client.get_richsync(
                best_match.track_id,
                desired_len
            )
            if richsync_content:
                print(f"   ✅ Retrieved Richsync ({len(richsync_content)} chars)")
        
        if not lrc_content and not richsync_content:
            print("   ⚠️  No synced lyrics available")
            return None, None, best_match.track_id
        
        return lrc_content, richsync_content, best_match.track_id
        
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None, None, None


def save_to_airtable(
    record_id: str,
    lrc_content: Optional[str],
    richsync_content: Optional[str],
    track_id: Optional[int]
) -> bool:
    """
    Save Musixmatch data to Airtable record.
    
    Args:
        record_id: Airtable record ID
        lrc_content: LRC format content
        richsync_content: Richsync JSON content
        track_id: Musixmatch track ID
        
    Returns:
        True if successful
    """
    try:
        api_key = os.getenv('AIRTABLE_API_KEY')
        base_id = os.getenv('AIRTABLE_BASE_ID')
        table_name = os.getenv('AIRTABLE_TABLE_NAME', 'Tracks')
        
        api = Api(api_key)
        table = api.table(base_id, table_name)
        
        # Prepare update fields
        update_fields = {}
        
        if lrc_content:
            update_fields['LRC (Musixmatch)'] = lrc_content
        
        if richsync_content:
            update_fields['Richsync JSON (Musixmatch)'] = richsync_content
        
        # Try to save track ID, but continue if it fails
        if track_id:
            try:
                update_fields['Musixmatch Track ID'] = track_id
            except:
                pass
        
        # Try to save fetch date, but continue if it fails
        try:
            update_fields['Musixmatch Fetch Date'] = datetime.now().isoformat()
        except:
            pass
        
        # Update record
        if update_fields:
            table.update(record_id, update_fields)
            print(f"   💾 Saved to Airtable")
            return True
        else:
            print(f"   ⚠️  No fields to update")
            return False
        
    except Exception as e:
        # If the update fails, try again without the problematic fields
        print(f"   ⚠️  Error with some fields: {e}")
        try:
            # Try with just the essential fields
            minimal_fields = {}
            if lrc_content:
                minimal_fields['LRC (Musixmatch)'] = lrc_content
            if richsync_content:
                minimal_fields['Richsync JSON (Musixmatch)'] = richsync_content
            
            if minimal_fields:
                table.update(record_id, minimal_fields)
                print(f"   💾 Saved essential fields to Airtable (Track ID and Date skipped)")
                return True
        except Exception as e2:
            print(f"   ❌ Error saving to Airtable: {e2}")
            return False
        
        return False


def main():
    """Main batch processing function."""
    import argparse
    
    load_dotenv()
    
    parser = argparse.ArgumentParser(
        description='Batch fetch Musixmatch synced lyrics for all tracks in Airtable'
    )
    parser.add_argument(
        '--api-key',
        help='Musixmatch API key (or set MUSIXMATCH_API_KEY env var)'
    )
    parser.add_argument(
        '--record-id',
        help='Process a single Airtable record id instead of all tracks'
    )
    parser.add_argument(
        '--delay',
        type=float,
        default=1.0,
        help='Delay between requests in seconds (default: 1.0)'
    )
    parser.add_argument(
        '--pick',
        type=int,
        default=None,
        help='Pick Nth Musixmatch search result (1-based) when processing a track'
    )
    parser.add_argument(
        '--musixmatch-track-id',
        type=int,
        default=None,
        help='Select a specific Musixmatch track id from the search results'
    )
    parser.add_argument(
        '--prefer-richsync',
        action='store_true',
        help='When matching by duration, prefer candidates that have Richsync'
    )
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        help='Skip tracks that already have Musixmatch data'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of tracks to process (for testing)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Fetch data but do not save to Airtable'
    )
    
    args = parser.parse_args()
    
    # Get API key
    api_key = args.api_key or os.getenv('MUSIXMATCH_API_KEY')
    if not api_key:
        print("❌ Error: Musixmatch API key not provided")
        print("   Set MUSIXMATCH_API_KEY environment variable or use --api-key")
        return
    
    # Initialize Musixmatch client
    client = MusixmatchClient(api_key)
    
    # Fetch tracks from Airtable
    try:
        if args.record_id:
            records = get_single_track_from_airtable(args.record_id)
        else:
            records = get_all_tracks_from_airtable()
    except Exception as e:
        print(f"❌ Error fetching tracks from Airtable: {e}")
        return
    
    if not records:
        print("⚠️  No tracks found in database")
        return
    
    # Apply limit if specified
    if args.limit:
        records = records[:args.limit]
        print(f"🔢 Processing first {args.limit} tracks")
    
    # Process each track
    print(f"\n{'='*60}")
    print(f"Starting batch processing of {len(records)} tracks")
    print(f"{'='*60}\n")
    
    stats = {
        'total': len(records),
        'success': 0,
        'skipped': 0,
        'no_match': 0,
        'no_sync': 0,
        'error': 0
    }
    
    for i, record in enumerate(records, 1):
        record_id = record['id']
        fields = record.get('fields', {})
        
        # Extract track info
        track_name, artist_name, album_name, duration = extract_track_info(fields)
        probed_duration = probe_audio_duration_seconds_from_record(fields)
        target_duration = probed_duration if probed_duration else duration
        
        print(f"\n[{i}/{len(records)}] Processing: {artist_name} - {track_name}")
        print(f"   Record ID: {record_id}")
        if target_duration:
            label = "MP3 duration" if probed_duration else "Target duration"
            print(f"   🎵 {label}: {target_duration}s ({target_duration//60}:{target_duration%60:02d})")
        
        # Check if we should skip
        if args.skip_existing:
            has_lrc = fields.get('LRC (Musixmatch)')
            has_richsync = fields.get('Richsync JSON (Musixmatch)')
            if has_lrc or has_richsync:
                print(f"   ⏭️  Skipping (already has Musixmatch data)")
                stats['skipped'] += 1
                continue
        
        # Validate required fields
        if not track_name or not artist_name:
            print(f"   ⚠️  Missing track or artist name - skipping")
            stats['error'] += 1
            continue
        
        # Fetch from Musixmatch
        lrc_content, richsync_content, track_id = fetch_musixmatch_data(
            client,
            track_name,
            artist_name,
            album_name,
            target_duration,
            pick=args.pick,
            musixmatch_track_id=args.musixmatch_track_id,
            prefer_richsync_selection=args.prefer_richsync,
        )
        
        # Categorize result
        if track_id is None:
            stats['no_match'] += 1
        elif not lrc_content and not richsync_content:
            stats['no_sync'] += 1
        else:
            stats['success'] += 1
        
        # Save to Airtable (unless dry run)
        if not args.dry_run and (lrc_content or richsync_content or track_id):
            save_to_airtable(record_id, lrc_content, richsync_content, track_id)
        elif args.dry_run:
            print(f"   🔍 Dry run - not saving to Airtable")
        
        # Rate limiting
        if i < len(records):
            time.sleep(args.delay)
    
    # Print summary
    print(f"\n{'='*60}")
    print("BATCH PROCESSING COMPLETE")
    print(f"{'='*60}")
    print(f"Total tracks:        {stats['total']}")
    print(f"✅ Success:          {stats['success']}")
    print(f"⏭️  Skipped:          {stats['skipped']}")
    print(f"⚠️  No match found:   {stats['no_match']}")
    print(f"⚠️  No sync data:     {stats['no_sync']}")
    print(f"❌ Errors:           {stats['error']}")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
