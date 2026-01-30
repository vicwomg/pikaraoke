#!/bin/bash

# PiKaraoke Headless Mode Verification Script (Unix)
# This script starts PiKaraoke in headless mode, waits for initialization,
# and verifies that key web endpoints are serving content.

set -e

echo "Installing PiKaraoke for CI..."
./build_scripts/install/install.sh -y --local

echo "Starting PiKaraoke in headless mode..."
pikaraoke --headless > output.log 2>&1 &
PID=$!

# Function to cleanup on exit
cleanup() {
    echo "Cleaning up..."
    kill $PID || true
}
trap cleanup EXIT

echo "Waiting for PiKaraoke to initialize (max 30s)..."
INITIALIZED=false
for i in {1..30}; do
    if grep -q "Connect the player host to:" output.log; then
        echo "Found expected initialization output."
        INITIALIZED=true
        break
    fi
    sleep 1
done

if [ "$INITIALIZED" = false ]; then
    echo "Error: Timed out waiting for PiKaraoke to initialize."
    cat output.log
    exit 1
fi

echo "Verifying web endpoints..."
ENDPOINTS=(
    "/"
    "/splash"
    "/queue"
    "/search"
    "/browse"
    "/info"
)

FAILED=false
for path in "${ENDPOINTS[@]}"; do
    echo "Checking http://localhost:5555$path ..."
    if ! curl -s http://localhost:5555"$path" | grep -q "DOCTYPE"; then
        echo "Error: Failed to verify $path"
        FAILED=true
    fi
done

if [ "$FAILED" = true ]; then
    echo "One or more endpoint verifications failed."
    cat output.log
    exit 1
fi

echo "Headless mode verification successful!"
exit 0
