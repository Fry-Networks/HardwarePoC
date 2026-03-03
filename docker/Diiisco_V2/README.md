# Diiisco Node — Production Deployment

Dockerized deployment of the [official Diiisco node](https://github.com/Diiisco-Inc/diiisco-node) for automated rollout to customer machines.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Customer Machine (Windows / Linux)             │
│                                                 │
│  ┌───────────┐    ┌──────────────────────────┐  │
│  │  Ollama   │◄───│  Diiisco Node            │  │
│  │  :11434   │    │  :8181 (API)             │  │
│  │  (phi2)   │    │  - Algorand payments     │  │
│  └───────────┘    │  - OpenAI-compat API     │  │
│                   │  - Quote engine           │  │
│                   └──────────────────────────┘  │
│                                                 │
│  Diiisco Network ◄──── incoming prompts ────►   │
│  Algorand Mainnet ◄── payments to YOUR wallet   │
└─────────────────────────────────────────────────┘
```

## Quick Start

### 1. Build the image (on your build machine, not customer machines)

**Linux / macOS / CI:**
```bash
chmod +x build.sh
./build.sh              # Fetches wallet from 1Password
./build.sh --manual     # Or enter credentials manually
```

**Windows:**
```powershell
.\build.ps1             # Fetches wallet from 1Password
.\build.ps1 -Manual     # Or enter credentials manually
```

The build scripts pull your Algorand wallet credentials from 1Password and bake them into the Docker image. No `.env` files or secrets are shipped to customers.

### 2. Push to your private registry

```bash
docker tag diiisco-node:latest your-registry.io/diiisco-node:latest
docker push your-registry.io/diiisco-node:latest
```

Then update `docker-compose.yml` on the customer side to use the pre-built image instead of `build:`:

```yaml
  diiisco:
    image: your-registry.io/diiisco-node:latest   # ← replace the build: block
```

### 3. Deploy to customer machines

Copy `docker-compose.yml` to the customer machine and run:

```bash
docker compose up -d
```

That's it. Ollama starts, phi2 gets pulled automatically on first run, and the Diiisco node connects to the network.

## How It Works

| Container | Purpose | Port |
|---|---|---|
| `diiisco-ollama` | Runs LLM inference (phi2 model) | 11434 |
| `diiisco-ollama-init` | One-shot: pulls phi2 model on first run, then exits | — |
| `diiisco-node` | Real Diiisco node — accepts prompts, processes via Ollama, handles Algorand payments | 8181 |

**Earning flow:**
1. Your node registers on the Diiisco network
2. Users on the network send prompts
3. Your node processes them via Ollama (phi2)
4. Payment is sent to your Algorand wallet automatically

## Security Notes

- **Wallet credentials are baked into the Docker image at build time** — never shipped as `.env` files
- **Build args are not in the final image layers** thanks to multi-stage build — but the `environment.ts` config IS in the runtime image. Use a private registry.
- **Mnemonic on customer machines**: Since credentials are in the image, anyone with `docker exec` access could theoretically extract them. This is acceptable because it's YOUR wallet on THEIR machine — they can't steal funds, only contribute compute.
- **API key**: The Diiisco API endpoint on port 8181 is protected by bearer authentication. Change the default key in production.

## Configuration

### Change the LLM model

Edit `docker-compose.yml` and the Dockerfile `environment.ts` section:

| Model | Size | RAM Needed | Best For |
|---|---|---|---|
| `phi2` | ~1.7B | 2-4 GB | Laptops, low-end machines (default) |
| `llama3:8b` | ~8B | 8+ GB | Desktops |
| `mistral` | ~7B | 8+ GB | Good balance |
| `llama3:70b` | ~70B | 40+ GB | GPU machines only |

### 1Password configuration

Set these environment variables to customize 1Password lookup:

```bash
export OP_VAULT="YourVault"       # Default: "Private"
export OP_ITEM="YourItemName"     # Default: "Diiisco Wallet"
```

The 1Password item should have fields named `address` and `mnemonic`.

## Troubleshooting

**Node not earning?**
```bash
# Check if the node is running
docker logs diiisco-node

# Check if Ollama has the model
docker exec diiisco-ollama ollama list

# Check if the API is responding
curl http://localhost:8181/v1/models
```

**Ollama model not pulling?**
```bash
# Re-run the init container
docker compose run --rm ollama-init
```

**Port conflicts?**
Change the left side of the port mapping in `docker-compose.yml`:
```yaml
ports:
  - "9181:8181"   # Use 9181 externally instead of 8181
```
