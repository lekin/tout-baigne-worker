#!/usr/bin/env python3
"""Write a karaoke ASS overlay from a JSON spec.

Used by the admin app's capture-reel pipeline so every lyric overlay —
full karaoke videos, GKDA reels, captation reels — shares the exact same
renderer (src/render/ass_karaoke.py). Times in the spec are already on the
target video's timeline; any offset/skew mapping happens upstream.

Usage:
  python3 build_lyrics_ass.py <out.ass> [spec.json]
  (spec read from stdin when no spec file is given)

Spec:
  {
    "events": [
      {"start": 0.5, "end": 2.1, "text": "Wake me up", "top": false,
       "words": [{"text": "Wake", "start": 0.5, "end": 0.9}, ...]}
    ],
    "width": 1080, "height": 1920,          // PlayRes — vertical reel
    "font_size": 76,                        // default 76
    "font_name": "Space Mono",              // resolved via ffmpeg fontsdir
    "primary": "&H0000FFFF", "secondary": "&H00CCCCCC",
    "outline": "&HA64DFF",
    "progressive": true,                    // \\kf vs \\k
    "pos_top": 0.33, "pos_bottom": 0.72,    // line positions (ratios)
    "margin_lr": 480
  }

Prints "OK <path>" on success; exits non-zero with a message on stderr.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.render.ass_karaoke import write_karaoke_ass  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: build_lyrics_ass.py <out.ass> < spec.json", file=sys.stderr)
        return 2
    out_path = sys.argv[1]
    try:
        if len(sys.argv) >= 3:
            with open(sys.argv[2], "r", encoding="utf-8") as f:
                spec = json.load(f)
        else:
            spec = json.load(sys.stdin)
    except Exception as e:
        print(f"invalid spec JSON: {e}", file=sys.stderr)
        return 2

    events = spec.get("events")
    if not isinstance(events, list) or not events:
        print("spec.events: empty event list", file=sys.stderr)
        return 1

    ok = write_karaoke_ass(
        events,
        out_path,
        video_width=int(spec.get("width", 1920)),
        video_height=int(spec.get("height", 1080)),
        font_name=spec.get("font_name", "Space Mono"),
        font_size=int(spec.get("font_size", 76)),
        primary_colour=spec.get("primary", "&H0000FFFF"),
        secondary_colour=spec.get("secondary", "&H00CCCCCC"),
        outline_colour=spec.get("outline", "&HA64DFF"),
        progressive_fill=bool(spec.get("progressive", True)),
        pos_top_ratio=float(spec.get("pos_top", 0.33)),
        pos_bottom_ratio=float(spec.get("pos_bottom", 0.72)),
        margin_lr=int(spec.get("margin_lr", 480)),
    )
    if not ok:
        print("ASS writer produced no file", file=sys.stderr)
        return 1
    print(f"OK {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
