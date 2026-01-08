# UAC Removal: Architecture, Implementation, and Hand‑Offs

This document explains why UAC prompts occurred, the chosen architecture to eliminate them, what’s implemented, and where to find team‑specific guidance.

## Goals

- Run the GUI entirely in user context without UAC prompts.
- Delegate all privileged operations to a Windows service running as LocalSystem.
- Keep a clear boundary: GUI requests, service executes.

## Why UAC Prompts Happened

- Firewall rule management (e.g., `netsh advfirewall`) requires admin.
- Process termination in some contexts can require elevated rights.
- Writes to `%PROGRAMDATA%` are typically blocked for standard users (JSON/CSV status/config, encrypted measurements).

## Chosen Strategy

- **Service Delegation:** A Windows service (LocalSystem) performs privileged tasks.
- **Filesystem IPC Queue:** GUI writes request files to a service‑writable queue; the service consumes and performs the work, then writes results.

## Architecture Overview

```
GUI (User) ──writes JSON/ENC──▶ %PROGRAMDATA%\FryNetworks\miner-{CODE}\ops_queue
                                     │
                                     ▼
Service (LocalSystem) ─processes──▶ ProgramData folders (config/status/measurements)
                                     │
                                     └──▶ ops_processed markers for health/diagnostics
```

- `ops_queue`: Users have Modify permission; only place writable by GUI.
- `ops_processed`: Service‑owned markers/results for verification.
- ProgramData subfolders (`config/`, `status/`, `measurements/`) are service‑owned.

## Implementation Status

- GUI elevation removed; firewall/process control delegated to service.
- ProgramData file writes refactored to enqueue requests via `miner_GUI.utils.ops_queue_client`.
- Service daemon (`miner_GUI.services.privileged_ops_daemon`) consumes queue requests and writes outputs.
- Privileged operations consolidated in `miner_GUI.services.privileged_ops` (adds file I/O helpers and CLI options).

## Operations Handled by Service

- Firewall rules (add/remove, Mysterium setup).
- Process termination when required.
- File writes under ProgramData:
  - `config/*.json`, `status/*.json`.
  - `measurements/*.enc` (encrypted payloads).

## Verification & Testing (Quick)

1. Ensure installer set ACLs so Users can write to `ops_queue` and the service is running.
2. Trigger a GUI action that writes config/status; confirm the service produces files under ProgramData.
3. Check processed markers and timestamps under `ops_processed/` for recent activity.

Example (probe as a standard user):
```powershell
$queue = "$env:ProgramData\FryNetworks\miner-BM\ops_queue"
"probe" | Set-Content -Path (Join-Path $queue 'probe.txt') -Encoding UTF8
```

## Team Hand‑Offs

- Service Developers: See [docs/README_ServiceIPC.md](docs/README_ServiceIPC.md).
- Installer Team: See [docs/README_InstallerIPC.md](docs/README_InstallerIPC.md).

## Supporting Docs

- Background and options: [docs/PROGRAMDATA_FILE_PERMISSIONS.md](docs/PROGRAMDATA_FILE_PERMISSIONS.md)
- Implementation summary: [docs/PHASE_2_IMPLEMENTATION_COMPLETE.md](docs/PHASE_2_IMPLEMENTATION_COMPLETE.md)
- Quick reference: [docs/PHASE_2_QUICK_REFERENCE.md](docs/PHASE_2_QUICK_REFERENCE.md)

## Next Steps (Roadmap)

- Add strict path allowlists in the daemon to constrain writes.
- Provide an installer automation script for ACLs + NSSM setup.
- Add a simple ACL probe and an end‑to‑end integration test harness.
- Document health monitoring based on `ops_processed` freshness.
- Clean up the lint warning in `mysterium.py` for `data_dir_gui` import.
- Expand troubleshooting and rollback guidance.
