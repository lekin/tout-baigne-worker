#!/usr/bin/env python3
"""Run a batch of tracks through the remote RunPod Audio QA worker.

Usage:
    export RUNPOD_API_KEY=...
    python scripts/run_remote_qa_batch.py \
        --limit 100 \
        --endpoint-id ylkhb72ej3hijz \
        --worker-version dfc4d29 \
        --output output/qa/batches/remote_100_v1/ \
        --concurrency 2

Supports resume: re-running the same command will skip completed tracks.
"""
import argparse
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from src.qa.runner import KaraokeQARunner
from src.qa.timeline import build_timeline_transform


# Production separator config (do not tune)
SEPARATOR_CONFIG = {
    "model": "htdemucs",
    "overlap": 0.10,
    "shifts": 0,
    "split": True,
}


def _api_key() -> str:
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        raise RuntimeError("RUNPOD_API_KEY is not set")
    return key


def _redact_url(url: str) -> str:
    """Remove query params from GDrive URLs for logs."""
    if "?" in url:
        return url.split("?")[0] + "?..."
    return url


def _extract_gdrive_file_id(url: str) -> Optional[str]:
    """Extract a GDrive file id from a view or open URL."""
    patterns = [
        r"/d/([a-zA-Z0-9_-]{10,})",
        r"id=([a-zA-Z0-9_-]{10,})",
        r"([a-zA-Z0-9_-]{25,})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _direct_audio_url(record: Dict[str, Any]) -> Optional[str]:
    """Return a direct-download audio URL for a record, or None."""
    fields = record.get("fields", {})
    for key in ("Source Audio URL", "Audio File (from Source)"):
        val = fields.get(key)
        if val:
            if isinstance(val, list):
                val = val[0]
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val

    links = fields.get("Link (from GDrive Audio files)")
    if not links:
        return None
    if isinstance(links, str):
        links = [links]
    for link in links:
        if not isinstance(link, str):
            continue
        file_id = _extract_gdrive_file_id(link)
        if file_id:
            return f"https://drive.google.com/uc?export=download&id={file_id}"
    return None


def _has_lyrics(record: Dict[str, Any]) -> bool:
    fields = record.get("fields", {})
    for key in ("Richsync JSON (Musixmatch)", "LRC (Musixmatch)", "SRT (Musixmatch)", "Lyrics SRT", "Lyrics"):
        if fields.get(key):
            return True
    return False


def _build_request(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build an AudioQARequest payload from an Airtable record."""
    fields = record.get("fields", {})
    record_id = record.get("id")
    audio_url = _direct_audio_url(record)
    if not audio_url:
        return None
    if not _has_lyrics(record):
        return None

    transform = build_timeline_transform(record)
    runner = KaraokeQARunner(verbose=False)
    lyrics = runner._build_structured_lyrics(fields, transform, None)

    lines = []
    for ln in lyrics.lines:
        words = [
            {
                "text": w.text,
                "source_start_ms": w.source_start_ms,
                "source_end_ms": w.source_end_ms,
            }
            for w in ln.words
        ]
        lines.append({
            "text": ln.text,
            "source_start_ms": ln.source_start_ms,
            "source_end_ms": ln.source_end_ms,
            "words": words,
        })

    return {
        "record_id": record_id,
        "audio_url": audio_url,
        "audio_sha256": None,
        "lyrics": lines,
        "lyrics_source": lyrics.source,
        "lyrics_source_track_id": lyrics.source_track_id,
        "lyrics_language": lyrics.language,
        "transform": {
            "lyrics_to_source_offset_ms": transform.lyrics_to_source_offset_ms,
            "source_to_karaoke_audio_offset_ms": 0.0,
            "karaoke_audio_to_video_offset_ms": 0.0,
        },
        "source_duration_ms": None,
        "separator_model": SEPARATOR_CONFIG["model"],
        "separator_overlap": SEPARATOR_CONFIG["overlap"],
        "separator_shifts": SEPARATOR_CONFIG["shifts"],
        "separator_split": SEPARATOR_CONFIG["split"],
        "run_structural_qa": True,
        "run_stable_ts": False,
        "stem_retention": "failures_only",
        "return_stem": False,
    }


def _send_request(endpoint_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"https://{endpoint_id}.api.runpod.ai/run"
    start = time.monotonic()
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
        json={"input": payload},
        timeout=(10, 300),
    )
    r.raise_for_status()
    end = time.monotonic()
    data = r.json()
    data["_client_wall_time_s"] = round(end - start, 3)
    data["_worker"] = "RunPod"
    return data


def _classify_request_exception(e: Exception) -> str:
    if isinstance(e, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(e, requests.exceptions.HTTPError):
        status = e.response.status_code
        if status >= 500:
            return f"server_error_{status}"
        if status == 401 or status == 403:
            return "auth_error"
        return f"http_error_{status}"
    if isinstance(e, requests.exceptions.RequestException):
        return "network_error"
    return f"exception_{type(e).__name__}"


def _run_one(endpoint_id: str, record: Dict[str, Any], retries: int = 2, worker_version: Optional[str] = None) -> Dict[str, Any]:
    """Send one track to the worker, retry on transient infra errors."""
    payload = _build_request(record)
    if not payload:
        return {
            "record_id": record.get("id"),
            "infra_error": True,
            "infra_error_type": "missing_audio_or_lyrics",
        }

    attempt = 0
    last_error = None
    while attempt <= retries:
        try:
            result = _send_request(endpoint_id, payload)
            result["record_id"] = record.get("id")
            result["_attempt"] = attempt + 1
            if worker_version:
                result["_worker_version"] = worker_version
            return result
        except Exception as e:
            error_type = _classify_request_exception(e)
            last_error = str(e)
            attempt += 1
            if attempt > retries:
                break
            # backoff: 10s, 20s
            time.sleep(10 * attempt)

    return {
        "record_id": record.get("id"),
        "infra_error": True,
        "infra_error_type": _classify_request_exception(Exception(last_error)) if last_error else "unknown",
        "infra_error_detail": last_error,
        "_attempt": attempt,
        "_worker_version": worker_version,
    }


def _load_excluded_ids(input_dir: Path) -> Set[str]:
    excluded = set()
    for p in input_dir.glob("qa_benchmark_labels*.yaml"):
        data = yaml.safe_load(p.read_text())
        if not data:
            continue
        tracks = data if isinstance(data, list) else data.get("tracks", data.get("records", []))
        for item in tracks:
            if isinstance(item, dict) and item.get("record_id"):
                excluded.add(item["record_id"])
    return excluded


def _candidate_records(
    airtable: AirtableClient,
    excluded: Set[str],
    excluded_record_ids: List[str],
    min_duration_s: float = 60.0,
    max_duration_s: float = 360.0,
) -> List[Dict[str, Any]]:
    """Return records with audio, lyrics, and a duration that fits the worker timeout."""
    all_records = airtable.table.all()
    candidates = []
    for rec in all_records:
        rid = rec.get("id")
        if rid in excluded or rid in excluded_record_ids:
            continue
        fields = rec.get("fields", {})
        if not _direct_audio_url(rec):
            continue
        if not _has_lyrics(rec):
            continue
        dur = fields.get("Duration (Spotify)")
        if dur:
            if isinstance(dur, list):
                dur = dur[0]
            try:
                dur = float(dur)
            except Exception:
                dur = None
        if dur is None or not (min_duration_s <= dur <= max_duration_s):
            continue
        candidates.append(rec)
    return candidates


def _select_sample(candidates: List[Dict[str, Any]], limit: int, seed: int = 42) -> List[Dict[str, Any]]:
    """Select a representative sample, shuffled, with a reproducible seed."""
    if len(candidates) <= limit:
        return candidates
    rng = random.Random(seed)
    return rng.sample(candidates, limit)


def _record_artist_title(record: Dict[str, Any]) -> tuple:
    fields = record.get("fields", {})
    artist = fields.get("Artist (string)", "")
    title = fields.get("Title", "")
    return artist, title


def _record_genre_language(record: Dict[str, Any]) -> tuple:
    fields = record.get("fields", {})
    genres = fields.get("Genre", [])
    if isinstance(genres, list) and genres:
        genre = genres[0]
    else:
        genre = ""
    lang = fields.get("Language (auto)") or fields.get("Language") or ""
    return genre, lang


class BatchState:
    def __init__(self):
        self.lock = threading.Lock()
        self.verified = 0
        self.review = 0
        self.failed = 0
        self.infra = 0
        self.completed = 0

    def update(self, result: Dict[str, Any]) -> None:
        with self.lock:
            self.completed += 1
            if result.get("infra_error"):
                self.infra += 1
                return
            status = result.get("status")
            if status == "SYNC_VERIFIED":
                self.verified += 1
            elif status == "SYNC_NEEDS_REVIEW":
                self.review += 1
            elif status == "SYNC_FAILED":
                self.failed += 1


def _persist_result(
    result: Dict[str, Any],
    results_f,
    errors_f,
    results_lock: threading.Lock,
) -> None:
    with results_lock:
        results_f.write(json.dumps(result, ensure_ascii=False) + "\n")
        results_f.flush()
        if result.get("infra_error"):
            errors_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            errors_f.flush()


def _run_track(
    endpoint_id: str,
    idx: int,
    total: int,
    record: Dict[str, Any],
    batch_start: float,
    worker_version: Optional[str],
    max_retries: int,
    results_f,
    errors_f,
    results_lock: threading.Lock,
    state: BatchState,
    progress_lock: threading.Lock,
) -> None:
    rid = record.get("id")
    artist, title = _record_artist_title(record)
    track_start = time.monotonic()
    try:
        result = _run_one(endpoint_id, record, retries=max_retries, worker_version=worker_version)
    except Exception as e:
        result = {
            "record_id": rid,
            "infra_error": True,
            "infra_error_type": f"unexpected_{type(e).__name__}",
            "infra_error_detail": str(e),
            "_worker_version": worker_version,
        }
    track_elapsed = time.monotonic() - track_start

    result["_batch_index"] = idx
    result["_batch_track_start_s"] = round(track_start - batch_start, 3)
    result["_batch_track_elapsed_s"] = round(track_elapsed, 3)
    result["_artist"] = artist
    result["_title"] = title
    result["_duration_s"] = record.get("fields", {}).get("Duration (Spotify)")

    _persist_result(result, results_f, errors_f, results_lock)
    state.update(result)

    with progress_lock:
        if result.get("infra_error"):
            status = f"INFRA_ERROR {result.get('infra_error_type')}"
        else:
            diag = result.get("diagnosis", {}).get("type", "")
            status = f"{result.get('status')} {diag}"
        print(f"[{idx:03d}/{total}] {artist} — {title}")
        print(f"          {status}  {track_elapsed:.2f}s")
        print(f"          VERIFIED={state.verified} REVIEW={state.review} FAILED={state.failed} INFRA={state.infra}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a batch of tracks through the remote Audio QA worker")
    parser.add_argument("--endpoint-id", default=os.environ.get("RUNPOD_ENDPOINT_ID"))
    parser.add_argument("--worker-version", default="dfc4d29")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", required=True)
    parser.add_argument("--input-dir", default=str(REPO_ROOT / "input"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", action="store_true", help="Skip tracks that already have a result line")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--exclude-record-ids", default="", help="Comma-separated record IDs to exclude")
    args = parser.parse_args()

    if not args.endpoint_id:
        print("ERROR: --endpoint-id or RUNPOD_ENDPOINT_ID is required", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    manifest_path = output_dir / "manifest.json"
    summary_path = output_dir / "summary.json"
    errors_path = output_dir / "errors.jsonl"

    excluded = _load_excluded_ids(Path(args.input_dir))
    extra_excluded = set(x.strip() for x in args.exclude_record_ids.split(",") if x.strip())
    excluded |= extra_excluded

    print(f"Loaded {len(excluded)} benchmark/excluded record IDs")
    print(f"Endpoint: {args.endpoint_id}  worker version: {args.worker_version}  concurrency: {args.concurrency}")
    print(f"Output: {output_dir}")

    airtable = AirtableClient()
    candidates = _candidate_records(airtable, excluded, [], min_duration_s=60.0, max_duration_s=360.0)
    print(f"Found {len(candidates)} candidate records with audio, lyrics, and duration")

    if not candidates:
        print("ERROR: no candidate records", file=sys.stderr)
        return 1

    selected = _select_sample(candidates, args.limit, args.seed)
    print(f"Selected {len(selected)} tracks for the batch")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "endpoint_id": args.endpoint_id,
        "worker_version": args.worker_version,
        "limit": args.limit,
        "seed": args.seed,
        "concurrency": args.concurrency,
        "excluded_ids_count": len(excluded),
        "tracks": [
            {
                "index": i,
                "record_id": rec.get("id"),
                "artist": rec.get("fields", {}).get("Artist (string)"),
                "title": rec.get("fields", {}).get("Title"),
                "duration_s": rec.get("fields", {}).get("Duration (Spotify)", [None])[0] if isinstance(rec.get("fields", {}).get("Duration (Spotify)", []), list) else rec.get("fields", {}).get("Duration (Spotify)"),
                "audio_url": _redact_url(_direct_audio_url(rec) or ""),
            }
            for i, rec in enumerate(selected, start=1)
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    completed_ids = set()
    if args.resume and results_path.exists():
        for line in results_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if data.get("record_id"):
                    completed_ids.add(data["record_id"])
            except Exception:
                pass
        print(f"Resuming; {len(completed_ids)} tracks already completed")

    state = BatchState()
    batch_start = time.monotonic()
    results_lock = threading.Lock()
    progress_lock = threading.Lock()

    with open(results_path, "a", encoding="utf-8") as f, open(errors_path, "a", encoding="utf-8") as ef:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = {}
            for idx, rec in enumerate(selected, start=1):
                rid = rec.get("id")
                if rid in completed_ids:
                    print(f"[{idx:03d}/{len(selected)}] SKIP {rid} (already completed)")
                    continue
                fut = executor.submit(
                    _run_track,
                    args.endpoint_id,
                    idx,
                    len(selected),
                    rec,
                    batch_start,
                    args.worker_version,
                    args.max_retries,
                    f,
                    ef,
                    results_lock,
                    state,
                    progress_lock,
                )
                futures[fut] = (idx, rec)

            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    idx, rec = futures[fut]
                    print(f"[{idx:03d}/{len(selected)}] UNEXPECTED THREAD ERROR: {e}")

    batch_elapsed = time.monotonic() - batch_start
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "endpoint_id": args.endpoint_id,
        "worker_version": args.worker_version,
        "concurrency": args.concurrency,
        "total_tracks": len(selected),
        "batch_wall_time_s": round(batch_elapsed, 3),
        "status_counts": {
            "SYNC_VERIFIED": state.verified,
            "SYNC_NEEDS_REVIEW": state.review,
            "SYNC_FAILED": state.failed,
            "INFRA_ERROR": state.infra,
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\nBatch complete")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
