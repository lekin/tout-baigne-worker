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
  4. Runs the authenticated readiness probe and smoke test.

Requires `requests` (already in the project venv).
"""
import argparse
import os
import sys
import time
from typing import Any, Dict, Optional

import requests

from check_runpod_worker import RunPodReadinessProbe, _api_key, _redact_url


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}


def _rest_url(path: str) -> str:
    return f"https://rest.runpod.io/v1{path}"


def _get(path: str) -> Dict[str, Any]:
    r = requests.get(_rest_url(path), headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _patch(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.patch(_rest_url(path), headers=_headers(), json=body, timeout=10)
    if r.status_code >= 400:
        print(f"RunPod API error {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    return r.json()


def _get_endpoint(endpoint_id: str) -> Dict[str, Any]:
    return _get(f"/endpoints/{endpoint_id}")


def _get_template(template_id: str) -> Dict[str, Any]:
    return _get(f"/templates/{template_id}")


def _update_template_image(template_id: str, image_name: str, docker_args: Optional[str] = None) -> None:
    body: Dict[str, Any] = {"imageName": image_name}
    if docker_args is not None:
        body["dockerArgs"] = docker_args
    data = _patch(f"/templates/{template_id}", body)
    print(f"Updated template {data['id']} image to {_redact_url(data['imageName'])}")


def _scale_workers(endpoint_id: str, workers_min: int, workers_max: int) -> None:
    data = _patch(f"/endpoints/{endpoint_id}", {"workersMin": workers_min, "workersMax": workers_max})
    print(f"Scaled endpoint {data['id']} workers to min={data.get('workersMin')} max={data.get('workersMax')}")


def _wait_for_no_workers(endpoint_id: str, timeout: int = 120) -> bool:
    """Best-effort wait for the endpoint to have zero actual workers.

    The v1 endpoint object does not include a live worker count, so we poll the
    v1 workers endpoint. If it is unavailable, we fall back to a short sleep.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = requests.get(
                f"https://rest.runpod.io/v2/endpoints/{endpoint_id}/workers",
                headers=_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                total = data.get("summary", {}).get("total", 0)
                print(f"  live workers: {total}")
                if total == 0:
                    return True
        except Exception as e:
            print(f"  could not list workers: {e}")
        time.sleep(5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a worker image tag to RunPod")
    parser.add_argument("image_tag", help="Full image tag, e.g. ghcr.io/lekin/tout-baigne-worker:0.1.0")
    parser.add_argument("--endpoint-id", default=os.environ.get("RUNPOD_ENDPOINT_ID"))
    parser.add_argument("--args", default="python3 -u /app/worker/handler.py")
    parser.add_argument("--no-smoke", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--expected-version", default=os.environ.get("RUNPOD_WORKER_EXPECTED_VERSION"))
    args = parser.parse_args()

    if not args.endpoint_id:
        print("ERROR: --endpoint-id or RUNPOD_ENDPOINT_ID is required", file=sys.stderr)
        return 2

    print(f"Deploying image {_redact_url(args.image_tag)} to endpoint {args.endpoint_id}")

    endpoint = _get_endpoint(args.endpoint_id)
    template_id = endpoint["templateId"]
    print(f"Template: {template_id}")

    template = _get_template(template_id)
    print(f"Current image: {_redact_url(template['imageName'])}")

    _update_template_image(template_id, args.image_tag, args.args)

    print("Scaling workers to 0...")
    _scale_workers(args.endpoint_id, 0, 0)
    _wait_for_no_workers(args.endpoint_id)

    print("Scaling workers to 1...")
    _scale_workers(args.endpoint_id, 0, 1)

    print("Running readiness probe...")
    probe = RunPodReadinessProbe(
        endpoint_id=args.endpoint_id,
        expected_version=args.expected_version,
        overall_timeout=args.timeout,
    )
    result = probe.probe()

    if not result.ready:
        print(f"ERROR: {result.error_type} - {result.error}", file=sys.stderr)
        return 1

    print(f"\nDeployed {args.image_tag} to endpoint {args.endpoint_id}")
    print(f"  cold_start_seconds={result.ready_seconds}")
    print(f"  attempts={result.attempts}")
    print(f"  version={result.version}")

    if not args.no_smoke:
        from check_runpod_worker import run_smoke
        try:
            smoke_seconds = run_smoke(args.endpoint_id)
            print(f"  smoke_run_seconds={smoke_seconds}")
        except Exception as e:
            print(f"ERROR: smoke test failed: {e}", file=sys.stderr)
            return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
