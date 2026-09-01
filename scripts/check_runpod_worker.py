#!/usr/bin/env python3
"""Authenticated RunPod Load Balancer readiness probe.

Usage:
    export RUNPOD_API_KEY=...
    python scripts/check_runpod_worker.py --endpoint ylkhb72ej3hijz --timeout 120

The probe sends an `Authorization: Bearer <RUNPOD_API_KEY>` header to the
Load Balancer `/ping` endpoint, logs every attempt, and fails fast on 401/403.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise RuntimeError("missing_api_key: RUNPOD_API_KEY is not set")
    return key


def _lb_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}"}


def _redact_query(url: str) -> str:
    if "?" in url:
        return url.split("?")[0] + "?<redacted>"
    return url


def _format_status(status: int) -> str:
    if status < 200:
        return "info"
    if status < 300:
        return "ok"
    if status == 401 or status == 403:
        return "auth_error"
    if status == 408 or status == 429:
        return "rate/timeout"
    if status >= 500:
        return "server_error"
    return "client_error"


def _should_retry(status: int) -> bool:
    """Retry only transient/retryable HTTP statuses."""
    return status in (408, 429, 500, 502, 503, 504)


def _should_fail_fast(status: int) -> bool:
    """Auth and some client errors should not be retried."""
    return status in (401, 403) or (400 <= status < 500 and status not in (408, 429))


def _safe_response_preview(response: Optional[requests.Response], max_len: int = 120) -> str:
    if response is None:
        return "<no response>"
    try:
        text = response.text[:max_len]
        return text.strip()
    except Exception:
        return "<unreadable>"


@dataclass
class ProbeResult:
    ready: bool
    attempts: int
    ready_seconds: Optional[float]
    status: Optional[int]
    version: Optional[str]
    error: Optional[str]
    error_type: Optional[str]
    log: list


class RunPodReadinessProbe:
    """Probe a RunPod Load Balancer endpoint for readiness."""

    def __init__(
        self,
        endpoint_id: str,
        expected_version: Optional[str] = None,
        overall_timeout: int = 300,
        poll_interval: float = 3.0,
        request_timeout: tuple = (10.0, 30.0),
        base_url: Optional[str] = None,
    ):
        self.endpoint_id = endpoint_id
        self.base_url = base_url or f"https://{endpoint_id}.api.runpod.ai"
        self.ping_url = f"{self.base_url}/ping"
        self.expected_version = expected_version
        self.overall_timeout = overall_timeout
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout

    def probe(self) -> ProbeResult:
        headers = _lb_headers()
        deadline = time.monotonic() + self.overall_timeout
        log: list = []
        attempts = 0
        first_status: Optional[int] = None
        start = time.monotonic()

        while time.monotonic() < deadline:
            attempts += 1
            attempt_start = time.monotonic()
            response: Optional[requests.Response] = None
            try:
                response = requests.get(
                    self.ping_url,
                    headers=headers,
                    timeout=self.request_timeout,
                )
                status = response.status_code
                elapsed = time.monotonic() - attempt_start
                if first_status is None:
                    first_status = status

                preview = _safe_response_preview(response)
                log.append({
                    "attempt": attempts,
                    "elapsed": round(time.monotonic() - start, 2),
                    "status": status,
                    "request_seconds": round(elapsed, 2),
                    "body_preview": preview,
                })
                print(
                    f"{attempts:02d}   {time.monotonic() - start:.2f}s   HTTP {status}   "
                    f"request={elapsed:.2f}s   body={preview}",
                    flush=True,
                )

                if _should_fail_fast(status):
                    return ProbeResult(
                        ready=False,
                        attempts=attempts,
                        ready_seconds=None,
                        status=status,
                        version=None,
                        error=f"RunPod Load Balancer authentication failed: HTTP {status}",
                        error_type="authentication_failed" if status in (401, 403) else "unexpected_http_status",
                        log=log,
                    )

                if status == 200:
                    try:
                        body = response.json()
                    except Exception:
                        body = {}
                    if body.get("status") != "ok":
                        print(f"  ping returned status={body.get('status')}; continuing to poll")
                        time.sleep(self.poll_interval)
                        continue

                    version = body.get("version")
                    if self.expected_version and not (version == self.expected_version or (version or "").startswith(self.expected_version) or (self.expected_version or "").startswith(version or "")):
                        return ProbeResult(
                            ready=False,
                            attempts=attempts,
                            ready_seconds=None,
                            status=status,
                            version=version,
                            error=(
                                f"version_mismatch: expected {self.expected_version}, "
                                f"got {version}"
                            ),
                            error_type="version_mismatch",
                            log=log,
                        )

                    return ProbeResult(
                        ready=True,
                        attempts=attempts,
                        ready_seconds=round(time.monotonic() - start, 2),
                        status=status,
                        version=version,
                        error=None,
                        error_type=None,
                        log=log,
                    )

                if _should_retry(status):
                    time.sleep(self.poll_interval)
                    continue

                # Any other status we haven't handled is unexpected
                return ProbeResult(
                    ready=False,
                    attempts=attempts,
                    ready_seconds=None,
                    status=status,
                    version=None,
                    error=f"unexpected HTTP {status} from {self.ping_url}",
                    error_type="unexpected_http_status",
                    log=log,
                )

            except requests.exceptions.Timeout as e:
                elapsed = time.monotonic() - attempt_start
                log.append({
                    "attempt": attempts,
                    "elapsed": round(time.monotonic() - start, 2),
                    "status": None,
                    "request_seconds": round(elapsed, 2),
                    "body_preview": f"request timeout: {e}",
                })
                print(
                    f"{attempts:02d}   {time.monotonic() - start:.2f}s   TIMEOUT   request={elapsed:.2f}s",
                    flush=True,
                )
                time.sleep(self.poll_interval)

            except requests.exceptions.RequestException as e:
                elapsed = time.monotonic() - attempt_start
                log.append({
                    "attempt": attempts,
                    "elapsed": round(time.monotonic() - start, 2),
                    "status": None,
                    "request_seconds": round(elapsed, 2),
                    "body_preview": f"network error: {e}",
                })
                print(
                    f"{attempts:02d}   {time.monotonic() - start:.2f}s   NETWORK_ERROR   {e}",
                    flush=True,
                )
                time.sleep(self.poll_interval)

        return ProbeResult(
            ready=False,
            attempts=attempts,
            ready_seconds=None,
            status=first_status,
            version=None,
            error=f"readiness_timeout: worker did not become ready within {self.overall_timeout}s",
            error_type="readiness_timeout",
            log=log,
        )


def run_smoke(endpoint_id: str, request_timeout: tuple = (3.0, 30.0)) -> float:
    """Run a lightweight authenticated /run smoke test and return elapsed seconds."""
    base_url = f"https://{endpoint_id}.api.runpod.ai"
    url = f"{base_url}/run"
    headers = _lb_headers()
    payload = {
        "input": {
            "record_id": "smoke-test",
            "audio_url": "https://example.com/nonexistent.mp3",
            "lyrics": [],
        }
    }
    start = time.monotonic()
    response = requests.post(url, headers=headers, json=payload, timeout=request_timeout)
    response.raise_for_status()
    result = response.json()
    if result.get("success") is None:
        raise RuntimeError("smoke_test_failed: response missing 'success'")
    print("Smoke /run result:")
    print(json.dumps({
        "success": result.get("success"),
        "status": result.get("status"),
        "error_type": result.get("error_type"),
        "error": result.get("error"),
    }, indent=2))
    return round(time.monotonic() - start, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check RunPod Load Balancer worker readiness")
    parser.add_argument("--endpoint", default=os.environ.get("RUNPOD_ENDPOINT_ID"))
    parser.add_argument("--base-url", default=os.environ.get("RUNPOD_ENDPOINT_BASE_URL"))
    parser.add_argument("--expected-version", default=os.environ.get("RUNPOD_WORKER_EXPECTED_VERSION"))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("RUNPOD_READINESS_TIMEOUT", "300")))
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("RUNPOD_POLL_INTERVAL", "3.0")))
    parser.add_argument("--request-timeout", type=float, default=float(os.environ.get("RUNPOD_REQUEST_TIMEOUT", "30")), help="Per-request timeout in seconds (connect+read total)")
    parser.add_argument("--no-smoke", action="store_true")
    args = parser.parse_args()

    if not args.endpoint:
        print("ERROR: --endpoint or RUNPOD_ENDPOINT_ID is required", file=sys.stderr)
        return 2

    try:
        probe = RunPodReadinessProbe(
            endpoint_id=args.endpoint,
            base_url=args.base_url,
            expected_version=args.expected_version,
            overall_timeout=args.timeout,
            poll_interval=args.poll_interval,
            request_timeout=(max(1.0, args.request_timeout / 4), args.request_timeout),
        )
        result = probe.probe()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print("\nRunPod readiness probe")
    print(f"Endpoint: {args.endpoint}")
    print(f"ready={result.ready}")
    if result.ready:
        print(f"cold_start_seconds={result.ready_seconds}")
        print(f"attempts={result.attempts}")
        print(f"version={result.version}")
        if not args.no_smoke:
            try:
                smoke_seconds = run_smoke(args.endpoint)
                print(f"smoke_run_seconds={smoke_seconds}")
            except Exception as e:
                print(f"smoke_test_failed: {e}", file=sys.stderr)
                return 3
        return 0

    print(f"error_type={result.error_type}")
    print(f"error={result.error}")
    print(f"attempts={result.attempts}")
    print(f"last_status={result.status}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
