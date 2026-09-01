#!/usr/bin/env python3
"""Lightweight smoke test for a deployed RunPod Audio QA worker.

Usage:
    export RUNPOD_ENDPOINT_BASE_URL=https://<id>.api.runpod.ai
    python scripts/smoke_test_worker.py

Performs:
  1. GET /ping
  2. POST /run with a minimal request containing an empty lyric list
  3. Verifies the response can be deserialized and contains the expected fields.
"""
import json
import os
import sys
from typing import Any, Dict

import requests


def _base_url() -> str:
    url = os.environ.get("RUNPOD_ENDPOINT_BASE_URL") or os.environ.get("RUNPOD_ENDPOINT_URL")
    if not url:
        raise RuntimeError("RUNPOD_ENDPOINT_BASE_URL is not set")
    return url.rstrip("/")


def main() -> int:
    base_url = _base_url()
    print(f"Smoke testing endpoint: {base_url}")

    # 1. Health check
    ping_url = f"{base_url}/ping"
    r = requests.get(ping_url, timeout=10)
    print(f"  /ping status: {r.status_code}")
    r.raise_for_status()
    print(f"  /ping body: {r.text}")

    # 2. Send a trivial, intentionally failing-in-Demucs request to confirm pipeline
    run_url = f"{base_url}/run"
    payload: Dict[str, Any] = {
        "input": {
            "record_id": "smoke-test",
            "audio_url": "https://example.com/nonexistent.mp3",
            "lyrics": [],
        }
    }
    r = requests.post(run_url, json=payload, timeout=30)
    print(f"  /run status: {r.status_code}")
    r.raise_for_status()
    result = r.json()

    # 3. Verify fields
    assert "success" in result, "Missing 'success' in response"
    assert "error" in result, "Missing 'error' in response"
    assert "error_type" in result, "Missing 'error_type' in response"
    assert result["success"] is False, "Expected success=False for invalid audio URL"
    assert result["error_type"] in ("input_error", "download_error"), result

    print("  /run result:")
    print(json.dumps({
        "success": result["success"],
        "error_type": result["error_type"],
        "error": result["error"],
    }, indent=2))

    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
