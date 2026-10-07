#!/usr/bin/env python3
"""Run Phase A karaoke sync QA benchmark from a labelled track list."""
import argparse
import os
import sys
from pathlib import Path

# Add repo root to path when running from scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.qa.benchmark import load_benchmark_labels, run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase A sync QA benchmark: 20 labelled tracks -> Stable-ts results -> report."
    )
    parser.add_argument(
        "--labels",
        default="input/qa_benchmark_labels.yaml",
        help="Path to YAML/JSON benchmark labels file.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for benchmark report (default: settings.qa_output_dir/benchmarks).",
    )
    parser.add_argument(
        "--record-id",
        default=None,
        help="Run a single record instead of the full benchmark file.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-track metrics and diagnosis.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.labels) and not args.record_id:
        print(
            f"❌ Benchmark labels file not found: {args.labels}\n"
            "Create it with a 'tracks' list of record IDs and manual_status values."
        )
        return 1

    if args.record_id:
        from src.qa.models import BenchmarkLabel

        labels = [BenchmarkLabel(record_id=args.record_id, manual_status="unknown")]
    else:
        labels = load_benchmark_labels(args.labels)
        if not labels:
            print("❌ No tracks found in labels file.")
            return 1

    print(f"🎤 Running Phase A benchmark on {len(labels)} track(s)...")
    result = run_benchmark(labels, output_dir=args.output_dir, verbose=args.verbose)

    print("\n=== Benchmark summary ===")
    print(f"Total tracks: {result.total}")
    print(f"True positives (bad -> not verified): {result.true_positive}")
    print(f"True negatives (good -> verified): {result.true_negative}")
    print(f"False positives (bad -> verified): {result.false_positive}")
    print(f"False negatives (good -> not verified): {result.false_negative}")
    print(f"Precision: {result.precision:.3f}")
    print(f"Recall: {result.recall:.3f}")
    print(f"F1: {result.f1:.3f}")
    print(f"False-positive rate: {result.fpr:.3f}")
    print(f"False-negative rate: {result.fnr:.3f}")
    if result.details:
        unknown = result.details.get("unknown_count", 0)
        if unknown:
            print(f"Unknown labels: {unknown} (excluded from aggregate stats)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
