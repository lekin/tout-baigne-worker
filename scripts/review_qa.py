#!/usr/bin/env python3
"""CLI review UI for QA benchmark labels."""
import argparse
import os
import sys
from pathlib import Path

# Add repo root to path when running from scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.config import settings
from src.qa.review import (
    VALID_LABELS,
    VALID_SEVERITY,
    load_labels,
    refresh_benchmark,
    render_table,
    set_label,
)


def main():
    parser = argparse.ArgumentParser(description="Review and label QA benchmark tracks.")
    parser.add_argument(
        "--labels",
        type=str,
        default="input/qa_benchmark_labels.yaml",
        help="Path to benchmark labels YAML",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print a markdown table of current labels",
    )
    parser.add_argument(
        "--record-id",
        type=str,
        help="Record ID to label",
    )
    parser.add_argument(
        "--status",
        type=str,
        choices=sorted(VALID_LABELS),
        help="Manual sync status label",
    )
    parser.add_argument(
        "--severity",
        type=str,
        choices=sorted(VALID_SEVERITY),
        help="Human severity (obvious, subtle, unknown)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-run the benchmark report with current labels",
    )
    args = parser.parse_args()

    if args.list:
        labels = load_labels(args.labels)
        print(render_table(labels, reports_dir=str(settings.get_qa_output_path())))
        return 0

    if args.record_id and args.status:
        severity = args.severity or "unknown"
        set_label(
            args.record_id,
            args.status,
            human_severity=severity,
            labels_path=args.labels,
        )
        print(f"✅ {args.record_id} → {args.status} ({severity})")
        return 0

    if args.refresh:
        report_path = refresh_benchmark(labels_path=args.labels)
        print(f"✅ Benchmark report updated: {report_path}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
