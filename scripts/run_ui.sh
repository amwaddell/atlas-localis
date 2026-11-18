#!/usr/bin/env bash
# Launch the Streamlit chat UI for Atlas Localis.
#
# Usage:
#   bash scripts/run_ui.sh              # default port 8501
#   bash scripts/run_ui.sh --port 8502  # custom port
#
# Prerequisites:
#   1. pip install -r requirements.txt
#   2. LanceDB indexes built (vector + keyword FTS)
#   3. Model server running: bash scripts/start_model_server.sh
#   4. (Optional) bash scripts/run_phoenix.sh — to view traces

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/runtime_env.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure we're in the project root so relative paths in config work
cd "$PROJECT_ROOT"

echo "=========================================="
echo "  Atlas Localis — Local Research Terminal"
echo "=========================================="
echo ""
echo "Project root: $PROJECT_ROOT"
echo ""

# Use the selected dedicated interpreter, never an unrelated global executable.
if ! "$ATLAS_PYTHON" -c 'import streamlit' >/dev/null 2>&1; then
    echo "ERROR: Streamlit is missing from $ATLAS_PYTHON. Run explicit setup."
    exit 1
fi
if [ ! -d "$PROJECT_ROOT/data/indexes/active/lancedb" ]; then
    echo "ERROR: No active local index. Run scripts/setup.sh; inspect update_wikipedia.sh status."
    exit 1
fi

# Only port is configurable; binding and analytics cannot be overridden by argv.
ATLAS_UI_PORT=8501
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --port requires a number." >&2
                exit 1
            fi
            ATLAS_UI_PORT="$2"; shift 2 ;;
        *) echo "Unknown argument: $1 (supported: --port NUMBER)"; exit 1 ;;
    esac
done
"$ATLAS_PYTHON" -B "$ATLAS_ROOT/scripts/runtime_guard.py" --port "$ATLAS_UI_PORT"
exec "$ATLAS_PYTHON" -B -m streamlit run src/workbench/ui/streamlit_app.py \
    --server.headless true \
    --server.address 127.0.0.1 \
    --server.port "$ATLAS_UI_PORT" \
    --browser.gatherUsageStats false
