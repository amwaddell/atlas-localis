#!/usr/bin/env bash
set -euo pipefail
ATLAS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ATLAS_SETUP_PYTHON:-python3}" -B "$ATLAS_ROOT/scripts/provision.py" "$@"
