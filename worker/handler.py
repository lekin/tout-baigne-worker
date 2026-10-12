#!/usr/bin/env python3
"""FastAPI handler for the RunPod Load Balancer Audio QA + Karaoke Render worker."""
import logging
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from time import time
from typing import Any, Dict, Optional, Tuple

import torch
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.background import BackgroundTask

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from src.qa.audio_qa_executor import (
    AudioQAErrorType,
    AudioQARequest,
    AudioQAResult,
    run_audio_qa,
)
from src.qa.separator import PyTorchDemucsSeparator
from src.render.executor import (
    KaraokeRenderRequest,
    KaraokeRenderResult,
    run_karaoke_render,
)
from src.render.steps import download_asset
from src.video.highlights import analyze_highlights
from src.video.keywords import extract_keywords

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("audio_qa_worker")


def _gpu_name() -> str:
    if torch.cuda.is_available():
        try:
            return torch.cuda.get_device_name(0)
        except Exception:
            return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _preload_models() -> None:
    """Preload common Demucs models so warm jobs avoid download latency."""
    models_to_load = os.environ.get("PRELOAD_DEMUCS_MODELS", "htdemucs").split(",")
    for model_name in models_to_load:
        model_name = model_name.strip()
        if not model_name:
            continue
        logger.info("Preloading Demucs model: %s", model_name)
        try:
            PyTorchDemucsSeparator.preload_model(model_name)
            logger.info("Preloaded %s", model_name)
        except Exception as e:
            logger.warning("Could not preload %s: %s", model_name, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Remote Audio QA worker starting")
    _preload_models()
    logger.info("Remote Audio QA worker ready")
    yield


app = FastAPI(title="Remote Audio QA Worker", lifespan=lifespan)


class QAInput(BaseModel):
    input: Dict[str, Any]


# Rendered files awaiting client download (file_token return mode).
_RENDER_FILES: Dict[str, Tuple[str, float]] = {}
_RENDER_FILES_LOCK = threading.Lock()
_RENDER_FILE_TTL_S = 60 * 60

# Async render jobs (file_token return mode). The RunPod Load Balancer cuts
# requests after ~3min, so renders run in a background thread and the client
# polls GET /render/{token} then downloads GET /files/{token}.
_RENDER_JOBS: Dict[str, Dict[str, Any]] = {}
_RENDER_JOBS_LOCK = threading.Lock()
_RENDER_JOB_TTL_S = 60 * 60


@app.get("/ping")
def ping() -> Dict[str, str]:
    return {
        "status": "ok",
        "version": os.environ.get("WORKER_VERSION", "unknown"),
        "gpu": _gpu_name(),
    }


def _run_ffmpeg_info(flag: str) -> str:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", flag],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return (result.stdout or "") + (result.stderr or "")
    except Exception:
        return ""


def _nvenc_probe() -> str:
    """Actually try a 1s nvenc encode — 'ok' or the stderr tail."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i",
             "testsrc2=s=320x240:d=1:r=24", "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        if r.returncode == 0:
            return "ok"
        tail = (r.stderr or "").strip().splitlines()
        return "fail: " + " | ".join(tail[-4:])
    except Exception as e:
        return f"fail: {type(e).__name__}: {e}"


def _nvidia_smi() -> str:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        )
        return (r.stdout or r.stderr or "").strip()[:200]
    except Exception as e:
        return f"{type(e).__name__}: {e}"


@app.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    """Report render-relevant capabilities for post-deploy verification."""
    encoders_out = _run_ffmpeg_info("-encoders")
    filters_out = _run_ffmpeg_info("-filters")
    fonts = []
    try:
        fc = subprocess.run(
            ["fc-list", ":", "family"], capture_output=True, text=True, timeout=15,
        )
        fonts = sorted({line.strip().split(",")[0] for line in fc.stdout.splitlines() if line.strip()})
    except Exception:
        pass
    overlay_dir = os.environ.get("KARAOKE_OVERLAY_DIR", "/app/overlays")
    overlays = sorted(os.listdir(overlay_dir)) if os.path.isdir(overlay_dir) else []
    import re as _re

    return {
        "status": "ok",
        "version": os.environ.get("WORKER_VERSION", "unknown"),
        "gpu": _gpu_name(),
        "cpu_count": os.cpu_count(),
        "encoders": {
            "h264_nvenc": "h264_nvenc" in encoders_out,
            "h264_videotoolbox": "h264_videotoolbox" in encoders_out,
            "libx264": "libx264" in encoders_out,
        },
        "nvenc_probe": _nvenc_probe(),
        "nvidia_smi": _nvidia_smi(),
        "driver_caps": os.environ.get("NVIDIA_DRIVER_CAPABILITIES", ""),
        "filters": {
            "ass": bool(_re.search(r"(?<![\w-])ass(?![\w-])", filters_out)),
            "subtitles": "subtitles" in filters_out,
        },
        "fonts": fonts,
        "overlays": overlays,
        "intro": os.path.exists(os.environ.get("KARAOKE_INTRO_PATH", "/app/gkda-yellow.mp4")),
        "logo": os.path.exists(os.environ.get("KARAOKE_LOGO_PATH", "/app/Logo-YellowDropShadow.png")),
    }


def _cleanup_path(path: Optional[str]) -> None:
    try:
        if path:
            p = Path(path)
            if p.exists() and p.is_dir():
                import shutil

                shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


def _expire_render_jobs_locked() -> None:
    """Drop expired job entries and their files. Caller holds _RENDER_JOBS_LOCK."""
    now = time()
    for tok, job in list(_RENDER_JOBS.items()):
        if job.get("expires", 0) < now:
            _RENDER_JOBS.pop(tok, None)
            with _RENDER_FILES_LOCK:
                entry = _RENDER_FILES.pop(tok, None)
            if entry:
                _cleanup_path(str(Path(entry[0]).parent))


def _render_job_runner(token: str, render_request: KaraokeRenderRequest, gpu: str) -> None:
    try:
        result = run_karaoke_render(render_request, gpu_name=gpu)
    except Exception as e:
        logger.exception("Async render job %s unhandled exception", token)
        result = KaraokeRenderResult(
            success=False,
            record_id=render_request.record_id,
            error=f"{type(e).__name__}: {e}",
            error_type="internal_error",
            worker={"gpu": gpu},
        )
    logger.info(
        "Async render job %s completed: success=%s encoder=%s size=%s error=%s",
        token, result.success, result.encoder_used, result.size_bytes, result.error,
    )
    with _RENDER_JOBS_LOCK:
        job = _RENDER_JOBS.get(token)
        if job is None:
            if result.output_path:
                _cleanup_path(str(Path(result.output_path).parent))
            return
        job["status"] = "success" if result.success else "failed"
        payload = asdict(result)
        payload.pop("output_path", None)
        job["result"] = payload
        job["expires"] = time() + _RENDER_JOB_TTL_S
    if result.success and result.output_path:
        with _RENDER_FILES_LOCK:
            _RENDER_FILES[token] = (result.output_path, time() + _RENDER_FILE_TTL_S)


def _render_response_headers(result: KaraokeRenderResult) -> Dict[str, str]:
    import json as _json

    headers = {
        "X-Render-Success": str(bool(result.success)).lower(),
        "X-Render-Encoder": result.encoder_used or "",
        "X-Render-Size": str(result.size_bytes or 0),
        "X-Render-Sha256": result.sha256 or "",
        "X-Render-Timings": _json.dumps(result.timings or {}),
    }
    if result.error:
        headers["X-Render-Error"] = result.error[:500]
        headers["X-Render-Error-Type"] = result.error_type or ""
    return headers


@app.post("/render")
async def render(req: QAInput, request: Request):
    job_id = request.headers.get("x-runpod-job-id") or f"render-{int(time() * 1000)}"
    logger.info("Render job %s received: %s", job_id, _redact_input(req.input))

    try:
        render_request = KaraokeRenderRequest.from_payload(req.input)
    except (ValidationError, ValueError) as e:
        logger.warning("Render job %s validation error: %s", job_id, e)
        return JSONResponse(
            status_code=400,
            content=asdict(KaraokeRenderResult(
                success=False, error=f"invalid_request: {e}",
                error_type="input_error", worker={"gpu": _gpu_name()},
            )),
        )
    except Exception as e:
        logger.exception("Render job %s failed to parse request", job_id)
        return JSONResponse(
            status_code=400,
            content=asdict(KaraokeRenderResult(
                success=False, error=f"request_parse_error: {e}",
                error_type="input_error", worker={"gpu": _gpu_name()},
            )),
        )

    if render_request.return_mode == "file_token":
        # Async mode: the LB kills long requests, so run the render in a
        # thread and let the client poll GET /render/{token}.
        token = uuid.uuid4().hex
        with _RENDER_JOBS_LOCK:
            _expire_render_jobs_locked()
            _RENDER_JOBS[token] = {
                "status": "running",
                "created": time(),
                "expires": time() + _RENDER_JOB_TTL_S,
                "result": None,
            }
        threading.Thread(
            target=_render_job_runner,
            args=(token, render_request, _gpu_name()),
            daemon=True,
        ).start()
        return JSONResponse(content={
            "status": "accepted",
            "job_token": token,
            "status_path": f"/render/{token}",
        })

    try:
        result = run_karaoke_render(render_request, gpu_name=_gpu_name())
    except Exception as e:
        logger.exception("Render job %s unhandled exception", job_id)
        result = KaraokeRenderResult(
            success=False,
            record_id=render_request.record_id,
            error=f"{type(e).__name__}: {e}",
            error_type="internal_error",
            worker={"gpu": _gpu_name()},
        )

    logger.info(
        "Render job %s completed: success=%s encoder=%s size=%s error=%s",
        job_id, result.success, result.encoder_used, result.size_bytes, result.error,
    )

    if not result.success:
        return JSONResponse(
            status_code=500,
            content=asdict(result),
            headers=_render_response_headers(result),
        )

    # Default: stream the rendered MP4 back directly.
    job_dir = str(Path(result.output_path).parent)
    return FileResponse(
        result.output_path,
        media_type="video/mp4",
        filename=result.output_filename or "karaoke.mp4",
        headers=_render_response_headers(result),
        background=BackgroundTask(_cleanup_path, job_dir),
    )


@app.get("/render/{token}")
def render_job_status(token: str):
    with _RENDER_JOBS_LOCK:
        _expire_render_jobs_locked()
        job = _RENDER_JOBS.get(token)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown_or_expired_job")
    if job["status"] == "running":
        return JSONResponse(content={"status": "running"})
    payload = dict(job.get("result") or {})
    payload["status"] = job["status"]
    if job["status"] == "success":
        payload["download_path"] = f"/files/{token}"
        payload["download_token"] = token
    return JSONResponse(
        content=payload,
        status_code=200 if job["status"] == "success" else 500,
    )


@app.get("/files/{token}")
def download_rendered_file(token: str):
    with _RENDER_FILES_LOCK:
        entry = _RENDER_FILES.get(token)
    if not entry or entry[1] < time():
        raise HTTPException(status_code=404, detail="unknown_or_expired_token")
    path, _expiry = entry
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="file_no_longer_available")
    # No cleanup here: the file must survive retries until the job expires
    # (_expire_render_jobs_locked removes the job dir).
    return FileResponse(path, media_type="video/mp4", filename=Path(path).name)


def _safe_record_id(payload: Dict[str, Any]) -> str:
    return str(payload.get("record_id") or payload.get("id") or "unknown")


def _redact_input(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the input with sensitive URLs redacted."""
    redacted = {k: v for k, v in input_data.items() if k != "input"}
    for key in ("audio_url", "stem_upload_put_url", "video_url", "gdrive_video_url", "youtube_url"):
        if key in redacted and isinstance(redacted[key], str):
            redacted[key] = _redact_url(redacted[key])
    if "ass_content" in redacted:
        redacted["ass_content"] = f"<{len(str(redacted['ass_content']))} chars>"
    if "srt_content" in redacted:
        redacted["srt_content"] = f"<{len(str(redacted['srt_content']))} chars>"
    return redacted


def _redact_url(url: str) -> str:
    if "?" in url:
        return url.split("?")[0] + "?<redacted>"
    return url


@app.post("/run")
async def run(req: QAInput, request: Request) -> Dict[str, Any]:
    job_id = request.headers.get("x-runpod-job-id") or f"local-{int(time() * 1000)}"
    logger.info("Job %s received: %s", job_id, _redact_input(req.input))

    try:
        qa_request = AudioQARequest.from_payload(req.input)
    except ValidationError as e:
        logger.warning("Job %s validation error: %s", job_id, e)
        result = AudioQAResult(
            success=False,
            error=f"invalid_request: {e}",
            error_type=AudioQAErrorType.input_error.value,
            worker={"gpu": _gpu_name()},
        )
        return asdict(result)
    except Exception as e:
        logger.exception("Job %s failed to parse request", job_id)
        result = AudioQAResult(
            success=False,
            error=f"request_parse_error: {e}",
            error_type=AudioQAErrorType.input_error.value,
            worker={"gpu": _gpu_name()},
        )
        return asdict(result)

    try:
        result = run_audio_qa(qa_request, gpu_name=_gpu_name())
    except Exception as e:
        logger.exception("Job %s unhandled exception", job_id)
        result = AudioQAResult(
            success=False,
            record_id=qa_request.record_id,
            error=f"{type(e).__name__}: {e}",
            error_type=AudioQAErrorType.internal_error.value,
            worker={"gpu": _gpu_name()},
        )

    logger.info(
        "Job %s completed: success=%s status=%s error_type=%s",
        job_id,
        result.success,
        result.status,
        result.error_type,
    )
    return asdict(result)


_ANALYZE_MAX_DURATION_S = 30 * 60


@app.post("/analyze-video")
async def analyze_video(req: QAInput, request: Request) -> Dict[str, Any]:
    """Highlight/segment analysis for a captation video.

    input: {video_url, target_seconds?, max_segments?}
    Downloads to a temp file, runs ffmpeg-based scoring, returns ranked
    candidate segments for reel extraction. Sync — the LB ~3min window is
    ample for typical captation clips; long videos are rejected up front.
    """
    job_id = request.headers.get("x-runpod-job-id") or f"analyze-{int(time() * 1000)}"
    logger.info("Analyze job %s received: %s", job_id, _redact_input(req.input))

    video_url = str(req.input.get("video_url") or "")
    if not video_url:
        return {"success": False, "error": "missing video_url",
                "error_type": "input_error", "worker": {"gpu": _gpu_name()}}
    try:
        target_seconds = float(req.input.get("target_seconds") or 20.0)
        max_segments = int(req.input.get("max_segments") or 8)
        target_seconds = max(3.0, min(180.0, target_seconds))
        max_segments = max(1, min(50, max_segments))
        include_keywords = bool(req.input.get("include_keywords", True))
        keyword_frames = max(4, min(20, int(req.input.get("keyword_frames") or 10)))
        keyword_top_k = max(1, min(20, int(req.input.get("keyword_top_k") or 8)))
    except (TypeError, ValueError) as e:
        return {"success": False, "error": f"invalid params: {e}",
                "error_type": "input_error", "worker": {"gpu": _gpu_name()}}

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".mp4", delete=False, dir=tempfile.gettempdir()
        ) as tmp:
            tmp_path = tmp.name
        if not download_asset(video_url, tmp_path, timeout_seconds=300):
            return {"success": False, "error": "video download failed",
                    "error_type": "download_error", "worker": {"gpu": _gpu_name()}}

        result = analyze_highlights(
            tmp_path,
            target_seconds=target_seconds,
            max_segments=max_segments,
        )
        if include_keywords:
            # Keywords are best-effort — a model/cache failure must not
            # cost the caller the segment analysis.
            try:
                result["keywords"] = extract_keywords(
                    tmp_path,
                    duration=result["duration"],
                    n_frames=keyword_frames,
                    top_k=keyword_top_k,
                ).get("keywords", [])
            except Exception:
                logger.exception("Analyze job %s keyword extraction failed", job_id)
                result["keywords"] = []
    except subprocess.TimeoutExpired:
        logger.warning("Analyze job %s timed out", job_id)
        return {"success": False, "error": "analysis timed out",
                "error_type": "internal_error", "worker": {"gpu": _gpu_name()}}
    except Exception as e:
        logger.exception("Analyze job %s unhandled exception", job_id)
        return {"success": False, "error": f"{type(e).__name__}: {e}",
                "error_type": "internal_error", "worker": {"gpu": _gpu_name()}}
    finally:
        _cleanup_path(tmp_path)

    if result.get("duration", 0) > _ANALYZE_MAX_DURATION_S:
        return {"success": False, "error": "video exceeds 30min limit",
                "error_type": "input_error", "worker": {"gpu": _gpu_name()}}

    logger.info("Analyze job %s completed: %d segments", job_id,
                len(result.get("segments", [])))
    return {"success": True, "worker": {"gpu": _gpu_name()}, **result}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
