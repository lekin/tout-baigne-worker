# Production Docker image for the RunPod Remote Audio QA Worker.
#
# Base:      NVIDIA CUDA 12.1 runtime (Ubuntu 22.04)
# Python:    3.10
# PyTorch:   2.5.1+cu121
# Demucs:    4.0.1
#
# Build:
#   docker build -f worker/Dockerfile -t ghcr.io/lekin/tout-baigne-worker:latest .
#
# Run locally (CPU):
#   docker run --rm -p 8000:8000 ghcr.io/lekin/tout-baigne-worker:latest
#
# Run on GPU:
#   docker run --rm --gpus all -p 8000:8000 ghcr.io/lekin/tout-baigne-worker:latest

FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV TORCH_HOME=/app/.torch_home
ENV QA_CACHE_DIR=/workspace/qa_cache
ENV QA_WORK_DIR=/workspace/runpod_qa_work
ENV PRELOAD_DEMUCS_MODELS=htdemucs

# Install system dependencies: Python, pip, ffmpeg, libsndfile.
RUN apt-get update -qq \
    && apt-get install -y -qq \
        python3.10 \
        python3-pip \
        ffmpeg \
        libsndfile1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Symlink python3.10 as python3 / python for convenience.
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1

WORKDIR /app

# Install CUDA-compatible PyTorch first so demucs can resolve cleanly.
RUN python3 -m pip install --upgrade pip setuptools wheel \
    && python3 -m pip install \
        --index-url https://download.pytorch.org/whl/cu121 \
        torch==2.5.1+cu121 \
        torchaudio==2.5.1+cu121

# Copy and install worker-specific Python requirements.
COPY worker/requirements.txt /app/worker/requirements.txt
RUN python3 -m pip install -r /app/worker/requirements.txt

# Pre-download the default Demucs model so cold starts avoid network latency.
# Keep this layer BEFORE copying src/worker so code-only changes do not
# invalidate the model cache and force a long re-download.
RUN python3 - <<'PY'
from demucs.pretrained import get_model
model_name = __import__('os').environ.get("PRELOAD_DEMUCS_MODELS", "htdemucs").split(",")[0].strip()
print(f"Preloading Demucs model: {model_name}")
get_model(model_name)
print("Preload complete")
PY

# Copy the runtime source code needed by the worker.
COPY src /app/src
COPY worker /app/worker

# Inject the build-time git SHA as late as possible so only this tiny layer
# changes between code-only commits.
ARG WORKER_VERSION=unknown
ENV WORKER_VERSION=${WORKER_VERSION}

EXPOSE 8000

CMD ["python3", "/app/worker/handler.py"]
