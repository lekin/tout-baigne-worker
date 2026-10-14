#!/usr/bin/env python3
"""Write a karaoke ASS overlay from a JSON spec.

Used by the admin app's captation-reel pipeline — it invokes the SAME
renderers as the marketing reels (ASSKaraokeGenerator reel variants from
src/musixmatch_lyrics.py), so the lyric overlay is identical across every
reel. Times in the spec are already on the target video's timeline; any
offset/sync mapping happens upstream.

Usage:
  python3 build_lyrics_ass.py <out.ass> [spec.json]
  (spec read from stdin when no spec file is given)

Spec:
  {
    "lines": [{"start": 0.5, "end": 2.1, "text": "Wake me up"}],   // or:
    "richsync": [{"ts":0.5,"te":2.1,"l":[{"c":"W","o":0.0}...],"x":"..."}],
    "width": 1080, "height": 1920,
    "font_path": "legacy/karaoke/fonts/DejaVuSans-Bold.ttf",
    "region_x": 70, "region_y": 560, "region_w": 946, "region_h": 220,
    "font_size": 58, "progressive": true,
    "primary": "&H0000FFFF", "secondary": "&H00CCCCCC",
    "outline": "&HA64DFF"
  }

Prints "OK <path>" on success; exits non-zero with a message on stderr.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.musixmatch_lyrics import ASSKaraokeGenerator, SyncedLine  # noqa: E402


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

    font_path = spec.get(
        "font_path", "legacy/karaoke/fonts/DejaVuSans-Bold.ttf"
    )
    common = dict(
        video_width=int(spec.get("width", 1080)),
        video_height=int(spec.get("height", 1920)),
        font_path=font_path,
        progressive_fill=bool(spec.get("progressive", True)),
        primary_colour=spec.get("primary", "&H0000FFFF"),
        secondary_colour=spec.get("secondary", "&H00CCCCCC"),
        outline_colour=spec.get("outline", "&HA64DFF"),
        font_size=int(spec.get("font_size", 58)),
    )
    region = spec.get("region")
    if region:
        common.update(
            region_x=int(region[0]),
            region_y=int(region[1]),
            region_w=int(region[2]),
            region_h=int(region[3]),
        )

    if spec.get("richsync"):
        ass = ASSKaraokeGenerator.generate_from_richsync_reel(
            json.dumps(spec["richsync"]), **common
        )
    else:
        raw_lines = spec.get("lines")
        if not isinstance(raw_lines, list) or not raw_lines:
            print("spec: needs non-empty lines[] or richsync[]", file=sys.stderr)
            return 1
        synced = [
            SyncedLine(
                start=float(l["start"]),
                end=float(l["end"]),
                text=str(l["text"]),
            )
            for l in raw_lines
        ]
        ass = ASSKaraokeGenerator.generate_from_synced_lines_reel(
            synced, **common
        )
    if not ass:
        print("ASS generator produced no content", file=sys.stderr)
        return 1

    # Captation reels: never let a growing line wrap — clipped beats
    # two-line karaoke for readability.
    if spec.get("wrap") is False and "WrapStyle" not in ass:
        ass = ass.replace("Timer: 100.0000", "Timer: 100.0000\nWrapStyle: 2", 1)

    # The reel generators historically write the font *path* as Fontname —
    # normalize to the family name so libass resolves it via fontsdir.
    from src.render.steps import normalize_ass_font_path

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ass)
    normalize_ass_font_path(out_path)
    print(f"OK {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
