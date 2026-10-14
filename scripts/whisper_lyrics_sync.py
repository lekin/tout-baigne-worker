#!/usr/bin/env python3
"""Sync known lyrics onto an audio clip using Whisper word timestamps.

Used by the admin app's captation-reel pipeline: the rush's actual audio
(crowd singing included) is transcribed word-level, then the track's real
lyrics (Musixmatch RichSync/LRC text) are aligned onto those words with the
Needleman-Wunsch aligner — so the displayed text is the correct lyric while
the timing follows what is actually sung in the video. No master offset
required.

Usage:
  whisper_lyrics_sync.py <audio.wav> <spec.json|->  →  prints JSON
    {"lines": [{"start": s, "end": e, "text": "..."}], "mode": ..., "report": {...}}

Spec:
  {"lyrics": "line one\nline two\n...",           // raw lyric text
   "lines": [{"text": "..."}],                     // or pre-split lines
   "mode": "auto",                                 // aligned|transcript|auto
   "model": "small",                               // whisper model (env WHISPER_MODEL)
   "language": null}

Modes:
  transcript — whisper's own words, grouped per segment (no alignment).
  aligned    — known lyrics snapped onto whisper words; empty if nothing
               matches.
  auto       — aligned first, transcript when alignment finds nothing.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _transcribe_words(audio_path: str, model_name: str, language, prompt: str = ""):
    """whisper word-level transcription → [{word, start, end}]."""
    import contextlib

    import whisper

    model = whisper.load_model(model_name)
    # whisper prints "Detected language" etc. to stdout — keep stdout clean
    # for the JSON result.
    with contextlib.redirect_stdout(sys.stderr):
        result = model.transcribe(
            audio_path,
            word_timestamps=True,
            language=language or None,
            verbose=False,
            condition_on_previous_text=False,
            # Known lyrics prime the vocabulary — timing stays whisper's
            # own, so a transcript still follows the actual audio.
            initial_prompt=prompt or None,
        )
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            words.append(
                {
                    "word": w.get("word", "").strip(),
                    "start": float(w.get("start", 0.0)),
                    "end": float(w.get("end", 0.0)),
                    "confidence": float(w.get("probability", 1.0) or 0.0),
                }
            )
    return [w for w in words if w["word"]]


def _transcribe_segments(audio_path: str, model_name: str, language, prompt: str = ""):
    """Whisper segments → transcript lines (one ASS line per segment)."""
    import contextlib

    import whisper

    model = whisper.load_model(model_name)
    with contextlib.redirect_stdout(sys.stderr):
        result = model.transcribe(
            audio_path,
            word_timestamps=False,
            language=language or None,
            verbose=False,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
        )
    lines = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(
            {
                "start": float(seg.get("start", 0.0)),
                "end": float(seg.get("end", 0.0)),
                "text": text,
            }
        )
    return lines


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: whisper_lyrics_sync.py <audio> <spec.json|->", file=sys.stderr)
        return 2
    audio_path, spec_arg = sys.argv[1], sys.argv[2]
    try:
        if spec_arg == "-":
            spec = json.load(sys.stdin)
        else:
            with open(spec_arg, "r", encoding="utf-8") as f:
                spec = json.load(f)
    except Exception as e:
        print(f"invalid spec JSON: {e}", file=sys.stderr)
        return 2

    lyrics_text = spec.get("lyrics") or "\n".join(
        l.get("text", "") for l in spec.get("lines", [])
    )
    mode = spec.get("mode", "auto")
    if mode not in ("aligned", "transcript", "auto"):
        print(f"spec: unknown mode {mode!r}", file=sys.stderr)
        return 2
    if mode != "transcript" and not lyrics_text.strip():
        print("spec: no lyrics text", file=sys.stderr)
        return 1

    # vocals: isolate the voice stem first — crowd/instrumental noise is
    # what makes whisper hallucinate on rushes. On failure we keep the
    # raw audio rather than aborting.
    if spec.get("vocals"):
        import contextlib
        import tempfile

        vocals_path = os.path.join(
            tempfile.mkdtemp(prefix="lyr-vocals-"), "vocals.wav"
        )
        try:
            from src.qa.separator import (
                PyTorchDemucsSeparator,
                VocalSeparationConfig,
            )

            with contextlib.redirect_stdout(sys.stderr):
                ok = PyTorchDemucsSeparator().separate(
                    audio_path,
                    vocals_path,
                    VocalSeparationConfig(
                        model=spec.get("vocal_model", "htdemucs"),
                        device=spec.get("vocal_device"),
                    ),
                )
            if ok:
                audio_path = vocals_path
            else:
                print("vocal separation failed — raw audio", file=sys.stderr)
        except Exception as e:
            print(f"vocal separation unavailable: {e}", file=sys.stderr)

    model_name = spec.get("model") or os.environ.get("WHISPER_MODEL", "small")
    # initial_prompt is capped by whisper at ~224 tokens — the first chunk
    # of the lyric text is enough to prime vocabulary.
    prompt = (spec.get("prompt") or lyrics_text or "")[:1500]

    def transcript_lines():
        return _transcribe_segments(
            audio_path, model_name, spec.get("language"), prompt
        )

    if mode == "transcript":
        print(json.dumps({"lines": transcript_lines(), "mode": "transcript"}))
        return 0

    words = _transcribe_words(
        audio_path, model_name, spec.get("language"), prompt
    )
    lines, report = [], {}
    if words:
        from src.advanced_lyrics_aligner import AdvancedLyricsAligner

        # The aligner chatters to stdout (section detection, skips, merges) —
        # stdout is reserved for the JSON result, so logs go to stderr.
        import contextlib

        aligner = AdvancedLyricsAligner()
        with contextlib.redirect_stdout(sys.stderr):
            srt_content, report = aligner.align_lyrics_to_whisper(
                lyrics_text, words
            )

        # SRT → line dicts (kept simple — the aligner owns the formatting).
        import re

        for block in re.split(r"\n\s*\n", srt_content.strip()):
            rows = block.strip().split("\n")
            if len(rows) < 3 or " --> " not in rows[1]:
                continue
            a, b = rows[1].split(" --> ")

            def ts(v: str) -> float:
                h, m, rest = v.replace(",", ".").split(":")
                return int(h) * 3600 + int(m) * 60 + float(rest)

            lines.append(
                {"start": ts(a), "end": ts(b), "text": " ".join(rows[2:]).strip()}
            )

        # Sanity filter: the aligner interpolates unheard lines into
        # whatever gap remains — on clips where whisper only caught the
        # tail, that's a fast cascade of "catch-up" lyrics. Drop anything
        # too fast to read (>~20 cps) or fully contained in a merged line.
        lines = [
            l
            for l in lines
            if l["end"] - l["start"] >= max(0.7, len(l["text"]) / 20.0)
        ]
        lines = [
            l
            for l in lines
            if not any(
                o is not l and o["start"] <= l["start"] and l["end"] <= o["end"]
                for o in lines
            )
        ]

    if not lines and mode == "auto":
        # Nothing alignable — fall back to whisper's own transcript so the
        # reel still gets synced text.
        print(
            "alignment produced no lines — falling back to transcript",
            file=sys.stderr,
        )
        print(
            json.dumps(
                {
                    "lines": transcript_lines(),
                    "mode": "transcript",
                    "report": {"fallback": "alignment empty"},
                }
            )
        )
        return 0

    print(json.dumps({"lines": lines, "mode": "aligned", "report": report}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
