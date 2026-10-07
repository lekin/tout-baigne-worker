#!/usr/bin/env python3
"""CLI helper for MLX Demucs separation in an isolated venv."""
import argparse
import sys
from pathlib import Path

import demucs_mlx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="htdemucs")
    parser.add_argument("--shifts", type=int, default=0)
    parser.add_argument("--overlap", type=float, default=0.25)
    parser.add_argument("--split", action="store_true", default=False)
    args = parser.parse_args()

    sep = demucs_mlx.Separator(
        args.model,
        shifts=args.shifts,
        overlap=args.overlap,
        split=args.split,
        progress=True,
    )
    wav, stems = sep.separate_audio_file(args.audio, return_mx=False)
    vocals = stems["vocals"]
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    demucs_mlx.save_audio(vocals, args.output, samplerate=sep.samplerate, as_float=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
