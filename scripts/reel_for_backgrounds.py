"""Generate a 30s Instagram reel for each background image.

Usage:
    .venv/bin/python scripts/reel_for_backgrounds.py \
        --record-id recXXXXXXXXXXXXX \
        --backgrounds bg1.jpg,bg2.jpg,bg3.jpg \
        --output-dir output/reels

Or with URLs:
    .venv/bin/python scripts/reel_for_backgrounds.py \
        --record-id recXXXXXXXXXXXXX \
        --backgrounds https://...,https://... \
        --output-dir output/reels

Each background produces a separate MP4 named `{safe_track_name}_{index}_{background_name}.mp4`.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Instagram reels for a set of backgrounds")
    parser.add_argument("--record-id", required=True, help="Airtable track record ID")
    parser.add_argument("--backgrounds", required=True, help="Comma-separated list of background image paths or URLs")
    parser.add_argument("--duration", type=float, default=30.0, help="Reel duration in seconds (default: 30)")
    parser.add_argument("--output-dir", default="output/reels", help="Output directory")
    parser.add_argument("--fast", action="store_true", help="Use fast encoding preset")
    args = parser.parse_args()

    backgrounds = [b.strip() for b in args.backgrounds.split(",") if b.strip()]
    if not backgrounds:
        print("❌ No backgrounds provided", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)

    for i, bg in enumerate(backgrounds, start=1):
        safe_bg = Path(bg).stem if "/" not in bg and "\\" not in bg else f"bg_{i}"
        output = output_dir / f"reel_{safe_bg}_{i:02d}.mp4"
        cmd = [
            str(python), "-m", "src.cli", "reel",
            "--record-id", args.record_id,
            "--duration", str(args.duration),
            "--background", bg,
            "--output", str(output),
        ]
        if args.fast:
            cmd.append("--fast")

        print(f"\n[{i}/{len(backgrounds)}] Generating reel with background: {bg}")
        print(f"   Output: {output}")
        result = subprocess.run(cmd, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f"❌ Failed to generate reel for background {i}", file=sys.stderr)

    print("\n✅ Done")


if __name__ == "__main__":
    main()
