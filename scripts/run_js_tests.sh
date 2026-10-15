#!/usr/bin/env bash
# Run the JS unit suite (tests/js/) under the machine's node.
#
# The toolchain is pinned in package.json (vitest 3, the node-18 major) and
# package-lock.json, so every machine gets the same toolchain. The runtime is
# the node on the machine's PATH - node 18, the most widely deployed
# runtime, is enough - and this script installs no runtime of its own.
#
# The script is called from the pre-commit hook and from CI; it is also the
# direct way to run the JS suite.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v npm >/dev/null 2>&1; then
    echo "js-tests: npm is required to install the pinned toolchain; install node and npm, or run 'npm ci' first" >&2
    exit 1
fi

# Install the locked tree when it is missing; reuse node_modules otherwise.
if [ ! -x node_modules/.bin/vitest ]; then
    npm ci --no-audit --no-fund
fi

exec npm run test
