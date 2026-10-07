#!/usr/bin/env python3
"""Probe RunPod data centers for network volume + GPU availability."""
import json
import os
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent


def api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise RuntimeError("RUNPOD_API_KEY is not set")
    return key


def headers() -> dict:
    return {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}


def post(path: str, body: dict) -> requests.Response:
    return requests.post(f"https://rest.runpod.io/v1{path}", headers=headers(), json=body)


def delete(path: str) -> requests.Response:
    return requests.delete(f"https://rest.runpod.io/v1{path}", headers=headers())


def get(path: str) -> dict:
    r = requests.get(f"https://rest.runpod.io/v1{path}", headers=headers())
    r.raise_for_status()
    return r.json()


def data_centers_with_volumes() -> list[str]:
    # Known data centers reported by RunPod as supporting network volumes.
    # Kept conservative; extend if more become available.
    return [
        "AP-IN-2",
        "AP-JP-1",
        "CA-MTL-3",
        "CA-MTL-4",
        "EU-FR-1",
        "EU-NL-1",
        "EU-RO-1",
        "EUR-IS-1",
        "EUR-IS-3",
        "US-IL-1",
        "US-KS-2",
        "US-TX-1",
    ]


def probe_pod(dc: str) -> dict:
    """Create a tiny GPU pod in a data center and return result; clean up if running."""
    body = {
        "name": f"probe-{dc.lower().replace('-', '')}-{int(time.time())}",
        "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        "gpuTypeIds": [
            "NVIDIA GeForce RTX 3090",
            "NVIDIA GeForce RTX 4090",
            "NVIDIA RTX A5000",
            "NVIDIA A40",
            "NVIDIA L40S",
        ],
        "gpuTypePriority": "availability",
        "cloudType": "COMMUNITY",
        "computeType": "GPU",
        "containerDiskInGb": 50,
        "volumeInGb": 0,
        "supportPublicIp": False,
        "dataCenterIds": [dc],
    }
    r = post("/pods", body)
    out = {
        "data_center": dc,
        "status_code": r.status_code,
        "body": r.text,
        "pod_id": None,
        "pod_status": None,
    }
    if r.status_code < 300:
        data = r.json()
        pod_id = data["id"]
        out["pod_id"] = pod_id
        # Delete immediately to avoid billing; creation success is enough to know
        # a GPU of the requested types is provisionable in this data center.
        try:
            delete(f"/pods/{pod_id}")
        except Exception as e:
            out["delete_error"] = str(e)
    return out


def main() -> int:
    dcs = data_centers_with_volumes()
    print(f"Data centers with network volume support: {dcs}")
    if not dcs:
        print("No network volume data centers found")
        return 1

    results = []
    for dc in dcs:
        print(f"Probing {dc} ...")
        result = probe_pod(dc)
        results.append(result)
        print(json.dumps(result, indent=2))

    out_path = REPO_ROOT / "output" / "qa" / "runpod_probe_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nSaved to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
