#!/usr/bin/env python3
"""Benchmark CPU Demucs with overlap=0.1 and run QA on Sia - Chandelier."""
import sys
import time
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import torch
import torchaudio

from demucs.pretrained import get_model
from demucs.apply import apply_model

from src.qa.cache import hash_audio_file, vocals_cache_key, cached_vocals_path

audio_path = 'output/qa/cache/audio/57b505e7555a75626b326db27da25fc9.mp3'
overlap = 0.1

def main():
    wav, sr = torchaudio.load(audio_path)
    model = get_model('htdemucs')
    model.to('cpu')
    model.eval()
    if sr != model.samplerate:
        wav = torchaudio.transforms.Resample(sr, model.samplerate)(wav)
        sr = model.samplerate

    print(f'Running apply_model on CPU (shifts=0, split=True, overlap={overlap})...')
    t0 = time.perf_counter()
    with torch.no_grad():
        sources = apply_model(model, wav.unsqueeze(0).to('cpu'), device='cpu', shifts=0, split=True, overlap=overlap)[0]
    t1 = time.perf_counter()
    print(f'Separation wall time: {t1 - t0:.2f}s')

    vocals = sources[3]
    vocals_path = cached_vocals_path(vocals_cache_key(audio_path, separator="demucs"))
    # Use a distinct name for overlap variant so we do not overwrite the default cache.
    vocals_path = str(Path(vocals_path).with_suffix('')) + f'_overlap{overlap}_vocals.wav'
    Path(vocals_path).parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(vocals_path, vocals.cpu(), sr)
    print(f'Vocals saved: {vocals_path}')

    stem_info = json.loads(subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', vocals_path],
        capture_output=True, text=True, check=True
    ).stdout)['format']
    print(f'Stem duration: {float(stem_info["duration"]):.2f}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
