"""Detect the most likely chorus start time from synced lyrics."""

import re
from collections import Counter
from typing import List, Optional

from src.musixmatch_lyrics import RichsyncParser, SyncedLine


def _normalize_line(text: str) -> str:
    """Normalize a lyric line for comparison."""
    text = text or ""
    # Remove parenthetical / bracketed markers, keep the rest
    text = re.sub(r"[\(\[\{].*?[\)\]\}]", "", text)
    # Lowercase and keep only alphanumerics + spaces
    text = re.sub(r"[^\w\s]", "", text.lower())
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_chorus_start(
    synced_lines: List[SyncedLine],
    audio_duration: Optional[float] = None,
    excerpt_duration: float = 15.0,
    window_lines: int = 4,
    min_occurrences: int = 2,
    min_window_words: int = 4,
) -> float:
    """
    Find a chorus start time from synced lyric lines.

    The algorithm looks for the most frequently repeated block of consecutive
    lines (a ``window``) and returns the start time of its first occurrence.
    If no clear repeated section is found, it falls back to the middle of the
    track.

    Args:
        synced_lines: list of synced lyric lines.
        audio_duration: total track length in seconds.
        excerpt_duration: target length of the clip that will be extracted.
        window_lines: number of consecutive lines to group as a chorus window.
        min_occurrences: a window must appear at least this many times to be
            considered a chorus.
        min_window_words: a window must contain at least this many words.

    Returns:
        Start time in seconds for a clip starting at the chorus.
    """
    normalized: List[Tuple[SyncedLine, str]] = []
    for line in synced_lines:
        norm = _normalize_line(line.text)
        if len(norm.split()) >= 1:
            normalized.append((line, norm))

    if len(normalized) < window_lines:
        # Not enough lyrics to detect a chorus, fall back to center.
        return _fallback_start(audio_duration, excerpt_duration)

    # Build n-gram windows of consecutive normalized lines.
    counts: Counter = Counter()
    for i in range(len(normalized) - window_lines + 1):
        window = normalized[i : i + window_lines]
        if sum(len(n.split()) for _, n in window) < min_window_words:
            continue
        key = " || ".join(n for _, n in window)
        counts[key] += 1

    if not counts:
        return _fallback_start(audio_duration, excerpt_duration)

    best_key, best_count = counts.most_common(1)[0]
    if best_count < min_occurrences:
        return _fallback_start(audio_duration, excerpt_duration)

    # Find the first occurrence of the best window.
    first_idx: Optional[int] = None
    for i in range(len(normalized) - window_lines + 1):
        window = normalized[i : i + window_lines]
        key = " || ".join(n for _, n in window)
        if key == best_key:
            first_idx = i
            break

    if first_idx is None:
        return _fallback_start(audio_duration, excerpt_duration)

    chorus_start = normalized[first_idx][0].start

    # If we have an audio duration, make sure the clip fits inside the track.
    if audio_duration and audio_duration > 0:
        chorus_start = min(chorus_start, max(0.0, audio_duration - excerpt_duration))

    return max(0.0, chorus_start)


def _fallback_start(audio_duration: Optional[float], excerpt_duration: float) -> float:
    """Return a sensible default start time when no chorus is detected."""
    if audio_duration and audio_duration > excerpt_duration:
        return (audio_duration - excerpt_duration) / 2.0
    return 0.0


def detect_chorus_start_from_richsync(
    richsync_json: str,
    audio_duration: Optional[float] = None,
    excerpt_duration: float = 15.0,
) -> float:
    """Convenience wrapper that parses Musixmatch Richsync JSON."""
    lines = RichsyncParser.parse(richsync_json)
    return detect_chorus_start(lines, audio_duration, excerpt_duration)
