#!/usr/bin/env python3
"""Set up a RunPod Serverless Flex Audio QA worker backed by a network volume."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise RuntimeError("RUNPOD_API_KEY is not set")
    return key


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}


def _post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"https://rest.runpod.io/v1{path}"
    r = requests.post(url, headers=_headers(), json=body)
    if r.status_code >= 300:
        print(f"RunPod API error: {r.status_code} {r.text}")
        raise RuntimeError(f"RunPod API error: {r.status_code}")
    return r.json()


def _get(path: str) -> Dict[str, Any]:
    url = f"https://rest.runpod.io/v1{path}"
    r = requests.get(url, headers=_headers())
    if r.status_code >= 300:
        print(f"RunPod API error: {r.status_code} {r.text}")
        raise RuntimeError(f"RunPod API error: {r.status_code}")
    return r.json()


def _delete(path: str) -> None:
    url = f"https://rest.runpod.io/v1{path}"
    r = requests.delete(url, headers=_headers())
    if r.status_code >= 300:
        print(f"RunPod API error: {r.status_code} {r.text}")
        raise RuntimeError(f"RunPod API error: {r.status_code}")


def create_network_volume(name: str, size_gb: int, data_center_id: str) -> str:
    body = {"name": name, "size": size_gb, "dataCenterId": data_center_id}
    data = _post("/networkvolumes", body)
    return data["id"]


def create_setup_pod(name: str, network_volume_id: str, data_center_id: Optional[str] = None) -> Dict[str, Any]:
    pub = Path.home() / ".ssh" / "id_ed25519.pub"
    public_key = pub.read_text().strip() if pub.exists() else ""
    gpu_list = [
        "NVIDIA GeForce RTX 3090",
        "NVIDIA GeForce RTX 4090",
        "NVIDIA RTX A5000",
        "NVIDIA A40",
        "NVIDIA L40S",
    ]
    body: Dict[str, Any] = {
        "name": name,
        "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        "gpuTypeIds": gpu_list,
        "gpuTypePriority": "availability",
        "cloudType": "COMMUNITY",
        "computeType": "GPU",
        "containerDiskInGb": 50,
        "volumeInGb": 0,
        "networkVolumeId": network_volume_id,
        "volumeMountPath": "/workspace",
        "supportPublicIp": True,
        "ports": ["22/tcp"],
    }
    if data_center_id:
        body["dataCenterIds"] = [data_center_id]
    if public_key:
        body["env"] = {"PUBLIC_KEY": public_key}
    data = _post("/pods", body)
    return data


def wait_for_pod(pod_id: str, timeout: int = 600) -> Dict[str, Any]:
    for _ in range(timeout // 10):
        pod = _get(f"/pods/{pod_id}")
        status = pod.get("desiredStatus") or pod.get("status")
        if status == "RUNNING":
            return pod
        time.sleep(10)
    raise RuntimeError("Pod did not start in time")


def ssh_command(host: str, port: int, cmd: str) -> None:
    key = Path.home() / ".ssh" / "id_ed25519"
    full = f"ssh -i {key} -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{host} '{cmd}'"
    subprocess.run(full, shell=True, check=True)


def scp_to(host: str, port: int, local: str, remote: str) -> None:
    key = Path.home() / ".ssh" / "id_ed25519"
    full = f"scp -i {key} -P {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -r {local} root@{host}:{remote}"
    subprocess.run(full, shell=True, check=True)


def rsync_to(host: str, port: int, local: str, remote: str) -> None:
    key = Path.home() / ".ssh" / "id_ed25519"
    excludes = " ".join(f"--exclude={p}" for p in [
        ".venv", ".git", "output/qa/cache", "output/qa/cuda_bench", "output/qa/cuda_bench_bad",
        "output/qa/worker_test", "worker/venv", "*.pyc", "__pycache__",
    ])
    full = (
        f"rsync -avz -e 'ssh -i {key} -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' "
        f"{excludes} {local}/ root@{host}:{remote}"
    )
    subprocess.run(full, shell=True, check=True)


def _setup_script(repo_root: str) -> str:
    return f"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ffmpeg libsndfile1

mkdir -p /workspace/tout-baigne /workspace/qa_cache /workspace/torch_home

# Copy already present via scp, but ensure correct place.
cd /workspace/tout-baigne

# Create isolated venv inside the network volume.
python3 -m venv worker/venv
worker/venv/bin/pip install -q --upgrade pip setuptools wheel
worker/venv/bin/pip install -q -r worker/requirements.txt

export AIRTABLE_API_KEY=dummy
export AIRTABLE_BASE_ID=dummy
export QA_CACHE_DIR=/workspace/qa_cache
export TORCH_HOME=/workspace/torch_home

# Pre-download Demucs model weights.
worker/venv/bin/python - <<'PY'
import torch
from demucs.pretrained import get_model
for name in ["htdemucs", "hdemucs_mmi"]:
    m = get_model(name)
    print(f"preloaded {{name}}")
PY

chmod +x worker/start.sh
"""


def provision_network_volume(
    setup_name: str,
    volume_name: str,
    volume_size_gb: int,
    data_center_id: str,
) -> Dict[str, Any]:
    print("Creating network volume...")
    volume_id = create_network_volume(volume_name, volume_size_gb, data_center_id)
    print(f"  volume id: {volume_id}")

    print("Creating setup pod...")
    pod = create_setup_pod(setup_name, volume_id, data_center_id)
    pod_id = pod["id"]
    print(f"  pod id: {pod_id}")

    print("Waiting for pod to start...")
    pod = wait_for_pod(pod_id)
    host = pod["publicIp"]["address"]
    port = pod["publicIp"]["port"]
    print(f"  {host}:{port}")

    # Copy repo to pod volume.
    print("Copying repository to network volume...")
    rsync_to(host, port, str(REPO_ROOT), "/workspace/tout-baigne")

    # Write setup script and run.
    setup_path = REPO_ROOT / "worker" / "setup_remote.sh"
    setup_path.write_text(_setup_script(str(REPO_ROOT)))
    scp_to(host, port, str(setup_path), "/workspace/tout-baigne/worker/setup_remote.sh")
    print("Installing dependencies and pre-downloading model weights...")
    ssh_command(host, port, "bash /workspace/tout-baigne/worker/setup_remote.sh")

    # Clean up setup artifacts.
    setup_path.unlink(missing_ok=True)

    print("Terminating setup pod...")
    _delete(f"/pods/{pod_id}")
    return {"volume_id": volume_id, "data_center_id": data_center_id}


def create_serverless_template(name: str, container_disk_gb: int = 50) -> str:
    print("Creating serverless template...")
    body = {
        "name": name,
        "imageName": "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        "category": "NVIDIA",
        "isServerless": True,
        "containerDiskInGb": container_disk_gb,
        "volumeMountPath": "/workspace",
        "dockerStartCmd": ["bash", "/workspace/tout-baigne/worker/start.sh"],
        "env": {
            "AIRTABLE_API_KEY": "dummy",
            "AIRTABLE_BASE_ID": "dummy",
            "AIRTABLE_TABLE_NAME": "Tracks",
            "QA_CACHE_DIR": "/workspace/qa_cache",
            "TORCH_HOME": "/workspace/torch_home",
            "PYTHONUNBUFFERED": "1",
        },
    }
    data = _post("/templates", body)
    return data["id"]


def create_serverless_endpoint(
    name: str,
    template_id: str,
    volume_id: str,
    data_center_id: str,
    workers_min: int = 0,
    workers_max: int = 3,
    idle_timeout: int = 5,
    execution_timeout_ms: int = 600000,
) -> Dict[str, Any]:
    print("Creating serverless endpoint...")
    body = {
        "name": name,
        "templateId": template_id,
        "computeType": "GPU",
        "gpuTypeIds": [
            "NVIDIA GeForce RTX 3090",
            "NVIDIA GeForce RTX 4090",
            "NVIDIA RTX A5000",
            "NVIDIA A40",
            "NVIDIA L40S",
        ],
        "dataCenterIds": [data_center_id],
        "workersMin": workers_min,
        "workersMax": workers_max,
        "idleTimeout": idle_timeout,
        "executionTimeoutMs": execution_timeout_ms,
        "networkVolumeId": volume_id,
        "volumeMountPath": "/workspace",
        "flashboot": False,
        "scalerType": "QUEUE_DELAY",
        "scalerValue": 10,
    }
    data = _post("/endpoints", body)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup-name", default="tout-baigne-qa-setup")
    parser.add_argument("--volume-name", default="tout-baigne-qa-volume")
    parser.add_argument("--volume-size-gb", type=int, default=30)
    parser.add_argument("--data-center-id", default="US-KS-2")
    parser.add_argument("--template-name", default="tout-baigne-qa-template")
    parser.add_argument("--endpoint-name", default="tout-baigne-audio-qa")
    parser.add_argument("--output", default="output/qa/runpod_serverless/endpoint.json")
    parser.add_argument("--skip-volume-setup", action="store_true")
    parser.add_argument("--volume-id")
    args = parser.parse_args()

    os.makedirs(Path(args.output).parent, exist_ok=True)

    if args.skip_volume_setup:
        if not args.volume_id:
            raise RuntimeError("--skip-volume-setup requires --volume-id")
        volume_id = args.volume_id
    else:
        info = provision_network_volume(
            args.setup_name,
            args.volume_name,
            args.volume_size_gb,
            args.data_center_id,
        )
        volume_id = info["volume_id"]

    template_id = create_serverless_template(args.template_name)
    endpoint = create_serverless_endpoint(
        args.endpoint_name,
        template_id,
        volume_id,
        args.data_center_id,
    )

    out = {
        "volume_id": volume_id,
        "template_id": template_id,
        "endpoint_id": endpoint.get("id"),
        "endpoint_url": f"https://api.runpod.ai/v2/{endpoint.get('id')}/run",
        "endpoint_status_url": f"https://api.runpod.ai/v2/{endpoint.get('id')}/status",
    }
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    print(f"\nEndpoint saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
