# Space Acres - GUI Integration Guide (SDN)

## Overview

Space Acres is the **Autonomys Network farming** tool for SDN miners. It runs native `subspace-node` and `subspace-farmer` binaries managed by the service as child processes. The user selects an **SSD drive** and **farm size** through the GUI before starting.

Unlike other tools that only need an enable/disable toggle, Space Acres has a two-step setup: **configure** (pick SSD + size), then **enable** (start processes).

---

## gui_config.enc Schema (SDN)

The service populates `gui_config.enc` with the following SDN-specific fields:

```json
{
  "base_reward": 1.0,
  "per_tool_reward": 0.1,
  "spaceacres": false,
  "available_ssds": [
    { "path": "D:\\", "free_gb": 450.2, "total_gb": 500.0 },
    { "path": "E:\\", "free_gb": 900.7, "total_gb": 1000.0 }
  ],
  "spaceacres_config": {
    "farm_path": "D:\\autonomys-farm",
    "farm_size": "400G"
  }
}
```

| Field | Type | Description |
|---|---|---|
| `spaceacres` | `bool` | Whether Space Acres is approved/enabled (toggle state) |
| `available_ssds` | `list` | SSDs detected by the service at startup. Read-only for GUI |
| `available_ssds[].path` | `string` | Drive root path (Windows: `D:\`, Linux: `/mnt/ssd`) |
| `available_ssds[].free_gb` | `float` | Free space in GiB |
| `available_ssds[].total_gb` | `float` | Total capacity in GiB |
| `spaceacres_config` | `dict` | Farm settings sent by the GUI. Empty `{}` until configured |
| `spaceacres_config.farm_path` | `string` | Full folder path on the selected SSD |
| `spaceacres_config.farm_size` | `string` | Allocated size (e.g. `100G`, `500G`, `1T`) |

---

## UI Components

### 1. Space Acres Toggle

Standard enable/disable toggle, same pattern as presearch/diiisco.

**Visibility rule**: Show for all SDN miners. If `available_ssds` is present in gui_config.enc, the service has completed SSD detection.

**Disabled state**: If `available_ssds` is empty (no SSDs found), show the toggle greyed out with a tooltip:
> "No SSD drives detected. Space Acres requires an SSD for farming."

### 2. SSD Selection

When the user clicks the toggle ON (or opens Space Acres settings), show the list of available SSDs from `gui_config.enc -> available_ssds`.

**Display each SSD as a selectable card/row:**

```
 [D:\]  450.2 GB free / 500.0 GB total
 [E:\]  900.7 GB free / 1000.0 GB total
```

- Pre-select the SSD with the most free space
- Grey out SSDs with less than 100 GB free (minimum for node data + a small farm)
- If only one SSD is available, auto-select it

### 3. Farm Folder Path

After selecting an SSD, show a folder path input pre-filled with a default:

- **Windows**: `{selected_ssd_path}autonomys-farm` (e.g. `D:\autonomys-farm`)
- **Linux**: `{selected_ssd_path}/autonomys-farm` (e.g. `/mnt/ssd/autonomys-farm`)

The user can edit this or browse to a different folder **on the same SSD**.

### 4. Farm Size

Show a slider or input for farm size allocation.

**Constraints:**
- Minimum: `50G`
- Maximum: `available_ssds[selected].free_gb - 100` (reserve 100 GB for node + OS)
- Default: 50% of available free space, rounded to nearest 50G
- Display format: human-readable (e.g. "400 GB", "1.5 TB")
- **Value format sent to service**: string with unit suffix -- `100G`, `500G`, `1T`, `2T`

**Size presets** (optional, for convenience):
| Preset | Value | Note |
|---|---|---|
| Small | `100G` | Minimum viable farm |
| Medium | `500G` | Good balance |
| Large | `1T` | Recommended if space allows |
| Maximum | `{max}G` | All available minus 100 GB reserve |

---

## IPC Operations

### Step 1: Configure Farm (before starting)

Send the chosen path and size to the service:

```json
{
  "op": "configure_spaceacres",
  "farm_path": "D:\\autonomys-farm",
  "farm_size": "400G"
}
```

**Response:**
```json
{
  "success": true,
  "op": "configure_spaceacres",
  "request_id": "...",
  "timestamp": "2026-02-24T10:30:00+00:00"
}
```

This writes the config to `gui_config.enc -> spaceacres_config`. The service reads it when starting the native binaries.

**Validation by service:**
- Both `farm_path` and `farm_size` are required (error if missing)
- The service does NOT validate the path exists or is an SSD -- the GUI should enforce this from the `available_ssds` list

### Step 2: Enable Space Acres (toggle ON)

Same pattern as presearch/diiisco -- write `miner_config.json` with `spaceacres.enabled: true`, then reload:

```python
# 1. Write config
config = {"spaceacres": {"enabled": True}}
send_op({
    "op": "write_config",
    "relative_path": "miner_config.json",
    "content": json.dumps(config)
})

# 2. Reload
send_op({"op": "reload_config"})
```

### Step 3: Start Space Acres

```json
{
  "op": "start_docker_container",
  "container_name": "spaceacres-node"
}
```

> Note: The op name `start_docker_container` is kept for backward compatibility but Space Acres now runs as native binaries, not Docker containers.

The service downloads the Autonomys binaries (first time only), starts the node, waits for it to become healthy, then starts the farmer.

**Progress feedback:**

During startup the service writes progress updates to:
```
ops_processed/{request_id}.progress.json
```

The GUI should poll this file alongside `{request_id}.done.json` every 500ms.
When `.progress.json` exists, show the phase/detail to the user and extend the
poll timeout to **900 seconds** (first-time binary downloads can take minutes).

```json
{
  "request_id": "...",
  "phase": "downloading",
  "detail": "Downloading consensus node... 45% (50 MB / 109 MB)",
  "updated_at": "2026-02-27T15:21:07+00:00"
}
```

| Phase | Detail | Typical duration |
|---|---|---|
| `downloading` | Downloading Autonomys binaries... | 30s-5 min (first time) / skipped (cached) |
| `starting_node` | Starting consensus node... | 1-5s |
| `waiting_for_node` | Waiting for node RPC... | 30s-5 min (first sync) / 10-30s (restart) |
| `starting_farmer` | Starting farmer... | 1-5s |

**Response (success):**
```json
{
  "success": true,
  "op": "start_docker_container",
  "sync_status_path": "C:\\ProgramData\\FryNetworks\\miner-SDN\\space-acres\\sync.json",
  "initial_status": "syncing"
}
```

> **IMPORTANT**: `success: true` means the processes started successfully, NOT that
> the node is fully synced and ready. When `sync_status_path` is present, the GUI
> **MUST** read that file to determine actual readiness. The `initial_status` field
> gives the status at the moment startup finished (usually `"syncing"` on first start).
> Poll `sync_status_path` every 2-5 seconds until `status` becomes `"running"`.

**Error cases:**
```json
{
  "success": false,
  "error": "Space Acres not configured: missing farm_path or farm_size"
}
```

### Step 4: Stop (toggle OFF)

```json
{
  "op": "stop_docker_container",
  "container_name": "spaceacres-node"
}
```

This stops both the node and farmer processes.

### Setup Firewall (once, on first enable)

```json
{
  "op": "setup_spaceacres_firewall"
}
```

Opens ports:
- **30333/TCP** -- Node P2P
- **30533/TCP** -- Farmer DSN
- **9944/TCP** -- Node RPC (localhost only)

---

## Complete Flow

```
User opens SDN GUI
    |
    +-- GUI reads gui_config.enc
    |   +-- available_ssds -> populate SSD list
    |   +-- spaceacres_config -> show current config (if any)
    |   +-- spaceacres -> toggle state
    |
    +-- User selects SSD + folder + size
    |   +-- GUI sends: configure_spaceacres(farm_path, farm_size)
    |
    +-- User flips toggle ON
    |   +-- GUI sends: write_config (spaceacres.enabled = true)
    |   +-- GUI sends: reload_config
    |   +-- GUI sends: setup_spaceacres_firewall  (first time only)
    |   +-- GUI sends: start_docker_container(spaceacres-node)
    |
    +-- User flips toggle OFF
        +-- GUI sends: stop_docker_container(spaceacres-node)
        +-- GUI sends: write_config (spaceacres.enabled = false)
        +-- GUI sends: reload_config
```

---

## Status Display

The service polls process status every measurement cycle (~60s). Status is available in `cache/latest.json`:

```json
{
  "selected_tools": ["spaceacres"],
  "spaceacres_stats": {
    "enabled": true,
    "running": true,
    "node_healthy": true,
    "farmer_running": true,
    "status": "running",
    "error": null
  }
}
```

**Status values:**

| `status` | Meaning | Suggested UI |
|---|---|---|
| `running` | Both processes up, synced and finalized | Green indicator |
| `syncing` | Node importing blocks to chain head | Blue indicator + progress bar |
| `finalizing` | Blocks imported, GRANDPA catching up | Blue indicator + "Finalizing..." |
| `stalled` | Sync or finalization stuck (no progress for 5 min), auto-restarting | Orange indicator + "Restarting node..." |
| `degraded` | Process problem (farmer crash) | Orange indicator |
| `stopped` | Processes exist but are stopped | Yellow indicator |
| `not_created` | Processes not started | Grey indicator, show setup prompt |
| `unknown` | Polling error | Orange indicator |

**Additional fields for detail panel:**

| Field | Type | Description |
|---|---|---|
| `node_healthy` | `bool` | Node RPC is reachable |
| `farmer_running` | `bool` | Farmer process is alive |
| `error` | `string\|null` | Error message if something went wrong |

---

## Rewards Impact

Space Acres contributes to the SDN parametric reward multiplier:

```
reward = base_reward + per_tool_reward * tool_count
```

- **Without Space Acres**: `tool_count = 0` -> user gets `base_reward` only
- **With Space Acres running**: `tool_count = 1` -> user gets `base_reward + per_tool_reward`

> **IMPORTANT**: `gui_config.enc -> spaceacres: true` means the user toggled the tool ON
> (it is *enabled*). It does **NOT** mean the tool is actively contributing to rewards.
> The tool only earns rewards when `sync.json -> earning_rewards: true`
> (i.e. `status == "running"` -- both processes up, blockchain synced and finalized).
>
> The GUI must use `earning_rewards` from the sync file to compute the displayed
> multiplier, not the `spaceacres` flag from `gui_config.enc`.

Show in the GUI:
- When `earning_rewards: true`: "Space Acres active: +{per_tool_reward} reward multiplier"
- When `earning_rewards: false` and `status` is `"syncing"` or `"finalizing"`: "Space Acres syncing -- not earning bonus yet"

---

## Important Notes

1. **SSD is mandatory** -- HDDs are not supported by Autonomys. The service detects SSDs via hardware queries; only show drives from `available_ssds`
2. **SSDs are scanned every 30 seconds** -- but `gui_config.enc -> available_ssds` is only updated on significant changes: a drive is added/removed, or free space changes by more than 100 GB. This keeps the list stable for the GUI
3. **Farm data is persistent** -- stopping Space Acres does NOT delete farm data. The user keeps their plotted data
4. **Initial sync takes time** -- the node needs hours to sync the blockchain; the farmer then needs hours/days to plot. Set expectations in the UI
5. **`configure_spaceacres` must be called before start** -- the service validates that `FARM_PATH` and `FARM_SIZE` are set and rejects the start if they're missing
6. **Reward address is embedded at build time** -- from 1Password (`op://SDN/SpaceAcres/wallet`). The GUI never sees or sets this
7. **Node name is automatic** -- set from the miner key, no user input needed
8. **Autonomys version is auto-resolved** -- the service queries the GitHub releases API on each start to find the latest stable `mainnet-*` release tag. The result is cached for 24 hours. Binaries are downloaded automatically on first use and when a new version is available. No EXE rebuild needed
9. **No Docker required** -- Space Acres runs as native Windows binaries. Docker is only needed for presearch/diiisco tools

---

## Blockchain Sync Status

On first start (or after a long offline period), the Autonomys node must sync the blockchain. This can take minutes to hours. During sync, the farmer will not run until the node catches up.

The service writes sync progress to a plain JSON file every 60 seconds:

**Path:** `C:\ProgramData\FryNetworks\miner-SDN\space-acres\sync.json`

```json
{
  "status": "syncing",
  "earning_rewards": false,
  "syncPhase": "block_sync",
  "node_healthy": true,
  "farmer_running": false,
  "isSyncing": true,
  "currentBlock": 6739649,
  "highestBlock": 6752483,
  "syncPercent": 99.81,
  "finalizedBlock": 6739649,
  "finalizationGap": 0,
  "peers": 71,
  "error": null,
  "updated_at": "2026-02-27T18:50:00+00:00"
}
```

### Status Values

| `status` | Meaning | Suggested GUI text |
|---|---|---|
| `"syncing"` | Node is importing blocks to reach chain head | See `syncPhase` for display text |
| `"finalizing"` | Blocks imported, GRANDPA finalization catching up | "Finalizing: {finalizationGap} blocks remaining" |
| `"stalled"` | Sync or finalization stuck for 5+ min, service auto-restarting node | "Restarting node..." |
| `"running"` | Fully synced, finalized, and farming | "Running - Fully synced" |
| `"degraded"` | Process problem (not sync-related) | "Degraded - check logs" |
| `"stopped"` | Processes exist but not running | "Stopped" |
| `"not_created"` | Processes not started | "Not configured" |

### syncPhase

The `syncPhase` field tells the GUI **which display message to show** during syncing. It is `null` when not syncing.

| `syncPhase` | Condition | Suggested GUI text |
|---|---|---|
| `"dsn_download"` | `currentBlock == 0`, initial DSN snap sync download | "Downloading blockchain data..." (no progress bar -- blocks jump from 0 to millions suddenly) |
| `"block_sync"` | `currentBlock > 0`, importing remaining blocks | "Syncing blockchain: {syncPercent}% ({currentBlock} / {highestBlock} blocks)" |
| `"finalization"` | Block imports done, GRANDPA catching up | "Finalizing: {finalizationGap} blocks remaining" |
| `null` | Not syncing (running, stopped, etc.) | Use `status` field |

> **IMPORTANT -- DSN snap sync behavior**: On first start, the node downloads a blockchain
> snapshot from the Distributed Storage Network (DSN). During this phase, `currentBlock`
> stays at `0` for 1-5 minutes, then **jumps to ~99% in a single step**. Do NOT show a
> progress bar during this phase -- it would be stuck at 0% then instantly jump to 99%.
> Show "Downloading blockchain data..." instead. The remaining ~1% syncs block-by-block.
>
> **Stall auto-recovery**: If `currentBlock` (during sync) or `finalizedBlock` (during
> finalization) doesn't advance for 5 minutes, the service automatically restarts the
> node process. The status changes to `"stalled"` during restart. A 15-minute cooldown
> prevents excessive restarts. After 5 consecutive failed restarts, the service stops
> trying and the status stays `"stalled"` until the next manual restart.

### GUI Polling

- Poll `space-acres/sync.json` every 2-5 seconds while the Space Acres panel is visible
- File is updated by the service every ~60 seconds
- When `isSyncing` is `null`: RPC not reachable yet (node still starting), show "Starting node..."
- When `syncPhase` is `"dsn_download"`: show "Downloading blockchain data..." (no progress bar)
- When `syncPhase` is `"block_sync"`: show progress bar using `syncPercent` and `currentBlock` / `highestBlock`
- When `syncPhase` is `"finalization"`: show "Finalizing: {finalizationGap} blocks remaining"
- When `status` transitions to `"running"`: show success notification

---

## Network Ports

Tell users to forward these ports on their router for optimal performance:

| Port | Protocol | Purpose |
|---|---|---|
| 30333 | TCP | Node peer-to-peer |
| 30533 | TCP | Farmer DSN (Distributed Storage Network) |
| 9944 | TCP | Node RPC (localhost only, not exposed externally) |

---

## File Paths

```
C:\ProgramData\FryNetworks\miner-SDN\
+-- space-acres/                   <- All Space Acres files
|   +-- bin/                       <- Native binaries (auto-downloaded)
|   |   +-- subspace-node-*.exe
|   |   +-- subspace-farmer-*.exe
|   |   +-- current_version.txt
|   +-- node-data/                 <- Node blockchain database
|   +-- logs/
|   |   +-- node.log               <- Node stdout/stderr
|   |   +-- farmer.log             <- Farmer stdout/stderr
|   +-- run/
|   |   +-- node.pid               <- Node process PID (for orphan cleanup)
|   |   +-- farmer.pid             <- Farmer process PID
|   +-- sync.json                  <- Blockchain sync progress (plain JSON, updated every 60s)
+-- config/
|   +-- space_acres_config.json    <- spaceacres enabled toggle
|   +-- gui_config.enc             <- available_ssds + spaceacres_config (encrypted)
+-- ops_queue/                     <- Send IPC requests here
+-- ops_processed/                 <- Read IPC responses here
+-- logs/
    +-- service.err.log            <- Service debug log
```

Linux equivalent: `/opt/FryNetworks/miner-SDN/`
