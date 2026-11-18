#!/bin/bash
# Start local llama.cpp server with a GGUF model on Apple Silicon (Metal).
#
# Uses llama-server from Homebrew (brew install llama.cpp) which provides
# an OpenAI-compatible API at http://$HOST:$PORT/v1
#
# Usage: bash scripts/start_model_server.sh
#
# To swap models, change MODEL_FILE below (everything else stays the same).

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/runtime_env.sh"
cd "$ATLAS_ROOT"

# ── Model selection (change ONLY this line to swap models) ──────────────
MODEL_FILE="Ministral-3-8B-Instruct-2512-Q4_K_M.gguf"
# Previous: Ministral-8B-Instruct-2410-Q4_K_M.gguf
# Alternatives you may download later:
#   Ministral-3-8B-Instruct-2512-Q5_K_M.gguf   (better quality, ~6 GB)
#   Ministral-3-8B-Instruct-2512-Q8_0.gguf      (high quality,  ~9 GB)
# ────────────────────────────────────────────────────────────────────────

# ── Server settings ─────────────────────────────────────────────────────
MODEL_DIR="${ATLAS_ROOT}/data/models"
MODEL_PATH="${MODEL_DIR}/${MODEL_FILE}"
HOST="127.0.0.1"
PORT="${LLM_PORT:-8080}"
"$ATLAS_PYTHON" -B "$ATLAS_ROOT/scripts/runtime_guard.py" --host "$HOST" --port "$PORT" --free-port

# ── Performance tuning (Metal / Apple Silicon) ──────────────────────────
N_GPU_LAYERS=-1       # -1 = offload every layer to Metal GPU
CONTEXT_SIZE=8192     # 8k is snappy; raise to 16384 if you need longer context
# ────────────────────────────────────────────────────────────────────────

llama-server --version
echo "Starting llama.cpp server..."
echo "  Model : ${MODEL_FILE}"
echo "  Path  : ${MODEL_PATH}"
echo "  Server: http://${HOST}:${PORT}"
echo "  GPU   : Metal (all layers offloaded)"
echo "  Context size: ${CONTEXT_SIZE}"
echo ""

# Check model file exists
if [ ! -f "$MODEL_PATH" ]; then
    echo "❌  Model file not found: $MODEL_PATH"
    echo ""
    echo "Provision the GGUF during explicit online setup; see docs/dev/setup.md."
    exit 1
fi

# Check llama-server is installed
if ! command -v llama-server &> /dev/null; then
    echo "❌  llama-server not found."
    echo ""
    echo "Install it with:"
    echo "  brew install llama.cpp"
    exit 1
fi

echo "Press Ctrl+C to stop"
echo ""

exec llama-server \
    --model "$MODEL_PATH" \
    --alias ministral-3-8b-instruct \
    --parallel 1 \
    --host "$HOST" \
    --port "$PORT" \
    --n-gpu-layers "$N_GPU_LAYERS" \
    --ctx-size "$CONTEXT_SIZE"
