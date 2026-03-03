#!/bin/bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <version> [MINER_CODE ...]" >&2
  exit 1
fi

VERSION=$1
shift

if [[ ! $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must be in format X.Y.Z" >&2
  exit 1
fi

declare -a MINER_CODES
if [ $# -gt 0 ]; then
  MINER_CODES=("$@")
else
  MINER_CODES=(BM IDM ODM ISM OSM RDN SDN SVN AEM IRM)
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUILD_SCRIPT="${SCRIPT_DIR}/build_PoC_linux.sh"

# 1Password CLI is required — each build resolves its own secrets
if ! command -v op >/dev/null 2>&1; then
  echo "Error: 1Password CLI (op) not found. Install from: https://1password.com/downloads/command-line/" >&2
  exit 1
fi
echo "[OK] 1Password CLI detected"

if [ ! -x "$BUILD_SCRIPT" ]; then
  echo "Build script not found or not executable: $BUILD_SCRIPT" >&2
  exit 1
fi

echo "Building version $VERSION for: ${MINER_CODES[*]}"

for CODE in "${MINER_CODES[@]}"; do
  echo "\n=============================="
  echo "Building $CODE v$VERSION"
  echo "=============================="
  "$BUILD_SCRIPT" "$CODE" "$VERSION"
done

echo "\nAll builds completed."
