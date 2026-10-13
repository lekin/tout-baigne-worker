"""Shared ASS karaoke writer — the single source of truth for the lyric
overlay look across every render path (full karaoke videos, GKDA vertical
reels, captation reels).

Recipe (identical to KaraokeGenerator.srt_to_ass_karaoke):
- Space Mono bold, sung=yellow (&H0000FFFF) over upcoming=grey/pink,
  pink outline (&HA64DFF), blurred black glow layer underneath.
- \\fad in/out, \\an5\\pos at the upper (~33%) / lower (~72%) third,
  alternating per line, line slowly grows 100→120% while displayed.
- \\kf progressive fill per word. When the caller supplies word-level
  timing (RichSync / Whisper), each \\kf duration is the word's real span
  (gap absorbed into the next word's sweep); otherwise words are spread
  evenly over the line — the historical SRT behaviour.

An "event" is {start, end, text, words?: [{text, start, end}]} with times
in seconds on the TARGET video timeline — the caller owns any
offset/skew/window mapping.
"""

import os
from typing import List, Optional, Sequence, TypedDict


# Default palette — same defaults as srt_to_ass_karaoke / _pick_ass_palette.
DEFAULT_PRIMARY = "&H0000FFFF"   # yellow (sung)
DEFAULT_SECONDARY = "&H00CCCCCC"  # light grey (upcoming)
DEFAULT_OUTLINE = "&HA64DFF"     # pink-magenta (BGR)

FONT_FAMILY = "Space Mono"


class KaraokeWord(TypedDict):
    text: str
    start: float
    end: float


class KaraokeEvent(TypedDict, total=False):
    start: float
    end: float
    text: str
    words: List[KaraokeWord]
    # Force the upper/lower position; defaults to alternating by index.
    top: bool


def format_timestamp_ass(seconds: float) -> str:
    """Seconds → ASS timestamp (H:MM:SS.cc)."""
    cs = int(round(seconds * 100))
    s, cs = divmod(cs, 100)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _escape_ass_text(text: str) -> str:
    return text.replace("{", "").replace("}", "").replace("\\", "").replace("\n", " ").strip()


def _event_words(event: KaraokeEvent) -> List[KaraokeWord]:
    """Words for an event: caller-supplied timing, else even split over
    the event duration (the legacy SRT behaviour)."""
    if event.get("words"):
        return event["words"]
    parts = [w for w in event["text"].split() if w.strip()]
    if not parts:
        return []
    start, end = event["start"], event["end"]
    per = (end - start) / len(parts)
    return [
        {"text": parts[i], "start": start + i * per, "end": start + (i + 1) * per}
        for i in range(len(parts))
    ]


def write_karaoke_ass(
    events: Sequence[KaraokeEvent],
    ass_path: str,
    video_width: int = 1920,
    video_height: int = 1080,
    font_name: str = FONT_FAMILY,
    font_size: int = 76,
    primary_colour: str = DEFAULT_PRIMARY,
    secondary_colour: str = DEFAULT_SECONDARY,
    outline_colour: str = DEFAULT_OUTLINE,
    progressive_fill: bool = True,
    pos_top_ratio: float = 0.33,
    pos_bottom_ratio: float = 0.72,
    margin_lr: int = 480,
    title: str = "Karaoke",
) -> bool:
    """Write an ASS file for the given karaoke events.

    Events are emitted on two layers per line: a blurred black "Shadow"
    glow under the "Main" text. Position alternates between the upper and
    lower third on successive events (long callers may pre-chunk lines —
    chunks of one logical line share its position via `event["y_hint"]`
    if present, else the running index is used).
    """
    karaoke_tag = "\\kf" if progressive_fill else "\\k"
    center_x = video_width // 2
    pos_y_top = int(video_height * pos_top_ratio)
    pos_y_bottom = int(video_height * pos_bottom_ratio)

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\n")
        f.write(f"Title: {title}\n")
        f.write("ScriptType: v4.00+\n")
        f.write("Collisions: Normal\n")
        f.write(f"PlayResX: {video_width}\n")
        f.write(f"PlayResY: {video_height}\n")
        f.write("Timer: 100.0000\n\n")

        f.write("[V4+ Styles]\n")
        f.write(
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        )
        f.write(
            f"Style: Main,{font_name},{font_size},{primary_colour},{secondary_colour},{outline_colour},&H64000000,"
            f"1,0,0,0,100,100,0,0,1,3,0,2,{margin_lr},{margin_lr},450,1\n"
        )
        f.write(
            f"Style: Shadow,{font_name},{font_size},&H00FFFFFF,&HFFFFFFFF,&HFFFFFF,&H64000000,"
            f"1,0,0,0,100,100,0,0,1,0,0,2,{margin_lr},{margin_lr},450,1\n"
        )
        f.write(
            f"Style: MainTop,{font_name},{font_size},{primary_colour},{secondary_colour},{outline_colour},&H64000000,"
            f"1,0,0,0,100,100,0,0,1,3,0,8,{margin_lr},{margin_lr},450,1\n"
        )
        f.write(
            f"Style: ShadowTop,{font_name},{font_size},&H00FFFFFF,&HFFFFFFFF,&HFFFFFF,&H64000000,"
            f"1,0,0,0,100,100,0,0,1,0,0,8,{margin_lr},{margin_lr},450,1\n\n"
        )

        f.write("[Events]\n")
        f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

        for idx, event in enumerate(events):
            words = _event_words(event)
            if not words:
                continue
            start, end = event["start"], event["end"]
            if end - start <= 0:
                continue
            start_ass = format_timestamp_ass(start)
            end_ass = format_timestamp_ass(end)

            if event.get("words"):
                # Caller-supplied word timing: each \k sweeps until the next
                # word starts — the fill rides through inter-word silence.
                ks = []
                for i, w in enumerate(words):
                    until = words[i + 1]["start"] if i + 1 < len(words) else end
                    ks.append(max(1, int(round((until - max(w["start"], start)) * 100))))
            else:
                # Legacy SRT behaviour: integer even split over the line,
                # remainder dumped on the last word — byte-identical to the
                # original srt_to_ass_karaoke output.
                total_cs = int((end - start) * 100)
                per_word_cs = max(total_cs // len(words), 1)
                ks = [per_word_cs] * len(words)
                ks[-1] += total_cs - per_word_cs * len(words)

            is_top = event["top"] if "top" in event else idx % 2 == 1
            style_main = "MainTop" if is_top else "Main"
            style_shadow = "ShadowTop" if is_top else "Shadow"
            pos_y = pos_y_top if is_top else pos_y_bottom
            pos_tag = f"\\an5\\pos({center_x},{pos_y})"
            duration_ms = int((end - start) * 1000)

            main_line = (
                "{\\fad(500,500)" + pos_tag
                + "\\fscx100\\fscy100\\t(0," + str(duration_ms) + ",\\fscx120\\fscy120)}"
            )
            shadow_line = (
                "{\\fad(500,500)" + pos_tag
                + "\\blur10\\3c&H000000&\\4c&H000000&\\fscx100\\fscy100\\t(0,"
                + str(duration_ms) + ",\\fscx120\\fscy120)}"
            )
            for w, kcs in zip(words, ks):
                token = "{" + karaoke_tag + str(kcs) + "}" + _escape_ass_text(w["text"])
                main_line += token + " "
                shadow_line += token + " "
            main_line = main_line.strip()
            shadow_line = shadow_line.strip()

            f.write(f"Dialogue: 0,{start_ass},{end_ass},{style_shadow},,0,0,0,,{shadow_line}\n")
            f.write(f"Dialogue: 1,{start_ass},{end_ass},{style_main},,0,0,0,,{main_line}\n")

    return os.path.exists(ass_path) and os.path.getsize(ass_path) > 0
