"""
Advanced lyrics alignment using Needleman-Wunsch dynamic programming.
Aligns Genius lyrics to Whisper word timestamps with confidence scoring.
"""

import re
import json
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import unicodedata


@dataclass
class WordTimestamp:
    """Word-level timestamp from Whisper."""
    word: str
    start: float
    end: float
    confidence: float = 1.0


@dataclass
class AlignedLine:
    """Aligned subtitle line with confidence."""
    text: str
    start: float
    end: float
    confidence: float
    matched_words: int
    total_words: int
    notes: List[str]


class TokenNormalizer:
    """Normalize tokens for alignment."""
    
    # Common contractions to expand
    CONTRACTIONS = {
        "i'm": "i am",
        "you're": "you are",
        "he's": "he is",
        "she's": "she is",
        "it's": "it is",
        "we're": "we are",
        "they're": "they are",
        "i've": "i have",
        "you've": "you have",
        "we've": "we have",
        "they've": "they have",
        "i'll": "i will",
        "you'll": "you will",
        "he'll": "he will",
        "she'll": "she will",
        "we'll": "we will",
        "they'll": "they will",
        "i'd": "i would",
        "you'd": "you would",
        "he'd": "he would",
        "she'd": "she would",
        "we'd": "we would",
        "they'd": "they would",
        "won't": "will not",
        "can't": "cannot",
        "don't": "do not",
        "doesn't": "does not",
        "didn't": "did not",
        "isn't": "is not",
        "aren't": "are not",
        "wasn't": "was not",
        "weren't": "were not",
        "hasn't": "has not",
        "haven't": "have not",
        "hadn't": "had not",
        "shouldn't": "should not",
        "wouldn't": "would not",
        "couldn't": "could not",
        "let's": "let us",
        "that's": "that is",
        "who's": "who is",
        "what's": "what is",
        "where's": "where is",
        "when's": "when is",
        "why's": "why is",
        "how's": "how is",
    }
    
    # Number word mappings
    NUMBER_WORDS = {
        '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
        '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine',
        '10': 'ten'
    }
    
    @staticmethod
    def normalize(text: str) -> str:
        """Normalize text for alignment."""
        # Lowercase
        text = text.lower()
        
        # Remove accents
        text = ''.join(
            c for c in unicodedata.normalize('NFD', text)
            if unicodedata.category(c) != 'Mn'
        )
        
        # Normalize numbers to words (1 2 3 → one two three)
        # This helps match "1 2 3" with "one two three"
        for digit, word in TokenNormalizer.NUMBER_WORDS.items():
            text = re.sub(r'\b' + digit + r'\b', word, text)
        
        # Expand contractions
        for contraction, expansion in TokenNormalizer.CONTRACTIONS.items():
            text = re.sub(r'\b' + contraction + r'\b', expansion, text)
        
        # Remove punctuation except apostrophes in words
        text = re.sub(r"[^\w\s']", ' ', text)
        
        # Remove standalone apostrophes
        text = re.sub(r"\s'\s", ' ', text)
        text = re.sub(r"^'|'$", '', text)
        
        # Normalize whitespace
        text = ' '.join(text.split())
        
        return text
    
    @staticmethod
    def tokenize(text: str) -> List[str]:
        """Tokenize normalized text."""
        normalized = TokenNormalizer.normalize(text)
        return [w for w in normalized.split() if w]


class NeedlemanWunschAligner:
    """Monotonic Needleman-Wunsch alignment for lyrics."""
    
    def __init__(self, match_score: float = 2.0, mismatch_penalty: float = -1.0, 
                 gap_penalty: float = -0.5):
        self.match_score = match_score
        self.mismatch_penalty = mismatch_penalty
        self.gap_penalty = gap_penalty
    
    def align(self, genius_tokens: List[str], whisper_tokens: List[str]) -> List[Tuple[Optional[int], Optional[int]]]:
        """
        Align genius tokens to whisper tokens using monotonic DP.
        
        Returns:
            List of (genius_idx, whisper_idx) pairs. None indicates gap.
        """
        n = len(genius_tokens)
        m = len(whisper_tokens)
        
        # DP matrix: dp[i][j] = best score aligning genius[:i] to whisper[:j]
        dp = [[float('-inf')] * (m + 1) for _ in range(n + 1)]
        dp[0][0] = 0
        
        # Traceback matrix
        traceback = [[None] * (m + 1) for _ in range(n + 1)]
        
        # Fill DP matrix (monotonic: only move right/down/diagonal)
        for i in range(n + 1):
            for j in range(m + 1):
                if i == 0 and j == 0:
                    continue
                
                candidates = []
                
                # Match/mismatch (diagonal)
                if i > 0 and j > 0:
                    score = self.match_score if genius_tokens[i-1] == whisper_tokens[j-1] else self.mismatch_penalty
                    candidates.append((dp[i-1][j-1] + score, 'match'))
                
                # Gap in whisper (move down in genius)
                if i > 0:
                    candidates.append((dp[i-1][j] + self.gap_penalty, 'gap_whisper'))
                
                # Gap in genius (move right in whisper)
                if j > 0:
                    candidates.append((dp[i][j-1] + self.gap_penalty, 'gap_genius'))
                
                if candidates:
                    best_score, best_move = max(candidates, key=lambda x: x[0])
                    dp[i][j] = best_score
                    traceback[i][j] = best_move
        
        # Traceback to get alignment
        alignment = []
        i, j = n, m
        
        while i > 0 or j > 0:
            move = traceback[i][j]
            
            if move == 'match':
                alignment.append((i-1, j-1))
                i -= 1
                j -= 1
            elif move == 'gap_whisper':
                alignment.append((i-1, None))
                i -= 1
            elif move == 'gap_genius':
                alignment.append((None, j-1))
                j -= 1
            else:
                break
        
        alignment.reverse()
        return alignment


class AdvancedLyricsAligner:
    """Advanced lyrics aligner with confidence scoring and section detection."""
    
    def __init__(self):
        self.normalizer = TokenNormalizer()
        self.aligner = NeedlemanWunschAligner()
    
    def _detect_repeated_sections(self, lyrics_lines: List[str]) -> Dict[str, List[int]]:
        """
        Detect repeated sections (chorus, verse patterns) using n-gram similarity.
        
        Returns:
            Dict mapping section hash to list of line indices
        """
        from collections import defaultdict
        import hashlib
        
        sections = defaultdict(list)
        section_groups = []  # List of (start_idx, end_idx, hash) for multi-line sections
        
        # Detect multi-line repeated sections (e.g., chorus)
        window_size = 4  # Look for 4-line patterns
        for i in range(len(lyrics_lines) - window_size + 1):
            # Get window of lines
            window_lines = lyrics_lines[i:i+window_size]
            
            # Create fingerprint from all tokens in window
            all_tokens = []
            for line in window_lines:
                all_tokens.extend(self.normalizer.tokenize(line))
            
            if len(all_tokens) < 5:
                continue
            
            # Hash the section
            fingerprint = ' '.join(all_tokens[:10])  # First 10 tokens
            section_hash = hashlib.md5(fingerprint.encode()).hexdigest()[:8]
            
            section_groups.append((i, i+window_size, section_hash, fingerprint))
        
        # Find repeated multi-line sections
        from collections import Counter
        hash_counts = Counter(sg[2] for sg in section_groups)
        repeated_hashes = {h for h, count in hash_counts.items() if count >= 2}
        
        # Group by hash
        repeated_sections = defaultdict(list)
        for start, end, hash_val, fingerprint in section_groups:
            if hash_val in repeated_hashes:
                repeated_sections[hash_val].append({
                    'start': start,
                    'end': end,
                    'fingerprint': fingerprint,
                    'lines': lyrics_lines[start:end]
                })
        
        return dict(repeated_sections)
    
    def align_lyrics_to_whisper(
        self,
        genius_lyrics: str,
        whisper_words: List[Dict[str, any]]
    ) -> Tuple[str, Dict]:
        """
        Align Genius lyrics to Whisper word timestamps with line-by-line precision.
        
        Args:
            genius_lyrics: Raw Genius lyrics text
            whisper_words: List of {word, start, end, confidence} dicts
            
        Returns:
            (srt_content, report_dict)
        """
        # Parse Genius lyrics into lines
        genius_lines = [line.strip() for line in genius_lyrics.split('\n') if line.strip()]
        
        # Detect repeated sections (chorus detection)
        repeated_sections = self._detect_repeated_sections(genius_lines)
        if repeated_sections:
            print(f"🔍 Detected {len(repeated_sections)} repeated sections (chorus/verse patterns)")
        
        # Convert whisper words to WordTimestamp objects
        word_timestamps = [
            WordTimestamp(
                word=w.get('word', '').strip(),
                start=w.get('start', 0.0),
                end=w.get('end', 0.0),
                confidence=w.get('confidence', 1.0)
            )
            for w in whisper_words
        ]
        
        # Cache word timestamps for precise splitting
        self._word_timestamps_cache = word_timestamps
        
        # Create a flat list of all Whisper words for global alignment
        # IMPORTANT: Split multi-word tokens (e.g., "i will" from "I'll")
        all_whisper_tokens = []
        whisper_token_to_word_idx = []  # Map token index to original word index
        
        for idx, w in enumerate(word_timestamps):
            normalized = self.normalizer.normalize(w.word)
            # Split on spaces to handle contractions properly
            sub_tokens = normalized.split()
            for sub_token in sub_tokens:
                all_whisper_tokens.append(sub_token)
                whisper_token_to_word_idx.append(idx)
        
        # Align each Genius line individually to find its position
        aligned_lines = []
        whisper_idx = 0  # Current position in Whisper words
        all_skipped_lines = []  # Track ALL skipped lines for interpolation
        
        for line_num, genius_line in enumerate(genius_lines, 1):
            # Tokenize this line
            genius_tokens = self.normalizer.tokenize(genius_line)
            
            if not genius_tokens:
                continue
            
            # Search for this line's tokens in the remaining Whisper words
            # IMPORTANT: Only search FORWARD from whisper_idx (strictly monotonic)
            best_match = self._find_best_line_match(
                genius_tokens,
                all_whisper_tokens,
                word_timestamps,
                whisper_idx,
                line_num,
                whisper_token_to_word_idx
            )
            
            if best_match:
                # Fill in the original Genius line text
                best_match['text'] = genius_line
                aligned_lines.append(best_match)
                # CRITICAL: Update whisper_idx to AFTER this match
                # This ensures next line searches AFTER this one (monotonic)
                whisper_idx = best_match.get('last_whisper_idx', whisper_idx)
            else:
                # Line not found - accumulate for interpolation
                all_skipped_lines.append((line_num, genius_line))
                if line_num <= 3:  # Only log first few misses
                    print(f"   ⚠️  Skipped line {line_num}: '{genius_line[:40]}...' (no match in Whisper)")
        
        # Interpolate skipped lines if we have aligned lines
        if aligned_lines and all_skipped_lines:
            print(f"   🔄 Interpolating {len(all_skipped_lines)} skipped lines into gaps...")
            aligned_lines = self._interpolate_skipped_lines(
                aligned_lines, 
                all_skipped_lines, 
                genius_lines,
                word_timestamps
            )
        
        # Split long lines and apply timing constraints
        final_lines = self._split_and_constrain_lines(aligned_lines)
        
        # Generate SRT
        srt_content = self._generate_srt(final_lines)
        
        # Generate report with section detection
        report = self._generate_report(final_lines, repeated_sections)
        
        return srt_content, report
    
    def _interpolate_skipped_lines(
        self,
        aligned_lines: List[Dict],
        skipped_lines: List[Tuple[int, str]],
        all_genius_lines: List[str],
        word_timestamps: List[WordTimestamp]
    ) -> List[Dict]:
        """
        Interpolate skipped lines into gaps between aligned lines.
        Uses temporal interpolation based on song duration and line positions.
        
        Returns:
            Combined list of aligned + interpolated lines, sorted by time
        """
        if not aligned_lines or not word_timestamps:
            return aligned_lines
        
        # Get total song duration
        song_duration = word_timestamps[-1].end
        total_genius_lines = len(all_genius_lines)
        print(f"   📊 Song duration: {song_duration:.1f}s, Total Genius lines: {total_genius_lines}")
        
        # Calculate average duration per line based on aligned lines
        if len(aligned_lines) >= 2:
            total_aligned_duration = sum(line['end'] - line['start'] for line in aligned_lines)
            avg_line_duration = total_aligned_duration / len(aligned_lines)
        else:
            avg_line_duration = 3.0  # Default 3 seconds per line
        
        # Build a map of aligned lines by text for repetition detection
        aligned_by_text = {}
        for aligned in aligned_lines:
            text = aligned.get('text', '').strip()
            if text and text not in aligned_by_text:
                aligned_by_text[text] = aligned
        
        # Group skipped lines by whether they're after the last aligned line
        last_aligned_line_num = max([line.get('line_num', 0) for line in aligned_lines]) if aligned_lines else 0
        lines_after_last = [(num, text) for num, text in skipped_lines if num > last_aligned_line_num]
        lines_before_last = [(num, text) for num, text in skipped_lines if num <= last_aligned_line_num]
        
        # Interpolate each skipped line
        interpolated = []
        repetition_count = 0
        
        for line_num, genius_line in skipped_lines:
            # Check if this line is a repetition of an already aligned line
            if genius_line.strip() in aligned_by_text:
                # This is a repetition! Use the duration from the first occurrence
                first_occurrence = aligned_by_text[genius_line.strip()]
                duration = first_occurrence['end'] - first_occurrence['start']
                
                # Find the position of this line in the original Genius lyrics
                line_position_ratio = line_num / total_genius_lines
                
                # Find surrounding aligned lines to place it correctly
                prev_line = None
                next_line = None
                
                for aligned in aligned_lines:
                    aligned_line_num = aligned.get('line_num', 0)
                    if aligned_line_num < line_num:
                        prev_line = aligned
                    elif aligned_line_num > line_num and next_line is None:
                        next_line = aligned
                        break
                
                # Place it temporally between surrounding lines
                if prev_line and next_line:
                    gap_start = prev_line['end']
                    gap_end = next_line['start']
                    gap_duration = gap_end - gap_start
                    
                    lines_in_gap = next_line['line_num'] - prev_line['line_num']
                    position_in_gap = line_num - prev_line['line_num']
                    position_ratio = position_in_gap / lines_in_gap
                    
                    estimated_start = gap_start + (gap_duration * position_ratio)
                    estimated_end = min(estimated_start + duration, gap_end - 0.1)
                elif prev_line:
                    # After last aligned line - distribute evenly
                    if lines_after_last:
                        # Find position in the "after" list
                        after_index = next((i for i, (num, _) in enumerate(lines_after_last) if num == line_num), 0)
                        # Distribute from last aligned line to end of song
                        remaining_duration = song_duration - prev_line['end']
                        time_per_line = remaining_duration / (len(lines_after_last) + 1)
                        estimated_start = prev_line['end'] + (time_per_line * (after_index + 1))
                        estimated_end = min(estimated_start + duration, song_duration)
                    else:
                        estimated_start = prev_line['end'] + 0.5
                        estimated_end = min(estimated_start + duration, song_duration)
                elif next_line:
                    estimated_end = next_line['start'] - 0.5
                    estimated_start = max(0, estimated_end - duration)
                else:
                    estimated_start = song_duration * line_position_ratio
                    estimated_end = min(estimated_start + duration, song_duration)
                
                # Create interpolated line with higher confidence (it's a known repetition)
                interpolated_line = {
                    'text': genius_line,
                    'start': estimated_start,
                    'end': estimated_end,
                    'confidence': 0.6,  # Higher confidence for repetitions
                    'matched_words': first_occurrence.get('matched_words', 0),
                    'total_words': first_occurrence.get('total_words', 0),
                    'notes': ['Repetition (copied timing from first occurrence)'],
                    'line_num': line_num,
                    'matched_word_indices': []
                }
                interpolated.append(interpolated_line)
                repetition_count += 1
                continue
            
            # Not a repetition - use standard interpolation
            # Find the position of this line in the original Genius lyrics
            line_position_ratio = line_num / total_genius_lines
            
            # Find surrounding aligned lines
            prev_line = None
            next_line = None
            
            for aligned in aligned_lines:
                aligned_line_num = aligned.get('line_num', 0)
                if aligned_line_num < line_num:
                    prev_line = aligned
                elif aligned_line_num > line_num and next_line is None:
                    next_line = aligned
                    break
            
            # Estimate timing based on surrounding lines
            if prev_line and next_line:
                # Interpolate between two aligned lines
                gap_start = prev_line['end']
                gap_end = next_line['start']
                gap_duration = gap_end - gap_start
                
                # Calculate position within gap
                lines_in_gap = next_line['line_num'] - prev_line['line_num']
                position_in_gap = line_num - prev_line['line_num']
                position_ratio = position_in_gap / lines_in_gap
                
                # Estimate timing
                estimated_start = gap_start + (gap_duration * position_ratio)
                estimated_end = min(estimated_start + avg_line_duration, gap_end - 0.1)
                
            elif prev_line:
                # After last aligned line - distribute evenly
                if lines_after_last:
                    # Find position in the "after" list
                    after_index = next((i for i, (num, _) in enumerate(lines_after_last) if num == line_num), 0)
                    # Distribute from last aligned line to end of song
                    remaining_duration = song_duration - prev_line['end']
                    time_per_line = remaining_duration / (len(lines_after_last) + 1)
                    estimated_start = prev_line['end'] + (time_per_line * (after_index + 1))
                    estimated_end = min(estimated_start + avg_line_duration, song_duration)
                else:
                    estimated_start = prev_line['end'] + 0.5
                    estimated_end = min(estimated_start + avg_line_duration, song_duration)
                
            elif next_line:
                # Before first aligned line
                estimated_end = next_line['start'] - 0.5
                estimated_start = max(0, estimated_end - avg_line_duration)
                
            else:
                # No aligned lines - use position ratio
                estimated_start = song_duration * line_position_ratio
                estimated_end = min(estimated_start + avg_line_duration, song_duration)
            
            # Create interpolated line
            interpolated_line = {
                'text': genius_line,
                'start': estimated_start,
                'end': estimated_end,
                'confidence': 0.3,  # Low confidence for interpolated lines
                'matched_words': 0,
                'total_words': len(self.normalizer.tokenize(genius_line)),
                'notes': ['Interpolated (no Whisper match)'],
                'line_num': line_num,
                'matched_word_indices': []
            }
            interpolated.append(interpolated_line)
        
        # Combine aligned and interpolated lines, sort by line number
        all_lines = aligned_lines + interpolated
        all_lines.sort(key=lambda x: x.get('line_num', 0))
        
        print(f"   📊 Alignment summary: {len(aligned_lines)} aligned, {len(interpolated)} interpolated (total: {len(aligned_lines) + len(interpolated)})")
        if repetition_count > 0:
            print(f"   ✅ Added {len(interpolated)} interpolated lines:")
            print(f"      - {repetition_count} repetitions (confidence 60%)")
            print(f"      - {len(interpolated) - repetition_count} standard interpolations (confidence 30%)")
        else:
            print(f"   ✅ Added {len(interpolated)} interpolated lines (marked as low confidence)")
        
        # Fix overlapping timestamps by sorting by start time and spacing them out
        all_lines.sort(key=lambda x: x['start'])
        for i in range(1, len(all_lines)):
            prev_line = all_lines[i-1]
            curr_line = all_lines[i]
            
            # If current line starts before previous line ends, adjust it
            if curr_line['start'] < prev_line['end']:
                # Start current line right after previous line
                curr_line['start'] = prev_line['end'] + 0.01
                # Ensure minimum duration of 0.5s
                if curr_line['end'] <= curr_line['start']:
                    curr_line['end'] = curr_line['start'] + 0.5
        
        return all_lines
    
    def _find_best_line_match(
        self,
        genius_tokens: List[str],
        all_whisper_tokens: List[str],
        word_timestamps: List[WordTimestamp],
        start_idx: int,
        line_num: int,
        token_to_word_idx: List[int] = None
    ) -> Optional[Dict]:
        """
        Find the best match for a Genius line in Whisper words.
        Uses sliding window to find the best alignment position.
        """
        if not genius_tokens:
            return None
        
        # Search window: look ahead up to 200 words (increased for longer songs)
        # This allows finding matches even in songs with many repetitions
        search_end = min(start_idx + 200, len(all_whisper_tokens))
        
        best_score = -float('inf')
        best_start = start_idx
        best_end = start_idx
        
        # Sliding window search with fuzzy matching
        best_matched = 0
        for i in range(start_idx, search_end):
            # Try to match genius_tokens starting at position i
            score = 0
            matched = 0
            
            # Allow some flexibility in matching (skip words if needed)
            whisper_pos = i
            for j, g_token in enumerate(genius_tokens):
                # Look ahead a few words to find a match (fuzzy matching)
                found = False
                for k in range(3):  # Look ahead up to 3 words
                    if whisper_pos + k >= len(all_whisper_tokens):
                        break
                    if g_token == all_whisper_tokens[whisper_pos + k]:
                        score += 2  # Match
                        matched += 1
                        whisper_pos += k + 1
                        found = True
                        break
                
                if not found:
                    score -= 0.5  # Smaller penalty for mismatch (more permissive)
                    whisper_pos += 1
            
            # Prefer earlier matches but with smaller penalty
            distance_penalty = (i - start_idx) * 0.05
            adjusted_score = score - distance_penalty
            
            if adjusted_score > best_score:
                best_score = adjusted_score
                best_start = i
                best_end = whisper_pos
                best_matched = matched
        
        # More permissive threshold: at least 20% of words matched
        min_required_matches = max(1, len(genius_tokens) // 5)
        
        # If we found a reasonable match
        if best_score > 0 and best_matched >= min_required_matches:
            # Get actual matched indices (map back to original word indices)
            matched_word_indices = []
            matched_count = 0
            
            for j, g_token in enumerate(genius_tokens):
                token_idx = best_start + j
                if token_idx < len(all_whisper_tokens) and g_token == all_whisper_tokens[token_idx]:
                    # Map token index back to word index
                    if token_to_word_idx:
                        word_idx = token_to_word_idx[token_idx]
                    else:
                        word_idx = token_idx
                    
                    if word_idx not in matched_word_indices:  # Avoid duplicates
                        matched_word_indices.append(word_idx)
                    matched_count += 1
            
            if not matched_word_indices:
                return None
            
            # Get timing from matched words
            first_word = word_timestamps[matched_word_indices[0]]
            last_word = word_timestamps[matched_word_indices[-1]]
            
            start_time = first_word.start
            end_time = last_word.end
            
            # Calculate confidence
            confidence = matched_count / len(genius_tokens) if genius_tokens else 0.0
            avg_word_conf = sum(word_timestamps[i].confidence for i in matched_word_indices) / len(matched_word_indices)
            final_confidence = (confidence + avg_word_conf) / 2
            
            notes = []
            if confidence < 0.5:
                notes.append("Low word match rate")
            if avg_word_conf < 0.7:
                notes.append("Low Whisper confidence")
            
            # We need to pass the original Genius line text, not normalized tokens
            # This will be set by the caller
            return {
                'text': '',  # Will be filled by caller with original line
                'start': start_time,
                'end': end_time,
                'confidence': final_confidence,
                'matched_words': matched_count,
                'total_words': len(genius_tokens),
                'notes': notes,
                'last_whisper_idx': matched_word_indices[-1] + 1,
                'line_num': line_num,
                'matched_word_indices': matched_word_indices
            }
        
        return None
    
    def _align_line(
        self,
        genius_line: str,
        word_timestamps: List[WordTimestamp],
        start_idx: int,
        line_num: int
    ) -> Optional[Dict]:
        """Align a single Genius line to Whisper words with precise word-level timing."""
        # Tokenize genius line
        genius_tokens = self.normalizer.tokenize(genius_line)
        
        if not genius_tokens:
            return None
        
        # Get window of whisper tokens (look ahead based on line length)
        window_size = max(len(genius_tokens) * 2, 20)
        end_idx = min(start_idx + window_size, len(word_timestamps))
        whisper_window = word_timestamps[start_idx:end_idx]
        
        if not whisper_window:
            return None
        
        # Normalize whisper tokens
        whisper_tokens = [self.normalizer.normalize(w.word) for w in whisper_window]
        
        # Align using Needleman-Wunsch
        alignment = self.aligner.align(genius_tokens, whisper_tokens)
        
        # Extract matched words and timestamps with precise timing
        matched_indices = []
        matched_count = 0
        genius_word_times = []  # Track which genius words matched to which whisper words
        
        for g_idx, w_idx in alignment:
            if g_idx is not None and w_idx is not None:
                if genius_tokens[g_idx] == whisper_tokens[w_idx]:
                    matched_indices.append(start_idx + w_idx)
                    matched_count += 1
                    genius_word_times.append((g_idx, start_idx + w_idx))
        
        if not matched_indices:
            return None
        
        # Use FIRST and LAST matched word for tight timing
        # This prevents the 6-second problem by using actual word boundaries
        first_word = word_timestamps[matched_indices[0]]
        last_word = word_timestamps[matched_indices[-1]]
        
        start_time = first_word.start
        end_time = last_word.end
        
        # Ensure reasonable duration (not too short)
        duration = end_time - start_time
        if duration < 0.5:
            # Extend slightly if too short
            end_time = start_time + 0.5
        
        # Calculate confidence
        confidence = matched_count / len(genius_tokens) if genius_tokens else 0.0
        
        # Average word confidence
        avg_word_conf = sum(word_timestamps[i].confidence for i in matched_indices) / len(matched_indices)
        
        # Combined confidence
        final_confidence = (confidence + avg_word_conf) / 2
        
        notes = []
        if confidence < 0.5:
            notes.append("Low word match rate")
        if avg_word_conf < 0.7:
            notes.append("Low Whisper confidence")
        if duration > 5.0:
            notes.append(f"Long duration ({duration:.1f}s) - may need splitting")
        
        return {
            'text': genius_line,
            'start': start_time,
            'end': end_time,
            'confidence': final_confidence,
            'matched_words': matched_count,
            'total_words': len(genius_tokens),
            'notes': notes,
            'last_whisper_idx': matched_indices[-1] + 1,
            'line_num': line_num,
            'matched_word_indices': matched_indices  # For section detection
        }
    
    def _split_and_constrain_lines(self, aligned_lines: List[Dict]) -> List[Dict]:
        """Split long lines and apply timing constraints with word-level precision."""
        print(f"   🔧 Splitting/constraining {len(aligned_lines)} lines...")
        final_lines = []
        
        for line in aligned_lines:
            text = line['text']
            start = line['start']
            end = line['end']
            duration = end - start
            
            # Check if line needs splitting (> 42 chars OR > 4s duration)
            needs_split = len(text) > 42 or duration > 4.0
            
            if not needs_split:
                # Apply constraints to single line
                constrained = self._apply_timing_constraints(line)
                final_lines.append(constrained)
            else:
                # Split into multiple lines using actual word timestamps if available
                words = text.split()
                num_words = len(words)
                
                if num_words <= 1:
                    # Can't split single word, just constrain
                    final_lines.append(self._apply_timing_constraints(line))
                    continue
                
                # Split at midpoint
                mid_idx = num_words // 2
                
                line1_text = ' '.join(words[:mid_idx])
                line2_text = ' '.join(words[mid_idx:])
                
                # Use actual word timestamps for precise split if available
                matched_indices = line.get('matched_word_indices', [])
                if matched_indices and hasattr(self, '_word_timestamps_cache'):
                    # Find the word timestamp at the split point
                    if mid_idx < len(matched_indices):
                        split_word_idx = matched_indices[mid_idx]
                        mid_time = self._word_timestamps_cache[split_word_idx].start
                    else:
                        # Fallback to estimation
                        mid_time = start + (duration * mid_idx / num_words)
                else:
                    # Estimate timing based on word count
                    mid_time = start + (duration * mid_idx / num_words)
                
                line1 = {**line, 'text': line1_text, 'end': mid_time, 'notes': line.get('notes', []).copy()}
                line2 = {**line, 'text': line2_text, 'start': mid_time, 'notes': line.get('notes', []).copy()}
                
                # Recursively split if still too long
                for subline in [line1, line2]:
                    if len(subline['text']) > 42 or (subline['end'] - subline['start']) > 4.0:
                        # Recursive split
                        sub_result = self._split_and_constrain_lines([subline])
                        final_lines.extend(sub_result)
                    else:
                        final_lines.append(self._apply_timing_constraints(subline))
        
        # Group lines with very small gaps (better UX than rapid succession)
        # BUT: Don't merge too aggressively or we lose lines
        MIN_GAP = 0.2  # Minimum gap between subtitles (200ms) - reduced to keep more lines
        MAX_MERGED_LENGTH = 60  # Max characters for merged line - reduced to avoid losing content
        
        # Keep merging until no more merges are possible
        merged_lines = final_lines
        changed = True
        merge_iterations = 0
        
        while changed and merge_iterations < 10:  # Max 10 iterations to prevent infinite loop
            changed = False
            new_merged = []
            i = 0
            
            while i < len(merged_lines):
                current = merged_lines[i]
                
                # Try to merge with next line
                if i < len(merged_lines) - 1:
                    next_line = merged_lines[i+1]
                    gap = next_line['start'] - current['end']
                    
                    # Merge if gap is too small AND combined text fits
                    combined_text = current['text'] + ' ' + next_line['text']
                    should_merge = (gap < MIN_GAP and len(combined_text) <= MAX_MERGED_LENGTH)
                    
                    if should_merge:
                        # Merge the two lines
                        merged = {
                            'text': combined_text,
                            'start': current['start'],
                            'end': next_line['end'],
                            'confidence': (current['confidence'] + next_line['confidence']) / 2,
                            'matched_words': current['matched_words'] + next_line['matched_words'],
                            'total_words': current['total_words'] + next_line['total_words'],
                            'notes': current.get('notes', []) + [f"Merged (gap: {gap:.2f}s)"],
                            'line_num': current.get('line_num', 0),
                            'matched_word_indices': current.get('matched_word_indices', []) + next_line.get('matched_word_indices', [])
                        }
                        new_merged.append(merged)
                        i += 2  # Skip both lines
                        changed = True
                        continue
                
                # No merge - add current line as-is
                new_merged.append(current)
                i += 1
            
            merged_lines = new_merged
            merge_iterations += 1
        
        if merge_iterations > 1:
            print(f"   🔗 Merged lines in {merge_iterations} iterations")
        
        # Now prevent overlaps and add tail
        for i in range(len(merged_lines)):
            # Try to add 100ms tail for readability
            desired_end = merged_lines[i]['end'] + 0.1
            
            # Check if this would overlap with next line
            if i < len(merged_lines) - 1:
                next_start = merged_lines[i+1]['start']
                
                # ALWAYS prevent overlap - no exceptions
                if desired_end >= next_start:
                    # Leave a small gap (10ms minimum) between subtitles
                    merged_lines[i]['end'] = next_start - 0.01
                else:
                    # Safe to add tail
                    merged_lines[i]['end'] = desired_end
            else:
                # Last line - safe to add tail
                merged_lines[i]['end'] = desired_end
        
        print(f"   ✅ Final output: {len(merged_lines)} lines (from {len(aligned_lines)} input)")
        return merged_lines
    
    def _apply_timing_constraints(self, line: Dict) -> Dict:
        """Apply timing constraints (1-6s duration, ≤17 CPS)."""
        duration = line['end'] - line['start']
        text_len = len(line['text'])
        
        # Ensure 1-6s duration
        if duration < 1.0:
            line['end'] = line['start'] + 1.0
            line['notes'].append("Extended to 1s minimum")
        elif duration > 6.0:
            line['end'] = line['start'] + 6.0
            line['notes'].append("Capped at 6s maximum")
        
        # Check CPS (characters per second)
        duration = line['end'] - line['start']
        cps = text_len / duration if duration > 0 else 0
        
        if cps > 17:
            # Extend duration to meet CPS constraint
            new_duration = text_len / 17
            line['end'] = line['start'] + new_duration
            line['notes'].append(f"Extended to meet CPS constraint (was {cps:.1f})")
        
        return line
    
    def _generate_srt(self, lines: List[Dict]) -> str:
        """Generate SRT content from aligned lines."""
        srt_blocks = []
        
        for i, line in enumerate(lines, 1):
            start = line['start']
            end = line['end']
            
            # Validate timestamps
            if end <= start:
                print(f"⚠️  Invalid timestamp at line {i}: {start:.3f}s → {end:.3f}s (end before start!)")
                print(f"    Text: '{line['text'][:50]}...'")
                # Fix by adding minimum duration
                end = start + 0.5
            
            start_ts = self._format_timestamp(start)
            end_ts = self._format_timestamp(end)
            
            srt_blocks.append(f"{i}\n{start_ts} --> {end_ts}\n{line['text']}\n")
        
        return '\n'.join(srt_blocks)
    
    def _generate_report(self, lines: List[Dict], repeated_sections: Dict = None) -> Dict:
        """Generate confidence report with section detection."""
        total_confidence = sum(line['confidence'] for line in lines)
        avg_confidence = total_confidence / len(lines) if lines else 0.0
        
        line_reports = []
        for line in lines:
            line_reports.append({
                'line_num': line.get('line_num', 0),
                'text': line['text'],
                'start': round(line['start'], 3),
                'end': round(line['end'], 3),
                'duration': round(line['end'] - line['start'], 3),
                'confidence': round(line['confidence'], 3),
                'matched_words': line['matched_words'],
                'total_words': line['total_words'],
                'match_rate': round(line['matched_words'] / line['total_words'], 3) if line['total_words'] > 0 else 0,
                'notes': line.get('notes', [])
            })
        
        report = {
            'summary': {
                'total_lines': len(lines),
                'avg_confidence': round(avg_confidence, 3),
                'high_confidence_lines': sum(1 for l in lines if l['confidence'] >= 0.8),
                'low_confidence_lines': sum(1 for l in lines if l['confidence'] < 0.5),
            },
            'lines': line_reports
        }
        
        # Add repeated sections info if available
        if repeated_sections:
            report['repeated_sections'] = {
                'count': len(repeated_sections),
                'sections': [
                    {
                        'fingerprint': sections[0]['fingerprint'],
                        'occurrences': len(sections),
                        'first_occurrence': sections[0]['lines']
                    }
                    for sections in repeated_sections.values()
                ]
            }
        
        return report
    
    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Format seconds to SRT timestamp."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
