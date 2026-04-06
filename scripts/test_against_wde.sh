#!/usr/bin/env bash
# End-to-End Test against real /opt/wde data
set -e

# Setup environment to point to the real digest if it exists
export WDE_ROOT="/opt/wde"

echo "=== Running E2E Test against $WDE_ROOT ==="

# Check if real digest exists
if [ ! -d "$WDE_ROOT/docs/repo_summary/latest" ]; then
    echo "Warning: No digest found at $WDE_ROOT/docs/repo_summary/latest"
    echo "Cannot run real e2e tests. Skipping."
    exit 0
fi

# Run some basic commands to ensure it doesn't crash on real data
echo "Testing 'find' command..."
project-map find --query "user" || { echo "Failed find command"; exit 1; }

echo ""
echo "Testing 'impact' command (we'll just pick a common hit from the output)..."
# We'll just run an impact on a random fake symbol to see if it handles missing gracefully
project-map impact --fqn "com.wde.NotReal" || { echo "Failed impact command"; exit 1; }

echo ""
echo "E2E Test Completed Successfully."
