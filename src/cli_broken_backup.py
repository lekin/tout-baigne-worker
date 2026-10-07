"""Command-line interface for karaoke generation."""
import click
from pathlib import Path
import tempfile
import urllib.request
from datetime import datetime
from typing import Optional, List
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from src.config import settings
from src.airtable_client import AirtableClient
from src.karaoke_generator import KaraokeGenerator
from src.genius_lyrics import GeniusLyricsFetcher
from src.audio_analyzer import AudioAnalyzer
from src.musixmatch_lyrics import ASSKaraokeGenerator


def get_audio_duration(audio_path: str) -> Optional[int]:
    """
    Get duration of an audio file in seconds using ffprobe.
    
    Args:
        audio_path: Path to audio file
        
    Returns:
        Duration in seconds, or None if failed
    """
    try:
        import subprocess
        import json
        
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            audio_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        duration = float(data['format']['duration'])
        return int(duration)
    except Exception as e:
        print(f"Warning: Could not get audio duration: {e}")
        return None


def shift_srt_timing(srt_content: str, offset_seconds: float) -> str:
    """
    Shift all timestamps in an SRT file by a given offset.
    
    Args:
        srt_content: SRT content as string
        offset_seconds: Offset in seconds (positive = delay, negative = advance)
    
    Returns:
        Modified SRT content with shifted timestamps
    """
    import re
    from datetime import timedelta
    
    def parse_timestamp(ts: str) -> timedelta:
        """Parse SRT timestamp to timedelta."""
        h, m, s_ms = ts.split(':')
        s, ms = s_ms.split(',')
        return timedelta(hours=int(h), minutes=int(m), seconds=int(s), milliseconds=int(ms))
    
    def format_timestamp(td: timedelta) -> str:
        """Format timedelta to SRT timestamp."""
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        milliseconds = td.microseconds // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
    
    def shift_line(match):
        """Shift a timestamp line."""
        start_ts, end_ts = match.group(1), match.group(2)
        start_td = parse_timestamp(start_ts) + timedelta(seconds=offset_seconds)
        end_td = parse_timestamp(end_ts) + timedelta(seconds=offset_seconds)
        
        # Don't allow negative timestamps
        if start_td.total_seconds() < 0:
            start_td = timedelta(0)
        if end_td.total_seconds() < 0:
            end_td = timedelta(0)
        
        return f"{format_timestamp(start_td)} --> {format_timestamp(end_td)}"
    
    # Pattern to match SRT timestamp lines
    pattern = r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})'
    return re.sub(pattern, shift_line, srt_content)


@click.group()
def cli():
    """Karaoke Generator CLI - Generate karaoke videos from music clips."""
    pass


@cli.command()
@click.option(
    '--record-id',
    required=True,
    help='Airtable record ID'
)
@click.option(
    '--output',
    type=str,
    default=None,
    help='Output path for the karaoke video'
)
@click.option('--fast', is_flag=True, help='Use fast encoding preset (lower quality but faster)')
@click.option('--refetch-lyrics', is_flag=True, help='Force refetch lyrics from Musixmatch even if they exist in Airtable')
def generate(refetch_lyrics: bool, fast: bool, output: Optional[str], record_id: str):
    """Generate a karaoke video for a single track using Musixmatch synced lyrics."""
    try:
        click.echo(f"🎬 Generating karaoke for record: {record_id}")
        click.echo(f"🎵 Using Musixmatch API for professionally synced lyrics\n")
        
        # Initialize clients
        airtable_client = AirtableClient()
        karaoke_generator = KaraokeGenerator()
        
        # Get record from Airtable
        record = airtable_client.get_record(record_id)
        fields = record.get("fields", {})
        
        # MUSIXMATCH WORKFLOW - This is now the only workflow
        if True:
            click.echo("🎵 Using Musixmatch API for professionally synced lyrics")
            
            # Check if we already have Musixmatch SRT
            musixmatch_srt = fields.get("SRT (Musixmatch)")
            
            if musixmatch_srt and not refetch_lyrics:
                click.echo("✓ Found existing Musixmatch SRT in Airtable")
                srt_content = musixmatch_srt
                
                # Still need to apply offset if specified
                lyrics_offset = fields.get('Lyrics to singing offset (s)')
                if lyrics_offset:
                    if isinstance(lyrics_offset, list) and lyrics_offset:
                        lyrics_offset = float(lyrics_offset[0])
                    else:
                        lyrics_offset = float(lyrics_offset)
                    
                    if lyrics_offset != 0:
                        click.echo(f"⏱️  Applying lyrics offset: {lyrics_offset:+.2f}s")
                        srt_content = shift_srt_timing(srt_content, lyrics_offset)
            else:
                # Fetch from Musixmatch API
                click.echo("📥 Fetching synced lyrics from Musixmatch...")
                
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).parent))
                
                from musixmatch_lyrics import get_synced_lyrics
                import os
                
                # Get track info
                track_name = fields.get('Title') or fields.get('Track')
                artist_name = fields.get('Name (from Artist)')
                album_name = fields.get('Album') or fields.get('Spotify release name')
                
                # Handle list fields
                if isinstance(artist_name, list):
                    artist_name = artist_name[0] if artist_name else None
                if isinstance(album_name, list):
                    album_name = album_name[0] if album_name else None
                
                if not track_name or not artist_name:
                    click.echo("❌ Missing track or artist name in Airtable", err=True)
                    return
                
                # Get Musixmatch API key
                api_key = os.getenv('MUSIXMATCH_API_KEY')
                if not api_key:
                    click.echo("❌ MUSIXMATCH_API_KEY not set in .env", err=True)
                    return
                
                # Get audio duration from GDrive file for accurate matching
                # First check if we already have it cached in Airtable
                audio_duration = fields.get('Duration (GDrive)')
                if isinstance(audio_duration, list) and audio_duration:
                    audio_duration = int(float(audio_duration[0]))
                elif audio_duration:
                    audio_duration = int(float(audio_duration))
                    click.echo(f"✓ Using cached GDrive duration: {audio_duration}s ({audio_duration//60}:{audio_duration%60:02d})")
                else:
                    audio_duration = None
                
                # If not cached, download and get duration from GDrive file
                if not audio_duration:
                    stored_audio_url = airtable_client.get_audio_file_url(record)
                    
                    if stored_audio_url:
                        click.echo(f"🎵 Downloading audio from GDrive to get duration...")
                        try:
                            import tempfile
                            import urllib.request
                            temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                            urllib.request.urlretrieve(stored_audio_url, temp_audio.name)
                            audio_duration = get_audio_duration(temp_audio.name)
                            if audio_duration:
                                click.echo(f"✓ Audio duration from GDrive: {audio_duration}s ({audio_duration//60}:{audio_duration%60:02d})")
                                # Save to Airtable for future use
                                try:
                                    airtable_client.update_record(record_id, {'Duration (GDrive)': audio_duration})
                                    click.echo(f"✓ Saved duration to Airtable")
                                except Exception as e:
                                    click.echo(f"⚠️  Could not save duration: {e}")
                            import os
                            os.unlink(temp_audio.name)
                        except Exception as e:
                            click.echo(f"⚠️  Could not get duration from GDrive audio: {e}")
                
                # Fallback to Spotify duration if GDrive fails
                if not audio_duration:
                    audio_duration = (
                        fields.get('Duration (Spotify)') or
                        fields.get('Duration (from GDrive Audio files)') or
                        fields.get('Duration')
                    )
                    if isinstance(audio_duration, list) and audio_duration:
                        audio_duration = int(float(audio_duration[0]))
                    elif audio_duration:
                        audio_duration = int(float(audio_duration))
                    else:
                        audio_duration = None
                
                # Fetch synced lyrics
                srt_content, metadata = get_synced_lyrics(
                    track_name=track_name,
                    artist_name=artist_name,
                    api_key=api_key,
                    album_name=album_name,
                    output_format='srt',
                    target_duration=audio_duration
                )
                
                if not srt_content:
                    click.echo("❌ Failed to get synced lyrics from Musixmatch", err=True)
                    return
                
                click.echo(f"✓ Retrieved {metadata['line_count']} synced lines from Musixmatch")
                
                # Save to Airtable (WITHOUT offset - we'll apply offset during video generation)
                import re
                lyrics_text = re.sub(r'^\d+\s*$', '', srt_content, flags=re.MULTILINE)
                lyrics_text = re.sub(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\s*$', '', lyrics_text, flags=re.MULTILINE)
                lyrics_text = re.sub(r'\n{3,}', '\n\n', lyrics_text).strip()
                
                airtable_client.update_record(record_id, {
                    'SRT (Musixmatch)': srt_content,
                    'Musixmatch lyrics': lyrics_text
                })
                click.echo("✓ Saved Musixmatch SRT and lyrics to Airtable")
                
                # Now apply lyrics offset for video generation
                lyrics_offset = fields.get('Lyrics to singing offset (s)')
                if lyrics_offset:
                    if isinstance(lyrics_offset, list) and lyrics_offset:
                        lyrics_offset = float(lyrics_offset[0])
                    else:
                        lyrics_offset = float(lyrics_offset)
                    
                    if lyrics_offset != 0:
                        click.echo(f"⏱️  Applying lyrics offset: {lyrics_offset:+.2f}s")
                        # Shift all timestamps in the SRT
                        srt_content = shift_srt_timing(srt_content, lyrics_offset)
            
            # FAST PATH: Skip directly to video generation with Musixmatch SRT
            # No need for Whisper, Lalal.ai, or Genius alignment
            click.echo("\n⚡ Fast path: Skipping Whisper/Lalal.ai, using Musixmatch SRT directly")
            
            # Get video URL for fallback
            video_url = airtable_client.get_video_url(record)
            
            # Import needed modules
            import tempfile
            import urllib.request
            
            # Download video file if available, otherwise use YouTube
            stored_video_url = airtable_client.get_video_file_url(record)
            video_file_path = None
            
            if stored_video_url:
                click.echo(f"💾 Found stored video file in Airtable")
                try:
                    temp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    click.echo(f"📥 Downloading video file from Airtable...")
                    urllib.request.urlretrieve(stored_video_url, temp_video.name)
                    video_file_path = temp_video.name
                    click.echo(f"✓ Using stored video file")
                except Exception as e:
                    click.echo(f"⚠️  Could not download stored video: {e}")
                    video_file_path = None
            
            # Download original audio from Google Drive (REQUIRED)
            stored_audio_url = airtable_client.get_audio_file_url(record)
            
            if not stored_audio_url:
                click.echo(f"❌ No audio file found in Google Drive - cannot proceed with Musixmatch workflow", err=True)
                click.echo(f"   The Musixmatch workflow requires original audio from GDrive", err=True)
                return
            
            click.echo(f"🎵 Found original audio file (Google Drive)")
            try:
                temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                click.echo(f"📥 Downloading original audio from Google Drive...")
                urllib.request.urlretrieve(stored_audio_url, temp_audio.name)
                audio_file_path = temp_audio.name
                click.echo(f"✓ Will use original audio from GDrive")
            except Exception as e:
                click.echo(f"❌ Could not download audio from GDrive: {e}", err=True)
                return
            
            # Generate output path
            import os
            if not output:
                track_name = fields.get('Name', 'karaoke')
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_name = track_name.replace('/', '_').replace(' ', '_')
                output = f"output/{safe_name}_{timestamp}_karaoke.mp4"
            
            os.makedirs(os.path.dirname(output) or '.', exist_ok=True)
            
            # Generate karaoke video (always with GDrive audio)
            if video_file_path:
                # Use stored video file + GDrive audio
                # Generate to temp file first, then replace audio with GDrive audio
                temp_output = output.replace('.mp4', '_temp.mp4')
                success, result = karaoke_generator.generate_karaoke(
                    video_path=video_file_path,
                    srt_content=srt_content,
                    output_path=temp_output,
                    logo_path=karaoke_generator.logo_path,
                    fast_mode=fast
                )
                
                if success:
                    # Replace audio with original audio from GDrive
                    click.echo(f"🎵 Replacing audio with original track from GDrive...")
                    import subprocess
                    
                    # Get audio duration
                    audio_duration = get_audio_duration(audio_file_path)
                    click.echo(f"   Audio duration: {audio_duration}s")
                    
                    # Loop video to match audio duration
                    cmd = [
                        'ffmpeg', '-y',
                        '-stream_loop', '-1',  # Loop video indefinitely
                        '-i', temp_output,  # Video with subtitles
                        '-i', audio_file_path,  # Original audio from GDrive
                        '-map', '0:v:0',  # Take video from first input
                        '-map', '1:a:0',  # Take audio from second input
                        '-c:v', 'libx264',  # Re-encode video (needed for looping)
                        '-preset', 'ultrafast',  # Fast encoding
                        '-crf', '23',  # Good quality
                        '-c:a', 'copy',  # Copy audio (no re-encoding)
                        '-t', str(audio_duration),  # Explicit duration from audio
                        output
                    ]
                    
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True)
                        if result.returncode != 0:
                            click.echo(f"FFmpeg stderr: {result.stderr}")
                            raise subprocess.CalledProcessError(result.returncode, cmd)
                        os.unlink(temp_output)
                        click.echo(f"✓ Audio replaced with original track from GDrive")
                    except subprocess.CalledProcessError as e:
                        click.echo(f"❌ Failed to replace audio: {e}", err=True)
                        return
            else:
                # Download from YouTube but use GDrive audio
                success, result = karaoke_generator.generate_karaoke_from_youtube(
                    youtube_url=video_url,
                    output_path=output,
                    srt_content=srt_content,
                    logo_path=karaoke_generator.logo_path,
                    fast_mode=fast,
                    vocals_file=None,  # No vocal separation needed
                    audio_file=audio_file_path,  # Use GDrive audio!
                    audio_to_video_offset=0,
                    time_offset=time_offset,
                    no_separate_vocals=True,  # Skip vocal separation
                    auto_offset=not no_auto_offset
                )
            
            if success:
                click.echo(f"\n✅ Karaoke video generated successfully!")
                click.echo(f"📹 Output: {output}")
                file_size = os.path.getsize(output) / (1024 * 1024)
                click.echo(f"📊 File size: {file_size:.1f} MB")
                
                # Upload video to Airtable
                click.echo(f"\n📤 Uploading video to Airtable...")
                try:
                    airtable_client.upload_video_file(record_id, output)
                    click.echo(f"✓ Video uploaded to Airtable 'Video file' field")
                except Exception as e:
                    click.echo(f"⚠️  Could not upload video to Airtable: {e}")
            else:
                click.echo(f"\n❌ Failed to generate karaoke video: {result}", err=True)
    
    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
            click.echo(f"Using SRT file: {srt_file}")
            try:
                with open(srt_file, 'r', encoding='utf-8') as f:
                    srt_content = f.read()
            except Exception as e:
                click.echo(f"❌ Error loading SRT file: {e}", err=True)
                return
        elif not use_musixmatch:  # Only do Whisper/Genius workflow if not using Musixmatch
            # Check force retranscribe first
            if force_retranscribe:
                click.echo("🔄 Force retranscribe enabled - ignoring existing SRT")
                srt_content = None
            else:
                # Check if we should use Genius SRT or Whisper SRT
                genius_srt = airtable_client.get_genius_srt(record)
                whisper_srt = airtable_client.get_whisper_srt(record)
                
                # Force re-alignment if requested
                if force_realign_genius and use_genius:
                    click.echo("🔄 Force Genius re-alignment enabled - ignoring existing Genius SRT")
                    genius_srt = None
                
                # If using Genius and we have both Genius lyrics and Whisper SRT, sync them
                if use_genius and not genius_srt and whisper_srt:
                    genius_lyrics_text = fields.get("Genius lyrics")
                    whisper_words_json = fields.get("Whisper words (JSON)")
                    
                    if genius_lyrics_text:
                        click.echo("🎵 Syncing Genius lyrics with Whisper word-level timestamps...")
                        try:
                            # Try to use word-level timestamps if available
                            if whisper_words_json:
                                import json
                                from src.advanced_lyrics_aligner import AdvancedLyricsAligner
                                
                                click.echo("✓ Using advanced word-level alignment (Needleman-Wunsch)")
                                whisper_words = json.loads(whisper_words_json)
                                
                                aligner = AdvancedLyricsAligner()
                                genius_srt, report = aligner.align_lyrics_to_whisper(genius_lyrics_text, whisper_words)
                            
                                click.echo(f"📊 Alignment confidence: {report['summary']['avg_confidence']:.1%}")
                                click.echo(f"   High confidence: {report['summary']['high_confidence_lines']}/{report['summary']['total_lines']} lines")
                                
                                if report['summary']['low_confidence_lines'] > 0:
                                    click.echo(f"   ⚠️  Low confidence: {report['summary']['low_confidence_lines']} lines")
                            else:
                                # Fallback to segment-based alignment
                                click.echo("⚠️  No word timestamps, using segment-based alignment")
                                from src.lyrics_sync import match_lyrics_to_segments
                                import re
                                
                                # Parse Whisper SRT to extract timecodes
                                segments = []
                                srt_blocks = whisper_srt.strip().split('\n\n')
                                for block in srt_blocks:
                                    lines = block.split('\n')
                                    if len(lines) >= 3:
                                        # Parse timestamp line
                                        timestamp_line = lines[1]
                                        match = re.match(r'(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})', timestamp_line)
                                        if match:
                                            h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
                                            start = int(h1)*3600 + int(m1)*60 + int(s1) + int(ms1)/1000
                                            end = int(h2)*3600 + int(m2)*60 + int(s2) + int(ms2)/1000
                                            text = '\n'.join(lines[2:])
                                            segments.append({'start': start, 'end': end, 'text': text})
                                
                                genius_srt = match_lyrics_to_segments(genius_lyrics_text, segments)
                            
                            if genius_srt:
                                click.echo("✓ Successfully synced Genius lyrics with Whisper timecodes!")
                                # Save to Airtable
                                airtable_client.save_srt_only(record_id, genius_srt, srt_type="genius")
                                click.echo("✓ Saved Genius SRT to Airtable")
                                srt_content = genius_srt
                            else:
                                click.echo("⚠️  Sync failed, using Whisper SRT")
                                srt_content = whisper_srt
                        except Exception as e:
                            click.echo(f"⚠️  Error syncing Genius lyrics: {e}")
                            import traceback
                            traceback.print_exc()
                            srt_content = whisper_srt
                    else:
                        click.echo("⚠️  No Genius lyrics found, using Whisper SRT")
                        srt_content = whisper_srt
                else:
                    # Use existing SRT based on use_genius flag
                    if use_genius:
                        # Priority: Genius > Whisper
                        srt_content = airtable_client.get_lyrics_srt(record)
                    else:
                        # Use Whisper SRT only
                        srt_content = airtable_client.get_whisper_srt(record)
        
        # Check for original audio file (Google Drive MP3)
        import os
        import tempfile
        import urllib.request
        
        stored_audio_url = airtable_client.get_audio_file_url(record)
        audio_file_path = None
        audio_to_video_offset = 0.0
        
        if stored_audio_url:
            click.echo(f"🎵 Found original audio file (Google Drive)")
            try:
                temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                click.echo(f"📥 Downloading original audio from Google Drive...")
                urllib.request.urlretrieve(stored_audio_url, temp_audio.name)
                audio_file_path = temp_audio.name
                file_size = os.path.getsize(audio_file_path) / (1024 * 1024)
                click.echo(f"✓ Original audio downloaded: {file_size:.1f} MB")
                
                # Get audio to video offset
                audio_to_video_offset = airtable_client.get_audio_to_video_offset(record)
                if audio_to_video_offset != 0:
                    click.echo(f"⏱️  Audio to video offset: {audio_to_video_offset:+.1f}s")
            except Exception as e:
                click.echo(f"⚠️  Could not download audio from Google Drive: {e}")
                audio_file_path = None
        
        # Check for existing video file in Airtable
        stored_video_url = airtable_client.get_video_file_url(record)
        video_file_path = None
        if stored_video_url:
            click.echo(f"💾 Found stored video file in Airtable")
            # Download video file from Airtable
            try:
                temp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                click.echo(f"📥 Downloading video file from Airtable...")
                urllib.request.urlretrieve(stored_video_url, temp_video.name)
                video_file_path = temp_video.name
                click.echo(f"✓ Using stored video file (skipping YouTube download)")
            except Exception as e:
                click.echo(f"⚠️  Could not download stored video: {e}")
                video_file_path = None
        
        # Check for existing vocal file in Airtable
        stored_vocal_url = airtable_client.get_vocal_file_url(record)
        if stored_vocal_url and not vocals_file:
            click.echo(f"💾 Found stored vocal file in Airtable")
            # Download vocal file from Airtable
            try:
                temp_vocal = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                click.echo(f"📥 Downloading vocal file from Airtable...")
                urllib.request.urlretrieve(stored_vocal_url, temp_vocal.name)
                vocals_file = temp_vocal.name
                click.echo(f"✓ Using stored vocal file (skipping Lalal.ai)")
            except Exception as e:
                click.echo(f"⚠️  Could not download stored vocal: {e}")
                vocals_file = None
        
        # Get language from Airtable (for Whisper transcription)
        # Language can be a list (multi-select) or a string
        language_field = fields.get("Language")
        if isinstance(language_field, list) and language_field:
            language = language_field[0]  # Take first language
            click.echo(f"🌍 Language specified: {language}")
        elif isinstance(language_field, str) and language_field:
            language = language_field
            click.echo(f"🌍 Language specified: {language}")
        else:
            language = None
        
        # Check for Genius lyrics in Airtable or fetch from API
        genius_lyrics_text = fields.get("Genius lyrics")
        
        if not genius_lyrics_text and use_genius:
            # Fetch from Genius API if not in Airtable
            try:
                click.echo("🎵 Genius lyrics not found in Airtable, fetching from API...")
                genius = GeniusLyricsFetcher()
                artist = fields.get("Artist (string)") or fields.get("Name (from Artist)", [""])[0] if isinstance(fields.get("Name (from Artist)"), list) else fields.get("Artist", "")
                title = fields.get("Title") or fields.get("Name", "")
                
                if artist and title:
                    genius_lyrics_text = genius.search_lyrics(artist, title)
                    if genius_lyrics_text:
                        click.echo("✓ Got lyrics from Genius API")
                        # Save to Airtable for future use
                        try:
                            airtable_client.update_record(record_id, {"Genius lyrics": genius_lyrics_text})
                            click.echo("✓ Saved Genius lyrics to Airtable")
                        except Exception as e:
                            click.echo(f"⚠️  Could not save lyrics to Airtable: {e}")
                    else:
                        click.echo("⚠️  Could not find lyrics on Genius")
                else:
                    click.echo("⚠️  Missing artist or title for Genius search")
            except Exception as e:
                click.echo(f"⚠️  Genius API error: {e}")
        elif genius_lyrics_text:
            click.echo(f"✓ Using Genius lyrics from Airtable ({len(genius_lyrics_text)} characters)")
        
        # Use Genius lyrics for sync if available
        if genius_lyrics_text and not srt_content:
            click.echo("🎵 Will sync Genius lyrics with Whisper timing")
        
        # Determine output path
        if not output:
            output_dir = settings.get_output_path()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            track_name = fields.get("Name", record_id).replace(" ", "_")
            output_filename = f"{track_name}_{timestamp}_karaoke.mp4"
            output = str(output_dir / output_filename)
        
        # Generate karaoke (will download from YouTube and transcribe if needed)
        if srt_content:
            click.echo("✓ Using existing SRT content from Airtable")
        else:
            click.echo("⚠️  No SRT content found - will transcribe with Whisper")
        
        # Get time offset from Airtable or detect automatically
        if time_offset is None and not no_auto_offset:
            # Check if offset is stored in Airtable
            stored_offset = airtable_client.get_time_offset(record)
            if stored_offset != 0.0:  # Changed: also use negative offsets
                time_offset = stored_offset
                click.echo(f"💾 Using stored time offset from Airtable: {time_offset:+.1f}s")
            else:
                # Only auto-detect if no SRT exists (first generation)
                if not srt_content:
                    click.echo("🔍 Auto-detecting singing start time...")
                    # Will be detected during video generation
                    time_offset = None
                else:
                    # SRT exists but no offset set, use 0
                    time_offset = 0.0
        elif time_offset is None:
            time_offset = 0.0
        
        # Apply time offset if specified
        if time_offset and time_offset != 0.0 and srt_content:
            click.echo(f"⏱️  Applying time offset: {time_offset:+.1f} seconds")
            srt_content = karaoke_generator.adjust_srt_timing(srt_content, time_offset)
        
        if fast:
            click.echo("⚡ FAST MODE enabled - using ultrafast encoding preset")
        
        # Separate vocals by default (use Lalal.ai), unless --no-separate-vocals is set
        separate_vocals = not no_separate_vocals
        
        if separate_vocals:
            click.echo("🎵 Vocal separation enabled (Lalal.ai)")
        else:
            click.echo("⚠️  Vocal separation disabled - using full audio")
        
        success, result = karaoke_generator.generate_karaoke_from_youtube(
            youtube_url=video_url,
            output_path=output,
            srt_content=srt_content,
            auto_transcribe=not bool(srt_content or srt_file),  # Skip transcription if we have SRT
            separate_vocals=separate_vocals,
            genius_lyrics=genius_lyrics_text,
            vocals_file=vocals_file,
            auto_detect_offset=time_offset is None and not no_auto_offset,
            fast_mode=fast,
            video_file=video_file_path,
            airtable_record_id=record_id,
            audio_file=audio_file_path,
            audio_to_video_offset=audio_to_video_offset,
            language=language
        )
        
        if success:
            # Save SRT and detected offset to Airtable (only if SRT was generated)
            if isinstance(result, dict):
                final_srt = result.get('srt_content')
                detected_offset = result.get('detected_offset', 0.0)
                vocal_path = result.get('vocal_path')
                
                # SRT is already saved by transcribe_video (both Whisper and Genius if applicable)
                # Only save offset if detected
                if final_srt and not srt_content:
                    if detected_offset != 0.0:
                        click.echo(f"💾 Saving offset ({detected_offset:.1f}s) to Airtable...")
                        airtable_client.save_offset_only(record_id, detected_offset)
                        click.echo("✅ Offset saved")
                    
                    # Note about vocal file upload
                    if vocal_path and not stored_vocal_url:
                        click.echo(f"💾 Vocal file info:")
                        airtable_client.upload_file_to_airtable(record_id, vocal_path, "Vocal")
                elif srt_content:
                    click.echo("ℹ️  Using existing SRT from Airtable (not overwriting)")
            
            click.echo(f"✅ Karaoke video generated successfully: {output if isinstance(result, dict) else result}")
            
            # Send WhatsApp notification
            try:
                from notify import send_notification
                # Get track info from Airtable
                airtable = AirtableClient()
                record = airtable.get_record(record_id)
                if record:
                    fields = record.get('fields', {})
                    artist = fields.get('Artist', 'Unknown Artist')
                    if isinstance(artist, list):
                        artist = artist[0] if artist else 'Unknown Artist'
                    track = fields.get('Track', 'Unknown Track')
                    send_notification(f"✅ Karaoke complete: {artist} - {track}")
            except Exception as notify_error:
                click.echo(f"⚠️  Could not send notification: {notify_error}")
        else:
            click.echo(f"❌ Failed to generate karaoke: {result}", err=True)
            
    except Exception as e:
        click.echo(f"❌ Error: {str(e)}", err=True)


@cli.command()
@click.option(
    '--genre',
    '-g',
    multiple=True,
    help='Filter by genre (can be specified multiple times)'
)
@click.option(
    '--event-type',
    '-e',
    multiple=True,
    help='Filter by event type (can be specified multiple times)'
)
@click.option(
    '--decade',
    '-d',
    multiple=True,
    help='Filter by decade (can be specified multiple times)'
)
@click.option(
    '--max-records',
    '-n',
    type=int,
    help='Maximum number of records to process'
)
@click.option(
    '--dry-run',
    is_flag=True,
    help='Show what would be processed without actually generating videos'
)
@click.option(
    '--separate-vocals',
    is_flag=True,
    help='Separate vocals with AI for better transcription accuracy (adds 2-4 min per video)'
)
def batch(
    genre: tuple,
    event_type: tuple,
    decade: tuple,
    max_records: Optional[int],
    dry_run: bool,
    separate_vocals: bool
):
    """Generate karaoke videos for multiple tracks matching filters."""
    try:
        click.echo("🔍 Fetching records from Airtable...")
        
        # Initialize clients
        airtable_client = AirtableClient()
        karaoke_generator = KaraokeGenerator()
        
        # Convert tuples to lists
        genres = list(genre) if genre else None
        event_types = list(event_type) if event_type else None
        decades = list(decade) if decade else None
        
        # Get records
        records = airtable_client.get_records_by_filters(
            genres=genres,
            event_types=event_types,
            decades=decades,
            max_records=max_records
        )
        
        if not records:
            click.echo("ℹ️  No records found matching filters")
            return
        
        click.echo(f"📋 Found {len(records)} record(s) matching filters")
        
        if dry_run:
            click.echo("\n🔍 Dry run - would process:")
            for record in records:
                fields = record.get("fields", {})
                name = fields.get("Name", record["id"])
                click.echo(f"  - {name} ({record['id']})")
            return
        
        # Process records
        processed = 0
        failed = 0
        
        with click.progressbar(
            records,
            label='Processing records',
            show_pos=True
        ) as bar:
            for record in bar:
                try:
                    record_id = record["id"]
                    fields = record.get("fields", {})
                    name = fields.get("Name", record_id)
                    
                    # Get video URL and SRT content
                    video_url = airtable_client.get_video_url(record)
                    srt_content = airtable_client.get_lyrics_srt(record)
                    
                    if not video_url or not srt_content:
                        click.echo(f"\n⚠️  Skipping {name}: missing video or lyrics")
                        failed += 1
                        continue
                    
                    # Generate output path
                    output_dir = settings.get_output_path()
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    track_name = name.replace(" ", "_")
                    output_filename = f"{track_name}_{timestamp}_karaoke.mp4"
                    output_path = output_dir / output_filename
                    
                    # Generate karaoke (will download from YouTube and transcribe if needed)
                    success, result = karaoke_generator.generate_karaoke_from_youtube(
                        youtube_url=video_url,
                        output_path=str(output_path),
                        srt_content=srt_content,
                        auto_transcribe=True,
                        separate_vocals=separate_vocals
                    )
                    
                    if success:
                        airtable_client.update_record(
                            record_id,
                            {"Karaoke Video Path": str(output_path)}
                        )
                        processed += 1
                    else:
                        click.echo(f"\n❌ Failed to process {name}: {result}")
                        failed += 1
                        
                except Exception as e:
                    click.echo(f"\n❌ Error processing record: {str(e)}")
                    failed += 1
        
        click.echo(f"\n✅ Batch generation completed:")
        click.echo(f"   - Processed: {processed}")
        click.echo(f"   - Failed: {failed}")
        click.echo(f"   - Total: {len(records)}")
        
        # Send WhatsApp notification for batch completion
        try:
            from notify import send_notification
            send_notification(f"✅ Batch complete: {processed}/{len(records)} videos generated ({failed} failed)")
        except Exception as notify_error:
            click.echo(f"⚠️  Could not send notification: {notify_error}")
        
    except Exception as e:
        click.echo(f"❌ Error: {str(e)}", err=True)


@cli.command()
def config():
    """Show current configuration."""
    click.echo("📋 Current Configuration:")
    click.echo(f"   Airtable Base ID: {settings.airtable_base_id}")
    click.echo(f"   Airtable Table: {settings.airtable_table_name}")
    click.echo(f"   Video Size: {settings.karaoke_video_width}x{settings.karaoke_video_height}")
    click.echo(f"   Output Directory: {settings.output_dir}")
    click.echo(f"   Font Path: {settings.karaoke_font_path}")
    click.echo(f"   Logo Path: {settings.karaoke_logo_path}")


if __name__ == '__main__':
    cli()
