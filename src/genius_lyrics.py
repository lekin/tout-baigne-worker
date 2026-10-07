"""Genius API integration for fetching accurate lyrics."""
import lyricsgenius
from typing import Optional
from src.config import settings


class GeniusLyricsFetcher:
    """Fetches lyrics from Genius API."""
    
    def __init__(self, api_token: Optional[str] = None):
        """
        Initialize Genius API client.
        
        Args:
            api_token: Genius API token (uses config if not provided)
        """
        token = api_token or settings.genius_api_token
        if not token:
            raise ValueError("Genius API token not configured")
        
        self.genius = lyricsgenius.Genius(
            token,
            verbose=False,
            remove_section_headers=False,  # Keep section headers [Verse], [Chorus], etc.
            skip_non_songs=True,
            excluded_terms=["(Remix)", "(Live)"],
            timeout=30,  # Increase timeout to 30 seconds
            retries=3  # Retry up to 3 times
        )
    
    def search_lyrics(self, artist: str, title: str) -> Optional[str]:
        """
        Search for song lyrics on Genius.
        
        Args:
            artist: Artist name
            title: Song title
            
        Returns:
            Lyrics text or None if not found
        """
        import time
        
        # Try multiple search strategies
        search_attempts = [
            (title, artist),  # Full search
            (title.split('(')[0].strip(), artist),  # Remove parentheses
            (title, None),  # Title only
        ]
        
        for attempt_num, (search_title, search_artist) in enumerate(search_attempts, 1):
            try:
                if attempt_num > 1:
                    print(f"🔍 Trying alternative search (attempt {attempt_num})...")
                else:
                    print(f"🔍 Searching Genius for: {search_artist} - {search_title}" if search_artist else f"🔍 Searching Genius for: {search_title}")
                
                song = self.genius.search_song(search_title, search_artist)
                
                if song and song.lyrics:
                    print(f"✓ Found lyrics on Genius!")
                    # Clean up lyrics
                    lyrics = song.lyrics
                    
                    # Remove common artifacts
                    artifacts = [
                        "EmbedShare URLCopyEmbedCopy",
                        "Embed",
                        "You might also like",
                        "See Kesha Live",
                        "Get tickets as low as"
                    ]
                    for artifact in artifacts:
                        lyrics = lyrics.replace(artifact, "")
                    
                    # Fix parentheses with newlines inside them
                    # Replace newlines within parentheses with spaces
                    import re
                    # Match parentheses with content including newlines
                    def fix_parentheses(match):
                        content = match.group(1)
                        # Replace newlines with spaces inside parentheses
                        fixed = content.replace('\n', ' ').strip()
                        return f"({fixed})"
                    
                    lyrics = re.sub(r'\(([^)]*)\)', fix_parentheses, lyrics)
                    
                    # Remove extra whitespace
                    lyrics = "\n".join(line.strip() for line in lyrics.split("\n") if line.strip())
                    
                    return lyrics
                    
                # Wait before retry
                if attempt_num < len(search_attempts):
                    time.sleep(1)
                    
            except Exception as e:
                print(f"⚠️  Search attempt {attempt_num} failed: {e}")
                if attempt_num < len(search_attempts):
                    time.sleep(2)
                continue
        
        print("⚠️  Could not find lyrics on Genius after all attempts")
        return None
    
    def lyrics_to_simple_srt(self, lyrics: str, duration: float) -> str:
        """
        Convert plain lyrics to simple SRT format with even timing.
        
        Args:
            lyrics: Plain text lyrics
            duration: Total duration of the song in seconds
            
        Returns:
            SRT formatted string
        """
        lines = [line.strip() for line in lyrics.split("\n") if line.strip()]
        
        if not lines:
            return ""
        
        # Calculate time per line
        time_per_line = duration / len(lines)
        
        srt_lines = []
        for i, line in enumerate(lines, start=1):
            start_time = (i - 1) * time_per_line
            end_time = i * time_per_line
            
            # Format timestamps
            start_srt = self._seconds_to_srt_timestamp(start_time)
            end_srt = self._seconds_to_srt_timestamp(end_time)
            
            # Add SRT entry
            srt_lines.append(f"{i}")
            srt_lines.append(f"{start_srt} --> {end_srt}")
            srt_lines.append(line)
            srt_lines.append("")  # Blank line
        
        return "\n".join(srt_lines)
    
    @staticmethod
    def _seconds_to_srt_timestamp(seconds: float) -> str:
        """Convert seconds to SRT timestamp format."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
