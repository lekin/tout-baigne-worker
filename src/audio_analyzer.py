"""Audio analysis utilities for detecting singing start time."""
import subprocess
import json
import tempfile
import os
from typing import Optional, Tuple


class AudioAnalyzer:
    """Analyze audio to detect when singing starts."""
    
    @staticmethod
    def detect_singing_start(video_path: str, vocals_path: Optional[str] = None) -> float:
        """
        Detect when singing starts in a video/audio file.
        
        Uses multiple methods:
        1. Whisper transcription timing (most reliable)
        2. Audio energy analysis (fallback)
        
        Args:
            video_path: Path to video file
            vocals_path: Optional path to separated vocals (more accurate)
            
        Returns:
            Time offset in seconds when singing starts
        """
        # Method 1: Use Whisper to detect first speech
        offset = AudioAnalyzer._detect_with_whisper(vocals_path or video_path)
        
        if offset is not None and offset > 0:
            return offset
        
        # Method 2: Fallback to audio energy analysis
        return AudioAnalyzer._detect_with_energy(vocals_path or video_path)
    
    @staticmethod
    def _detect_with_whisper(audio_path: str) -> Optional[float]:
        """
        Use Whisper to detect first speech/singing.
        
        Args:
            audio_path: Path to audio file
            
        Returns:
            Start time in seconds, or None if detection fails
        """
        try:
            from faster_whisper import WhisperModel
            
            # Use tiny model for fast detection
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            
            # Transcribe only first 60 seconds
            segments, info = model.transcribe(
                audio_path,
                language="en",
                vad_filter=True,  # Voice Activity Detection
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    threshold=0.5
                )
            )
            
            # Get first segment with actual speech
            for segment in segments:
                # Skip very short segments (likely noise)
                if segment.end - segment.start > 0.5:
                    # Round to nearest 0.5 second
                    start_time = round(segment.start * 2) / 2
                    print(f"🎤 Detected singing start at {start_time}s (Whisper)")
                    return start_time
            
            return None
            
        except Exception as e:
            print(f"⚠️  Whisper detection failed: {e}")
            return None
    
    @staticmethod
    def _detect_with_energy(audio_path: str, threshold: float = 0.1) -> float:
        """
        Detect singing start using audio energy analysis.
        
        Args:
            audio_path: Path to audio file
            threshold: Energy threshold (0-1)
            
        Returns:
            Start time in seconds
        """
        try:
            # Use FFmpeg to analyze audio energy
            cmd = [
                'ffmpeg',
                '-i', audio_path,
                '-af', f'silencedetect=noise=-30dB:d=0.5',
                '-f', 'null',
                '-'
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                stderr=subprocess.STDOUT
            )
            
            # Parse silence detection output
            lines = result.stdout.split('\n')
            for line in lines:
                if 'silence_end' in line:
                    # Extract time from: [silencedetect @ ...] silence_end: 10.5
                    parts = line.split('silence_end:')
                    if len(parts) > 1:
                        try:
                            end_time = float(parts[1].split('|')[0].strip())
                            # Round to nearest 0.5 second
                            start_time = round(end_time * 2) / 2
                            print(f"🎤 Detected singing start at {start_time}s (Energy)")
                            return start_time
                        except:
                            continue
            
            # Default: assume singing starts at 0
            return 0.0
            
        except Exception as e:
            print(f"⚠️  Energy detection failed: {e}")
            return 0.0
    
    @staticmethod
    def detect_intro_duration(video_path: str, vocals_path: Optional[str] = None) -> Tuple[float, str]:
        """
        Detect intro duration and provide confidence level.
        
        Args:
            video_path: Path to video file
            vocals_path: Optional path to separated vocals
            
        Returns:
            Tuple of (offset_seconds, confidence_level)
            confidence_level: "high", "medium", "low"
        """
        offset = AudioAnalyzer.detect_singing_start(video_path, vocals_path)
        
        # Determine confidence based on offset value
        if offset == 0:
            confidence = "low"  # Couldn't detect, defaulting to 0
        elif offset < 2:
            confidence = "medium"  # Very short intro, might be noise
        else:
            confidence = "high"  # Clear intro detected
        
        return offset, confidence


def analyze_and_suggest_offset(video_path: str, vocals_path: Optional[str] = None) -> dict:
    """
    Analyze audio and suggest time offset with detailed info.
    
    Args:
        video_path: Path to video file
        vocals_path: Optional path to separated vocals
        
    Returns:
        Dict with offset, confidence, and recommendation
    """
    analyzer = AudioAnalyzer()
    offset, confidence = analyzer.detect_intro_duration(video_path, vocals_path)
    
    result = {
        "offset_seconds": offset,
        "confidence": confidence,
        "recommendation": "",
        "auto_apply": False
    }
    
    if confidence == "high":
        result["recommendation"] = f"Strong detection: Apply {offset}s offset"
        result["auto_apply"] = True
    elif confidence == "medium":
        result["recommendation"] = f"Moderate detection: Consider {offset}s offset"
        result["auto_apply"] = offset > 3  # Only auto-apply if significant
    else:
        result["recommendation"] = "No clear intro detected, using 0s offset"
        result["auto_apply"] = True
    
    return result
