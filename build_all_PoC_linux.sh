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

# Default 1Password reference for Autonomys API key (CI can override by setting OPREF_AUTONOMYS_KEY)
OPREF_AUTONOMYS_KEY=${OPREF_AUTONOMYS_KEY:-"op://VPS/Hardware_API/AUTONOMYS_API_KEY"}

# If `op` is available, try to resolve the Autonomys API key and export it for child build scripts
if command -v op >/dev/null 2>&1; then
  if [ -n "$OPREF_AUTONOMYS_KEY" ]; then
    set +e
    AUTONOMYS_API_KEY=$(op read "$OPREF_AUTONOMYS_KEY" 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$AUTONOMYS_API_KEY" ]; then
      export AUTONOMYS_API_KEY
      echo "[OK] Retrieved Autonomys API key from 1Password"
    else
      echo "[WARN] Could not read Autonomys API key from 1Password reference: $OPREF_AUTONOMYS_KEY"
      unset AUTONOMYS_API_KEY
    fi
    set -e
  fi
fi

# Ensure child builds see the enable flag (embed at build time)
export AUTONOMYS_UPLOAD_ENABLED=1

if [ ! -x "$BUILD_SCRIPT" ]; then
  echo "Build script not found or not executable: $BUILD_SCRIPT" >&2
  exit 1
fi

echo "Building version $VERSION for: ${MINER_CODES[*]}"

for CODE in "${MINER_CODES[@]}"; do
  echo "\n=============================="
  echo "Building $CODE v$VERSION"
  echo "=============================="
  # Pass through environment variables so the child build embeds them
  AUTONOMYS_UPLOAD_ENABLED=${AUTONOMYS_UPLOAD_ENABLED} AUTONOMYS_API_KEY=${AUTONOMYS_API_KEY:-} "$BUILD_SCRIPT" "$CODE" "$VERSION"
done

echo "\nAll builds completed."
