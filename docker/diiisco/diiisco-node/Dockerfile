###############################################################################
# Diiisco Node Dockerfile
#
# Multi-stage build:
#   Stage 1 - Clone repo, inject wallet config, compile TypeScript
#   Stage 2 - Slim runtime image with only compiled JS + deps
#
# Build args (injected at build time from 1Password / CI):
#   ALGO_ADDRESS   - Your Algorand wallet address
#   ALGO_MNEMONIC  - Your 24-word Algorand mnemonic
#   API_KEY        - Bearer token for the Diiisco API endpoint
#
# Usage:
#   docker build \
#     --build-arg ALGO_ADDRESS="YOUR_ADDR" \
#     --build-arg ALGO_MNEMONIC="YOUR_MNEMONIC" \
#     --build-arg API_KEY="sk-your-production-key" \
#     -t diiisco-node:latest .
###############################################################################

# ── Stage 1: Build ──────────────────────────────────────────────────────
FROM node:22-slim AS builder

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Clone the official Diiisco node repo
RUN git clone --depth 1 https://github.com/Diiisco-Inc/diiisco-node.git .

# Install dependencies
RUN npm install

# Build args — these are injected at image build time (CI/CD pipeline)
ARG ALGO_ADDRESS
ARG ALGO_MNEMONIC
ARG API_KEY="sk-diiisco-prod-key"

# Generate the environment.ts config with baked-in credentials.
# Must match the repo's expected format: default export + configureEnvironment.
RUN cat > src/environment/environment.ts <<'ENVEOF'
import { Environment } from "./environment.types";
import { selectHighestStakeQuote } from "../utils/quoteSelectionMethods";
import { createQuoteFromInputTokens } from "../utils/quoteCreationMethods";
import { deepMerge } from "../utils/deepMerge";

const environment: Environment = {
  peerIdStorage: {
    path: "/app/data/"
  },
  models: {
    enabled: true,
    baseURL: "http://diiisco-ollama",
    port: 11434,
    apiKey: "",
    chargePer1MTokens: {
      default: 0.000001
    }
  },
  algorand: {
ENVEOF

# Inject secrets separately to avoid shell escaping issues with mnemonics
RUN echo "    addr: \"${ALGO_ADDRESS}\"," >> src/environment/environment.ts && \
    echo "    mnemonic: \"${ALGO_MNEMONIC}\"," >> src/environment/environment.ts && \
    cat >> src/environment/environment.ts <<'ENVEOF'
    network: "mainnet",
    client: {
      address: "https://mainnet-api.algonode.cloud/",
      port: 443,
      token: ""
    },
  },
  api: {
    enabled: true,
    bearerAuthentication: true,
    keys: [
ENVEOF

RUN echo "      \"${API_KEY}\"" >> src/environment/environment.ts && \
    cat >> src/environment/environment.ts <<'ENVEOF'
    ],
    port: 8181
  },
  quoteEngine: {
    waitTime: 1000,
    quoteSelectionFunction: selectHighestStakeQuote,
    quoteCreationFunction: [createQuoteFromInputTokens]
  },
};

/**
 * Override environment settings. Call BEFORE creating Application instance.
 */
export function configureEnvironment(overrides: Partial<Environment>): void {
  Object.assign(environment, deepMerge(environment, overrides));
}

export default environment;
ENVEOF

# Compile TypeScript
RUN npm run build

# ── Stage 2: Runtime ────────────────────────────────────────────────────
FROM node:22-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy compiled output and dependencies
COPY --from=builder /build/dist ./dist
COPY --from=builder /build/node_modules ./node_modules
COPY --from=builder /build/package.json ./package.json

RUN mkdir -p /app/data

EXPOSE 8181

CMD ["node", "dist/index.js"]
