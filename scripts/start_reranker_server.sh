#!/usr/bin/env bash
# -------------------------------------------------------------------
# Start the Infinity reranker server.
#
# Serves BAAI/bge-reranker-v2-m3 on port 8082.
# Models must already be provisioned; startup is offline.
#
# Prerequisites:
#   pip install "infinity-emb[all]"
#
# Usage:
#   bash scripts/start_reranker_server.sh
#   bash scripts/start_reranker_server.sh --port 8082
#   RERANKER_MODEL=BAAI/bge-reranker-v2-m3 bash scripts/start_reranker_server.sh
# -------------------------------------------------------------------

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/runtime_env.sh"

# Configurable via environment
MODEL="${RERANKER_MODEL:-BAAI/bge-reranker-v2-m3}"
PORT="${RERANKER_PORT:-8082}"
HOST="${RERANKER_HOST:-127.0.0.1}"

# Parse CLI args (override env)
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)  MODEL="$2";  shift 2 ;;
        --port)   PORT="$2";   shift 2 ;;
        --host)   HOST="$2";   shift 2 ;;
        *)        echo "Unknown arg: $1"; exit 1 ;;
    esac
done

echo "============================================="
echo "  Infinity Reranker Server"
echo "============================================="
echo "  Model:  ${MODEL}"
echo "  Host:   ${HOST}"
echo "  Port:   ${PORT}"
echo "  URL:    http://${HOST}:${PORT}"
echo "============================================="
echo ""

# Check infinity is installed
if ! command -v infinity_emb &> /dev/null; then
    echo "ERROR: infinity_emb not found."
    echo ""
    echo "Install with:"
    echo '  pip install "infinity-emb[all]"'
    echo ""
    exit 1
fi

# Validate before starting Infinity. Keep the semantic model ID client-side.
SERVED_MODEL="$MODEL"
MODEL="$("$ATLAS_PYTHON" -B "$ATLAS_ROOT/scripts/runtime_guard.py" --host "$HOST" --port "$PORT" --model "$MODEL")"

# Launch
exec infinity_emb v2 \
    --model-id "${MODEL}" \
    --served-model-name "${SERVED_MODEL}" \
    --port "${PORT}" \
    --host "${HOST}" \
    --engine torch