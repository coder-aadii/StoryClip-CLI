#!/usr/bin/env bash
# run_server.sh - Starts the local VoiceStudio speech synthesis service
# pointing to dedicated model storage in /home/rails/Work/AI-Models.

set -euo pipefail

AI_MODELS_DIR="${AI_MODELS_DIR:-/home/rails/Work/AI-Models}"
VOICESTUDIO_DIR="${AI_MODELS_DIR}/voicestudio"
HF_CACHE_DIR="${AI_MODELS_DIR}/huggingface"
TORCH_CACHE_DIR="${AI_MODELS_DIR}/torch"
PORT="${PORT:-38123}"

echo "=========================================================="
echo " Starting VoiceStudio Local Service"
echo " Model Storage Directory : ${VOICESTUDIO_DIR}"
echo " HuggingFace Cache       : ${HF_CACHE_DIR}"
echo " Service Listening Port  : ${PORT}"
echo "=========================================================="

mkdir -p "${VOICESTUDIO_DIR}/checkpoints"
mkdir -p "${VOICESTUDIO_DIR}/voices"
mkdir -p "${HF_CACHE_DIR}"
mkdir -p "${TORCH_CACHE_DIR}"

export HF_HOME="${HF_CACHE_DIR}"
export TORCH_HOME="${TORCH_CACHE_DIR}"
export VOICESTUDIO_MODELS_DIR="${VOICESTUDIO_DIR}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [ "${1:-}" != "--docker" ] && [ -f "${SCRIPT_DIR}/server.py" ]; then
    echo "[VoiceStudio] Launching native Python FastAPI server on CPU..."
    PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
    if [ ! -x "${PYTHON_BIN}" ]; then
        PYTHON_BIN="python3"
    fi
    exec "${PYTHON_BIN}" -m uvicorn services.voicestudio.server:app --host 0.0.0.0 --port "${PORT}"
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "[VoiceStudio] Launching via Docker container..."
    GPU_FLAG=""
    if command -v nvidia-smi >/dev/null 2>&1; then
        GPU_FLAG="--gpus all"
    fi
    exec docker run --rm -it ${GPU_FLAG} \
        -p "${PORT}:38123" \
        -v "${AI_MODELS_DIR}:/models" \
        -e HF_HOME="/models/huggingface" \
        -e TORCH_HOME="/models/torch" \
        -e MODELS_DIR="/models/voicestudio" \
        palashdeb/omnivoice-studio:latest
else
    echo "Error: Neither local uvicorn server nor Docker is available."
    exit 1
fi
