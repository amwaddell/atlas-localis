#!/usr/bin/env bash
# Native LLM only; cached E5/BGE are owned by the application's resource cache.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/runtime_env.sh"
exec "$ATLAS_PYTHON" -B "$ATLAS_ROOT/scripts/native_runtime.py" "$@"
