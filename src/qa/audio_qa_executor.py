"""Standalone audio QA executor for remote GPU workers."""
import hashlib
import json
import logging
import os
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import torch

logger = logging.getLogger("audio_qa_worker")


class AudioQAErrorType(str, Enum):
    """Structured error taxonomy for infrastructure failures."""

    input_error = "input_error"
    download_error = "download_error"
    hash_mismatch = "hash_mismatch"
    demucs_error = "demucs_error"
    qa_error = "qa_error"
    cuda_error = "cuda_error"
    timeout = "timeout"
    internal_error = "internal_error"


@dataclass
class AudioQARequest:
    """Request to run audio QA on a source audio and a set of lyrics."""

    record_id: Optional[str] = None
    audio_url: str = ""
    audio_sha256: Optional[str] = None
    lyrics: List[Dict[str, Any]] = field(default_factory=list)
    lyrics_source: str = "lrc"
    lyrics_source_track_id: Optional[str] = None
    lyrics_language: Optional[str] = None
    transform: Dict[str, float] = field(default_factory=dict)
    source_duration_ms: Optional[float] = None
    separator_model: str = "htdemucs"
    separator_overlap: float = 0.10
    separator_shifts: int = 0
    separator_split: bool = True
    run_structural_qa: bool = True
    run_stable_ts: bool = False
    stem_retention: str = "failures_only"  # never, failures_only, always
    return_stem: bool = False
    stem_upload_put_url: Optional[str] = None

    @classmethod
    def from_payload(cls, data: Dict[str, Any]) -> "AudioQARequest":
        """Build an AudioQARequest from a client payload.

        Supports the legacy flat format as well as a nested `separator` /
        `options` format for future clients.
        """
        if "input" in data:
            data = data["input"]

        # If a nested separator/options block is present, merge it into the
        # flat fields we already support.
        if "separator" in data and isinstance(data["separator"], dict):
            sep = data.pop("separator")
            data.setdefault("separator_model", sep.get("model", "htdemucs"))
            data.setdefault("separator_overlap", sep.get("overlap", 0.10))
            data.setdefault("separator_shifts", sep.get("shifts", 0))
            data.setdefault("separator_split", sep.get("split", True))
        if "options" in data and isinstance(data["options"], dict):
            opts = data.pop("options")
            data.setdefault("run_structural_qa", opts.get("run_structural_qa", True))
            data.setdefault("run_stable_ts", opts.get("run_stable_ts", False))
            data.setdefault("stem_retention", opts.get("stem_retention", "failures_only"))
            data.setdefault("return_stem", opts.get("return_stem", False))
            data.setdefault("stem_upload_put_url", opts.get("stem_upload_put_url"))

        # Validate retention values.
        retention = data.get("stem_retention", "failures_only")
        if retention not in ("never", "failures_only", "always"):
            raise ValueError(f"Invalid stem_retention: {retention}")

        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def separator_config(self) -> Dict[str, Any]:
        return {
            "model": self.separator_model,
            "overlap": self.separator_overlap,
            "shifts": self.separator_shifts,
            "split": self.separator_split,
        }


@dataclass
class AudioQAResult:
    """Compact result from the audio QA worker."""

    success: bool
    record_id: Optional[str] = None
    status: Optional[str] = None
    diagnosis: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None
    gates: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    separator: Optional[Dict[str, Any]] = None
    worker: Optional[Dict[str, Any]] = None
    stem_reference: Optional[str] = None


def _redact_url(url: Optional[str]) -> str:
    """Redact query strings that may contain signatures/tokens."""
    if not url:
        return ""
    if "?" in url:
        return url.split("?")[0] + "?<redacted>"
    return url


def _download_url(url: str, dst: str, timeout: int = 300) -> None:
    if url.startswith("file://"):
        import shutil

        shutil.copy(url[7:], dst)
        return
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _upload_to_put_url(file_path: str, put_url: str, timeout: int = 300) -> bool:
    """Upload a file to a pre-signed PUT URL (provider-agnostic)."""
    try:
        with open(file_path, "rb") as f:
            r = requests.put(put_url, data=f, timeout=timeout)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("Stem upload failed: %s", e)
        return False


def _import_qa_modules():
    from src.qa.diagnosis import diagnose, status_from_gates_and_diagnosis
    from src.qa.gates import evaluate_gates
    from src.qa.models import LyricLine, LyricWord, StructuredLyrics, TimelineTransform
    from src.qa.scoring import compute_sync_metrics
    from src.qa.separator import PyTorchDemucsSeparator, VocalSeparationConfig
    from src.qa.structural import StructuralAnalyzer

    return {
        "diagnose": diagnose,
        "status_from_gates_and_diagnosis": status_from_gates_and_diagnosis,
        "evaluate_gates": evaluate_gates,
        "LyricLine": LyricLine,
        "LyricWord": LyricWord,
        "StructuredLyrics": StructuredLyrics,
        "TimelineTransform": TimelineTransform,
        "compute_sync_metrics": compute_sync_metrics,
        "PyTorchDemucsSeparator": PyTorchDemucsSeparator,
        "VocalSeparationConfig": VocalSeparationConfig,
        "StructuralAnalyzer": StructuralAnalyzer,
    }


def _build_lyrics(lines_data: List[Dict[str, Any]], source: str, track_id: Optional[str], language: Optional[str]) -> Any:
    mods = _import_qa_modules()
    LyricLine = mods["LyricLine"]
    LyricWord = mods["LyricWord"]
    StructuredLyrics = mods["StructuredLyrics"]

    lines: List[Any] = []
    for idx, item in enumerate(lines_data):
        words: List[Any] = []
        for widx, w in enumerate(item.get("words", [])):
            words.append(
                LyricWord(
                    id=f"W{idx+1:04d}-{widx+1:04d}",
                    text=str(w.get("text", "")),
                    source_start_ms=w.get("source_start_ms"),
                    source_end_ms=w.get("source_end_ms"),
                )
            )
        lines.append(
            LyricLine(
                id=f"L{idx+1:04d}",
                text=str(item.get("text", "")).strip(),
                source_start_ms=float(item["source_start_ms"]),
                source_end_ms=item.get("source_end_ms"),
                words=words,
            )
        )
    return StructuredLyrics(
        source=source,
        source_track_id=str(track_id) if track_id else None,
        language=language,
        lines=lines,
    )


def _predicted_duration_ms(lyrics: Any) -> Optional[float]:
    ends = [ln.predicted_end_ms for ln in lyrics.lines if ln.predicted_end_ms is not None]
    return max(ends) if ends else None


def _source_duration_ms(audio_path: str, provided: Optional[float]) -> float:
    if provided is not None:
        return provided
    try:
        import torchaudio

        wav, sr = torchaudio.load(audio_path)
        return float(wav.shape[-1]) / sr * 1000.0
    except Exception:
        return 0.0


def _media_duration_ms(path: str) -> Optional[float]:
    try:
        import torchaudio

        wav, sr = torchaudio.load(path)
        return float(wav.shape[-1]) / sr * 1000.0
    except Exception:
        return None


def _to_json(obj: Any) -> Any:
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json(v) for v in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_json(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if obj != obj:  # NaN
        return None
    return obj


def _maybe_delete_stem(stem_cache_path: Path, request: AudioQARequest, status_value: Optional[str]) -> None:
    """Apply stem retention policy."""
    if request.stem_retention == "always":
        return
    if request.stem_retention == "failures_only" and status_value != "SYNC_VERIFIED":
        return
    # "never" or success with failures_only => delete
    try:
        if stem_cache_path.exists():
            stem_cache_path.unlink()
            logger.info("Deleted retained stem: %s", stem_cache_path)
    except Exception as e:
        logger.warning("Failed to delete stem %s: %s", stem_cache_path, e)


def _handle_upload_and_reference(
    stem_cache_path: Path,
    request: AudioQARequest,
    status_value: Optional[str],
) -> Optional[str]:
    """Return a reference for the retained stem, uploading if a PUT URL was supplied."""
    if request.stem_retention == "never":
        return None
    if request.stem_retention == "failures_only" and status_value == "SYNC_VERIFIED":
        return None
    if not stem_cache_path.exists():
        return None

    if request.stem_upload_put_url:
        if _upload_to_put_url(str(stem_cache_path), request.stem_upload_put_url):
            # Reference is the PUT URL with query signature redacted for logs.
            return request.stem_upload_put_url.split("?")[0]
        logger.warning("Stem upload failed, leaving local reference only.")
    return str(stem_cache_path)


def _resolve_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def run_audio_qa(request: AudioQARequest, work_dir: Optional[str] = None, gpu_name: Optional[str] = None) -> AudioQAResult:
    """Run Demucs + Structural QA for a single candidate."""
    mods = _import_qa_modules()
    VocalSeparationConfig = mods["VocalSeparationConfig"]
    PyTorchDemucsSeparator = mods["PyTorchDemucsSeparator"]
    StructuralAnalyzer = mods["StructuralAnalyzer"]
    TimelineTransform = mods["TimelineTransform"]
    compute_sync_metrics = mods["compute_sync_metrics"]
    evaluate_gates = mods["evaluate_gates"]
    diagnose = mods["diagnose"]
    status_from_gates_and_diagnosis = mods["status_from_gates_and_diagnosis"]

    record_id = request.record_id or "unknown"
    work_dir = work_dir or os.path.join(tempfile.gettempdir(), "runpod_qa_work")
    os.makedirs(work_dir, exist_ok=True)

    total_start = time.time()
    timings: Dict[str, float] = {
        "download_ms": 0.0,
        "separation_ms": 0.0,
        "alignment_ms": 0.0,
        "scoring_ms": 0.0,
    }

    def fail(error_type: AudioQAErrorType, detail: str) -> AudioQAResult:
        logger.error("[%s] %s: %s", record_id, error_type.value, detail)
        return AudioQAResult(
            success=False,
            record_id=record_id,
            error=detail,
            error_type=error_type.value,
            worker={
                "gpu": gpu_name or "unknown",
                "runtime_ms": (time.time() - total_start) * 1000.0,
                "timings_ms": timings,
            },
        )

    # Validate request.
    if not request.audio_url:
        return fail(AudioQAErrorType.input_error, "missing audio_url")
    if not request.lyrics:
        return fail(AudioQAErrorType.input_error, "missing lyrics")

    try:
        # Download source audio.
        logger.info(
            "[%s] Downloading audio from %s",
            record_id,
            _redact_url(request.audio_url),
        )
        audio_filename = os.path.basename(request.audio_url.split("?")[0]) or "source.mp3"
        audio_path = os.path.join(work_dir, f"{record_id}_{audio_filename}")
        t0 = time.time()
        _download_url(request.audio_url, audio_path)
        timings["download_ms"] = (time.time() - t0) * 1000.0
        logger.info("[%s] Audio downloaded in %.1f ms", record_id, timings["download_ms"])
    except Exception as e:
        return fail(AudioQAErrorType.download_error, f"audio_download_failed: {e}")

    # Validate hash.
    if request.audio_sha256:
        logger.info("[%s] Validating audio sha256", record_id)
        try:
            actual = _sha256_file(audio_path)
            if actual.lower() != request.audio_sha256.lower():
                logger.warning(
                    "[%s] Hash mismatch: expected %s... got %s...",
                    record_id,
                    request.audio_sha256[:16],
                    actual[:16],
                )
                return fail(AudioQAErrorType.hash_mismatch, "audio_sha256_mismatch")
        except Exception as e:
            return fail(AudioQAErrorType.hash_mismatch, f"audio_sha256_validation_failed: {e}")

    try:
        # Build lyrics and transform.
        lyrics = _build_lyrics(
            request.lyrics,
            request.lyrics_source,
            request.lyrics_source_track_id,
            request.lyrics_language,
        )
        transform = TimelineTransform(**request.transform)
        source_duration_ms = _source_duration_ms(audio_path, request.source_duration_ms)

        # Vocal separation.
        device = _resolve_device()
        logger.info("[%s] Separating vocals with %s on %s", record_id, request.separator_model, device)

        sep_config = VocalSeparationConfig(
            backend="pytorch_demucs",
            model=request.separator_model,
            overlap=request.separator_overlap,
            shifts=request.separator_shifts,
            split=request.separator_split,
            device=device,
            package_version=None,
        )

        from src.qa.cache import hash_audio_file

        audio_hash = hash_audio_file(audio_path)
        stem_cache_path = Path(sep_config.cached_path(audio_path))
        cache_hit = stem_cache_path.exists()

        if cache_hit:
            logger.info("[%s] Stem cache hit: %s", record_id, stem_cache_path.name)
            timings["separation_ms"] = 0.0
        else:
            t0 = time.time()
            sep = PyTorchDemucsSeparator(device=device)
            ok = sep.separate(audio_path, stem_cache_path, sep_config)
            if not ok:
                return fail(AudioQAErrorType.demucs_error, "vocal_separation_failed")
            timings["separation_ms"] = (time.time() - t0) * 1000.0
            logger.info("[%s] Demucs completed in %.1f ms", record_id, timings["separation_ms"])
            if stem_cache_path.exists():
                cache_hit = True

        if not cache_hit or not stem_cache_path.exists():
            return fail(AudioQAErrorType.demucs_error, "vocal_stem_missing")

        stem_duration_ms = _media_duration_ms(str(stem_cache_path))
        stem_delta_ms = (stem_duration_ms or 0.0) - source_duration_ms

        # Structural QA.
        logger.info("[%s] Running Structural QA", record_id)
        t0 = time.time()
        aligner = StructuralAnalyzer()
        alignment = aligner.align(audio_path, lyrics, vocals_path=str(stem_cache_path))
        if alignment.error or not any(ln.predicted_start_ms is not None for ln in alignment.lyrics.lines):
            return fail(
                AudioQAErrorType.qa_error,
                alignment.error or "alignment_no_predictions",
            )
        timings["alignment_ms"] = (time.time() - t0) * 1000.0
        logger.info("[%s] Structural QA completed in %.1f ms", record_id, timings["alignment_ms"])

        logger.info("[%s] Computing metrics, gates, and diagnosis", record_id)
        t0 = time.time()
        predicted_duration_ms = _predicted_duration_ms(alignment.lyrics)
        metrics = compute_sync_metrics(
            alignment.lyrics, transform, source_duration_ms, predicted_duration_ms
        )
        gate_results = evaluate_gates(metrics)
        diagnosis = diagnose(
            metrics,
            alignment.lyrics,
            estimated_global_offset_ms=alignment.estimated_global_offset_ms,
            lyrics_to_source_offset_ms=transform.lyrics_to_source_offset_ms,
        )
        status = status_from_gates_and_diagnosis(gate_results, diagnosis)
        timings["scoring_ms"] = (time.time() - t0) * 1000.0

        status_value = status.value if hasattr(status, "value") else str(status)

        # Stem retention and optional upload.
        stem_reference = _handle_upload_and_reference(stem_cache_path, request, status_value)
        _maybe_delete_stem(stem_cache_path, request, status_value)

        total_ms = (time.time() - total_start) * 1000.0
        logger.info("[%s] status=%s runtime_ms=%.0f", record_id, status_value, total_ms)

        return AudioQAResult(
            success=True,
            record_id=record_id,
            status=status_value,
            diagnosis=_to_json(diagnosis),
            metrics=_to_json(metrics),
            gates=_to_json(gate_results),
            separator={
                "backend": "pytorch_demucs",
                "model": request.separator_model,
                "overlap": request.separator_overlap,
                "shifts": request.separator_shifts,
                "split": request.separator_split,
                "separation_time_ms": timings["separation_ms"],
                "cache_hit": cache_hit,
                "stem_reference": stem_reference,
                "stem_duration_ms": stem_duration_ms,
                "stem_delta_ms": stem_delta_ms,
                "demucs_version": getattr(__import__("demucs", fromlist=["__version__"]), "__version__", "unknown"),
            },
            worker={
                "gpu": gpu_name or (torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"),
                "runtime_ms": total_ms,
                "timings_ms": timings,
                "audio_hash": audio_hash,
            },
            stem_reference=stem_reference,
        )
    except Exception as e:
        logger.exception("[%s] Internal worker error", record_id)
        return fail(AudioQAErrorType.internal_error, f"{type(e).__name__}: {e}")
