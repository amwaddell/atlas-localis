#!/usr/bin/env bash
# Explicit refresh and offline lifecycle operations; never upgrades models/deps.
set -euo pipefail
ATLAS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ATLAS_ROOT/scripts/runtime_env.sh"
cd "$ATLAS_ROOT"
if [[ $# -eq 0 ]]; then set -- update; fi
exec "$ATLAS_PYTHON" -B -m workbench.data_build.provisioning "$@"
