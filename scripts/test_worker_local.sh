#!/usr/bin/env bash
# Local smoke test for worker/handler.py.
# Starts the handler, runs the authenticated smoke test, then stops the handler.
set -euo pipefail

cd "$(dirname "$0")/.."

export WORKER_VERSION="${WORKER_VERSION:-local-test}"

.venv/bin/python worker/handler.py &
PID=$!

cleanup() {
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 5

RUNPOD_API_KEY=dummy \
RUNPOD_ENDPOINT_BASE_URL=http://localhost:8000 \
    .venv/bin/python scripts/smoke_test_worker.py
