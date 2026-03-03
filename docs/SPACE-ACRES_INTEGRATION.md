# Space Acres (Autonomys Network) - LVS Integration

## Overview

This module integrates **Autonomys Network farming** (Space Acres) into the SDNas an optional mining tool. It runs via Docker Compose and is managed through the SDN GUI with activation handled by the elevated ops service.

**Cross-platform**: Works on both **Windows** (Docker Desktop / WSL2) and **Linux** (native Docker Engine). The manager is pure Python with no bash dependency.

### Architecture

```
┌─────────────────────────────────────────────────┐
│  SDN GUI                                        │
│  ┌───────────────────────────────────────────┐  │
│  │ Tools Panel → Space Acres Toggle [ON/OFF] │  │
│  └──────────────────┬────────────────────────┘  │
│                     │ ops request               │
│  ┌──────────────────▼────────────────────────┐  │
│  │ Elevated Ops Service                      │  │
│  │  SpaceAcresManager (Python)               │  │
│  │  1. preflight() → check Docker            │  │
│  │  2. write_env() → SSD path/size from GUI  │  │
│  │  3. start()     → validate + compose up   │  │
│  │  4. status()    → JSON → GUI panel        │  │
│  └──────────────────┬────────────────────────┘  │
└─────────────────────┼───────────────────────────┘
                      │
    ┌─────────────────▼──────────────────┐
    │  Docker Compose Stack              │
    │  ┌──────────────────────────────┐  │
    │  │ Sdn-autonomys-node           │  │
    │  │ (consensus + blockchain)     │  │
    │  │ Port 30333 (P2P)             │  │
    │  │ Port 9944  (RPC, internal)   │  │
    │  └──────────┬───────────────────┘  │
    │             │ depends_on (healthy) │
    │  ┌──────────▼───────────────────┐  │
    │  │ Sdn-autonomys-farmer         │  │
    │  │ (plotting + farming)         │  │
    │  │ Port 30433 (DSN)             │  │
    │  └──────────────────────────────┘  │
    └────────────────────────────────────┘
```

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Node + Farmer stack definition |
| `.env.example` | Template — `REWARD_ADDRESS` uses 1Password `op://` reference |
| `space_acres_manager.py` | Cross-platform Python manager (module + CLI) |

## Secrets & Configuration Flow

### Reward Address (1Password)
The `REWARD_ADDRESS` is stored as a 1Password secret reference (`op://SDN/SpaceAcres/wallet`) and is the **same across all SDN nodes**. It is embedded during the build process — end users never see or set the raw wallet address.

### Farm Path & Size (GUI → Ops Request)
The GUI detects available SSDs, lets the user select one, and sends the path and desired size via an ops request to the elevated service. The manager validates the disk is actually an SSD before proceeding.

### Node Name (Miner Key)
`NODE_NAME` is automatically populated from the miner key — no user input required.

## Integration Into Your Ops Service

### As a Python Module

```python
from space_acres_manager import SpaceAcresManager

manager = SpaceAcresManager("/path/to/space-acres-integration")

# -- GUI: Check if Docker is available (controls toggle visibility) ----------
async def is_space_acres_available() -> bool:
    result = await manager.preflight()
    return result.passed

# -- GUI: User selects SSD and clicks Enable ---------------------------------
async def handle_space_acres_enable(ssd_path: str, ssd_size: str, miner_key: str):
    # write_env() carries forward the 1Password REWARD_ADDRESS from .env.example
    # and merges in the ops-request values from the GUI
    manager.write_env({
        "FARM_PATH": ssd_path,        # from GUI SSD selection
        "FARM_SIZE": ssd_size,         # from GUI capacity slider
        "miner_key": miner_key,        # resolves {miner-key} in NODE_NAME
    })

    # Start the stack (runs preflight + validate + pull + up)
    result = await manager.start()
    if not result.success:
        raise RuntimeError(result.message)
    return result

# -- GUI: User clicks Disable -----------------------------------------------
async def handle_space_acres_disable():
    result = await manager.stop()
    return result

# -- GUI: Poll for dashboard status ------------------------------------------
async def get_space_acres_status() -> dict:
    return await manager.status()
    # Returns:
    # {
    #   "tool": "space-acres",
    #   "enabled": true,
    #   "platform": "Windows",  # or "Linux"
    #   "node": {
    #     "container": "sdn-autonomys-node",
    #     "status": "running",
    #     "health": "healthy",
    #     "started_at": "2025-02-24T10:30:00Z",
    #     "image": "ghcr.io/autonomys/node:mainnet-2025-aug-20"
    #   },
    #   "farmer": { ... }
    # }
```

### As a CLI (for testing or manual ops)

```bash
# All commands output JSON for easy parsing

python space_acres_manager.py preflight
python space_acres_manager.py validate
python space_acres_manager.py start
python space_acres_manager.py status
python space_acres_manager.py logs --lines 500
python space_acres_manager.py stop
python space_acres_manager.py update
python space_acres_manager.py destroy --force
```

## Platform-Specific Notes

### Windows
- Requires **Docker Desktop** (which includes Docker Compose V2) --> already done through "docker" boolean in the weekly json file.
- Uses `CREATE_NO_WINDOW` flag to prevent console flashing
- Docker Desktop service: `com.docker.service`
- Farm paths use Windows format: `C:\ProgramData\FryNetworks\miner-SDN\farm`

### Linux
- Uses native Docker Engine + Compose V2 plugin
- Docker daemon service: `docker.service`
- Farm paths: `/opt/FryNetworks/miner-SDN/farm`
- May need user in `docker` group or `sudo` for non-root

## Configuration

### Build-Time Settings (baked into `.env.example`)

| Variable | Source | Description |
|---|---|---|
| `REWARD_ADDRESS` | 1Password `op://SDN/SpaceAcres/wallet` | Same for all SDN nodes, resolved at build |
| `AUTONOMYS_VERSION` | Pinned in `.env.example` | Docker image tag |

### Runtime Settings (populated by GUI → ops request → `write_env()`)

| Variable | Source | Description |
|---|---|---|
| `FARM_PATH` | GUI SSD selection | Host directory for farm data (**SSD required**) |
| `FARM_SIZE` | GUI capacity slider | Disk space to allocate (e.g. `100G`, `1T`) |
| `NODE_NAME` | Auto from miner key | Telemetry display name |

### Optional Settings

| Variable | Default | Description |
|---|---|---|
| `TIMEZONE` | `UTC` | Container timezone |
| `NODE_P2P_PORT` | `30333` | Node P2P port |
| `FARMER_DSN_PORT` | `30433` | Farmer DSN port |

## Network Requirements

**Forward these ports on the router** for optimal performance:
- **30333/TCP** — Node peer-to-peer communication
- **30433/TCP** — Farmer DSN (Distributed Storage Network)

## Resource Expectations

| Phase | CPU | RAM | Duration |
|---|---|---|---|
| Node sync | Moderate | ~4 GiB | Hours to ~1 day |
| Plotting | High (uses GPU if available) | ~4-8 GiB | Hours to days |
| Steady-state farming | Low | ~2 GiB | Ongoing |

Disk: Node needs ~100 GiB SSD + the farm size you configure. **HDDs are not supported.**

## Validation Checks

The manager performs these checks before starting:

| Check | Type | Details |
|---|---|---|
| Docker installed | Preflight | Finds binary on PATH or common install locations |
| Docker daemon running | Preflight | `docker info` succeeds |
| Compose V2 available | Preflight | `docker compose version` succeeds |
| Ports 30333/30433 free | Preflight | Socket bind test |
| `REWARD_ADDRESS` format | Validate | Accepts `su*`, `st*`, `5*`, or `op://` references |
| `FARM_PATH` writable | Validate | `os.access` check |
| `FARM_PATH` is SSD | Validate | Linux: `/sys/block/*/queue/rotational`, Windows: `Get-PhysicalDisk` |
| `FARM_PATH` free space | Validate | Warns if < 50 GiB available |
| `FARM_SIZE` set | Validate | Must be non-empty |

## GUI Test Cases to Add

For your existing test framework:

| Test ID | Category | Validation |
|---|---|---|
| `SA-001` | Tool Toggle | Toggle visible when `preflight()` passes |
| `SA-002` | Tool Toggle | Toggle disabled with tooltip when Docker unavailable |
| `SA-003` | Status Panel | Node status shows syncing → running transition |
| `SA-004` | Status Panel | Farmer status shows waiting → plotting → farming |
| `SA-005` | Config Panel | SSD selection only shows SSD drives, not HDDs |
| `SA-006` | Config Panel | Farm size slider respects available SSD capacity |
| `SA-007` | Config Panel | Farm path validates writable directory |
| `SA-008` | Rewards History | Space Acres earnings appear in rewards history |
| `SA-009` | Platform | `status()` returns correct platform field |
| `SA-010` | Lifecycle | start → status → stop → status round-trip |
| `SA-011` | Secrets | `REWARD_ADDRESS` `op://` reference preserved through write_env |
| `SA-012` | Naming | `NODE_NAME` resolves `{miner-key}` from miner key |
