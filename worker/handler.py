#!/usr/bin/env python3
"""FastAPI handler for the RunPod Load Balancer Audio QA worker."""
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from time import time
from typing import Any, Dict

import torch
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from src.qa.audio_qa_executor import (
    AudioQAErrorType,
    AudioQARequest,
    AudioQAResult,
    run_audio_qa,
)
from src.qa.separator import PyTorchDemucsSeparator

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
    _preload_models()
    yield


app = FastAPI(title="Remote Audio QA Worker", lifespan=lifespan)


class QAInput(BaseModel):
    input: Dict[str, Any]


@app.get("/ping")
def ping() -> Dict[str, str]:
    return {
        "status": "ok",
        "version": os.environ.get("WORKER_VERSION", "unknown"),
    }


def _safe_record_id(payload: Dict[str, Any]) -> str:
    return str(payload.get("record_id") or payload.get("id") or "unknown")


def _redact_input(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the input with sensitive URLs redacted."""
    redacted = {k: v for k, v in input_data.items() if k != "input"}
    if "audio_url" in redacted and isinstance(redacted["audio_url"], str):
        redacted["audio_url"] = _redact_url(redacted["audio_url"])
    if "stem_upload_put_url" in redacted and isinstance(redacted["stem_upload_put_url"], str):
        redacted["stem_upload_put_url"] = _redact_url(redacted["stem_upload_put_url"])
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
