#!/usr/bin/env python3
"""Phase B2: retry still-failing tracks with a measured global-offset correction.

For each record that failed Phase A and was not rescued by Phase B, take the
Phase A drift fit (predicted = source*slope + intercept) and re-run remote QA
with lyrics_to_source_offset_ms += intercept. If the track verifies, the fix
can be persisted to the 'Lyrics to singing offset (s)' Airtable field.

Optionally also tries a drift rescale: pre-scale every line/word source time
by (slope, intercept) and send offset=0 (for progressive_drift cases).

Usage:
    export RUNPOD_API_KEY=...
    python scripts/run_remote_phase_b2_offsets.py \
        --endpoint-id ylkhb72ej3hijz \
        --input output/qa/batches/remote_500_v1/results.jsonl \
        --phase-b output/qa/batches/remote_500_v1/phase_b/phase_b_results.jsonl \
        --output output/qa/batches/remote_500_v1/phase_b2/ \
        --concurrency 4
"""
import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from dotenv import load_dotenv

load_dotenv()

from src.airtable_client import AirtableClient
from run_remote_qa_batch import _build_request, _send_request

OFFSET_LIMIT_MS = 15_000  # don't try absurd offsets
SLOPE_LIMIT = 0.20        # try drift rescale only if |slope-1| <= 0.20


def _load_phase_a(path: str) -> Dict[str, Dict[str, Any]]:
    out = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("record_id") and not d.get("infra_error"):
            out[d["record_id"]] = d
    return out


def _load_rescued(path: Optional[str]) -> set:
    if not path or not Path(path).exists():
        return set()
    return {
        json.loads(l)["record_id"]
        for l in Path(path).read_text().splitlines()
        if l.strip() and json.loads(l).get("rescued")
    }


def _rescale_lyrics(payload: Dict[str, Any], slope: float, intercept_ms: float) -> None:
    """Apply corrected = source*slope + intercept to all lyric timestamps."""
    for line in payload.get("lyrics", []):
        for key in ("source_start_ms", "source_end_ms"):
            v = line.get(key)
            if v is not None:
                line[key] = max(0.0, v * slope + intercept_ms)
        for w in line.get("words", []):
            for key in ("source_start_ms", "source_end_ms"):
                v = w.get(key)
                if v is not None:
                    w[key] = max(0.0, v * slope + intercept_ms)


def _process(record_id: str, phase_a: Dict[str, Any], record, endpoint_id: str, try_drift: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {"record_id": record_id, "tries": []}
    fields = record.get("fields", {})
    out["artist"] = fields.get("Artist (string)")
    out["title"] = fields.get("Title")

    metrics = phase_a.get("metrics") or {}
    intercept = metrics.get("drift_intercept_ms")
    slope = metrics.get("drift_slope")
    out["orig_status"] = phase_a.get("status")
    out["orig_diag"] = (phase_a.get("diagnosis") or {}).get("type")
    out["orig_offset_s"] = fields.get("Lyrics to singing offset (s)") or 0.0

    base = _build_request(record)
    if not base:
        out["error"] = "missing_audio_or_lyrics"
        return out

    tries: List[tuple] = []
    # 1) Global-offset retry.
    if intercept is not None and abs(intercept) <= OFFSET_LIMIT_MS and abs(intercept) > 50:
        p = json.loads(json.dumps(base))
        p["transform"]["lyrics_to_source_offset_ms"] = (
            p["transform"].get("lyrics_to_source_offset_ms", 0.0) + intercept
        )
        tries.append(("offset", p, {"intercept_ms": intercept}))
    # 2) Drift rescale retry (pre-scale lyric times, offset 0).
    if try_drift and slope is not None and abs(slope - 1.0) <= SLOPE_LIMIT and abs(slope - 1.0) > 0.002:
        p = json.loads(json.dumps(base))
        _rescale_lyrics(p, slope, intercept or 0.0)
        p["transform"]["lyrics_to_source_offset_ms"] = 0.0
        tries.append(("drift", p, {"slope": slope, "intercept_ms": intercept}))

    if not tries:
        out["error"] = "no_correction_to_try"
        return out

    for name, payload, meta in tries:
        try:
            result = _send_request(endpoint_id, payload)
            m = result.get("metrics") or {}
            ls = m.get("line_start") or {}
            attempt = {
                "mode": name,
                **meta,
                "status": result.get("status"),
                "diagnosis": (result.get("diagnosis") or {}).get("type"),
                "median_ms": ls.get("median_error_ms"),
                "p90_ms": ls.get("p90_error_ms"),
                "coverage": m.get("line_alignment_coverage"),
                "wall_s": result.get("_client_wall_time_s"),
            }
        except Exception as e:
            attempt = {"mode": name, **meta, "error": str(e)}
        out["tries"].append(attempt)
        if attempt.get("status") == "SYNC_VERIFIED":
            out["rescued"] = True
            out["rescued_mode"] = name
            out["rescued_meta"] = meta
            break
    out.setdefault("rescued", False)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-id", default=os.environ.get("RUNPOD_ENDPOINT_ID"))
    parser.add_argument("--input", required=True, help="Phase A results.jsonl")
    parser.add_argument("--phase-b", default=None, help="phase_b_results.jsonl (to skip rescued)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--drift", action="store_true", help="Also try drift rescale")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    phase_a = _load_phase_a(args.input)
    rescued = _load_rescued(args.phase_b)
    targets = {
        rid: d for rid, d in phase_a.items()
        if d.get("status") != "SYNC_VERIFIED" and rid not in rescued
    }
    items = sorted(targets.items(), key=lambda kv: kv[0])
    if args.limit:
        items = items[: args.limit]
    print(f"Phase B2: {len(items)} still-failing records")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "phase_b2_results.jsonl"

    airtable = AirtableClient()
    state = {"done": 0, "rescued": 0}
    lock = threading.Lock()

    def _run(item):
        rid, pa = item
        try:
            record = airtable.get_record(rid)
        except Exception as e:
            r = {"record_id": rid, "error": f"airtable: {e}"}
        else:
            r = _process(rid, pa, record, args.endpoint_id, args.drift)
        with lock:
            rf.write(json.dumps(r, ensure_ascii=False) + "\n")
            rf.flush()
            state["done"] += 1
            if r.get("rescued"):
                state["rescued"] += 1
            print(
                f"[{state['done']}/{len(items)}] {r.get('artist','')} — {r.get('title','')} | "
                f"orig={r.get('orig_status')}/{r.get('orig_diag')} rescued={r.get('rescued')} "
                f"mode={r.get('rescued_mode')} err={r.get('error')} tries={len(r.get('tries',[]))}",
                flush=True,
            )

    with open(results_path, "a", encoding="utf-8") as rf:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = [ex.submit(_run, it) for it in items]
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception as e:
                    print(f"UNEXPECTED: {e}")

    print(f"\nPhase B2 done: {state['rescued']}/{state['done']} rescued")
    (output_dir / "phase_b2_summary.json").write_text(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
