"""Highlight/segment detection for event captation videos.

Pure-ffmpeg analysis, no ML deps — designed to run inside the existing
RunPod worker image and locally:

  visual dynamism : lavfi.scene_score per frame (continuous signal — club
                    footage rarely trips absolute cut thresholds)
  loudness        : ebur128 frame metadata -> momentary LUFS timeline

Candidate windows of a target reel duration are scored on:
  - mean momentary loudness (energy proxy — crowd/DJ peaks)
  - mean scene score + spike count (visual dynamism)
and returned ranked, non-overlapping, clamped to the media bounds.
"""
import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass
from typing import List, Tuple

logger = logging.getLogger("video_highlights")

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")
_SCENE_RE = re.compile(r"lavfi\.scene_score=([0-9.eE+-]+)")
_LUFS_RE = re.compile(r"lavfi\.r128\.M=(-?[0-9.]+)")
_META_RE = re.compile(r"frame:\d+\s+pts:\d+\s+pts_time:([0-9.]+)")


def _run(cmd: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


def probe_media(path: str) -> dict:
    """ffprobe duration/width/height/fps."""
    res = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ], timeout=60)
    info = json.loads(res.stdout or "{}")
    dur = float(info.get("format", {}).get("duration") or 0)
    width = height = 0
    fps = 0.0
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            width = int(s.get("width") or 0)
            height = int(s.get("height") or 0)
            fr = s.get("avg_frame_rate") or "0/1"
            try:
                num, den = fr.split("/")
                fps = float(num) / float(den) if float(den) else 0.0
            except Exception:
                fps = 0.0
            break
    return {"duration": dur, "width": width, "height": height, "fps": fps}


def scene_scores(path: str, timeout: int = 600) -> List[Tuple[float, float]]:
    """(t, scene_score) per frame — continuous dynamism signal."""
    res = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", path,
        "-vf", "select='gte(scene,0)',metadata=mode=print:key=lavfi.scene_score",
        "-an", "-f", "null", "-",
    ], timeout=timeout)
    out = res.stderr or ""
    pts = _META_RE.findall(out)
    scores = _SCENE_RE.findall(out)
    return [(float(t), float(s)) for t, s in zip(pts, scores)]


def loudness_timeline(path: str, timeout: int = 600) -> List[Tuple[float, float]]:
    """(t, momentary LUFS) samples from ebur128 frame metadata."""
    res = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", path,
        "-af", "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M",
        "-vn", "-f", "null", "-",
    ], timeout=timeout)
    out = res.stderr or ""
    pts = _META_RE.findall(out)
    lufs = _LUFS_RE.findall(out)
    return [(float(t), float(m)) for t, m in zip(pts, lufs)]


@dataclass
class HighlightSegment:
    start: float
    end: float
    score: float
    mean_lufs: float
    mean_scene: float
    spike_count: int


def _mean_lufs(samples: List[Tuple[float, float]], start: float, end: float) -> float:
    """Energy-mean LUFS over [start,end) (LUFS is logarithmic — mean in linear)."""
    vals = [m for t, m in samples if start <= t < end and m > -70]
    if not vals:
        return -70.0
    linear = sum(10 ** (v / 10.0) for v in vals) / len(vals)
    return 10 * math.log10(linear) if linear > 0 else -70.0


def _window_mean(samples: List[Tuple[float, float]], start: float, end: float) -> float:
    vals = [v for t, v in samples if start <= t < end]
    return sum(vals) / len(vals) if vals else 0.0


def _percentile(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def rank_segments(
    duration: float,
    scenes: List[Tuple[float, float]],
    loudness: List[Tuple[float, float]],
    target_seconds: float = 20.0,
    max_segments: int = 8,
    hop_fraction: float = 0.25,
) -> List[HighlightSegment]:
    """Score sliding windows; return ranked non-overlapping segments."""
    if duration <= 0:
        return []
    win = min(target_seconds, duration)
    hop = max(1.0, win * hop_fraction)

    usable = [m for _, m in loudness if m > -70]
    lo = _percentile(usable, 5)
    hi = _percentile(usable, 95)
    lufs_span = max(1.0, hi - lo)

    scene_vals = [v for _, v in scenes]
    scene_hi = _percentile(scene_vals, 95) or 1.0
    spike_thresh = max(_percentile(scene_vals, 90), 0.05)
    spikes = [t for t, v in scenes if v >= spike_thresh]

    candidates: List[HighlightSegment] = []
    t = 0.0
    while t + win <= duration + 1e-6:
        end = t + win
        lufs = _mean_lufs(loudness, t, end)
        scene = _window_mean(scenes, t, end)
        spike_n = sum(1 for s in spikes if t <= s < end)
        energy = max(0.0, min(1.0, (lufs - lo) / lufs_span))
        dynamism = max(0.0, min(1.0, scene / scene_hi))
        score = round(0.6 * energy + 0.4 * dynamism, 4)
        candidates.append(HighlightSegment(
            start=round(t, 2), end=round(end, 2), score=score,
            mean_lufs=round(lufs, 1), mean_scene=round(scene, 4),
            spike_count=spike_n,
        ))
        t += hop

    candidates.sort(key=lambda s: s.score, reverse=True)
    picked: List[HighlightSegment] = []
    for c in candidates:
        if len(picked) >= max_segments:
            break
        if all(c.end <= p.start or c.start >= p.end for p in picked):
            picked.append(c)
    picked.sort(key=lambda s: s.start)
    return picked


def analyze_highlights(
    video_path: str,
    target_seconds: float = 20.0,
    max_segments: int = 8,
) -> dict:
    """Full analysis on a local video file → serializable result."""
    media = probe_media(video_path)
    duration = media["duration"]
    scenes = scene_scores(video_path)
    loud = loudness_timeline(video_path)
    segments = rank_segments(
        duration, scenes, loud,
        target_seconds=target_seconds, max_segments=max_segments,
    )
    scene_vals = [v for _, v in scenes]
    return {
        "duration": duration,
        "width": media["width"],
        "height": media["height"],
        "fps": media["fps"],
        "orientation": "portrait" if media["height"] > media["width"] else "landscape",
        "scene_score_max": max(scene_vals) if scene_vals else 0.0,
        "target_seconds": target_seconds,
        "segments": [s.__dict__ for s in segments],
    }
