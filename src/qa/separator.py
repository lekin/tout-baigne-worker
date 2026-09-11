"""Vocal-separation backends for QA with content-addressed caching."""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union

import torch
import torchaudio

from demucs.pretrained import get_model
from demucs.apply import apply_model

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class VocalSeparationConfig:
    """Configuration for one vocal-separation run.

    This is the identity of the vocal stem used for the QA cache key.
    """

    backend: str = "demucs_cpu"  # demucs_cpu, demucs_mps, mlx, ffmpeg
    model: str = "htdemucs"      # htdemucs, hdemucs_mmi, ...
    package_version: Optional[str] = None
    shifts: int = 0
    overlap: float = 0.25
    split: bool = True
    device: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def identity_dict(self) -> Dict[str, Any]:
        d = {
            "backend": self.backend,
            "model": self.model,
            "shifts": self.shifts,
            "overlap": self.overlap,
            "split": self.split,
        }
        if self.package_version:
            d["package_version"] = self.package_version
        if self.extra:
            d["extra"] = self.extra
        return d

    def cache_key(self, audio_path: Union[str, Path]) -> str:
        """Return the content-addressed cache key for this stem."""
        from src.qa.cache import hash_audio_file, _hash_dict

        audio_hash = hash_audio_file(str(audio_path))
        settings_hash = _hash_dict(self.identity_dict())
        return f"{audio_hash}_{settings_hash}"

    def cached_path(self, audio_path: Union[str, Path]) -> Path:
        from src.qa.cache import get_vocals_cache_path

        return get_vocals_cache_path() / f"{self.cache_key(audio_path)}_vocals.wav"


class VocalSeparator(Protocol):
    """Backend that can separate a vocal stem and write it to a file."""

    name: str

    def separate(
        self,
        audio_path: Union[str, Path],
        output_path: Union[str, Path],
        config: VocalSeparationConfig,
    ) -> bool:
        """Separate vocals from *audio_path* and write a WAV to *output_path*.

        Returns True on success. The caller is responsible for cache key logic.
        """
        ...


class PyTorchDemucsSeparator:
    """Demucs via the installed PyTorch demucs package."""

    name = "pytorch_demucs"
    _MODEL_CACHE: Dict[str, Any] = {}

    def __init__(self, device: Optional[str] = None):
        self.device = device

    @classmethod
    def preload_model(cls, model_name: str) -> None:
        """Download and cache a Demucs model at import/startup time."""
        if model_name not in cls._MODEL_CACHE:
            cls._MODEL_CACHE[model_name] = get_model(model_name)

    def separate(
        self,
        audio_path: Union[str, Path],
        output_path: Union[str, Path],
        config: VocalSeparationConfig,
    ) -> bool:
        try:
            device = torch.device(self._resolve_device())
            model = self._MODEL_CACHE.get(config.model)
            if model is None:
                model = get_model(config.model)
                self._MODEL_CACHE[config.model] = model
            model.to(device)
            model.eval()

            wav, sr = torchaudio.load(str(audio_path))
            if wav.shape[0] == 1:
                wav = wav.repeat(2, 1)
            if sr != model.samplerate:
                wav = torchaudio.transforms.Resample(sr, model.samplerate)(wav)
                sr = model.samplerate

            with torch.no_grad():
                wav_input = wav.unsqueeze(0).to(device)
                # The original code catches MPS failure and falls back to CPU.
                try:
                    sources = apply_model(
                        model,
                        wav_input,
                        device=device,
                        shifts=config.shifts,
                        split=config.split,
                        overlap=config.overlap,
                    )[0]
                except (NotImplementedError, RuntimeError) as e:
                    if device.type != "cpu":
                        print(f"⚠️ {device.type.upper()} failed ({e}), falling back to CPU...")
                        device = torch.device("cpu")
                        model.to(device)
                        wav_input = wav_input.cpu()
                        sources = apply_model(
                            model,
                            wav_input,
                            device=device,
                            shifts=config.shifts,
                            split=config.split,
                            overlap=config.overlap,
                        )[0]
                    else:
                        raise

            # source index for "vocals" may differ per model; htdemucs/hdemucs_mmi use 4-source.
            vocals = sources[3]
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            torchaudio.save(str(output_path), vocals.cpu(), sr)
            return os.path.getsize(output_path) > 1000
        except Exception as e:
            print(f"❌ PyTorch Demucs separation failed: {e}")
            return False

    def _resolve_device(self) -> str:
        if self.device:
            return self.device
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"


class FFmpegVocalSeparator:
    """Fast center-channel extraction via FFmpeg; lower quality, CPU only."""

    name = "ffmpeg"

    def separate(
        self,
        audio_path: Union[str, Path],
        output_path: Union[str, Path],
        config: VocalSeparationConfig,
    ) -> bool:
        from src.karaoke_generator import KaraokeGenerator
        path = KaraokeGenerator.separate_vocals_ffmpeg(
            str(audio_path),
            output_dir=str(Path(output_path).parent),
            keep_files=True,
        )
        if not path:
            return False
        if Path(path) != Path(output_path):
            os.replace(path, output_path)
        return os.path.getsize(output_path) > 1000


class MLXDemucsSeparator:
    """Demucs via an isolated MLX virtual environment.

    The production venv does not need MLX dependencies; the helper script in
    the isolated venv does the work and writes the requested output path.
    """

    name = "mlx_demucs"

    def __init__(self, mlx_venv: Optional[Union[str, Path]] = None):
        repo_root = Path(__file__).resolve().parents[2]
        self.mlx_venv = Path(mlx_venv) if mlx_venv else repo_root / ".venv_bench_mlx"

    def separate(
        self,
        audio_path: Union[str, Path],
        output_path: Union[str, Path],
        config: VocalSeparationConfig,
    ) -> bool:
        script = Path(__file__).resolve().parents[2] / "scripts" / "separate_mlx.py"
        if not script.exists():
            print(f"❌ MLX helper script not found: {script}")
            return False

        python = self.mlx_venv / "bin" / "python"
        if not python.exists():
            print(f"❌ MLX venv not found: {self.mlx_venv}")
            return False

        cmd = [
            str(python),
            str(script),
            "--audio",
            str(audio_path),
            "--output",
            str(output_path),
            "--model",
            config.model,
            "--shifts",
            str(config.shifts),
            "--overlap",
            str(config.overlap),
        ]
        if config.split:
            cmd.append("--split")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=sys.stdout,
                stderr=sys.stderr,
                text=True,
            )
            return os.path.getsize(output_path) > 1000
        except subprocess.CalledProcessError as e:
            print(f"❌ MLX Demucs separation failed: {e}")
            return False


# Friendly aliases for python-audio-separator model filenames.
AUDIO_SEPARATOR_ALIASES: Dict[str, str] = {
    "mdx23c": "MDX23C-8KFFT-InstVoc_HQ.ckpt",
    "melband_roformer": "MelBandRoformerSYHFTV3Epsilon.ckpt",
    "bs_roformer": "bs_roformer_vocals_resurrection_unwa.ckpt",
    "kuielab": "kuielab_a_vocals.onnx",
    "kuielab_a_vocals": "kuielab_a_vocals.onnx",
}


def resolve_audio_separator_model(model: str) -> str:
    """Return a concrete model filename, mapping friendly aliases if needed."""
    return AUDIO_SEPARATOR_ALIASES.get(model, model)


class PythonAudioSeparator:
    """Unified audio-separator backend (python-audio-separator package).

    Supports MDX, MDXC/RoFormer and Demucs models with a single pipeline.
    Models are loaded on first use and cached per filename in a class-level map.
    """

    name = "audio_separator"
    _SEPARATOR_CACHE: Dict[str, Any] = {}

    def __init__(self, device: Optional[str] = None):
        self.device = device

    def _resolve_device(self) -> Optional[str]:
        if self.device:
            return self.device
        if torch.cuda.is_available():
            return "cuda"
        return None

    def _get_separator(self, model_filename: str, model_file_dir: Path, output_dir: Path, config: VocalSeparationConfig) -> Any:
        from audio_separator.separator import Separator

        params = config.extra or {}
        params_key = json.dumps(params, sort_keys=True, default=str)
        cache_key = f"{model_filename}:{model_file_dir}:{params_key}"
        if cache_key not in self._SEPARATOR_CACHE:
            use_cuda = torch.cuda.is_available()
            use_mps = getattr(torch.backends.mps, "is_available", lambda: False)()
            use_accelerated = use_cuda or use_mps
            # torch.compile is only reliable on CUDA; MPS/CPU fall back to eager.
            use_compile = use_cuda
            kwargs: Dict[str, Any] = {}
            if "mdx" in params:
                kwargs["mdx_params"] = params["mdx"]
            if "vr" in params:
                kwargs["vr_params"] = params["vr"]
            if "demucs" in params:
                kwargs["demucs_params"] = params["demucs"]
            if "mdxc" in params:
                kwargs["mdxc_params"] = params["mdxc"]
            sep = Separator(
                log_level=logging.WARNING,
                model_file_dir=str(model_file_dir),
                output_dir=str(output_dir),
                output_format="WAV",
                output_single_stem="Vocals",
                sample_rate=44100,
                use_native_fp16=use_accelerated,
                use_torch_compile=use_compile,
                **kwargs,
            )
            sep.load_model(model_filename)
            self._SEPARATOR_CACHE[cache_key] = sep
        return self._SEPARATOR_CACHE[cache_key]

    def separate(
        self,
        audio_path: Union[str, Path],
        output_path: Union[str, Path],
        config: VocalSeparationConfig,
    ) -> bool:
        try:
            from importlib.metadata import version
            audio_sep_version = version("audio-separator")
        except Exception:
            audio_sep_version = "unknown"

        model_filename = resolve_audio_separator_model(config.model)

        # Model download/preload directory.
        model_file_dir = Path(settings.qa_audio_separator_model_dir or "/workspace/audio-separator-models")
        model_file_dir.mkdir(parents=True, exist_ok=True)

        # Temporary output directory to isolate files.
        output_dir = Path(tempfile.mkdtemp(prefix="audio-sep-"))
        try:
            sep = self._get_separator(model_filename, model_file_dir, output_dir, config)
            output_files: List[str] = sep.separate(str(audio_path))
            if not output_files:
                logger.error("audio-separator returned no output files")
                return False

            # Use the first (and usually only) file produced.
            # audio-separator returns paths relative to output_dir for some models.
            vocals_path = Path(sep.output_dir) / Path(output_files[0]).name
            if not vocals_path.exists():
                logger.error("audio-separator output file not found: %s", vocals_path)
                return False

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(vocals_path, output_path)
            return os.path.getsize(output_path) > 1000
        except Exception as e:
            logger.exception("audio-separator separation failed: %s", e)
            return False
        finally:
            try:
                shutil.rmtree(output_dir, ignore_errors=True)
            except Exception:
                pass


def get_vocal_separator(config: VocalSeparationConfig, mlx_venv: Optional[Path] = None) -> VocalSeparator:
    """Return the appropriate separator for the given configuration."""
    if config.backend == "audio_separator":
        return PythonAudioSeparator(device=config.device)
    if config.backend == "ffmpeg":
        return FFmpegVocalSeparator()
    if config.backend.startswith("mlx"):
        return MLXDemucsSeparator(mlx_venv=mlx_venv)
    return PyTorchDemucsSeparator(device=config.device)
