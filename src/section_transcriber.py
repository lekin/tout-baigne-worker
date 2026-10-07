"""Section-based transcription using Genius lyrics structure."""
import re
from typing import List, Dict, Optional, Tuple
from faster_whisper import WhisperModel


class SectionTranscriber:
    """Transcribe audio section-by-section using structured Genius lyrics."""
    
    def __init__(self, model_size: str = "large-v3", device: str = "cpu", compute_type: str = "int8"):
        """Initialize Whisper model for section-based transcription."""
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
    
    @staticmethod
    def parse_sections(lyrics: str) -> List[Dict[str, str]]:
        """
        Parse Genius lyrics into sections.
        
        Args:
            lyrics: Full lyrics text with section headers like [Verse], [Chorus]
        
        Returns:
            List of dicts with 'type' and 'text' keys
        """
        sections = []
        current_section = None
        current_text = []
        
        for line in lyrics.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Check if line is a section header [Verse], [Chorus], etc.
            section_match = re.match(r'\[([^\]]+)\]', line)
            
            if section_match:
                # Save previous section if exists
                if current_section:
                    # Don't include section header - just the lyrics text
                    sections.append({
                        'type': current_section,
                        'text': '\n'.join(current_text).strip()
                    })
                
                # Start new section
                current_section = section_match.group(1)
                current_text = []
            else:
                # Add line to current section
                if current_section:
                    current_text.append(line)
        
        # Add final section
        if current_section and current_text:
            # Don't include section header - just the lyrics text
            sections.append({
                'type': current_section,
                'text': '\n'.join(current_text).strip()
            })
        
        return sections
    
    def estimate_section_boundaries(
        self,
        audio_duration: float,
        sections: List[Dict[str, str]]
    ) -> List[Tuple[float, float]]:
        """
        Estimate time boundaries for each section based on text length.
        
        Args:
            audio_duration: Total audio duration in seconds
            sections: List of section dicts
        
        Returns:
            List of (start_time, end_time) tuples for each section
        """
        # Calculate relative lengths based on character count
        total_chars = sum(len(s['text']) for s in sections)
        
        if total_chars == 0:
            # Fallback: equal distribution
            section_duration = audio_duration / len(sections)
            return [(i * section_duration, (i + 1) * section_duration) 
                    for i in range(len(sections))]
        
        # Distribute time proportionally to text length
        boundaries = []
        current_time = 0.0
        
        for section in sections:
            section_chars = len(section['text'])
            section_duration = (section_chars / total_chars) * audio_duration
            
            start_time = current_time
            end_time = current_time + section_duration
            boundaries.append((start_time, end_time))
            
            current_time = end_time
        
        return boundaries
    
    def transcribe_section(
        self,
        audio_path: str,
        start_time: float,
        end_time: float,
        section_lyrics: str,
        language: str = "en"
    ) -> List[Dict]:
        """
        Transcribe a specific section of audio with section-specific prompt.
        
        Args:
            audio_path: Path to audio file
            start_time: Section start time in seconds
            end_time: Section end time in seconds
            section_lyrics: Lyrics for this section (used as prompt)
            language: Language code
        
        Returns:
            List of segment dicts with word-level timestamps
        """
        # Transcribe with section-specific prompt
        segments, info = self.model.transcribe(
            audio_path,
            task="transcribe",
            language=language,
            vad_filter=False,
            beam_size=8,  # High beam for better coverage
            word_timestamps=True,
            temperature=0.0,
            initial_prompt=section_lyrics,  # Section-specific prompt
            # Baseline thresholds
            condition_on_previous_text=False,
            no_speech_threshold=0.75,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            # Clip to section boundaries
            clip_timestamps=f"{start_time},{end_time}"
        )
        
        # Convert to our format
        segments_list = []
        for seg in segments:
            # Only include segments within our time range
            if seg.start >= start_time and seg.end <= end_time:
                seg_dict = {
                    'start': seg.start,
                    'end': seg.end,
                    'text': seg.text,
                    'words': []
                }
                
                if hasattr(seg, 'words') and seg.words:
                    for word in seg.words:
                        seg_dict['words'].append({
                            'word': word.word,
                            'start': word.start,
                            'end': word.end,
                            'probability': getattr(word, 'probability', 1.0)
                        })
                
                segments_list.append(seg_dict)
        
        return segments_list
    
    def transcribe_with_sections(
        self,
        audio_path: str,
        genius_lyrics: str,
        audio_duration: float,
        language: str = "en"
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Transcribe entire audio using section-based approach.
        
        Args:
            audio_path: Path to audio file
            genius_lyrics: Full Genius lyrics with section headers
            audio_duration: Total audio duration in seconds
            language: Language code
        
        Returns:
            Tuple of (all_segments, sections_info)
        """
        print("📑 Parsing lyrics into sections...")
        sections = self.parse_sections(genius_lyrics)
        
        if not sections:
            print("⚠️  No sections found, falling back to full transcription")
            return self._transcribe_full(audio_path, genius_lyrics, language)
        
        print(f"✓ Found {len(sections)} sections:")
        for i, section in enumerate(sections, 1):
            print(f"   [{i}] {section['type']}: {len(section['text'])} chars")
        
        print("\n📊 Estimating section boundaries...")
        boundaries = self.estimate_section_boundaries(audio_duration, sections)
        
        all_segments = []
        sections_info = []
        
        for i, (section, (start, end)) in enumerate(zip(sections, boundaries), 1):
            print(f"\n🎤 Transcribing section {i}/{len(sections)}: {section['type']} ({start:.1f}s - {end:.1f}s)")
            
            section_segments = self.transcribe_section(
                audio_path,
                start,
                end,
                section['text'],
                language
            )
            
            print(f"   ✓ Captured {len(section_segments)} segments")
            
            all_segments.extend(section_segments)
            sections_info.append({
                'type': section['type'],
                'start': start,
                'end': end,
                'lyrics': section['text'],
                'segments': len(section_segments)
            })
        
        print(f"\n✅ Section-based transcription complete: {len(all_segments)} total segments")
        
        return all_segments, sections_info
    
    def _transcribe_full(
        self,
        audio_path: str,
        lyrics: str,
        language: str
    ) -> Tuple[List[Dict], List[Dict]]:
        """Fallback: transcribe full audio without sections."""
        segments, info = self.model.transcribe(
            audio_path,
            task="transcribe",
            language=language,
            vad_filter=False,
            beam_size=5,
            word_timestamps=True,
            temperature=0.0,
            initial_prompt=lyrics,
            condition_on_previous_text=False,
            no_speech_threshold=0.75,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0
        )
        
        segments_list = []
        for seg in segments:
            seg_dict = {
                'start': seg.start,
                'end': seg.end,
                'text': seg.text,
                'words': []
            }
            
            if hasattr(seg, 'words') and seg.words:
                for word in seg.words:
                    seg_dict['words'].append({
                        'word': word.word,
                        'start': word.start,
                        'end': word.end,
                        'probability': getattr(word, 'probability', 1.0)
                    })
            
            segments_list.append(seg_dict)
        
        return segments_list, []
