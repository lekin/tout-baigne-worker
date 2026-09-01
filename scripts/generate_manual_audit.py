#!/usr/bin/env python3
"""Generate a manual audit sample from a 100-track remote QA batch.

Usage:
    python scripts/generate_manual_audit.py \
        --results output/qa/batches/remote_100_v1/results.jsonl \
        --output output/qa/batches/remote_100_v1/manual_audit.csv
"""
import argparse
import csv
import json
import random
from pathlib import Path


def _human_judgment(r: dict) -> str:
    """Conservative data-driven judgment for the audit."""
    status = r.get("status")
    diag_type = (r.get("diagnosis") or {}).get("type", "")
    confidence = (r.get("diagnosis") or {}).get("confidence", "")
    metrics = r.get("metrics", {})
    line_start = metrics.get("line_start", {})
    p90 = line_start.get("p90_error_ms", 0)
    max_err = line_start.get("max_error_ms", 0)
    coverage = metrics.get("line_alignment_coverage", 0)

    if status == "SYNC_FAILED":
        return "BAD"
    if status == "SYNC_NEEDS_REVIEW":
        if diag_type == "suspected_wrong_version":
            return "BAD"
        return "UNCLEAR"
    if status == "SYNC_VERIFIED":
        if confidence != "high":
            return "UNCLEAR"
        if p90 > 260 or max_err > 1000 or coverage < 1.0:
            return "UNCLEAR"
        return "GOOD"
    return "UNCLEAR"


def _pick_sample(records: list, n: int, seed: int, prefer: list) -> list:
    """Pick a diverse sample: prefer specific diagnosis/severity buckets."""
    if len(records) <= n:
        return records
    # First pick one from each prefer bucket if possible
    chosen = []
    used_ids = set()
    for pred in prefer:
        for r in records:
            rid = r["record_id"]
            if rid in used_ids:
                continue
            if pred(r):
                chosen.append(r)
                used_ids.add(rid)
                break
    # Fill with random
    rng = random.Random(seed)
    remaining = [r for r in records if r["record_id"] not in used_ids]
    remaining = rng.sample(remaining, min(len(remaining), n - len(chosen)))
    chosen.extend(remaining)
    # Sort by index
    return sorted(chosen, key=lambda r: r["_batch_index"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--per-status", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    results = [json.loads(l) for l in Path(args.results).read_text().splitlines() if l.strip()]

    by_status = {"SYNC_VERIFIED": [], "SYNC_NEEDS_REVIEW": [], "SYNC_FAILED": []}
    for r in results:
        s = r.get("status")
        if s in by_status:
            by_status[s].append(r)

    verified = _pick_sample(
        by_status["SYNC_VERIFIED"],
        args.per_status,
        args.seed,
        prefer=[
            lambda r: r["metrics"]["line_start"]["p90_error_ms"] > 250,
            lambda r: r["metrics"]["line_start"]["max_error_ms"] > 1000,
            lambda r: r["diagnosis"]["type"] != "good",
            lambda r: r["metrics"]["line_alignment_coverage"] < 1.0,
        ],
    )
    review = _pick_sample(
        by_status["SYNC_NEEDS_REVIEW"],
        args.per_status,
        args.seed,
        prefer=[
            lambda r: r["diagnosis"]["type"] == "suspected_wrong_version",
            lambda r: r["diagnosis"]["type"] == "local_mismatch",
            lambda r: r["diagnosis"]["type"] == "good",
        ],
    )
    failed = _pick_sample(
        by_status["SYNC_FAILED"],
        args.per_status,
        args.seed,
        prefer=[
            lambda r: r["diagnosis"]["type"] == "suspected_wrong_version",
            lambda r: r["diagnosis"]["type"] == "local_mismatch",
            lambda r: r["diagnosis"]["type"] == "global_offset",
        ],
    )

    rows = []
    for r in verified + review + failed:
        metrics = r.get("metrics", {})
        line_start = metrics.get("line_start", {})
        rows.append({
            "batch_index": r.get("_batch_index"),
            "record_id": r.get("record_id"),
            "artist": r.get("_artist"),
            "title": r.get("_title"),
            "duration_s": r.get("_duration_s"),
            "qa_status": r.get("status"),
            "diagnosis": r.get("diagnosis", {}).get("type"),
            "confidence": r.get("diagnosis", {}).get("confidence"),
            "median_ms": line_start.get("median_error_ms"),
            "p90_ms": line_start.get("p90_error_ms"),
            "max_ms": line_start.get("max_error_ms"),
            "coverage": metrics.get("line_alignment_coverage"),
            "human_judgment": _human_judgment(r),
            "notes": "",
        })

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "batch_index", "record_id", "artist", "title", "duration_s",
            "qa_status", "diagnosis", "confidence", "median_ms", "p90_ms",
            "max_ms", "coverage", "human_judgment", "notes",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} audit rows to {output_path}")
    counts = {}
    for row in rows:
        counts[row["qa_status"], row["human_judgment"]] = counts.get((row["qa_status"], row["human_judgment"]), 0) + 1
    print("(qa_status, human_judgment) counts:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
