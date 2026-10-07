#!/usr/bin/env python3
"""
Analyze timestamp drift in SRT files.
Shows timing distribution to identify drift issues.
"""

import sys
import re
from datetime import timedelta

def parse_srt_time(time_str):
    """Parse SRT timestamp to seconds."""
    # Format: HH:MM:SS,mmm
    match = re.match(r'(\d+):(\d+):(\d+),(\d+)', time_str)
    if match:
        h, m, s, ms = map(int, match.groups())
        return h * 3600 + m * 60 + s + ms / 1000
    return 0

def analyze_srt(srt_path):
    """Analyze SRT file for timing patterns and drift."""
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Parse all subtitle blocks
    blocks = content.strip().split('\n\n')
    subtitles = []
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            # Line 0: index
            # Line 1: timestamps
            # Line 2+: text
            time_line = lines[1]
            match = re.match(r'(\S+) --> (\S+)', time_line)
            if match:
                start_str, end_str = match.groups()
                start = parse_srt_time(start_str)
                end = parse_srt_time(end_str)
                text = ' '.join(lines[2:])
                
                subtitles.append({
                    'start': start,
                    'end': end,
                    'duration': end - start,
                    'text': text
                })
    
    if not subtitles:
        print("❌ No subtitles found in SRT")
        return
    
    # Analysis
    print("=" * 70)
    print(f"SRT Analysis: {srt_path}")
    print("=" * 70)
    print()
    
    print(f"📊 Total subtitles: {len(subtitles)}")
    print(f"⏱️  Duration: {subtitles[0]['start']:.1f}s - {subtitles[-1]['end']:.1f}s")
    print(f"📏 Total span: {subtitles[-1]['end'] - subtitles[0]['start']:.1f}s")
    print()
    
    # Duration statistics
    durations = [s['duration'] for s in subtitles]
    avg_duration = sum(durations) / len(durations)
    max_duration = max(durations)
    min_duration = min(durations)
    
    print(f"⏳ Subtitle durations:")
    print(f"   Average: {avg_duration:.2f}s")
    print(f"   Min: {min_duration:.2f}s")
    print(f"   Max: {max_duration:.2f}s")
    print()
    
    # Check for drift by analyzing gaps between subtitles
    gaps = []
    for i in range(len(subtitles) - 1):
        gap = subtitles[i+1]['start'] - subtitles[i]['end']
        gaps.append(gap)
    
    if gaps:
        avg_gap = sum(gaps) / len(gaps)
        max_gap = max(gaps)
        print(f"🔍 Gaps between subtitles:")
        print(f"   Average: {avg_gap:.2f}s")
        print(f"   Max: {max_gap:.2f}s")
        print()
    
    # Analyze timing at different points (check for drift)
    print("🎯 Timing checkpoints:")
    checkpoints = [0, 60, 120, 180, 240]  # 0s, 1min, 2min, 3min, 4min
    
    for checkpoint in checkpoints:
        # Find subtitle closest to this time
        closest = min(subtitles, key=lambda s: abs(s['start'] - checkpoint))
        if abs(closest['start'] - checkpoint) < 30:  # Within 30s
            print(f"   {checkpoint//60}:{checkpoint%60:02d} → {closest['start']:.1f}s: '{closest['text'][:40]}...'")
    print()
    
    # Look for potential drift indicators
    print("⚠️  Potential issues:")
    issues = []
    
    # Long gaps (>5s)
    long_gaps = [(i, gap) for i, gap in enumerate(gaps) if gap > 5]
    if long_gaps:
        issues.append(f"   • {len(long_gaps)} long gaps (>5s)")
        for i, gap in long_gaps[:3]:  # Show first 3
            time = subtitles[i]['end']
            print(f"     - Gap of {gap:.1f}s at {time:.1f}s")
    
    # Very long subtitles (>6s)
    long_subs = [(i, s) for i, s in enumerate(subtitles) if s['duration'] > 6]
    if long_subs:
        issues.append(f"   • {len(long_subs)} very long subtitles (>6s)")
        for i, s in long_subs[:3]:
            print(f"     - {s['duration']:.1f}s at {s['start']:.1f}s: '{s['text'][:40]}...'")
    
    # Very short subtitles (<0.5s)
    short_subs = [(i, s) for i, s in enumerate(subtitles) if s['duration'] < 0.5]
    if short_subs:
        issues.append(f"   • {len(short_subs)} very short subtitles (<0.5s)")
    
    if not issues:
        print("   ✅ No obvious timing issues detected")
    
    print()
    print("=" * 70)
    
    # Show first and last few subtitles
    print("\n📝 First 3 subtitles:")
    for i, s in enumerate(subtitles[:3]):
        print(f"   [{i+1}] {s['start']:.1f}s-{s['end']:.1f}s ({s['duration']:.1f}s): {s['text'][:50]}")
    
    print("\n📝 Last 3 subtitles:")
    for i, s in enumerate(subtitles[-3:], len(subtitles)-2):
        print(f"   [{i}] {s['start']:.1f}s-{s['end']:.1f}s ({s['duration']:.1f}s): {s['text'][:50]}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_drift.py <srt_file>")
        sys.exit(1)
    
    srt_path = sys.argv[1]
    analyze_srt(srt_path)
