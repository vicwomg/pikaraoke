#!/usr/bin/env bash
# Run the JS unit suite (tests/js/) under the machine's node.
#
# The toolchain is pinned in package.json and package-lock.json. The runtime
# is the node on the machine's PATH; vitest 5 needs node 22 or newer.
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
