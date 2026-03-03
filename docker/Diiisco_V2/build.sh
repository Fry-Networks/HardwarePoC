#!/usr/bin/env bash
###############################################################################
# build.sh — Build the Diiisco node Docker image with wallet credentials
#
# This script is meant to run in your CI/CD pipeline or on your build machine.
# It pulls the Algorand wallet credentials from 1Password and bakes them into
# the Docker image at build time. The resulting image contains NO .env files
# or exposed secrets — credentials live only in the compiled environment.ts.
#
# Prerequisites:
#   - 1Password CLI (op) installed and authenticated
#   - Docker installed
#   - Access to the 1Password vault containing the .enc item
#
# Usage:
#   ./build.sh                    # Uses 1Password CLI
#   ./build.sh --manual           # Prompts for credentials interactively
#
# Environment variable overrides (for CI systems):
#   ALGO_ADDRESS=...  ALGO_MNEMONIC=...  ./build.sh
###############################################################################

set -euo pipefail

IMAGE_NAME="diiisco-node"
IMAGE_TAG="latest"
API_KEY="${API_KEY:-sk-diiisco-prod-$(openssl rand -hex 8)}"

echo "╔══════════════════════════════════════════════════╗"
echo "║       Diiisco Node — Docker Image Builder        ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Resolve credentials ────────────────────────────────────────────────

if [[ -n "${ALGO_ADDRESS:-}" && -n "${ALGO_MNEMONIC:-}" ]]; then
    echo "✓ Using credentials from environment variables"

elif [[ "${1:-}" == "--manual" ]]; then
    echo "Manual mode — enter credentials:"
    read -rp "  Algorand address: " ALGO_ADDRESS
    read -rsp "  Algorand mnemonic (24 words): " ALGO_MNEMONIC
    echo ""

else
    echo "Fetching credentials from 1Password..."

    # ── Adjust these to match your 1Password item ──
    OP_VAULT="${OP_VAULT:-Private}"
    OP_ITEM="${OP_ITEM:-Diiisco Wallet}"

    ALGO_ADDRESS=$(op item get "$OP_ITEM" --vault "$OP_VAULT" --fields "address" 2>/dev/null) || {
        echo "✗ Failed to fetch address from 1Password."
        echo "  Make sure 'op' is installed and you're signed in: op signin"
        echo "  Or run: ./build.sh --manual"
        exit 1
    }
    ALGO_MNEMONIC=$(op item get "$OP_ITEM" --vault "$OP_VAULT" --fields "mnemonic" 2>/dev/null) || {
        echo "✗ Failed to fetch mnemonic from 1Password."
        exit 1
    }
    echo "✓ Credentials loaded from 1Password"
fi

# ── Validate ───────────────────────────────────────────────────────────

if [[ -z "$ALGO_ADDRESS" || -z "$ALGO_MNEMONIC" ]]; then
    echo "✗ Error: ALGO_ADDRESS and ALGO_MNEMONIC must not be empty."
    exit 1
fi

WORD_COUNT=$(echo "$ALGO_MNEMONIC" | wc -w)
if [[ "$WORD_COUNT" -ne 25 ]]; then
    echo "⚠ Warning: Mnemonic has $WORD_COUNT words (expected 25 for Algorand)."
    read -rp "  Continue anyway? [y/N]: " CONFIRM
    [[ "$CONFIRM" =~ ^[Yy]$ ]] || exit 1
fi

echo "  Address: ${ALGO_ADDRESS:0:8}...${ALGO_ADDRESS: -6}"
echo "  Mnemonic: ${ALGO_MNEMONIC%% *} ... [redacted]"
echo ""

# ── Build ──────────────────────────────────────────────────────────────

echo "Building Docker image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""

docker build \
    --build-arg ALGO_ADDRESS="$ALGO_ADDRESS" \
    --build-arg ALGO_MNEMONIC="$ALGO_MNEMONIC" \
    --build-arg API_KEY="$API_KEY" \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    ./diiisco-node

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  ✓ Build complete: ${IMAGE_NAME}:${IMAGE_TAG}              ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Next steps:                                     ║"
echo "║  1. Push to your registry:                       ║"
echo "║     docker tag ${IMAGE_NAME}:${IMAGE_TAG} registry/path:${IMAGE_TAG} ║"
echo "║     docker push registry/path:${IMAGE_TAG}              ║"
echo "║  2. Deploy to customers:                         ║"
echo "║     docker compose up -d                         ║"
echo "╚══════════════════════════════════════════════════╝"
