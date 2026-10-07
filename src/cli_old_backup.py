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
