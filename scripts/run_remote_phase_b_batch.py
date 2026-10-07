#!/usr/bin/env python3
"""Phase B remote batch: try alternate Musixmatch candidates for failed tracks.

For every record in a Phase A results.jsonl that is not SYNC_VERIFIED, search
Musixmatch for alternate lyric candidates, evaluate each through the remote
RunPod worker (same audio + separator config, so the worker's retained vocal
stem may be reused), and keep the best candidate.

Usage:
    export RUNPOD_API_KEY=...
    python scripts/run_remote_phase_b_batch.py \
        --endpoint-id ylkhb72ej3hijz \
        --input output/qa/batches/remote_500_v1/results.jsonl \
        --output output/qa/batches/remote_500_v1/phase_b/
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from src.musixmatch_lyrics import MusixmatchClient, MusixmatchTrack
from src.qa.models import TimelineTransform
from src.qa.runner import KaraokeQARunner
from run_remote_qa_batch import (
    SEPARATOR_CONFIG,
    _direct_audio_url,
    _send_request,
)

MAX_CANDIDATES = 5
PLAUSIBLE_DURATION_DELTA_MS = 35_000


def _record_artist_title(fields: Dict[str, Any]) -> tuple:
    artist = fields.get("Artist (string)", "")
    if isinstance(artist, list):
        artist = artist[0] if artist else ""
    title = fields.get("Title") or fields.get("Name") or ""
    return str(artist), str(title)


def _source_duration_ms(record: Dict[str, Any]) -> Optional[float]:
    dur = record.get("fields", {}).get("Duration (Spotify)")
    if isinstance(dur, list):
        dur = dur[0] if dur else None
    try:
        return float(dur) * 1000.0 if dur is not None else None
    except Exception:
        return None


def _serialize_lyrics(lyrics) -> List[Dict[str, Any]]:
    lines = []
    for ln in lyrics.lines:
        words = [
            {"text": w.text, "source_start_ms": w.source_start_ms, "source_end_ms": w.source_end_ms}
            for w in ln.words
        ]
        lines.append({
            "text": ln.text,
            "source_start_ms": ln.source_start_ms,
            "source_end_ms": ln.source_end_ms,
            "words": words,
        })
    return lines


def _build_candidate_request(
    record_id: str,
    audio_url: str,
    lyrics_obj,
    source_duration_ms: Optional[float],
    candidate_track_id: Any,
) -> Dict[str, Any]:
    return {
        "record_id": record_id,
        "audio_url": audio_url,
        "audio_sha256": None,
        "lyrics": _serialize_lyrics(lyrics_obj),
        "lyrics_source": "musixmatch_alt",
        "lyrics_source_track_id": str(candidate_track_id),
        "lyrics_language": None,
        "transform": {
            "lyrics_to_source_offset_ms": 0.0,
            "source_to_karaoke_audio_offset_ms": 0.0,
            "karaoke_audio_to_video_offset_ms": 0.0,
        },
        "source_duration_ms": source_duration_ms,
        "separator_model": SEPARATOR_CONFIG["model"],
        "separator_overlap": SEPARATOR_CONFIG["overlap"],
        "separator_shifts": SEPARATOR_CONFIG["shifts"],
        "separator_split": SEPARATOR_CONFIG["split"],
        "run_structural_qa": True,
        "run_stable_ts": False,
        "stem_retention": "failures_only",
        "return_stem": False,
    }


def _fetch_candidate_lyrics(client: MusixmatchClient, candidate: MusixmatchTrack) -> tuple:
    """Return (source, content) for the best available synced lyrics."""
    if candidate.has_richsync:
        try:
            content = client.get_richsync(candidate.track_id)
            if content:
                return "richsync", content
        except Exception:
            pass
    if candidate.has_subtitles:
        try:
            content = client.get_subtitle(candidate.track_id)
            if content:
                return "lrc", content
        except Exception:
            pass
    return "none", None


def _verdict(result: Dict[str, Any]) -> tuple:
    """Extract (status, diagnosis, median, p90, coverage) from a worker result."""
    status = result.get("status")
    diag = (result.get("diagnosis") or {}).get("type")
    metrics = result.get("metrics") or {}
    ls = metrics.get("line_start") or {}
    return (
        status,
        diag,
        ls.get("median_error_ms"),
        ls.get("p90_error_ms"),
        metrics.get("line_alignment_coverage"),
    )


def _attempt_score(attempt: Dict[str, Any]) -> tuple:
    return (
        1 if attempt.get("status") == "SYNC_VERIFIED" else 0,
        attempt.get("coverage") or 0.0,
        -(attempt.get("median_ms") or 1e9),
        -(attempt.get("p90_ms") or 1e9),
    )


def _process_record(
    record_id: str,
    original_status: str,
    original_diag: Optional[str],
    airtable: AirtableClient,
    client: MusixmatchClient,
    endpoint_id: str,
    max_candidates: int,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "record_id": record_id,
        "original_status": original_status,
        "original_diagnosis": original_diag,
        "attempts": [],
    }
    try:
        record = airtable.get_record(record_id)
    except Exception as e:
        out["error"] = f"airtable: {e}"
        return out
    fields = record.get("fields", {})
    artist, title = _record_artist_title(fields)
    out["artist"] = artist
    out["title"] = title

    audio_url = _direct_audio_url(record)
    if not audio_url:
        out["error"] = "no_audio_url"
        return out
    source_duration_ms = _source_duration_ms(record)

    try:
        candidates = client.search_track(title, artist)
    except Exception as e:
        out["error"] = f"musixmatch_search: {e}"
        return out
    if not candidates:
        out["error"] = "no_candidates"
        return out

    client.hydrate_track_lengths(candidates, limit=max_candidates * 2)
    if source_duration_ms:
        candidates = [
            c for c in candidates
            if not c.track_length or abs(c.track_length * 1000 - source_duration_ms) <= PLAUSIBLE_DURATION_DELTA_MS
        ]

    original_track_id = str(fields.get("Musixmatch Track ID") or "")
    candidates = [c for c in candidates if str(c.track_id) != original_track_id][:max_candidates]
    if not candidates:
        out["error"] = "no_plausible_candidates"
        return out

    runner = KaraokeQARunner(verbose=False)
    transform = TimelineTransform(
        lyrics_to_source_offset_ms=0.0,
        source_to_karaoke_audio_offset_ms=0.0,
        karaoke_audio_to_video_offset_ms=0.0,
    )

    for candidate in candidates:
        source, content = _fetch_candidate_lyrics(client, candidate)
        if not content:
            continue
        try:
            lyrics_obj = runner.build_structured_lyrics_from_source(
                richsync_json=content if source == "richsync" else None,
                lrc_content=content if source == "lrc" else None,
                srt_content=content if source == "srt" else None,
                track_id=candidate.track_id,
                transform=transform,
                source_duration_ms=source_duration_ms,
            )
        except Exception as e:
            out["attempts"].append({
                "musixmatch_track_id": str(candidate.track_id),
                "error": f"lyrics_parse: {e}",
            })
            continue

        payload = _build_candidate_request(
            record_id, audio_url, lyrics_obj, source_duration_ms, candidate.track_id
        )
        try:
            result = _send_request(endpoint_id, payload)
        except Exception as e:
            out["attempts"].append({
                "musixmatch_track_id": str(candidate.track_id),
                "error": f"worker: {e}",
            })
            continue

        status, diag, median, p90, coverage = _verdict(result)
        out["attempts"].append({
            "musixmatch_track_id": str(candidate.track_id),
            "track_name": candidate.track_name,
            "artist_name": candidate.artist_name,
            "album_name": candidate.album_name,
            "source": source,
            "status": status,
            "diagnosis": diag,
            "median_ms": median,
            "p90_ms": p90,
            "coverage": coverage,
            "worker_wall_s": result.get("_client_wall_time_s"),
        })

    if out["attempts"]:
        best = max(
            (a for a in out["attempts"] if not a.get("error")),
            key=_attempt_score,
            default=None,
        )
        out["best_attempt_id"] = best["musixmatch_track_id"] if best else None
        out["best_status"] = best.get("status") if best else None
        out["best_diagnosis"] = best.get("diagnosis") if best else None
        out["rescued"] = bool(best and best.get("status") == "SYNC_VERIFIED")
    else:
        out["best_attempt_id"] = None
        out["best_status"] = None
        out["rescued"] = False
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-id", default=os.environ.get("RUNPOD_ENDPOINT_ID"))
    parser.add_argument("--input", required=True, help="Phase A results.jsonl")
    parser.add_argument("--output", required=True, help="Phase B output dir")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES)
    parser.add_argument("--limit", type=int, default=0, help="Debug: cap number of failed records")
    parser.add_argument("--include-review", action="store_true",
                        help="Also iterate SYNC_NEEDS_REVIEW (default: only SYNC_FAILED)")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if not args.endpoint_id:
        print("ERROR: --endpoint-id or RUNPOD_ENDPOINT_ID required", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "phase_b_results.jsonl"

    # Collect failed records from Phase A.
    failed: List[tuple] = []
    for line in Path(args.input).read_text().splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("infra_error"):
            continue
        status = d.get("status")
        if status == "SYNC_FAILED" or (
            args.include_review and status in ("SYNC_NEEDS_REVIEW", None)
        ):
            failed.append((d["record_id"], status, (d.get("diagnosis") or {}).get("type")))

    done = set()
    if args.resume and results_path.exists():
        for line in results_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                done.add(json.loads(line)["record_id"])
            except Exception:
                pass
    failed = [f for f in failed if f[0] not in done]
    if args.limit:
        failed = failed[: args.limit]
    print(f"Phase B: {len(failed)} failed records to iterate (resume={len(done)})")

    airtable = AirtableClient()
    client = MusixmatchClient(os.environ.get("MUSIXMATCH_API_KEY") or "")

    state = {"done": 0, "rescued": 0}
    lock = threading.Lock()

    def _run(item):
        rid, status, diag = item
        r = _process_record(
            rid, status, diag, airtable, client,
            args.endpoint_id, args.max_candidates,
        )
        with lock:
            results_f.write(json.dumps(r, ensure_ascii=False) + "\n")
            results_f.flush()
            state["done"] += 1
            if r.get("rescued"):
                state["rescued"] += 1
            print(
                f"[{state['done']}/{len(failed)}] {r.get('artist','')} — {r.get('title','')} | "
                f"orig={status}/{diag} -> best={r.get('best_status')}/{r.get('best_diagnosis')} "
                f"rescued={r.get('rescued')} attempts={len(r.get('attempts',[]))} err={r.get('error')}",
                flush=True,
            )

    with open(results_path, "a", encoding="utf-8") as results_f:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = [ex.submit(_run, item) for item in failed]
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as e:
                    print(f"UNEXPECTED: {e}")

    summary = {
        "total_failed_iterated": state["done"],
        "rescued": state["rescued"],
    }
    (output_dir / "phase_b_summary.json").write_text(json.dumps(summary, indent=2))
    print("\nPhase B complete")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
