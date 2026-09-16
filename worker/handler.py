#!/usr/bin/env python3
"""FastAPI handler for the RunPod Load Balancer Audio QA + Karaoke Render worker."""
import logging
import os
import subprocess
import sys
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
_RENDER_FILE_TTL_S = 15 * 60


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
        "encoders": {
            "h264_nvenc": "h264_nvenc" in encoders_out,
            "h264_videotoolbox": "h264_videotoolbox" in encoders_out,
            "libx264": "libx264" in encoders_out,
        },
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

    if render_request.return_mode == "file_token":
        token = uuid.uuid4().hex
        with _RENDER_FILES_LOCK:
            # Expire stale entries and remove their files.
            now = time()
            for k, (stale_path, exp) in list(_RENDER_FILES.items()):
                if exp < now:
                    _RENDER_FILES.pop(k, None)
                    _cleanup_path(str(Path(stale_path).parent))
            _RENDER_FILES[token] = (result.output_path, now + _RENDER_FILE_TTL_S)
        result.download_token = token
        result.download_path = f"/files/{token}"
        payload = asdict(result)
        payload.pop("output_path", None)
        return JSONResponse(content=payload, headers=_render_response_headers(result))

    # Default: stream the rendered MP4 back directly.
    job_dir = str(Path(result.output_path).parent)
    return FileResponse(
        result.output_path,
        media_type="video/mp4",
        filename=result.output_filename or "karaoke.mp4",
        headers=_render_response_headers(result),
        background=BackgroundTask(_cleanup_path, job_dir),
    )


@app.get("/files/{token}")
def download_rendered_file(token: str):
    with _RENDER_FILES_LOCK:
        entry = _RENDER_FILES.pop(token, None)
    if not entry:
        raise HTTPException(status_code=404, detail="unknown_or_expired_token")
    path, _expiry = entry
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail="file_no_longer_available")
    job_dir = str(Path(path).parent)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=Path(path).name,
        background=BackgroundTask(_cleanup_path, job_dir),
    )


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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
