#!/usr/bin/env python3
"""Refine a coarse master anchor by audio cross-correlation.

Shazam's `offset` is accurate to ~±1-2 s. This script sharpens it by
correlating the onset envelopes (spectral flux — robust to crowd noise,
which is additive) of the rush segment and the master audio around the
coarse estimate, then measuring the drift between the first and second
half of the segment.

Usage:
  master_align.py <segment_audio> <master_audio> <coarse_anchor_s> [coarse_slope]

`coarse_anchor_s` is the master position (s) heard at segment t=0.
Prints one JSON document on stdout:
  {"anchor": 90.83, "slope": 0.9987, "confidence": 0.42, "refined": true}
`refined` is false when the correlation peaks are too weak to trust —
the caller should then keep the coarse values (or fall back).
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from scipy.signal import correlate, stft

SR = 16000
HOP = 160  # 10 ms hop → envelope at 100 fps
SEARCH_S = 10.0  # ± search window around the coarse anchor (lone Shazam
# probes can be several seconds off)
MIN_SCORE = 0.18  # NCC peak below this = crowd buried the signal


def _pcm(path: str) -> np.ndarray:
    p = subprocess.run(
        [
            os.environ.get("FFMPEG_BIN", "ffmpeg"),
            "-v", "error", "-i", path,
            "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _env(x: np.ndarray) -> np.ndarray:
    _, _, Z = stft(x, fs=SR, nperseg=512, noverlap=512 - HOP,
                   padded=False, boundary=None)
    mag = np.abs(Z)
    e = np.maximum(np.diff(mag, axis=1), 0.0).sum(axis=0)
    e -= e.mean()
    return e


def _ncc(seg: np.ndarray, master: np.ndarray, lo: int, hi: int):
    """Best normalized correlation of `seg` inside master[lo:hi].

    Returns (offset_frames, score) or None when the window is too small."""
    lo = max(0, lo)
    hi = min(len(master), hi)
    if hi - lo <= len(seg):
        return None
    m = master[lo:hi]
    c = correlate(m, seg, mode="valid", method="fft")
    i = int(np.argmax(c))
    norm = np.linalg.norm(seg) * np.linalg.norm(m[i : i + len(seg)])
    return lo + i, (float(c[i] / norm) if norm > 0 else 0.0)


def main() -> int:
    if len(sys.argv) < 4:
        print(
            "usage: master_align.py <seg> <master> <coarse_anchor> [slope]",
            file=sys.stderr,
        )
        return 2
    seg_path, master_path = sys.argv[1], sys.argv[2]
    coarse_anchor = float(sys.argv[3])
    coarse_slope = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0

    seg_env = _env(_pcm(seg_path))
    master_env = _env(_pcm(master_path))
    fps = SR / HOP
    seg_dur = len(seg_env) / fps
    if seg_dur < 4:
        print(json.dumps({"refined": False, "reason": "segment too short"}))
        return 0

    search = int(SEARCH_S * fps)
    split = 0.45  # correlate [0, 0.45d) and [0.55d, d) independently

    w1 = seg_env[: int(seg_dur * split * fps)]
    w2 = seg_env[int(seg_dur * split * fps) :]
    t2 = seg_dur * split  # segment time where window 2 starts

    r1 = _ncc(
        w1, master_env,
        int((coarse_anchor - SEARCH_S) * fps),
        int((coarse_anchor + SEARCH_S + seg_dur * split) * fps),
    )
    r2 = _ncc(
        w2, master_env,
        int((coarse_anchor + t2 * coarse_slope - SEARCH_S) * fps),
        int((coarse_anchor + t2 * coarse_slope + SEARCH_S + seg_dur * (1 - split)) * fps),
    )
    del search

    if not r1 or r1[1] < MIN_SCORE:
        print(json.dumps({
            "refined": False,
            "reason": "weak correlation",
            "score1": r1[1] if r1 else None,
        }))
        return 0

    anchor = r1[0] / fps
    slope = coarse_slope
    if r2 and r2[1] >= MIN_SCORE:
        m2 = r2[0] / fps  # master position at segment t=t2
        rate = (m2 - anchor) / t2
        if 0.9 < rate < 1.1:
            slope = rate
    confidence = round(min(r1[1], r2[1] if r2 else r1[1]), 3)
    print(json.dumps({
        "refined": True,
        "anchor": round(anchor, 3),
        "slope": round(slope, 5),
        "confidence": confidence,
        "score1": round(r1[1], 3),
        "score2": round(r2[1], 3) if r2 else None,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
