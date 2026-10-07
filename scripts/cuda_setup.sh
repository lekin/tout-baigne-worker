#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a disposable Ubuntu/CUDA worker for the Karaoke vocal-separation benchmark.
# Provider-agnostic: prefers a pre-installed PyTorch+CUDA image, otherwise creates a venv.

VENV_DIR="${VENV_DIR:-${HOME}/.venvs/cuda_bench}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
PYTORCH_CUDA_INDEX="${PYTORCH_CUDA_INDEX:-https://download.pytorch.org/whl/cu124}"
DEMUCS_VERSION="${DEMUCS_VERSION:-}"

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "ERROR: nvidia-smi not found. This host does not appear to have an NVIDIA GPU."
    exit 1
fi

echo "GPU info:"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
echo

# Ensure ffmpeg is available (it is pre-installed on many RunPod templates; install if missing)
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "Installing ffmpeg..."
    apt-get update -qq
    apt-get install -y -qq ffmpeg
fi

# Detect if a CUDA-capable PyTorch is already present in the system Python.
SYS_PYTORCH_CUDA="false"
if python3 -c "import torch; import sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
    SYS_PYTORCH_CUDA="true"
    echo "System Python already has PyTorch with CUDA. Skipping torch install."
fi

if [[ "${SYS_PYTORCH_CUDA}" == "true" ]]; then
    python3 -m pip install -q --upgrade pip
    if [[ -n "${DEMUCS_VERSION}" ]]; then
        python3 -m pip install -q "demucs==${DEMUCS_VERSION}"
    else
        python3 -m pip install -q demucs
    fi
    python3 -m pip install -q soundfile
    ACTIVE_PYTHON="python3"
else
    echo "No system PyTorch with CUDA found. Installing Python ${PYTHON_VERSION} and a fresh venv."
    apt-get update -qq
    apt-get install -y -qq python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python${PYTHON_VERSION}-pip

    rm -rf "${VENV_DIR}"
    python${PYTHON_VERSION} -m venv "${VENV_DIR}"
    source "${VENV_DIR}/bin/activate"

    pip install -q --upgrade pip setuptools wheel
    pip install -q soundfile
    pip install -q torch torchaudio --index-url "${PYTORCH_CUDA_INDEX}"
    if [[ -n "${DEMUCS_VERSION}" ]]; then
        pip install -q "demucs==${DEMUCS_VERSION}"
    else
        pip install -q demucs
    fi
    ACTIVE_PYTHON="${VENV_DIR}/bin/python"
fi

PYTHON_FULL_VERSION=$(${ACTIVE_PYTHON} --version 2>&1)
PYTORCH_VERSION=$(${ACTIVE_PYTHON} -c 'import torch; print(torch.__version__)')
CUDA_AVAILABLE=$(${ACTIVE_PYTHON} -c 'import torch; print("true" if torch.cuda.is_available() else "false")')
DEMUCS_VERSION_INSTALLED=$(${ACTIVE_PYTHON} -c 'import demucs; print(demucs.__version__)')

echo "Python: ${PYTHON_FULL_VERSION}"
echo "PyTorch: ${PYTORCH_VERSION}"
echo "CUDA available: ${CUDA_AVAILABLE}"
echo "Demucs: ${DEMUCS_VERSION_INSTALLED}"

if [[ "${CUDA_AVAILABLE}" != "true" ]]; then
    echo "ERROR: PyTorch cannot see a CUDA device. Check NVIDIA driver / CUDA install."
    exit 1
fi

echo "Bootstrap complete. Active Python: ${ACTIVE_PYTHON}"
