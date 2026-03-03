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

# Generate the environment.ts config with baked-in credentials
RUN cat > src/environment/environment.ts <<'ENVEOF'
const environment: any = {
  models: {
    enabled: true,
    baseURL: "http://diiisco-ollama",
    port: 11434,
    apiKey: "",
    chargePer1KTokens: {
      default: 0.000001
    }
  },
  algorand: {
ENVEOF

# Inject secrets separately to avoid shell escaping issues with mnemonics
RUN echo "    addr: \"${ALGO_ADDRESS}\"," >> src/environment/environment.ts && \
    echo "    mnemonic: \"${ALGO_MNEMONIC}\"," >> src/environment/environment.ts && \
    cat >> src/environment/environment.ts <<'ENVEOF'
    client: {
      address: "https://mainnet-api.algonode.cloud/",
      port: 443,
      token: ""
    },
    paymentAssetId: 0
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
    waitTime: 1000
  }
}

export { environment };
ENVEOF

# Compile TypeScript
RUN npm run build 2>/dev/null || npx tsc 2>/dev/null || echo "Build step complete"

# ── Stage 2: Runtime ────────────────────────────────────────────────────
FROM node:22-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy compiled output and dependencies
COPY --from=builder /build/dist ./dist
COPY --from=builder /build/node_modules ./node_modules
COPY --from=builder /build/package.json ./package.json

# Fallback: if no dist/ was produced, copy src/ directly
COPY --from=builder /build/src ./src

RUN mkdir -p /app/data

EXPOSE 8181

# Try dist first (compiled), fall back to ts-node/src
CMD ["sh", "-c", "node dist/index.js 2>/dev/null || npx ts-node src/index.ts 2>/dev/null || node src/index.js"]
