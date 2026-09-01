#!/usr/bin/env python3
"""Deploy a specific worker image tag to the configured RunPod endpoint.

Usage:
    export RUNPOD_API_KEY=...
    export RUNPOD_ENDPOINT_ID=ylkhb72ej3hijz
    python scripts/deploy_runpod_worker.py ghcr.io/lekin/tout-baigne-worker:<tag>

The script:
  1. Updates the endpoint template to the requested image tag.
  2. Optionally sets the container start args if provided.
  3. Scales workers to 0 and waits for shutdown.
  4. Scales workers back to 1 to force a cold start with the new image.
  5. Polls /ping until the worker reports healthy.
  6. Runs a lightweight smoke test via the /run endpoint.
"""
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional

import requests


def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise RuntimeError("RUNPOD_API_KEY is not set")
    return key


def _graphql(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = "https://api.runpod.io/graphql"
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(data["errors"][0]["message"])
    return data["data"]


def _endpoint_query(endpoint_id: str) -> str:
    return f"""
    {{
        myself {{
            endpoint(id: "{endpoint_id}") {{
                id
                name
                template {{
                    id
                    name
                    imageName
                    dockerArgs
                    env {{
                        key
                        value
                    }}
                }}
            }}
        }}
    }}
    """


def _save_template_mutation(
    template_id: str,
    name: str,
    image_name: str,
    docker_args: str,
    env_pairs: list,
) -> str:
    env_entries = ", ".join([f'{{ key: "{e["key"]}", value: "{e["value"]}" }}' for e in env_pairs])
    return f"""
    mutation {{
        saveTemplate(input: {{
            id: "{template_id}",
            name: "{name}",
            imageName: "{image_name}",
            dockerArgs: "{docker_args}",
            containerDiskInGb: 50,
            volumeInGb: 0,
            ports: "80/http,8000/http",
            env: [{env_entries}],
            isServerless: true,
            startSsh: true,
            isPublic: false,
            readme: ""
        }}) {{
            id
            imageName
            dockerArgs
        }}
    }}
    """


def _update_workers_mutation(endpoint_id: str, workers_min: int, workers_max: int) -> str:
    return f"""
    mutation {{
        updateEndpoint(input: {{
            id: "{endpoint_id}",
            workersMin: {workers_min},
            workersMax: {workers_max}
        }}) {{
            id
            workers {{
                min
                max
            }}
        }}
    }}
    """


def _list_endpoint_workers_query(endpoint_id: str) -> str:
    return f"""
    {{
        myself {{
            endpoint(id: "{endpoint_id}") {{
                workers {{
                    total
                }}
            }}
        }}
    }}
    """


def _get_template_env(endpoint_id: str) -> tuple:
    data = _graphql(_endpoint_query(endpoint_id))
    endpoint = data["myself"]["endpoint"]
    template = endpoint["template"]
    return template["id"], template["name"], template["imageName"], template["dockerArgs"], template["env"]


def _update_template(template_id: str, name: str, image_name: str, docker_args: str, env: list) -> None:
    data = _graphql(_save_template_mutation(template_id, name, image_name, docker_args, env))
    updated = data["saveTemplate"]
    print(f"Updated template {updated['id']} to image {updated['imageName']}")


def _scale_workers(endpoint_id: str, workers_min: int, workers_max: int) -> None:
    data = _graphql(_update_workers_mutation(endpoint_id, workers_min, workers_max))
    print(f"Scaled endpoint {data['updateEndpoint']['id']} workers to min={workers_min} max={workers_max}")


def _wait_for_worker_count(endpoint_id: str, target_total: int, timeout: int = 120) -> None:
    start = time.time()
    while time.time() - start < timeout:
        data = _graphql(_list_endpoint_workers_query(endpoint_id))
        total = data["myself"]["endpoint"]["workers"]["total"]
        print(f"  current workers: {total}")
        if total == target_total:
            return
        time.sleep(5)
    raise RuntimeError(f"Timed out waiting for worker count to reach {target_total}")


def _wait_for_ping(base_url: str, timeout: int = 300) -> float:
    start = time.time()
    url = f"{base_url}/ping"
    while time.time() - start < timeout:
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                print(f"  /ping healthy after {time.time() - start:.1f}s")
                return time.time() - start
        except Exception as e:
            print(f"  ping error: {e}")
        time.sleep(5)
    raise RuntimeError("Timed out waiting for /ping to become healthy")


def _smoke_test(base_url: str) -> None:
    """Run a minimal smoke test against the running worker."""
    url = f"{base_url}/run"
    payload = {
        "input": {
            "audio_url": "https://example.com/nonexistent.mp3",
            "lyrics": [],
            "record_id": "smoke-test",
        }
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    result = r.json()
    print("Smoke test result:", json.dumps({
        "success": result.get("success"),
        "status": result.get("status"),
        "error": result.get("error"),
        "error_type": result.get("error_type"),
    }, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a worker image tag to RunPod")
    parser.add_argument("image_tag", help="Full image tag, e.g. ghcr.io/lekin/tout-baigne-worker:0.1.0")
    parser.add_argument("--endpoint-id", default=os.environ.get("RUNPOD_ENDPOINT_ID"))
    parser.add_argument("--base-url", default=os.environ.get("RUNPOD_ENDPOINT_BASE_URL", ""))
    parser.add_argument("--args", default="python3 -u /app/worker/handler.py")
    parser.add_argument("--no-smoke", action="store_true")
    args = parser.parse_args()

    if not args.endpoint_id:
        print("ERROR: --endpoint-id or RUNPOD_ENDPOINT_ID is required")
        return 1
    if not args.base_url:
        # default from endpoint ID
        args.base_url = f"https://{args.endpoint_id}.api.runpod.ai"

    print(f"Deploying image {args.image_tag} to endpoint {args.endpoint_id}")

    template_id, name, old_image, old_args, env = _get_template_env(args.endpoint_id)
    print(f"Current template {template_id} image: {old_image}")

    _update_template(template_id, name, args.image_tag, args.args, env)

    print("Scaling workers to 0...")
    _scale_workers(args.endpoint_id, 0, 0)
    _wait_for_worker_count(args.endpoint_id, 0, timeout=120)

    print("Scaling workers to 1...")
    _scale_workers(args.endpoint_id, 0, 1)

    print("Waiting for /ping...")
    cold_start = _wait_for_ping(args.base_url, timeout=600)

    if not args.no_smoke:
        print("Running smoke test...")
        _smoke_test(args.base_url)

    print(f"\nDeployed {args.image_tag} to endpoint {args.endpoint_id}")
    print(f"  base_url: {args.base_url}")
    print(f"  cold_start_seconds: {cold_start:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
