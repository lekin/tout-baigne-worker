"""Content keywords for captation videos via CLIP zero-shot tagging.

Samples frames evenly across the video, encodes them with an open_clip
ViT-B/32, and scores a curated event vocabulary against the mean text
similarity. Returns ranked (tag, score) pairs — the point is exploratory
tagging ("voir ce que ça donne"), not ground truth.

The model (~350MB weights) downloads once to the torch hub cache
(QA_CACHE_DIR on the worker). Lazy-loaded on first request.
"""
import logging
import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("video_keywords")

_MODEL_NAME = "ViT-B-32"
_PRETRAINED = "laion2b_s34b_b79k"

# Curated event/clubbing vocabulary — CLIP prompts, not class names.
KEYWORD_VOCAB: List[str] = [
    "karaoke singing into a microphone",
    "person singing on stage",
    "crowd dancing at a party",
    "dj performing behind decks",
    "concert stage with lights",
    "nightclub laser show",
    "people cheering with drinks",
    "bar counter with cocktails",
    "pool party in daylight",
    "people on a boat",
    "outdoor festival daytime",
    "fireworks at night",
    "confetti falling over a crowd",
    "smoke machine on a dance floor",
    "close-up of a smiling face",
    "wide shot of a large crowd",
    "phone filming a performance",
    "people dancing in a dark club",
    "performer holding a microphone",
    "disco ball reflecting light",
]

_model = None
_preprocess = None
_tokenizer = None
_text_features = None


def _device() -> str:
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_model():
    global _model, _preprocess, _tokenizer, _text_features
    if _model is not None:
        return
    import torch
    import open_clip

    device = _device()
    logger.info("Loading open_clip %s (%s) on %s", _MODEL_NAME, _PRETRAINED, device)
    model, _, preprocess = open_clip.create_model_and_transforms(
        _MODEL_NAME, pretrained=_PRETRAINED
    )
    model = model.to(device).eval()
    tokenizer = open_clip.get_tokenizer(_MODEL_NAME)
    with torch.no_grad():
        tokens = tokenizer(KEYWORD_VOCAB).to(device)
        text_features = model.encode_text(tokens)
        _text_features = text_features / text_features.norm(dim=-1, keepdim=True)
    _model, _preprocess, _tokenizer = model, preprocess, tokenizer
    logger.info("open_clip ready")


def sample_frames(video_path: str, duration: float, n: int = 10,
                  tmpdir: Optional[str] = None) -> List[str]:
    """Extract n frames evenly spaced between 5% and 95% of the duration."""
    if duration <= 0:
        return []
    tmpdir = tmpdir or tempfile.gettempdir()
    paths = []
    for i in range(n):
        t = duration * (0.05 + 0.9 * i / max(1, n - 1))
        out = os.path.join(tmpdir, f"kw_frame_{os.getpid()}_{i:03d}.jpg")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-ss", f"{t:.2f}", "-i", video_path,
             "-frames:v", "1", "-q:v", "3", "-y", out],
            timeout=60, check=False,
        )
        if os.path.exists(out) and os.path.getsize(out) > 0:
            paths.append(out)
    return paths


def extract_keywords(
    video_path: str,
    duration: float,
    n_frames: int = 10,
    top_k: int = 8,
) -> Dict:
    """Zero-shot CLIP tagging over sampled frames → ranked keywords."""
    _load_model()
    import torch
    from PIL import Image

    frame_paths = sample_frames(video_path, duration, n=n_frames)
    if not frame_paths:
        return {"keywords": [], "frames_sampled": 0}
    try:
        device = _device()
        tensors = []
        for p in frame_paths:
            try:
                tensors.append(_preprocess(Image.open(p).convert("RGB")))
            except Exception:
                logger.warning("unreadable frame %s", p)
        if not tensors:
            return {"keywords": [], "frames_sampled": 0}
        batch = torch.stack(tensors).to(device)
        with torch.no_grad():
            image_features = _model.encode_image(batch)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            # logit_scale ~100; softmax over vocab per frame then mean
            logits = image_features @ _text_features.T
            probs = (logits * _model.logit_scale.exp()).softmax(dim=-1)
            mean_probs = probs.mean(dim=0)
        scores = mean_probs.cpu().tolist()
        ranked = sorted(
            zip(KEYWORD_VOCAB, scores), key=lambda kv: kv[1], reverse=True
        )
        return {
            "keywords": [
                {"tag": tag, "score": round(float(s), 4)}
                for tag, s in ranked[:top_k]
            ],
            "frames_sampled": len(tensors),
        }
    finally:
        for p in frame_paths:
            try:
                os.unlink(p)
            except OSError:
                pass
