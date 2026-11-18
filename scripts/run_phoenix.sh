#!/usr/bin/env bash
# Optional read-only local trace viewer; dependency installation belongs to setup.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/runtime_env.sh"
export PHOENIX_HOST=127.0.0.1
# This installed Phoenix binds its writable gRPC collector to [::].
# Read-only disables that collector through its supported CLI/API contract.
echo "Starting read-only local Phoenix viewer (live ingestion pending Stage 6)."
exec "$ATLAS_PYTHON" -B -m phoenix.server.main --host 127.0.0.1 --read-only --no-internet serve
