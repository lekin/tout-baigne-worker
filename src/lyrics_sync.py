"""Sync Genius lyrics with Whisper timestamps for accurate karaoke."""
from typing import List, Tuple
from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    import re
    import unicodedata
    
    # Remove accents (important for French)
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    
    # Lowercase
    text = text.lower()
    
    # Remove common filler words that Whisper might transcribe differently
    fillers = ['yeah', 'oh', 'ah', 'hey', 'yo', 'uh', 'um']
    for filler in fillers:
        text = re.sub(rf'\b{filler}\b', '', text)
    
    # Remove punctuation
    text = re.sub(r'[^\w\s]', '', text)
    
    # Normalize spaces
    text = ' '.join(text.split())
    
    return text


def extract_word_timestamps(whisper_segments: List[dict]) -> List[dict]:
    """Extract word-level timestamps from Whisper segments if available."""
    words = []
    for seg in whisper_segments:
        # Check if segment has word-level timestamps
        if 'words' in seg and seg['words']:
            for word_info in seg['words']:
                words.append({
                    'text': normalize_text(word_info.get('word', '')),
                    'start': word_info.get('start', seg['start']),
                    'end': word_info.get('end', seg['end'])
                })
        else:
            # Fallback: use segment-level timing
            seg_words = normalize_text(seg.get('text', '')).split()
            seg_duration = seg['end'] - seg['start']
            time_per_word = seg_duration / max(len(seg_words), 1)
            
            for i, word in enumerate(seg_words):
                words.append({
                    'text': word,
                    'start': seg['start'] + i * time_per_word,
                    'end': seg['start'] + (i + 1) * time_per_word
                })
    
    return words


def match_lyrics_to_segments(
    genius_lyrics: str,
    whisper_segments: List[dict]
) -> str:
    """
    Match Genius lyrics (accurate text) with Whisper segments (accurate timing).
    
    Uses word-level timestamps when available for better synchronization.
    
    Args:
        genius_lyrics: Clean lyrics from Genius
        whisper_segments: Segments from Whisper with timing
        
    Returns:
        SRT formatted string with Genius lyrics and Whisper timing
    """
    # Try to extract word-level timestamps
    whisper_words = extract_word_timestamps(whisper_segments)
    
    if len(whisper_words) > 10:  # If we have good word-level data
        print(f"🎯 Using word-level timestamps ({len(whisper_words)} words detected)")
        return match_with_word_timing(genius_lyrics, whisper_words, whisper_segments)
    else:
        print("📝 Using segment-level timing (no word timestamps available)")
        return match_with_segment_timing(genius_lyrics, whisper_segments)


def match_with_word_timing(
    genius_lyrics: str,
    whisper_words: List[dict],
    whisper_segments: List[dict]
) -> str:
    """Match using word-level timestamps for better accuracy."""
    from difflib import SequenceMatcher
    
    genius_lines = [line.strip() for line in genius_lyrics.split('\n') if line.strip()]
    srt_lines = []
    word_idx = 0
    
    for line_num, genius_line in enumerate(genius_lines, 1):
        line_words = normalize_text(genius_line).split()
        
        if not line_words or word_idx >= len(whisper_words):
            continue
        
        # Find best matching window in whisper_words
        best_score = 0
        best_start_idx = word_idx
        window_size = len(line_words)
        
        # Search in a reasonable range
        search_range = min(word_idx + 50, len(whisper_words))
        
        for i in range(word_idx, search_range):
            if i + window_size > len(whisper_words):
                break
            
            window = whisper_words[i:i + window_size]
            window_text = ' '.join(w['text'] for w in window)
            line_text = ' '.join(line_words)
            
            score = SequenceMatcher(None, line_text, window_text).ratio()
            
            if score > best_score:
                best_score = score
                best_start_idx = i
        
        # Use the best match
        if best_score > 0.3:  # Reasonable threshold
            matched_words = whisper_words[best_start_idx:best_start_idx + window_size]
            start_time = matched_words[0]['start']
            end_time = matched_words[-1]['end']
            word_idx = best_start_idx + window_size
        else:
            # Fallback: use current position
            if word_idx < len(whisper_words):
                start_time = whisper_words[word_idx]['start']
                end_time = whisper_words[min(word_idx + window_size, len(whisper_words) - 1)]['end']
                word_idx += window_size
            else:
                start_time = whisper_segments[-1]['end'] if whisper_segments else 0
                end_time = start_time + 3
        
        # Format SRT entry
        start_srt = seconds_to_srt_timestamp(start_time)
        end_srt = seconds_to_srt_timestamp(end_time)
        
        srt_lines.append(f"{line_num}")
        srt_lines.append(f"{start_srt} --> {end_srt}")
        srt_lines.append(genius_line)
        srt_lines.append("")
    
    return '\n'.join(srt_lines)


def match_with_segment_timing(
    genius_lyrics: str,
    whisper_segments: List[dict]
) -> str:
    """Original matching logic using segment-level timing."""
    # Split Genius lyrics into lines
    genius_lines = [line.strip() for line in genius_lyrics.split('\n') if line.strip()]
    
    # Extract text from Whisper segments
    whisper_text = ' '.join(seg['text'].strip() for seg in whisper_segments)
    
    # Normalize both for comparison
    genius_normalized = normalize_text(' '.join(genius_lines))
    whisper_normalized = normalize_text(whisper_text)
    
    # Check similarity
    similarity = SequenceMatcher(None, genius_normalized, whisper_normalized).ratio()
    print(f"📊 Lyrics similarity: {similarity*100:.1f}%")
    
    # Even with low similarity, try to sync using timing
    if similarity < 0.5:
        print(f"⚠️  Low text similarity ({similarity*100:.1f}%) but will use Whisper timing with Genius lyrics")
        print("This gives accurate lyrics with approximate timing from Whisper")
    
    # Build SRT with Genius lyrics and Whisper timing
    srt_lines = []
    
    # If we have very few Whisper segments compared to Genius lines, distribute evenly
    if len(whisper_segments) < len(genius_lines) * 0.3:
        print("⚠️  Too few Whisper segments - using time-based distribution")
        total_duration = whisper_segments[-1]['end'] if whisper_segments else 200
        time_per_line = total_duration / len(genius_lines)
        
        for line_num, genius_line in enumerate(genius_lines, 1):
            start_time = (line_num - 1) * time_per_line
            end_time = line_num * time_per_line
            
            start_srt = seconds_to_srt_timestamp(start_time)
            end_srt = seconds_to_srt_timestamp(end_time)
            
            srt_lines.append(f"{line_num}")
            srt_lines.append(f"{start_srt} --> {end_srt}")
            srt_lines.append(genius_line)
            srt_lines.append("")
        
        return '\n'.join(srt_lines)
    
    # Try to match Genius lines to Whisper segments
    whisper_idx = 0
    
    for line_num, genius_line in enumerate(genius_lines, 1):
        if whisper_idx >= len(whisper_segments):
            # Use remaining time for rest of lyrics
            if whisper_segments:
                last_end = whisper_segments[-1]['end']
                remaining_lines = len(genius_lines) - line_num + 1
                time_per_line = 3  # Default 3 seconds per line
                
                start_time = last_end + (line_num - len(whisper_segments)) * time_per_line
                end_time = start_time + time_per_line
                
                start_srt = seconds_to_srt_timestamp(start_time)
                end_srt = seconds_to_srt_timestamp(end_time)
                
                srt_lines.append(f"{line_num}")
                srt_lines.append(f"{start_srt} --> {end_srt}")
                srt_lines.append(genius_line)
                srt_lines.append("")
            continue
            
        # Find best matching segment for this line
        line_words = normalize_text(genius_line).split()
        words_to_match = len(line_words)
        
        # Get timing from corresponding Whisper segments
        start_time = whisper_segments[whisper_idx]['start']
        
        # Advance through segments to cover this line
        words_covered = 0
        end_idx = whisper_idx
        
        while words_covered < words_to_match and end_idx < len(whisper_segments):
            seg_words = len(normalize_text(whisper_segments[end_idx]['text']).split())
            words_covered += seg_words
            end_idx += 1
        
        end_time = whisper_segments[min(end_idx - 1, len(whisper_segments) - 1)]['end']
        
        # Format timestamps
        start_srt = seconds_to_srt_timestamp(start_time)
        end_srt = seconds_to_srt_timestamp(end_time)
        
        # Add SRT entry with GENIUS lyrics (accurate) and WHISPER timing
        srt_lines.append(f"{line_num}")
        srt_lines.append(f"{start_srt} --> {end_srt}")
        srt_lines.append(genius_line)  # Original Genius lyrics
        srt_lines.append("")
        
        whisper_idx = end_idx
    
    return '\n'.join(srt_lines)


def seconds_to_srt_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
