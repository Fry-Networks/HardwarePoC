# GUI Integration Guide - Autonomous Service Architecture

## Overview

The monitoring service has been refactored to collect measurements **autonomously** without requiring GUI involvement. The service now:

- **Reads configuration** from shared `config/` directory
- **Collects measurements** on a periodic schedule (configurable)
- **Writes measurements** to shared `measurements/` directory
- **Survives reboots** - measurements start immediately after boot

The GUI role has shifted from **measurement collection** to **configuration management and visualization**.

---

## Architecture

```
┌─────────────────────────────────────────┐
│          GUI (Unprivileged)             │
│  - User interaction                     │
│  - Configuration form submission        │
│  - Measurement visualization            │
└─────────────────────────────────────────┘
          ↓ Write config             ↓ Read measurements
          ↓ IPC reload signal        ↓ 
┌─────────────────────────────────────────┐
│      Shared Directories                 │
│  - config/                              │
│  - measurements/                        │
│  - ops_queue/ (IPC)                    │
└─────────────────────────────────────────┘
          ↑                           ↓
          ↑ Read/Write               ↓ Collect & Write
          ↑                           ↓
┌─────────────────────────────────────────┐
│     Service (Privileged, SystemService) │
│  - Autonomous measurement collection    │
│  - Configuration reading                │
│  - Firewall management                  │
│  - File operations (elevated privileges)│
└─────────────────────────────────────────┘
```

---

## Directory Structure

### Windows Paths
```
%PROGRAMDATA%\FryNetworks\miner-{MINER_CODE}\
├── config/                    # Configuration files (GUI writes, Service reads)
│   ├── miner_config.json      # Main miner settings
│   ├── mysterium_config.json
│   ├── presearch_config.json
│   ├── diiisco_config.json
│   ├── spaceacres_config.json
│   ├── bright_config.json
│   └── honeygain_config.json
├── measurements/              # Measurement files (Service writes, GUI reads)
│   ├── latest.json            # Latest measurements (JSON format for debugging)
│   ├── measurements_*.enc     # Encrypted historical measurements
│   └── ...
├── ops_queue/                 # IPC operation queue (GUI writes requests)
│   ├── *.json                 # Request files
│   └── ...
├── ops_processed/             # IPC operation results (Service writes)
│   ├── *.done.json
│   ├── *.processed
│   ├── *.error
│   └── health.json
└── logs/
    └── service.err.log
```

### Linux Paths
```
/var/lib/frynetworks/miner-{MINER_CODE}/
├── config/
├── measurements/
├── ops_queue/
├── ops_processed/
└── logs/
```

---

## Important: PoD (Proof of Data) Status

**The service now handles PoD collection autonomously.**

The service:
- Collects PoD status every measurement cycle
- Updates the local cache `podHours` structure
- Automatically sets the **"data" gate** in rewards calculations

The GUI **does NOT** need to write PoD status anymore. The `"data"` flag will be set correctly based on:
1. PoD HTTP endpoint status (port 8080): `GET http://localhost:8080/pod/status`
2. Fallback to file-based check: `{data_dir}/status/pod_status.json`
3. Default: `true` (benign failure assumption)

---

## GUI Tasks

### 1. **Write Configuration Files**

When user changes settings, GUI writes JSON files to `config/` directory.

#### Example: `miner_config.json`
```json
{
  "measurement_interval": 600,
  "mysterium_enabled": true,
  "presearch_enabled": false,
  "diiisco_enabled": false,
  "spaceacres_enabled": true,
  "bright_enabled": false,
  "honeygain_enabled": false
}
```

#### Example: `mysterium_config.json`
```json
{
  "enabled": true,
  "api_url": "http://localhost:4050",
  "wireguard_port": 51820,
  "identity": "0x123abc...",
  "provider_min_price": 50000,
  "provider_price_gib": 5000
}
```

#### Example: `presearch_config.json`
```json
{
  "enabled": true,
  "api_port": 80,
  "registration_code": "abc123def456",
  "auto_update": true
}
```

**Important:** 
- Write atomically to avoid partial reads
- Create parent directory if it doesn't exist
- Use UTF-8 encoding

---

### 2. **Trigger Configuration Reload**

After writing configuration files, send a `reload_config` operation via IPC queue:

#### Create IPC Request

File: `{data_dir}/ops_queue/{uuid}.json`

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "op": "reload_config"
}
```

**Steps:**
1. Generate unique `id` (UUID v4)
2. Create JSON file in `ops_queue/` directory
3. Poll `ops_processed/{id}.done.json` for confirmation (timeout: 10 seconds)
4. Check `success` field in result file

#### Example Python Implementation

```python
import json
import uuid
import os
import time
from pathlib import Path

def send_reload_config_ipc():
    """Send reload_config signal to service via IPC queue."""
    
    # Platform-specific data directory
    if sys.platform.startswith("win"):
        data_dir = Path(os.environ["PROGRAMDATA"]) / "FryNetworks" / f"miner-{MINER_CODE}"
    else:
        data_dir = Path("/var/lib/frynetworks") / f"miner-{MINER_CODE}"
    
    queue_dir = data_dir / "ops_queue"
    processed_dir = data_dir / "ops_processed"
    
    # Create directories if needed
    queue_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate request
    request_id = str(uuid.uuid4())
    request = {
        "id": request_id,
        "op": "reload_config"
    }
    
    # Write request to queue
    request_file = queue_dir / f"{request_id}.json"
    with open(request_file, "w", encoding="utf-8") as f:
        json.dump(request, f)
    
    # Wait for result (poll every 100ms, timeout 10s)
    result_file = processed_dir / f"{request_id}.done.json"
    for _ in range(100):  # 100 * 100ms = 10 seconds
        if result_file.exists():
            with open(result_file, "r", encoding="utf-8") as f:
                result = json.load(f)
            return result.get("success", False)
        time.sleep(0.1)
    
    return False  # Timeout
```

---

### 3. **Read Measurements**

Service writes measurements to `measurements/` directory. GUI reads these files for display.

#### Real-time Measurements: `latest.json`

```json
{
  "timestamp": "2026-01-08T14:30:45.123456+00:00",
  "software_version": "1.6.3",
  "poc_version": "v1.5.8",
  "miner_code": "BM",
  "enabled_pocs": ["mysterium", "spaceacres"],
  "hardware": {
    "cpu_percent": 42.5,
    "cpu_count": 8,
    "memory_percent": 68.3,
    "memory_total_gb": 16.0,
    "memory_used_gb": 10.9,
    "disk_percent": 45.2,
    "disk_total_gb": 500.0,
    "disk_used_gb": 226.0,
    "network_bytes_sent": 1234567890,
    "network_bytes_recv": 9876543210
  },
  "pod": {
    "status": true
  },
  "mysterium": {
    "status": "online",
    "health": {
      "uptime": 86400,
      "active_sessions": 3,
      "earned_tokens": 1234.56
    }
  },
  "spaceacres": {
    "status": "not_implemented"
  }
}
```

#### Historical Measurements: `measurements_*.enc`

Files are encrypted (base64-encoded, format for future Fernet encryption):
```python
import base64
import json

# Read encrypted measurement
with open("measurements_20260108_143045.enc", "rb") as f:
    encrypted_data = f.read()

# Decode base64
decoded = base64.b64decode(encrypted_data)

# Future: decrypt with Fernet key from service

# For now (development): decode as JSON
measurement = json.loads(decoded.decode('utf-8'))
```

---

## Integration Checklist

- [ ] **Configuration Management**
  - [ ] Read user input from UI forms
  - [ ] Validate configuration values
  - [ ] Write JSON files to `config/` directory atomically
  - [ ] Handle directory creation if needed

- [ ] **Config Reload Signal**
  - [ ] Generate UUID for each request
  - [ ] Write IPC request to `ops_queue/`
  - [ ] Poll `ops_processed/` for result
  - [ ] Handle timeout gracefully (fallback to eventual consistency)

- [ ] **Measurement Display**
  - [ ] Read `measurements/latest.json` periodically (every 10-30 seconds)
  - [ ] Parse JSON and display hardware stats
  - [ ] Display PoD status from `pod.status` field
  - [ ] Show PoC-specific stats (Mysterium earnings, etc.)
  - [ ] Handle missing/incomplete data gracefully

- [ ] **Error Handling**
  - [ ] Handle missing config files (show defaults)
  - [ ] Handle permission errors on config write
  - [ ] Handle IPC timeouts (show stale data or "loading...")
  - [ ] Handle missing measurements (show "collecting data...")

---

## Configuration Schema Examples

### Full `miner_config.json`
```json
{
  "measurement_interval": 600,
  "mysterium_enabled": true,
  "mysterium_provider": false,
  "presearch_enabled": true,
  "diiisco_enabled": false,
  "spaceacres_enabled": true,
  "bright_enabled": false,
  "honeygain_enabled": false,
  "log_level": "INFO",
  "debug_mode": false
}
```

### Full `mysterium_config.json`
```json
{
  "enabled": true,
  "api_url": "http://localhost:4050",
  "api_key": "your-api-key",
  "wireguard_port": 51820,
  "identity": "0x1234567890abcdef",
  "provider_mode": false,
  "minimum_stake": 1000000000000000000,
  "provider_min_price": 50000000000,
  "provider_price_gib": 5000000000,
  "auto_register": true,
  "country_whitelist": ["US", "CA", "GB"],
  "country_blacklist": []
}
```

### Full `presearch_config.json`
```json
{
  "enabled": true,
  "api_port": 80,
  "registration_code": "abc123def456ghi",
  "node_id": "presearch_node_123",
  "auto_update": true,
  "update_check_interval": 3600
}
```

### Full `spaceacres_config.json`
```json
{
  "enabled": true,
  "rpc_endpoint": "ws://localhost:9944",
  "farmer_key": "your-farmer-account-key",
  "plot_directory": "/mnt/plots",
  "reward_address": "13K1X...",
  "public_key": "your-public-key",
  "proof_of_spacetime_enabled": true
}
```

---

## Service Internals (For Reference)

### Measurement Collection Flow

```
1. Service starts → loads config via _read_service_config()
2. Measurement daemon thread starts (_start_measurement_daemon)
3. Every N seconds (configurable):
   a. Read enabled PoCs from config
   b. Collect hardware stats (psutil)
   c. Query each PoC API for stats
   d. Collect PoD status:
      - Query PoD HTTP endpoint (http://localhost:8080/pod/status)
      - Or check pod_status.json file
      - Default: true (benign failure)
   e. Update local cache podHours with PoD status
   f. Aggregate into measurement object
   g. Write to measurements/latest.json
   h. Write encrypted version to measurements/TIMESTAMP.enc
4. GUI reads latest.json and displays to user
5. Main loop reads podHours cache and sets "data" gate in rewards
```

**Key Point:** The "data" gate will be set based on actual PoD collection, not GUI input!

### IPC Operation Flow

```
1. GUI generates request (id, op, parameters)
2. GUI writes to ops_queue/{id}.json
3. Service polls ops_queue/ every 500ms
4. Service processes request
5. Service writes result to ops_processed/{id}.done.json
6. GUI reads result and confirms success
```

### Configuration Update Flow

```
1. User changes settings in GUI
2. GUI validates input
3. GUI writes config/*.json files atomically
4. GUI sends "reload_config" via IPC
5. Service receives reload_config operation
6. Service calls _reload_service_config()
7. Service updates internal cache
8. Next measurement cycle uses new config
```

---

## Troubleshooting

### "Config changes not taking effect"
- Check that config files were written to correct directory
- Verify JSON is valid (use JSON validator)
- Check that "reload_config" IPC message was sent
- Wait for next measurement cycle (up to N seconds)

### "Measurements not updating"
- Check that `measurements/latest.json` exists
- Verify file permissions (should be readable by GUI user)
- Check service logs: `%PROGRAMDATA%\FryNetworks\miner-{CODE}\logs\service.err.log`
- Verify service is running: `Get-Service FRY_PoC_BM_Service` (Windows)

### "IPC reload_config times out"
- Check ops_queue directory permissions
- Verify service daemon is running
- Check ops_processed/health.json for daemon status
- Fallback: wait for next scheduled measurement (up to N seconds for eventual consistency)

### "PoD status not updating"
- Check PoD service is running on port 8080
- Verify service can reach `http://localhost:8080/pod/status`
- Or check `{data_dir}/status/pod_status.json` exists and is valid
- Service will default to `true` if PoD can't be queried
- Check service logs for PoD collection errors

### "Data gate always false"
- Verify PoD status is being collected (see "PoD status not updating")
- Check latest.json includes `"pod": {"status": true/false}`
- Main measurement loop reads podHours cache automatically
- If PoD endpoint unavailable, service assumes `true` (benign failure)

---

## Best Practices

1. **Always validate user input** before writing config files
2. **Write atomically** - write to temp file, then rename
3. **Handle missing PoC configs gracefully** - service provides defaults
4. **Implement retry logic** for IPC operations (timeout → retry once)
5. **Show loading states** while waiting for new measurements
6. **Cache measurements locally** for offline display
7. **Poll measurements every 10-30 seconds** (not more frequently than collection interval)
8. **DO NOT write PoD status** - service handles this autonomously
9. **Display PoD status** from `measurements/latest.json` → `pod.status`
10. **Monitor PoD endpoint** - service checks `http://localhost:8080/pod/status` automatically

---

## Example GUI Workflow

```python
# 1. User submits form with new Mysterium settings
mysterium_config = {
    "enabled": True,
    "identity": user_input["identity"],
    "provider_min_price": int(user_input["min_price"]),
    "wireguard_port": 51820
}

# 2. Validate
if not mysterium_config["identity"].startswith("0x"):
    show_error("Invalid identity format")
    return

# 3. Write to config directory
config_path = get_config_dir() / "mysterium_config.json"
write_json_atomically(config_path, mysterium_config)

# 4. Send reload signal
success = send_reload_config_ipc()
if success:
    show_info("Configuration updated")
    # Next measurements will use new config
else:
    show_warning("Config reload timed out (will apply eventually)")

# 5. Measurements will reflect new settings after next collection cycle
# Display loading indicator until new measurements arrive
```

---

## API Endpoints (Future)

For future REST API integration, consider:
- `GET /api/v1/measurements/latest` → latest.json
- `POST /api/v1/config` → write config and trigger reload
- `GET /api/v1/config` → read current config
- `GET /api/v1/status` → daemon health

---

## Questions or Issues?

Refer to:
- [UAC Removal Architecture](README_UAC_Removal.md)
- [Service IPC Specification](README_ServiceIPC.md)
- Service logs: `{data_dir}/logs/service.err.log`
