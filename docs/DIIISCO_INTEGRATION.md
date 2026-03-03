# DIIISCO - GUI Integration Guide (RDN)

## Overview

DIIISCO is a **decentralized AI inference** tool for RDN miners. It runs as a Docker Compose stack (**Ollama** for the local LLM + **DIIISCO Node** connecting to the Algorand network). The DIIISCO node is built from a multi-stage Dockerfile that clones the [official repo](https://github.com/Diiisco-Inc/diiisco-node), injects Algorand wallet credentials at build time, and compiles TypeScript.

The GUI controls DIIISCO entirely through the **IPC ops_queue**. The GUI must **never** run Docker commands directly.

---

## CRITICAL: How Docker Startup Works

The DIIISCO containers are started by the **service**, not the GUI. The service:

1. Decrypts Algorand credentials (`ALGO_ADDRESS`, `ALGO_MNEMONIC`) from `diiisco_creds.enc`
2. Injects them as environment variables which Docker Compose forwards as **build args**
3. Runs `docker compose -f docker/diiisco/docker-compose.yml build` (bakes credentials into the image)
4. Runs `docker compose up -d` (starts Ollama, pulls phi model, then starts DIIISCO node)

**The GUI must NOT:**
- Run `docker run` or `docker compose` directly
- Handle or store Algorand credentials

**The GUI MUST:**
- Send `start_docker_container` / `stop_docker_container` ops via the IPC ops_queue
- Let the service handle all Docker operations

---

## gui_config.enc Schema (RDN)

The service populates `gui_config.enc` with the following RDN-specific fields:

```json
{
  "base_reward": 1.0,
  "per_tool_reward": 0.1,
  "presearch": false,
  "diiisco": false
}
```

| Field | Type | Description |
|---|---|---|
| `presearch` | `bool` | Whether Presearch is approved/enabled (toggle state) |
| `diiisco` | `bool` | Whether DIIISCO is approved/enabled (toggle state) |

---

## UI Components

### DIIISCO Toggle

Standard enable/disable toggle.

**Visibility rule**: Show only when Docker is available. The service gates this via `_is_docker_available()` (60-second cache). If Docker is unavailable, show the toggle greyed out with a tooltip:
> "Docker is required. Install Docker Desktop and ensure the daemon is running."

---

## IPC Operations

All operations use the **ops_queue** file-based IPC. See [README_GUI_Integration.md](README_GUI_Integration.md) for the general IPC mechanism.

### Step 1: Write Config (optional operational settings)

```json
{
  "op": "write_config",
  "relative_path": "diiisco_config.json",
  "content": "{\"enabled\": true}"
}
```

### Step 2: Enable DIIISCO (toggle ON)

Write `miner_config.json` with `diiisco.enabled: true`, then reload:

```json
{
  "op": "write_config",
  "relative_path": "miner_config.json",
  "content": "{\"diiisco\": {\"enabled\": true}}"
}
```

Then:
```json
{
  "op": "reload_config"
}
```

### Step 3: Setup Firewall (once, on first enable)

```json
{
  "op": "setup_diiisco_firewall"
}
```

Opens ports:
- **11434/TCP** -- Ollama API
- **8181/TCP** -- DIIISCO Node API

### Step 4: Start Docker Containers

```json
{
  "op": "start_docker_container",
  "container_name": "diiisco-node"
}
```

This starts the **entire compose stack** (Ollama, ollama-init model bootstrapper, and diiisco-node). The service:
1. Decrypts Algorand credentials from `diiisco_creds.enc`
2. Pulls images (`docker compose pull`) -- Ollama + Node.js base
3. Builds the diiisco-node image (`docker compose build`) -- clones repo, bakes credentials, compiles TS
4. Starts containers (`docker compose up -d`) -- Ollama starts, phi model pulled, then DIIISCO node

**Progress feedback:**

During startup the service writes progress updates to:
```
ops_processed/{request_id}.progress.json
```

The GUI should poll this file alongside `{request_id}.done.json` every 500ms.
When `.progress.json` exists, show the phase/detail to the user and extend the
poll timeout to **900 seconds** (first-time image downloads can take minutes).

```json
{
  "request_id": "c4e3a07943dc4e6c9805d5e7ede0c2ac",
  "phase": "pulling",
  "detail": "Downloading container images...",
  "updated_at": "2026-02-25T15:21:07+00:00"
}
```

| Phase      | Detail                              | Typical duration     |
|------------|-------------------------------------|----------------------|
| `pulling`  | Downloading container images...     | 1-10 min (cold) / instant (cached) |
| `building` | Building application image...       | 1-5 min (cold) / instant (cached) |
| `starting` | Starting containers...              | 5-30s                |

### Step 5: Stop (toggle OFF)

```json
{
  "op": "stop_docker_container",
  "container_name": "diiisco-node"
}
```

This stops all containers (`docker compose down`).

Then update the config:
```json
{
  "op": "write_config",
  "relative_path": "miner_config.json",
  "content": "{\"diiisco\": {\"enabled\": false}}"
}
```

Then:
```json
{
  "op": "reload_config"
}
```

---

## Complete Flow

```
User opens RDN GUI
    |
    +-- GUI reads gui_config.enc
    |   +-- diiisco -> toggle state
    |   +-- presearch -> toggle state (separate tool)
    |
    +-- User flips DIIISCO toggle ON
    |   +-- GUI sends: write_config (diiisco.enabled = true)
    |   +-- GUI sends: reload_config
    |   +-- GUI sends: setup_diiisco_firewall  (first time only)
    |   +-- GUI sends: start_docker_container("diiisco-node")
    |       |
    |       +-- Service handles:
    |           +-- Decrypt diiisco_creds.enc -> ALGO_ADDRESS, ALGO_MNEMONIC
    |           +-- docker compose build (bakes credentials into image)
    |           +-- docker compose up -d
    |           +-- Ollama starts -> phi model pulled by init container
    |           +-- DIIISCO node starts after Ollama healthcheck passes
    |
    +-- User flips DIIISCO toggle OFF
        +-- GUI sends: stop_docker_container("diiisco-node")
        +-- GUI sends: write_config (diiisco.enabled = false)
        +-- GUI sends: reload_config
```

---

## Status Display

The service polls DIIISCO status every measurement cycle. Status is available in `cache/latest.json`:

```json
{
  "selected_tools": ["diiisco"],
  "diiisco_stats": {
    "status": "running",
    "enabled": true,
    "running": true,
    "connected": true,
    "ollama_healthy": true,
    "model": "phi:latest",
    "cpu_percent": 12.5,
    "mem_usage_mb": 256.3,
    "error": null
  }
}
```

**Status values:**

| `status` | Meaning | Suggested UI |
|---|---|---|
| `running` | DIIISCO node reachable on port 8181 | Green indicator |
| `not_found` | Port 8181 not reachable | Grey indicator |
| `unknown` | Polling error | Orange indicator |

**Additional fields for detail panel:**

| Field | Type | Description |
|---|---|---|
| `ollama_healthy` | `bool` | Ollama responded on port 11434 |
| `connected` | `bool` | DIIISCO node port 8181 reachable |
| `model` | `string\|null` | LLM model name from Ollama (e.g. "phi:latest") |
| `cpu_percent` | `float\|null` | Container CPU usage % (from `docker stats`) |
| `mem_usage_mb` | `float\|null` | Container memory usage in MB (from `docker stats`) |
| `error` | `string\|null` | Error message if something went wrong |

---

## Docker Architecture

### Compose Stack

The stack is defined in `docker/diiisco/docker-compose.yml`:

| Container | Purpose | Port |
|---|---|---|
| `diiisco-ollama` | Runs LLM inference (phi model) | 11434 |
| `diiisco-ollama-init` | One-shot: pulls phi model on first run, then exits | -- |
| `diiisco-node` | Real Diiisco node -- accepts prompts, processes via Ollama, handles Algorand payments | 8181 |

Key points:
- `ollama/ollama:latest` is pulled from Docker Hub
- `diiisco-node` is built locally from `docker/diiisco/diiisco-node/Dockerfile` (clones official repo at build time)
- Algorand credentials are baked into the image as build args (forwarded from env vars by the service)
- Ollama healthcheck ensures DIIISCO starts only after the LLM is ready
- CPU-only mode (`OLLAMA_CPU_ONLY: "1"`)
- phi model is pulled by the ollama-init bootstrapper on first run

### Monitoring

The service monitors DIIISCO via:
1. **Port 8181 reachability** -- confirms the DIIISCO node is running
2. **Ollama `/api/tags`** -- confirms Ollama is healthy and extracts the active model
3. **`docker stats diiisco-node`** -- CPU and memory usage of the container

---

## Rewards Impact

DIIISCO contributes to the RDN parametric reward multiplier:

```
reward = base_reward + per_tool_reward * tool_count
```

- **Without DIIISCO**: `tool_count` excludes diiisco
- **With DIIISCO running**: `tool_count` includes diiisco (alongside presearch if active)

---

## Secret Management

Algorand credentials are **never** handled by the GUI:

1. **Build time**: `create_diiisco_creds.py` encrypts `ALGO_ADDRESS` + `ALGO_MNEMONIC` into `diiisco_creds.enc`
2. **Deployment**: `diiisco_creds.enc` is bundled into the service executable (or placed in config dir)
3. **Runtime**: Service decrypts credentials in memory and injects them as env vars for `docker compose build`
4. **Docker build**: Credentials are baked into the diiisco-node image via build args (written to `environment.ts`)
5. **No disk leaks**: No `.env` file, no temp files, no secrets in config JSON

---

## Important Notes

1. **Docker is required** -- `_is_docker_available()` gates the toggle
2. **First start is slow** -- Ollama must download (~9 GB), phi model (~1.7 GB), and diiisco-node image builds from source
3. **Container data is persistent** -- stopping DIIISCO does NOT delete the Ollama model or DIIISCO data
4. **The GUI must never run Docker commands** -- all container lifecycle is managed by the service via the ops_queue
5. **Credentials are embedded at build time** -- from 1Password. The GUI never sees or sets Algorand keys

---

## Network Ports

| Port | Protocol | Purpose |
|---|---|---|
| 11434 | TCP | Ollama API |
| 8181 | TCP | DIIISCO Node API |

---

## File Paths

```
C:\ProgramData\FryNetworks\miner-RDN\
+-- config/
|   +-- miner_config.json       <- diiisco.enabled toggle
|   +-- diiisco_config.json     <- operational settings (optional)
|   +-- gui_config.enc          <- diiisco toggle state + rewards params (encrypted)
+-- ops_queue/                  <- Send IPC requests here
+-- ops_processed/              <- Read IPC responses here
+-- cache/
|   +-- latest.json             <- Current status including diiisco_stats
+-- logs/
    +-- service.err.log         <- Debug
```

Linux equivalent: `/opt/FryNetworks/miner-RDN/`
