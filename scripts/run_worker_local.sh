#!/usr/bin/env bash
# Run the worker handler locally on the project venv.
# This is for local development / smoke testing; it does not use RunPod.
set -euo pipefail

cd "$(dirname "$0")/.."

export WORKER_VERSION="${WORKER_VERSION:-$(git rev-parse --short HEAD 2>/dev/null || echo local)}"

.venv/bin/python worker/handler.py
