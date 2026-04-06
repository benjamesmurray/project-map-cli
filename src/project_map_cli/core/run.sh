#!/bin/bash
# run.sh - Wrapper for digest_tool_v6 with virtual environment

set -e

# Default WDE_ROOT if not set
: "${WDE_ROOT:=/opt/wde}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Virtual environment python not found at $VENV_PYTHON"
    exit 1
fi

# Ensure module resolution
export PYTHONPATH="$WDE_ROOT"

echo "run.sh DEBUG: WDE_ROOT=$WDE_ROOT" >&2
echo "run.sh DEBUG: ARG_COUNT=$#" >&2
echo "run.sh DEBUG: ARGS=$@" >&2

# Run the utility
# We pass through all arguments to the python module
cd "$WDE_ROOT"
exec "$VENV_PYTHON" -m infra.digest_tool_v6 "$@"
