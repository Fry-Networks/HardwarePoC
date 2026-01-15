# IPC API Quick Reference

## One-Minute Setup

```python
import json
import time
import uuid
from pathlib import Path

# Configuration
SERVICE_TYPE = "BM"  # or "SDN", "SVN", "AEM"
BASE_PATH = f"C:\\ProgramData\\FryNetworks\\miner-{SERVICE_TYPE}"
QUEUE_PATH = Path(BASE_PATH) / "ops_queue"
RESPONSE_PATH = Path(BASE_PATH) / "ops_processed"

def send_op(op_dict, timeout=10):
    """Send IPC operation and get response."""
    request_id = str(uuid.uuid4())
    request_file = QUEUE_PATH / f"{request_id}.json"
    response_file = RESPONSE_PATH / f"{request_id}.done.json"
    
    # Send
    request_file.write_text(json.dumps(op_dict))
    
    # Wait for response
    start = time.time()
    while not response_file.exists():
        if time.time() - start > timeout:
            return {"success": False, "error": "Timeout"}
        time.sleep(0.1)
    
    return json.loads(response_file.read_text())
```

---

## Common Operations

### 1. Update Configuration

```python
from datetime import datetime, timezone

# Prepare config (MUST be valid JSON)
config = {
    "mysterium": {"enabled": True, "api_key": "YOUR_KEY"},
    "bright": {"enabled": False},
    "honeygain": {"enabled": True, "device_key": "YOUR_KEY"},
    "presearch": {"enabled": False},
    "diiisco": {"enabled": False},
    "spaceacres": {"enabled": False}
}

# Validate
try:
    json.dumps(config)  # Validate JSON format
except:
    print("Invalid JSON!")
    exit(1)

# Write config
resp1 = send_op({
    "op": "write_config",
    "relative_path": "miner_config.json",
    "content": json.dumps(config)
})

if not resp1["success"]:
    print(f"Write failed: {resp1.get('error')}")
    exit(1)

# Reload to activate
resp2 = send_op({"op": "reload_config"})

if resp2["success"]:
    print("Configuration updated!")
else:
    print("Reload failed (config may still be active)")
```

### 2. Write Measurement

```python
from datetime import datetime, timezone

resp = send_op({
    "op": "write_measurement",
    "tool": "mysterium",  # or: bright, honeygain, presearch, diiisco, spaceacres
    "data_b64": "<base64 of encrypted measurement payload>"
})

if resp["success"]:
    print(f"Measurement recorded for {resp.get('tool')}")
else:
    print(f"Measurement failed: {resp.get('error')}")
```

### 3. Get Enabled tools

```python
config_file = Path(BASE_PATH) / "config" / "miner_config.json"
config = json.loads(config_file.read_text())
enabled_tools = [tool for tool, cfg in config.items() if cfg.get("enabled")]
print(f"Enabled tools: {enabled_tools}")
```

---

## Error Handling Pattern

```python
def safe_operation(op_dict, max_retries=3):
    """Operation with error handling and retry."""
    for attempt in range(max_retries):
        try:
            resp = send_op(op_dict, timeout=10)
            
            if resp["success"]:
                return resp
            
            error = resp.get("error", "Unknown error")
            
            # Handle specific errors
            if "UTF-8 BOM" in error:
                print("ERROR: File has UTF-8 BOM - use UTF-8 without BOM")
            elif "Access is denied" in error:
                print("ERROR: Service lacks admin rights")
            elif "Invalid JSON" in error:
                print("ERROR: Configuration is not valid JSON")
            else:
                print(f"ERROR: {error}")
            
            # Don't retry config errors
            if "Invalid" in error or "UTF-8" in error:
                return resp
            
        except Exception as e:
            print(f"Exception: {e}")
            
            if attempt < max_retries - 1:
                time.sleep(1 * (2 ** attempt))  # Exponential backoff
                continue
        
        return {"success": False, "error": f"Failed after {max_retries} attempts"}
    
    return resp
```

---

## Tool Mapping

**By Service Type:**

```python
TOOL_MAPPING = {
    "BM": ["mysterium", "bright", "honeygain"],
    "SDN": ["spaceacres"],
    "SVN": ["presearch", "diiisco"]
}

# Use like:
enabled_tools = [tool for tool in TOOL_MAPPING["BM"] if config[tool]["enabled"]]
```

---

## File Encoding (CRITICAL!)

**Python - CORRECT:**
```python
# Write config WITHOUT BOM (this is correct!)
content = json.dumps(config)  # String
request_file.write_text(content, encoding='utf-8')

# NOT using 'utf-8-sig' (that ADDS BOM)
```

**VS Code:**
- Bottom right: Click "UTF-8"
- Select "UTF-8" (not "UTF-8 with BOM")
- Save

**PowerShell - CORRECT:**
```powershell
# Use UTF-8 without BOM
$config | ConvertTo-Json | Out-File config.json -Encoding utf8

# NOT -Encoding UTF8 (adds BOM)
```

---

## Timestamps (CRITICAL!)

**ALWAYS USE UTC:**

```python
# CORRECT - UTC with timezone info
from datetime import datetime, timezone
timestamp = datetime.now(timezone.utc).isoformat()
# Result: "2026-01-09T00:36:24.123456+00:00"

# Also OK
timestamp = datetime.utcnow().isoformat() + "+00:00"

# WRONG - Local time
timestamp = datetime.now().isoformat()  # No timezone!

# WRONG - Manual offset
import os
offset = -5  # This is WRONG - varies by DST!
```

---

## Response Structure

All operations return this format in done.json:

```json
{
  "success": true,
  "op": "operation_name",
  "request_id": "unique-id",
  "timestamp": "2026-01-09T00:36:24+00:00",
  "error": null
}
```

When error:
```json
{
  "success": false,
  "op": "write_config",
  "error": "write_config requires relative_path and content"
}
```

---

## Testing Checklist

- [ ] Config write → reload cycle works
- [ ] Measurements recorded with UTC timestamps
- [ ] tool enable/disable works correctly
- [ ] 5+ operations in rapid succession succeed
- [ ] Service recovers from bad config gracefully
- [ ] Errors display clearly to user
- [ ] No UTF-8 BOM in files
- [ ] Service responds within 5 seconds
- [ ] Concurrent operations don't corrupt data

---

## Performance Expectations

| Metric | Value |
|--------|-------|
| write_config | <5ms |
| reload_config | <1ms |
| write_measurement | <10ms |
| Concurrent ops (10) | 0.55s total (18.11 ops/sec) |
| CPU baseline | 1.56% |
| Memory baseline | 6.5 MB |
| Measurement interval | 67.5s average |

---

## Troubleshooting Quick Guide

| Problem | Solution |
|---------|----------|
| Config not updating | Call reload_config after write_config |
| UTF-8 BOM error | Use UTF-8 without BOM encoding |
| Measurements not recorded | Verify tool enabled + UTC timestamp |
| Firewall fails | Service needs admin rights |
| Timeout errors | Check if service is running |
| Invalid JSON errors | Validate with `json.loads()` first |

---

## File Locations

```
C:\ProgramData\FryNetworks\miner-BM\
├── config/miner_config.json          ← Update via IPC
├── ops_queue/                        ← Send requests here
├── ops_processed/                    ← Responses here
├── measurements/                     ← Encrypted files
├── cache/latest.json                 ← Current status
└── logs/service.err.log              ← Debug logs
```

---

## Complete Minimal Example

```python
import json, time, uuid
from pathlib import Path
from datetime import datetime, timezone

SERVICE_TYPE = "BM"
BASE_PATH = f"C:\\ProgramData\\FryNetworks\\miner-{SERVICE_TYPE}"
QUEUE = Path(BASE_PATH) / "ops_queue"
RESPONSE = Path(BASE_PATH) / "ops_processed"

def op(cmd):
    rid = str(uuid.uuid4())
    (QUEUE / f"{rid}.json").write_text(json.dumps(cmd))
    start = time.time()
    while (rfile := RESPONSE / f"{rid}.done.json").exists() is False:
        if time.time() - start > 10: return {"success": False, "error": "Timeout"}
        time.sleep(0.1)
    return json.loads(rfile.read_text())

# Example: Update config
config = {
    "mysterium": {"enabled": True, "api_key": "KEY"},
    "bright": {"enabled": False},
    "honeygain": {"enabled": False},
    "presearch": {"enabled": False},
    "diiisco": {"enabled": False},
    "spaceacres": {"enabled": False}
}

# Write
print(op({"op": "write_config", "relative_path": "miner_config.json", "content": json.dumps(config)}))

# Reload
print(op({"op": "reload_config"}))

# Measurement
print(op({
    "op": "write_measurement",
    "tool": "mysterium",
    "data_b64": "<base64 of encrypted measurement payload>"
}))
```

---

**For complete documentation, see:** GUI_DEVELOPER_GUIDE.md
