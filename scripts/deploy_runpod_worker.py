#!/usr/bin/env python3
"""Deploy a specific worker image tag to the configured RunPod endpoint.

Usage:
    export RUNPOD_API_KEY=...
    export RUNPOD_ENDPOINT_ID=ylkhb72ej3hijz
    python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<tag>

The script:
  1. Verifies the image tag can be parsed.
  2. Updates the endpoint template to the requested image.
  3. Scales workers to 0, then back to 1 to force a cold start.
  4. Polls RunPod worker state via the v2 API.
  5. Runs short (30s) readiness probes with a 300s global deadline.
  6. Classifies the failure reason if startup does not succeed.
  7. Runs a smoke test.

Requires `requests` (already in the project venv).
"""
import argparse
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from check_runpod_worker import (
    RunPodReadinessProbe,
    _api_key,
    _redact_query,
    run_smoke,
)


RUNPOD_V1_BASE = "https://rest.runpod.io/v1"
RUNPOD_V2_BASE = "https://api.runpod.io/v2"


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}


def _get_v1(path: str) -> Dict[str, Any]:
    r = requests.get(f"{RUNPOD_V1_BASE}{path}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _patch_v1(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.patch(f"{RUNPOD_V1_BASE}{path}", headers=_headers(), json=body, timeout=10)
    if r.status_code >= 400:
        print(f"  RunPod API error {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    return r.json()


def _get_v2(path: str) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(f"{RUNPOD_V2_BASE}{path}", headers=_headers(), timeout=10)
        if r.status_code == 200:
            return r.json()
        print(f"  v2 API {path} returned HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  v2 API {path} error: {e}")
    return None


def _now() -> float:
    return time.monotonic()


def _fmt_duration(seconds: float) -> str:
    return f"{seconds:6.2f}s"


class WorkerStatePoller:
    """Poll the RunPod v2 workers endpoint in the background and keep a timeline."""

    def __init__(self, endpoint_id: str, poll_interval: float = 5.0, timeout: float = 400.0):
        self.endpoint_id = endpoint_id
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.timeline: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.poll_interval + 1)

    def _run(self) -> None:
        start = _now()
        while not self._stop.is_set() and (_now() - start) < self.timeout:
            data = _get_v2(f"/serverless/{self.endpoint_id}/workers")
            if data:
                summary = data.get("summary", {})
                workers = data.get("workers", data.get("items", []))
                self.timeline.append({
                    "elapsed": round(_now() - start, 2),
                    "summary": summary,
                    "workers": [
                        {"id": w.get("id"), "status": w.get("status"), "startedAt": w.get("startedAt"), "isStale": w.get("isStale")}
                        for w in workers
                    ],
                })
                total = summary.get("total", 0)
                throttled = summary.get("throttled", 0)
                running = summary.get("running", 0)
                initializing = summary.get("initializing", 0)
                idle = summary.get("idle", 0)
                unhealthy = summary.get("unhealthy", 0)
                print(f"  [{_fmt_duration(_now() - start)}] workers total={total} throttled={throttled} running={running} initializing={initializing} idle={idle} unhealthy={unhealthy}")
            self._stop.wait(self.poll_interval)


def _update_template_image(template_id: str, image_name: str, t0: float) -> None:
    print(f"[t={_fmt_duration(_now() - t0)}] Updating template {template_id} image to {_redact_query(image_name)} ...")
    data = _patch_v1(f"/templates/{template_id}", {"imageName": image_name})
    print(f"[t={_fmt_duration(_now() - t0)}] Updated template {data['id']} image to {_redact_query(data['imageName'])}")


def _scale_workers(endpoint_id: str, workers_min: int, workers_max: int, t0: float) -> None:
    print(f"[t={_fmt_duration(_now() - t0)}] Scaling endpoint {endpoint_id} workers to min={workers_min} max={workers_max} ...")
    data = _patch_v1(f"/endpoints/{endpoint_id}", {"workersMin": workers_min, "workersMax": workers_max})
    print(f"[t={_fmt_duration(_now() - t0)}] Scaled endpoint {data['id']} workers to min={data.get('workersMin')} max={data.get('workersMax')}")


def _get_endpoint(endpoint_id: str) -> Dict[str, Any]:
    return _get_v1(f"/endpoints/{endpoint_id}")


def _classify_failure(worker_timeline: List[Dict[str, Any]], last_status: Optional[int], error_type: str) -> str:
    if not worker_timeline:
        return "worker_never_allocated"
    last_summary = worker_timeline[-1].get("summary", {})
    if last_summary.get("throttled", 0) > 0:
        return "gpu_scheduling_throttled"
    if last_summary.get("unhealthy", 0) > 0:
        return "worker_unhealthy"
    if last_summary.get("total", 0) == 0:
        return "worker_never_allocated"
    if last_status is None:
        return "health_probe_timeout"
    if last_status >= 500:
        return "container_start_failure"
    return error_type


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a worker image tag to RunPod")
    parser.add_argument("image_tag", help="Full image tag, e.g. ghcr.io/lekin/tout-baigne-worker:0.1.0")
    parser.add_argument("--endpoint-id", default=os.environ.get("RUNPOD_ENDPOINT_ID"))
    parser.add_argument("--no-smoke", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--request-timeout", type=float, default=float(os.environ.get("RUNPOD_REQUEST_TIMEOUT", "30")), help="Per-request timeout in seconds")
    parser.add_argument("--poll-interval", type=float, default=float(os.environ.get("RUNPOD_POLL_INTERVAL", "3.0")))
    parser.add_argument("--worker-poll-interval", type=float, default=5.0)
    parser.add_argument("--propagation-wait", type=float, default=20.0)
    parser.add_argument("--expected-version", default=os.environ.get("RUNPOD_WORKER_EXPECTED_VERSION"))
    args = parser.parse_args()

    if not args.endpoint_id:
        print("ERROR: --endpoint-id or RUNPOD_ENDPOINT_ID is required", file=sys.stderr)
        return 2

    if not args.expected_version and ":" in args.image_tag:
        args.expected_version = args.image_tag.rsplit(":", 1)[1]

    t0 = _now()
    print(f"[t={_fmt_duration(0)}] Deploying image {_redact_query(args.image_tag)} to endpoint {args.endpoint_id}")

    endpoint = _get_endpoint(args.endpoint_id)
    template_id = endpoint["templateId"]

    _update_template_image(template_id, args.image_tag, t0)

    _scale_workers(args.endpoint_id, 0, 0, t0)

    # Best-effort wait for the old worker to terminate.
    print(f"[t={_fmt_duration(_now() - t0)}] Waiting 30s for old worker to terminate ...")
    time.sleep(30)

    _scale_workers(args.endpoint_id, 0, 1, t0)

    # Propagation wait: do not judge the endpoint before control-plane has had time to act.
    if args.propagation_wait > 0:
        print(f"[t={_fmt_duration(_now() - t0)}] Control-plane propagation wait: {args.propagation_wait}s ...")
        time.sleep(args.propagation_wait)

    # Start background worker-state polling.
    poller = WorkerStatePoller(args.endpoint_id, poll_interval=args.worker_poll_interval, timeout=args.timeout + 60)
    poller.start()

    print(f"[t={_fmt_duration(_now() - t0)}] Running readiness probe (per-request timeout={args.request_timeout}s, global={args.timeout}s) ...")
    probe = RunPodReadinessProbe(
        endpoint_id=args.endpoint_id,
        expected_version=args.expected_version,
        overall_timeout=args.timeout,
        poll_interval=args.poll_interval,
        request_timeout=(max(1.0, args.request_timeout / 4), args.request_timeout),
    )
    result = probe.probe()

    # Stop poller and consolidate timeline.
    poller.stop()

    print(f"\n[t={_fmt_duration(_now() - t0)}] Worker state timeline:")
    for entry in poller.timeline:
        summary = entry.get("summary", {})
        workers = entry.get("workers", [])
        worker_ids = ",".join(f"{w['id'][:8]}({w['status']})" for w in workers)
        print(
            f"  t={_fmt_duration(entry['elapsed'])} "
            f"total={summary.get('total')} throttled={summary.get('throttled')} "
            f"running={summary.get('running')} init={summary.get('initializing')} idle={summary.get('idle')} "
            f"unhealthy={summary.get('unhealthy')} workers=[{worker_ids}]"
        )

    if not result.ready:
        classified = _classify_failure(poller.timeline, result.status, result.error_type)
        print(f"\n[t={_fmt_duration(_now() - t0)}] ERROR: {classified} - {result.error}", file=sys.stderr)
        return 1

    print(f"\n[t={_fmt_duration(_now() - t0)}] Deployed {args.image_tag} to endpoint {args.endpoint_id}")
    print(f"  first_http_status={result.status}")
    print(f"  cold_start_seconds={result.ready_seconds}")
    print(f"  attempts={result.attempts}")
    print(f"  version={result.version}")

    if not args.no_smoke:
        smoke_start = _now()
        try:
            smoke_seconds = run_smoke(args.endpoint_id, request_timeout=(3.0, 30.0))
            print(f"  smoke_run_seconds={smoke_seconds} (start_to_response={_now() - smoke_start:.2f}s)")
        except Exception as e:
            print(f"ERROR: smoke test failed: {e}", file=sys.stderr)
            return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
