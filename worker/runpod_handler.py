#!/usr/bin/env python3
"""RunPod Serverless QUEUE handler for the Audio QA Worker (legacy)."""
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import runpod

from src.qa.audio_qa_executor import AudioQARequest, run_audio_qa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("audio_qa_worker")


def handler(job):
    """Handle a single RunPod Serverless job."""
    job_input = job.get("input", {})
    if not job_input:
        return asdict(run_audio_qa(AudioQARequest(error="missing_input")))

    try:
        request = AudioQARequest.from_payload(job_input)
    except Exception as e:
        logger.warning("Invalid request: %s", e)
        return asdict(run_audio_qa(AudioQARequest(error=f"invalid_request: {e}")))

    work_dir = os.environ.get("QA_WORK_DIR", "/workspace/runpod_qa_work")
    result = run_audio_qa(request, work_dir=work_dir)
    return asdict(result)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
