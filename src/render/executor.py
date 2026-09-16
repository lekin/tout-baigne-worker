"""Standalone karaoke render executor for the remote GPU worker.

Mirrors the local `generate` pipeline in src/cli.py:
  download assets -> ASS -> ffmpeg render -> audio mux -> intro overlay.

The worker never talks to Airtable: the client sends everything needed
(prebuilt ASS content, asset URLs, offsets) and downloads the MP4 result.
"""
import hashlib
import logging
import os
import re
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.render.steps import (
    _looks_like_html,
    apply_intro_overlay,
    download_asset,
    get_audio_duration,
    mux_replace_audio,
    resolve_video_encoder,
    synth_color_background,
)

logger = logging.getLogger("karaoke_render_worker")


class RenderErrorType(str, Enum):
    input_error = "input_error"
    download_error = "download_error"
    render_error = "render_error"
    internal_error = "internal_error"


@dataclass
class KaraokeRenderRequest:
    """Everything needed to render a final karaoke video remotely."""

    record_id: Optional[str] = None
    track_name: str = "karaoke"
    # Either a fully-built ASS file (preferred) or raw SRT to convert.
    ass_content: Optional[str] = None
    srt_content: Optional[str] = None
    # Asset URLs. audio_url is required; video falls back to a color background.
    audio_url: str = ""
    video_url: Optional[str] = None
    gdrive_video_url: Optional[str] = None
    youtube_url: Optional[str] = None
    # Render options
    audio_to_video_offset_s: float = 0.0
    fast: bool = False
    encoder: str = "auto"  # auto | nvenc | x264 | videotoolbox
    overlay_name: Optional[str] = None  # basename inside the baked overlays dir
    overlay_hue_deg: Optional[float] = None
    overlay_saturation: Optional[float] = None
    bg_color: str = "#64b5f6"
    apply_intro: bool = True
    video_width: int = 1920
    video_height: int = 1080
    return_mode: str = "binary"  # binary | file_token

    @classmethod
    def from_payload(cls, data: Dict[str, Any]) -> "KaraokeRenderRequest":
        if "input" in data:
            data = data["input"]

        if "overlay" in data and isinstance(data["overlay"], dict):
            ov = data.pop("overlay")
            data.setdefault("overlay_name", ov.get("name"))
            data.setdefault("overlay_hue_deg", ov.get("hue_deg"))
            data.setdefault("overlay_saturation", ov.get("saturation"))
        if "output" in data and isinstance(data["output"], dict):
            out = data.pop("output")
            data.setdefault("video_width", out.get("width", 1920))
            data.setdefault("video_height", out.get("height", 1080))
        if "options" in data and isinstance(data["options"], dict):
            opts = data.pop("options")
            data.setdefault("fast", opts.get("fast", False))
            data.setdefault("encoder", opts.get("encoder", "auto"))
            data.setdefault("apply_intro", opts.get("apply_intro", True))
            data.setdefault("return_mode", opts.get("return_mode", "binary"))

        if not data.get("ass_content") and not data.get("srt_content"):
            raise ValueError("ass_content or srt_content is required")
        if not data.get("audio_url"):
            raise ValueError("audio_url is required")

        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class KaraokeRenderResult:
    success: bool
    record_id: Optional[str] = None
    output_path: Optional[str] = None
    output_filename: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    encoder_used: Optional[str] = None
    timings: Dict[str, float] = field(default_factory=dict)
    download_token: Optional[str] = None
    download_path: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    worker: Optional[Dict[str, Any]] = None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_output_name(track_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9 ._()\-\[\]&']+", "_", str(track_name or "karaoke"))
    safe = safe.replace("'", "").strip(" ._") or "karaoke"
    return safe[:120]


def _resolve_overlay_path(overlay_name: Optional[str]) -> Optional[str]:
    """Resolve an overlay basename inside the configured overlays directory."""
    if not overlay_name:
        return None
    from src.config import settings

    overlay_dir = Path(settings.karaoke_overlay_dir)
    if not overlay_dir.is_absolute():
        overlay_dir = Path(__file__).resolve().parents[2] / overlay_dir
    candidate = overlay_dir / os.path.basename(overlay_name)
    if candidate.is_file():
        return str(candidate)
    logger.warning("Requested overlay %s not found in %s", overlay_name, overlay_dir)
    return None


def _download_video(req: KaraokeRenderRequest, job_dir: Path) -> Optional[str]:
    """Download the music video from the best available source."""
    candidates = [req.video_url, req.gdrive_video_url]
    for url in candidates:
        if not url:
            continue
        dst = str(job_dir / "video.mp4")
        logger.info("Downloading video: %s", url.split("?")[0])
        if download_asset(url, dst, timeout_seconds=600):
            return dst
        logger.warning("Video download failed for %s", url.split("?")[0])

    if req.youtube_url:
        try:
            from src.karaoke_generator import KaraokeGenerator

            dst = str(job_dir / "video_yt.mp4")
            gen = KaraokeGenerator()
            logger.info("Downloading video from YouTube: %s", req.youtube_url)
            if gen.download_youtube_video(req.youtube_url, dst):
                return dst
        except Exception as e:
            logger.warning("YouTube download failed: %s", e)
    return None


def run_karaoke_render(
    request: KaraokeRenderRequest,
    work_dir: Optional[str] = None,
    gpu_name: Optional[str] = None,
) -> KaraokeRenderResult:
    """Run the full karaoke render pipeline. Result keeps output_path for the
    handler to stream back (binary mode) or serve via a download token."""
    t0 = time.monotonic()
    timings: Dict[str, float] = {}
    worker = {"gpu": gpu_name} if gpu_name else {}

    base_dir = Path(work_dir or os.environ.get("RENDER_WORK_DIR") or "output/render_jobs")
    job_dir = base_dir / f"{request.record_id or 'job'}-{uuid.uuid4().hex[:8]}"
    job_dir.mkdir(parents=True, exist_ok=True)

    result = KaraokeRenderResult(success=False, record_id=request.record_id, worker=worker)

    try:
        # 1. Audio
        t = time.monotonic()
        audio_path = job_dir / "audio.mp3"
        logger.info("Downloading audio for %s", request.record_id)
        if not download_asset(request.audio_url, str(audio_path), timeout_seconds=600):
            result.error = "audio_download_failed"
            result.error_type = RenderErrorType.download_error.value
            return result
        if _looks_like_html(str(audio_path)) or audio_path.stat().st_size <= 1000:
            result.error = "audio_download_invalid"
            result.error_type = RenderErrorType.download_error.value
            return result
        timings["audio_download_s"] = round(time.monotonic() - t, 3)

        audio_duration = get_audio_duration(str(audio_path))

        # 2. Video (or color background fallback)
        t = time.monotonic()
        video_path = _download_video(request, job_dir)
        timings["video_download_s"] = round(time.monotonic() - t, 3)
        if not video_path:
            logger.info("No video source; using color background %s", request.bg_color)
            bg_path = job_dir / "bg.mp4"
            synth_color_background(
                request.bg_color, str(bg_path),
                request.video_width, request.video_height,
                float(audio_duration or 180), fast=request.fast,
            )
            video_path = str(bg_path)

        # 3. Subtitles
        ass_path: Optional[str] = None
        if request.ass_content:
            ass_path = str(job_dir / "karaoke.ass")
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(request.ass_content)

        # 4. Render (subtitles + overlay + logo)
        t = time.monotonic()
        from src.config import settings
        from src.karaoke_generator import KaraokeGenerator

        repo_root = Path(__file__).resolve().parents[2]
        intro_path = Path(settings.karaoke_intro_path)
        if not intro_path.is_absolute():
            intro_path = repo_root / intro_path

        gen = KaraokeGenerator(
            video_width=request.video_width,
            video_height=request.video_height,
        )
        temp_output = str(job_dir / "render_temp.mp4")
        effect_paths = None
        overlay_path = _resolve_overlay_path(request.overlay_name)
        if overlay_path:
            effect_paths = [overlay_path]

        _codec_args, _fps_args, encoder_name = resolve_video_encoder(request.encoder, fast=request.fast)
        logger.info("Rendering with encoder=%s (requested=%s)", encoder_name, request.encoder)

        ok, render_res = gen.generate_karaoke(
            video_path=video_path,
            srt_content=request.srt_content if not ass_path else None,
            ass_file=ass_path,
            output_path=temp_output,
            fast_mode=request.fast,
            max_duration_seconds=audio_duration,
            effect_overlay_paths=effect_paths,
            overlay_hue_deg=request.overlay_hue_deg,
            overlay_saturation=request.overlay_saturation,
            overlay_tint_color=request.bg_color,
            encoder=request.encoder,
        )
        timings["render_s"] = round(time.monotonic() - t, 3)
        if not ok:
            result.error = f"render_failed: {render_res}"
            result.error_type = RenderErrorType.render_error.value
            return result

        # 5. Mux original audio
        t = time.monotonic()
        output_name = f"{_safe_output_name(request.track_name)}_karaoke.mp4"
        final_path = job_dir / output_name
        if request.audio_to_video_offset_s:
            logger.info("Applying audio-to-video offset %+.2fs", request.audio_to_video_offset_s)
        mux_replace_audio(
            temp_output, str(audio_path), str(final_path),
            offset_s=float(request.audio_to_video_offset_s or 0.0),
            fast=request.fast, encoder=request.encoder,
        )
        timings["mux_s"] = round(time.monotonic() - t, 3)

        # 6. Intro overlay
        if request.apply_intro and intro_path.exists():
            t = time.monotonic()
            with_intro = job_dir / f"{final_path.stem}_intro.mp4"
            try:
                if apply_intro_overlay(
                    str(final_path), str(with_intro), str(intro_path),
                    request.video_width, request.video_height,
                    fast=request.fast, encoder=request.encoder,
                ):
                    os.replace(str(with_intro), str(final_path))
            except Exception as e:
                logger.warning("Intro overlay failed, keeping pre-intro output: %s", e)
            timings["intro_s"] = round(time.monotonic() - t, 3)

        result.success = True
        result.output_path = str(final_path)
        result.output_filename = output_name
        result.size_bytes = final_path.stat().st_size
        result.sha256 = _sha256_file(str(final_path))
        result.encoder_used = encoder_name
        timings["total_s"] = round(time.monotonic() - t0, 3)
        result.timings = timings
        return result

    except Exception as e:
        logger.exception("Render failed")
        result.error = f"{type(e).__name__}: {e}"
        result.error_type = RenderErrorType.internal_error.value
        result.timings = timings
        return result
