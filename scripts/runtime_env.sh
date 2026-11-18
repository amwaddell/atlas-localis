#!/usr/bin/env bash
# Source from runtime launchers; never install or acquire assets here.
ATLAS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ATLAS_DEFAULT_VENV="${ATLAS_ROOT}/.venv"
if [[ -x "${ATLAS_ROOT}/.atlas-venv/bin/python" ]]; then ATLAS_DEFAULT_VENV="${ATLAS_ROOT}/.atlas-venv"; fi
ATLAS_PYTHON="${ATLAS_PYTHON:-${ATLAS_DEFAULT_VENV}/bin/python}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1 ANONYMIZED_TELEMETRY=False
# Static output quantization fetches a calibration corpus; retain float32 output.
export INFINITY_EMBEDDING_DTYPE=float32 INFINITY_TRUST_REMOTE_CODE=false
export INFINITY_ANONYMOUS_USAGE_STATS=false
export PHOENIX_TELEMETRY_ENABLED=false PHOENIX_ALLOW_EXTERNAL_RESOURCES=false
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
export STREAMLIT_SERVER_ADDRESS=127.0.0.1
# Local clients also explicitly ignore proxies; curl health probes do so below.
"$ATLAS_PYTHON" -B "$ATLAS_ROOT/scripts/runtime_guard.py"
