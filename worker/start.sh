#!/bin/bash
set -e

# Remote Audio QA Worker startup for RunPod Load Balancer.

export PYTHONUNBUFFERED=1
export QA_CACHE_DIR=/workspace/qa_cache
export QA_WORK_DIR=/workspace/runpod_qa_work
export TORCH_HOME=/workspace/.torch_home
export PRELOAD_DEMUCS_MODELS=htdemucs

cd /app
exec python worker/handler.py
