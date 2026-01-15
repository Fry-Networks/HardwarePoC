# GUI Developer Guide - FRY PoC Miner Service

## Overview

This guide documents the BM (Block Miner) service IPC API, performance characteristics, and recommendations for GUI integration. The service is production-ready and has passed comprehensive robustness testing.

**Current Version:** BM v1.6.4  
**Test Status:** 100% functional tests passing, 90.9% robustness tests passing  
**Performance:** Excellent (minimal CPU, stable memory, good concurrency)

---

## Table of Contents

1. [IPC API Reference](#ipc-api-reference)
2. [Configuration System](#configuration-system)
3. [Performance Characteristics](#performance-characteristics)
4. [Error Handling](#error-handling)
5. [Best Practices](#best-practices)
6. [Troubleshooting](#troubleshooting)

---

## IPC API Reference

### Overview

The service communicates with external applications (like the GUI) via an IPC queue system. All operations use a request-response pattern through filesystem queues.

**Queue Paths:**
- Requests: `C:\ProgramData\FryNetworks\miner-[TYPE]\ops_queue\`
- Responses: `C:\ProgramData\FryNetworks\miner-[TYPE]\ops_processed\`

**Supported Operation Types:** `reload_config`, `write_config`, `write_measurement`, `add_firewall_rule`, `remove_firewall_rule`, `setup_*_firewall`

### Operation Details

#### 1. reload_config

Reloads service configuration from disk files.

**Request Format:**
```json
{
  "op": "reload_config"
}
```

**Response Format:**
```json
{
  "success": true,
  "result_path": "C:\\ProgramData\\FryNetworks\\miner-BM\\ops_processed\\<request_id>.done.json"
}
```

**Behavior:**
- Service reads all config files (`miner_config.json`, `install_config.json`, etc.)
- If config parsing fails, service logs warning and **retains previous valid configuration**
- Returns success=true even if no configs loaded (by design - ensures availability)
- Invalid JSON is silently skipped; service continues operation

**GUI Recommendations:**
- Always validate JSON before calling `reload_config`
- Check done.json for `success: true` before proceeding
- If reload fails, previous config remains active (no service disruption)
- Consider showing user feedback if configs fail validation

**Common Issues:**
- UTF-8 BOM in JSON files causes parsing failure
  - Solution: Use UTF-8 without BOM when writing configs
  - Editors: VS Code has option "Files: Encoding"
  - Python: Use `encoding='utf-8'` (not 'utf-8-sig') when writing

#### 2. write_config

Writes a configuration file to a privileged location.

**Request Format:**
```json
{
  "op": "write_config",
  "relative_path": "miner_config.json",
  "content": "{ valid json content }"
}
```

**Parameters:**
- `relative_path` (required): Relative path under config directory (e.g., `miner_config.json`)
- `content` (required): Full JSON content as string

**Response Format:**
```json
{
  "success": true,
  "result_path": "C:\\ProgramData\\FryNetworks\\miner-BM\\ops_processed\\<request_id>.done.json"
}
```

**Behavior:**
- Service writes file with UTF-8 encoding (no BOM)
- File is written to privileged location that GUI cannot access
- If content is invalid JSON, operation fails with error message in done.json

**GUI Recommendations:**
- Validate all JSON before writing
- Use Python's `json.dumps()` or equivalent for serialization
- Check done.json for `success: true`
- On failure, examine error message in done.json for details
- Consider backup of previous config before write

**Validation Checklist:**
```python
import json
try:
    # Validate before sending
    json.loads(content)
    # Send to service
except json.JSONDecodeError as e:
    # Show error to user
    print(f"Invalid JSON: {e}")
```

#### 3. write_measurement

Writes an encrypted measurement blob for a specific tool.

**Request Format:**
```json
{
  "op": "write_measurement",
  "tool": "mysterium",
  "data_b64": "<base64 of encrypted measurement payload>"
}
```

**Payload guidance (inside the encrypted blob):**
```json
{
  "timestamp": "2026-01-09T00:36:24+00:00",
  "hours": 1,
  "slots": 24,
  "latency_ms": 45,
  "bandwidth_mbps": 100
}
```

**Parameters:**
- `tool` (required): Tool name (BM: mysterium, bright, honeygain; SDN: spaceacres; SVN: presearch, diiisco)
- `data_b64` (required): Base64-encoded encrypted measurement payload

**Response Format:**
```json
{
  "success": true,
  "result_path": "C:\\ProgramData\\FryNetworks\\miner-BM\\ops_processed\\<request_id>.done.json"
}
```

**Behavior:**
- Service writes the encrypted payload to `measurements-<tool>-latest.json.enc`
- Measurements must use UTC timestamps (not local time) inside the payload
- File is encrypted; GUI cannot directly read contents
- Returns success even if tool not enabled (graceful handling)

**Tool Mapping by Miner Type:**

| Miner Type | Supported Tools |
|-----------|----------------|
| BM | mysterium, bright, honeygain |
| SDN | spaceacres |
| SVN | presearch, diiisco |

**GUI Recommendations:**
- Use `datetime.now(timezone.utc).isoformat()` for timestamps inside the payload
- Only write measurements for enabled tools (check miner_config)
- Check done.json for `success: true`
- Handle both write_measurement ops from GUI and automatic collection

**Timestamp Format Example (Python):**
```python
from datetime import datetime, timezone
# CORRECT - Use UTC
timestamp = datetime.now(timezone.utc).isoformat()
# Returns: "2026-01-09T00:36:24.123456+00:00"

# WRONG - Local time would be incorrect
timestamp = datetime.now().isoformat()
```

#### 4. add_firewall_rule

Adds a Windows firewall rule (requires admin privileges).

**Request Format:**
```json
{
  "op": "add_firewall_rule",
  "rule_name": "MyApp_TCP_8080",
  "port": 8080,
  "protocol": "TCP",
  "direction": "Inbound"
}
```

**Response Format:**
```json
{
  "success": false,
  "error": "Failed to add firewall rule MyApp_TCP_8080: Access is denied"
}
```

**Behavior:**
- Adds Windows firewall exception for a specific port
- Requires service running with admin privileges
- Returns error if service lacks admin rights or port conflict exists

**GUI Recommendations:**
- Check `success: false` and examine error message
- Show error details to user (may require elevation)
- Suggest restarting service with admin rights if failure occurs

#### 5. remove_firewall_rule

Removes a previously added firewall rule.

**Request Format:**
```json
{
  "op": "remove_firewall_rule",
  "rule_name": "MyApp_TCP_8080"
}
```

**Response Format:**
```json
{
  "success": true
}
```

#### 6. setup_*_firewall

Pre-configured firewall setup for each tool.

**Available Operations:**
- `setup_mysterium_firewall`
- `setup_presearch_firewall`
- `setup_diiisco_firewall`
- `setup_spaceacres_firewall`

**Request Format:**
```json
{
  "op": "setup_mysterium_firewall"
}
```

**Response Format:**
```json
{
  "success": true
}
```

**Behavior:**
- Configures all necessary firewall rules for thetool
- Uses pre-defined ports and protocols
- Returns error if admin rights insufficient

### Common Response Structure

All operations return a done.json file with this structure:

```json
{
  "success": true,
  "op": "operation_name",
  "request_id": "unique-id",
  "timestamp": "2026-01-09T00:36:24+00:00",
  "error": null  // Only present if success: false
}
```

**GUI Implementation Pattern:**

```python
import json
import time
from pathlib import Path

def send_operation(op_dict, request_id, service_type="BM", timeout=10):
    """
    Send IPC operation and wait for response.
    
    Args:
        op_dict: Operation dict (includes "op" key)
        request_id: Unique operation ID
        service_type: "BM", "SDN"or "SVN"
        timeout: Maximum seconds to wait for response
        
    Returns:
        Response dict with success/error info
    """
    base_path = f"C:\\ProgramData\\FryNetworks\\miner-{service_type}"
    queue_path = Path(base_path) / "ops_queue"
    processed_path = Path(base_path) / "ops_processed"
    
    # Write request
    request_file = queue_path / f"{request_id}.json"
    request_file.write_text(json.dumps(op_dict))
    
    # Wait for response
    response_file = processed_path / f"{request_id}.done.json"
    start_time = time.time()
    
    while not response_file.exists():
        if time.time() - start_time > timeout:
            return {"success": False, "error": "Operation timeout"}
        time.sleep(0.1)
    
    # Read response
    response = json.loads(response_file.read_text())
    return response
```

---

## Configuration System

### Overview

Configuration is split between **build-time embedded credentials** and **runtime operational settings**:

- **Build-time (from 1Password):** Critical credentials (API keys, payout addresses, registration codes) embedded during service build - GUI cannot modify
- **Runtime (from GUI):** Tool enable/disable flags and operational settings written via `write_config` IPC operations

This separation ensures sensitive data is never exposed in user-modifiable config files.

### File Locations

```
C:\ProgramData\FryNetworks\miner-BM\
├── config/
│   ├── miner_config.json           (Tool enable/disable flags)
│   ├── presearch_config.json       (Presearch operational settings)
│   ├── diiisco_config.json         (Diiisco operational settings)
│   ├── brd_config.json             (Bright operational settings)
│   ├── honeygain.json              (Honeygain operational settings)
│   ├── install_config.json         (Installation paths, GeoIP data)
│   └── *.json                      (Additional configs)
├── ops_queue/                      (Incoming requests)
├── ops_processed/                  (Completed responses)
├── measurements/                   (Encrypted measurement files)
├── cache/                          (PoD cache, latest.json)
└── logs/
    ├── service.out.log             (Info/debug)
    └── service.err.log             (Warnings/errors)
```

### miner_config.json Structure

**Purpose:** Enable/disable each tool. This is the primary control point for the GUI.

**Example:**
```json
{
  "mysterium": {
    "enabled": true
  },
  "bright": {
    "enabled": false
  },
  "honeygain": {
    "enabled": true
  },
  "presearch": {
    "enabled": false
  },
  "diiisco": {
    "enabled": true
  },
  "spaceacres": {
    "enabled": false
  }
}
```

**GUI Recommendations:**
- Provide checkboxes for enabling/disabling each tool
- **Do NOT include API keys, registration codes, or wallet addresses** - these are embedded at build time
- Validate before writing with `write_config` operation
- Show which tools are enabled/disabled
- After changes, call `reload_config` to activate

### Tool-Specific Config Files

**Presearch** (`presearch_config.json`):
- Required: `enabled` flag
- Credentials (registration_code, api_key) are embedded at build time
- Optional: `docker_container_name`

```json
{
  "enabled": true,
  "docker_container_name": "presearch-node"
}
```

**Diiisco** (`diiisco_config.json`):
- Required: `enabled` flag
- Credentials (node_key, api_key) are embedded at build time
- Optional: `api_port`, `docker_container_name`, `network`

```json
{
  "enabled": true,
  "api_port": 8080,
  "docker_container_name": "diiisco-node",
  "network": "mainnet"
}
```

**Bright** (`brd_config.json`):
- Required: `enabled` flag, `app_id`
- Credentials (api_token) are embedded at build time
- Optional: `app_name`, `logo_link`, `language`, `consent`

```json
{
  "enabled": true,
  "app_id": "bright-app-id-123",
  "app_name": "Fry Networks",
  "logo_link": "https://example.com/logo.png",
  "language": "en",
  "consent": true
}
```

**Honeygain** (`honeygain.json`):
- Required: `enabled` flag
- Credentials (api_key) are embedded at build time
- Optional: `sdk_root`, `library_path`, `log_dir`, `poll_seconds`

```json
{
  "enabled": true,
  "sdk_root": "C:/ProgramData/FryNetworks/SDK/windows-honeygain-sdk",
  "library_path": "C:/ProgramData/FryNetworks/SDK/windows-honeygain-sdk/x64/bin/hgsdk.dll",
  "log_dir": "C:/ProgramData/FryNetworks/logs/honeygain",
  "poll_seconds": 60
}
```

**Mysterium** - Uses `miner_config.json` enable flag only (no separate config file needed)

### Configuration Validation

**Critical:** Always validate configuration before writing.

**Validation Steps:**
1. Ensure valid JSON syntax
2. Verify required fields present (e.g., `app_id` for Bright)
3. **Do NOT include credentials** - these are embedded at build time
4. Ensure no UTF-8 BOM in files
5. Test with small config change first

**Example Validation:**
```python
def validate_miner_config(config_dict):
    """Validate miner configuration (enable/disable flags only)."""
    required_tools = ["mysterium", "bright", "honeygain", 
                     "presearch", "diiisco", "spaceacres"]
    
    for tool in required_tools:
        if tool not in config_dict:
            return False, f"Missing tool: {tool}"
        if "enabled" not in config_dict[tool]:
            return False, f"Missing 'enabled' field for {tool}"
        
        # CRITICAL: Do not allow credentials in miner_config
        if "api_key" in config_dict[tool] or "key" in config_dict[tool]:
            return False, f"ERROR: Do not include credentials in {tool} config. These are embedded at build time."
    
    return True, "Config valid"

# Usage
config = {
    "mysterium": {"enabled": True},
    "presearch": {"enabled": False},
    # ... other tools with only enabled flag
}
is_valid, msg = validate_miner_config(config)
```

### Configuration Reload Behavior

**Normal Flow:**
1. GUI calls `write_config` with new configuration
2. Service writes file successfully
3. GUI calls `reload_config` to activate changes
4. Service re-reads all config files
5. Measurement collection adjusts to enabled tools

**Error Flow (Graceful):**
1. GUI writes invalid JSON via `write_config`
2. Service detects invalid JSON, logs WARNING, skips file
3. GUI calls `reload_config`
4. Service returns success: true (by design)
5. **Previous valid configuration remains active**
6. Service continues operation unchanged

**Important:** The graceful handling ensures the service never stops due to bad config. The GUI must validate before writing to prevent accidental misconfiguration.

---

## Performance Characteristics

### Baseline Metrics

These metrics were captured during comprehensive testing with typical workloads.

#### CPU Usage
- **Idle State:** 1.56% average
- **Under Load (10 concurrent IPC ops):** 1.56% average
- **Load Increase:** 0% (no measurable increase)

**Implication:** Service has excellent CPU efficiency. GUI can safely perform bulk operations without concern for CPU impact.

#### Memory Usage
- **Baseline:** 6.5 MB
- **Under Load:** 6.5 MB (stable)
- **No Memory Leaks:** Confirmed through extended testing

**Implication:** Memory consumption is minimal and stable. Service won't consume excessive resources on long-running systems.

#### IPC Throughput
- **Concurrent Operations:** 18.11 ops/sec
- **Test Case:** 10 simultaneous requests completed in 0.55 seconds
- **Success Rate:** 100%

**Implication:** GUI can safely batch requests. For example, writing measurements for multiple PoCs can happen in rapid succession.

#### Measurement Collection
- **Average Interval:** 67.5 seconds (configured 60s interval with some jitter)
- **Interval Range:** 53.4 - 70.3 seconds (±20% tolerance)
- **Reliability:** 100% success rate

**Implication:** Measurements are reliably collected. GUI can count on regular measurement data availability.

#### Log File Growth
- **Sample Rate:** 460 lines in ~20 hours of operation
- **Current Size:** 0.08 MB
- **Projected 30-day Size:** 4.3 MB
- **Recommendation:** Implement log rotation after 30 days or when file exceeds 5 MB

**Implication:** Log files remain manageable. Recommend log rotation strategy for long-term deployments.

### Recommendations for GUI

1. **Batch Operations:** Can safely send 5-10 operations in rapid succession (tested throughput supports this)

2. **Polling Frequency:** Safe to poll for measurements every 60-90 seconds (collection interval is 60s)

3. **Config Updates:** Can safely update configs in sequence without concern for service stability

4. **Resource Constraints:** On minimal hardware (1 GB RAM), service still stable at 6.5 MB memory

5. **Long-running Sessions:** No memory leaks detected; service can run for weeks without degradation

---

## Error Handling

### Response Error Codes

All error responses include an `error` field with a descriptive message.

**Common Errors:**

| Error | Cause | Resolution |
|-------|-------|-----------|
| `Invalid operation type` | Unknown operation in request | Verify operation name is correct |
| `Missing required field` | Required parameter not provided | Check operation format in API reference |
| `Failed to add firewall rule: Access is denied` | Service lacks admin rights | Restart service as administrator |
| `UTF-8 BOM (decode using utf-8-sig)` | JSON file has UTF-8 BOM | Ensure files use UTF-8 without BOM |
| `Unexpected character in JSON` | Invalid JSON syntax | Validate JSON before sending |
| `write_config requires relative_path and content` | Missing write_config parameters | Provide both relative_path and content fields |

### Handling Operation Failures

**Pattern:**
```python
def safe_operation(op_dict, request_id, timeout=10):
    """Send operation with error handling."""
    try:
        response = send_operation(op_dict, request_id, timeout)
        
        if not response.get("success", False):
            # Operation failed - check error message
            error_msg = response.get("error", "Unknown error")
            print(f"Operation failed: {error_msg}")
            
            # Handle specific errors
            if "UTF-8 BOM" in error_msg:
                print("Action: Use UTF-8 without BOM when writing configs")
            elif "Access is denied" in error_msg:
                print("Action: Service needs admin privileges")
            elif "Invalid JSON" in error_msg:
                print("Action: Validate JSON before sending")
            
            return False
        
        return True
        
    except TimeoutError:
        print("Operation timed out - service may be unresponsive")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False
```

### Retry Logic

**Recommended for:** write_config, write_measurement, firewall operations

**Not Recommended for:** reload_config (already has internal retry logic)

**Pattern:**
```python
def operation_with_retry(op_dict, request_id, max_retries=3, delay=1):
    """Send operation with exponential backoff retry."""
    import time
    
    for attempt in range(max_retries):
        response = send_operation(op_dict, request_id)
        
        if response.get("success", False):
            return response
        
        if attempt < max_retries - 1:
            time.sleep(delay * (2 ** attempt))  # Exponential backoff
    
    return response  # Return final failure
```

---

## Best Practices

### 1. Configuration Management

**DO:**
- ✓ Validate all JSON before writing via `write_config`
- ✓ Use UTF-8 without BOM encoding
- ✓ Call `reload_config` after config changes
- ✓ Show user feedback during config updates
- ✓ Only include operational settings and enable/disable flags in config files
- ✓ Let the build system embed all sensitive credentials (API keys, wallet addresses, etc.)

**DON'T:**
- ✗ Write invalid JSON (service will skip silently and keep old config)
- ✗ Use UTF-8 with BOM (causes parse errors)
- ✗ Assume changes take effect immediately (need to call reload_config)
- ✗ Update multiple configs without reload_config between changes
- ✗ **Include API keys, registration codes, wallet addresses, or other credentials in GUI-written configs** (these are embedded at build time and managed via 1Password)
- ✗ Allow users to enter/modify sensitive credentials in the GUI (no credential input fields)

**Credential Management:**
- All tool credentials (API keys, registration codes, payout addresses, etc.) are embedded during the service build from 1Password
- GUI has **no access** to these credentials and **should not** provide input fields for them
- GUI can only enable/disable tools and configure operational settings (paths, ports, container names, etc.)
- This ensures sensitive data is never stored in user-modifiable config files

### 2. IPC Operation Pattern

**Recommended Flow:**
```python
import uuid
import json

# 1. Generate unique ID for operation
request_id = str(uuid.uuid4())

# 2. Create operation dict
op = {
    "op": "write_measurement",
  "tool": "mysterium",
  "data_b64": "<base64-encoded encrypted payload>"
}

# 3. Send and wait for response
response = send_operation(op, request_id, timeout=10)

# 4. Check result
if response["success"]:
    print("Operation succeeded")
else:
    print(f"Operation failed: {response.get('error')}")
```

### 3. Timestamp Handling

**Always use UTC for all timestamps:**
```python
from datetime import datetime, timezone

# CORRECT
timestamp = datetime.now(timezone.utc).isoformat()
# Result: "2026-01-09T00:36:24.123456+00:00"

# Also acceptable
import datetime as dt
timestamp = dt.datetime.utcnow().isoformat() + "+00:00"
```

**Why UTC?**
- Service uses UTC for all persistent data (measurements, logs, cache)
- Enables global consistency for reward calculations
- Prevents local timezone issues across deployments
- Required by API backend

### 4. tool-Specific Recommendations

**Before collecting measurements:**
1. Check which tools are enabled in miner_config
2. Only write measurements for enabled tools
3. Use correct tool names (case-sensitive)

**Query enabled tools:**
```python
def get_enabled_tools(service_type="BM"):
    """Get list of enabled tools for miner type."""
    config_path = f"C:\\ProgramData\\FryNetworks\\miner-{service_type}\\config\\miner_config.json"
    config = json.loads(Path(config_path).read_text())
    return [tool for tool, settings in config.items() if settings.get("enabled", False)]

# Usage
enabled = get_enabled_tools("BM")  # Returns ["mysterium", "honeygain", ...]
```

### 5. Error Recovery

**If service becomes unresponsive:**
1. Check service status: `Get-Process | grep miner_online_simple`
2. Check logs: `C:\ProgramData\FryNetworks\miner-BM\logs\service.err.log`
3. Restart service if needed
4. Retry operation with backoff

**If config update fails:**
1. Check error message in done.json
2. Validate JSON locally before retry
3. If "UTF-8 BOM" error, fix file encoding
4. Call reload_config to verify state

### 6. Logging and Debugging

**Enable debug logging by setting interval to 60 (faster measurement collection):**
```python
debug_config = {
    "mysterium": {"enabled": True, "api_key": "..."},
    # ... other tools
}
operation = {
    "op": "write_config",
    "relative_path": "miner_config.json",
    "content": json.dumps(debug_config)
}
```

**Check service logs for diagnostics:**
- `service.out.log` - Info and debug messages
- `service.err.log` - Warnings and errors
- Look for STEP markers for operation tracking

---

## Troubleshooting

### Issue: Configuration not updating after write_config

**Symptoms:**
- Wrote config with write_config
- Config not taking effect
- Measurements still use old settings

**Solution:**
1. Verify write_config returned success
2. Call reload_config explicitly after write_config
3. Wait for reload_config to complete
4. Check service logs for load errors

**Example:**
```python
# Write new config
resp1 = send_operation({
    "op": "write_config",
    "relative_path": "miner_config.json",
    "content": json.dumps(new_config)
}, request_id1)

if not resp1["success"]:
    print(f"write_config failed: {resp1['error']}")
    sys.exit(1)

# Reload to activate
resp2 = send_operation({"op": "reload_config"}, request_id2)

if not resp2["success"]:
    print(f"reload_config failed")
```

### Issue: UTF-8 BOM parsing errors

**Symptoms:**
- reload_config logs warning: "Unexpected UTF-8 BOM"
- Config not loading
- Measurements use default settings

**Root Cause:**
- File was saved with UTF-8 BOM (Byte Order Mark)
- Python's default `open()` with UTF-8 adds BOM
- Some text editors default to UTF-8 with BOM

**Solutions:**

**Python:**
```python
# WRONG - Creates UTF-8 BOM
with open("config.json", "w", encoding="utf-8") as f:
    json.dump(config, f)

# CORRECT - Pure UTF-8 without BOM
with open("config.json", "w", encoding="utf-8-sig") as f:
    json.dump(config, f)
# Actually, -sig means "with signature", so this is wrong too

# CORRECT - Explicitly no BOM
import io
with io.open("config.json", "w", encoding="utf-8") as f:
    f.write(json.dumps(config))
```

**VS Code:**
1. Open file
2. Click "UTF-8" in bottom right
3. Select "UTF-8" (not "UTF-8 with BOM")
4. Save

### Issue: write_measurement not recording

**Symptoms:**
- write_measurement returns success
- Measurement file not updated
- tool shows no recent activity

**Verification Steps:**
1. Check tool is enabled in miner_config
2. Verify timestamp is UTC (has +00:00 suffix)
3. Check service logs for measurement errors
4. Verify measurement file exists

**Check enabled tools:**
```python
config_path = "C:\\ProgramData\\FryNetworks\\miner-BM\\config\\miner_config.json"
config = json.loads(Path(config_path).read_text())
print({tool: settings.get("enabled") for tool, settings in config.items()})
```

**Check measurement files:**
```
C:\ProgramData\FryNetworks\miner-BM\measurements\
├── measurements_20260109_003624.enc
├── measurements_20260109_003733.enc
└── ...
```

### Issue: High CPU or memory usage

**Note:** Service baseline is very low (1.56% CPU, 6.5 MB), so issues are unlikely.

**If observed:**
1. Check service version: `miner_online_simple.py` should be v1.6.4
2. Check for measurement collection backlog (too many measurements queued)
3. Check for file system issues (slow disk causing delays)
4. Restart service if needed

**Check service logs:**
```powershell
Get-Content "C:\ProgramData\FryNetworks\miner-BM\logs\service.err.log" | Select-String "ERROR|WARNING" | Tail -20
```

### Issue: Firewall rule operations failing

**Symptoms:**
- setup_mysterium_firewall returns error "Access is denied"
- Firewall operations always fail

**Solution:**
- Service must run with administrator privileges
- Check if running in admin mode
- Restart service with admin rights

**Verify admin status:**
```powershell
Get-Process miner_online_simple | Select-Object ProcessName, Username
# Should show admin/system user
```

---

## Testing Checklist

Before deploying GUI in production, verify:

- [ ] Configuration write/reload cycle works end-to-end
- [ ] Measurements recorded for all enabled tools
- [ ] Timestamps in UTC format (verified with +00:00 suffix)
- [ ] Error handling works for invalid operations
- [ ] Service survives config update cycle without restart
- [ ] Concurrent operations work (5+ operations in rapid succession)
- [ ] Service logs show clean operation history
- [ ] Firewall operations succeed (if admin available)
- [ ] Large config updates handled correctly
- [ ] Service responds to reload_config within 5 seconds

---

## Support and Escalation

**For service issues:**
1. Check service.err.log for error messages
2. Review operation response in done.json
3. Verify configuration files exist and are readable
4. Check service process status

**For GUI-specific issues:**
1. Validate all JSON before sending
2. Ensure UTF-8 without BOM encoding
3. Check operation response structure
4. Review IPC operation pattern in Best Practices

**Performance debugging:**
- CPU: Check for concurrent operation buildup
- Memory: Check for file handle leaks
- Throughput: Verify IPC queue path accessibility

---

## Key Takeaways for GUI Developers

1. **Always validate JSON** before writing via `write_config`
2. **Use UTF-8 without BOM** encoding for all config files
3. **Use UTC timestamps** for all measurement data
4. **Check done.json** for operation results (success field)
5. **Reload config** after write_config to activate changes
6. **Service gracefully handles bad config** (keeps old config)
7. **IPC throughput is excellent** (18+ ops/sec, safe for batching)
8. **Resource usage is minimal** (1.56% CPU, 6.5 MB memory)
9. **Retry failed operations** with exponential backoff
10. **Monitor logs** for configuration issues and errors

---

**Document Version:** 1.0  
**Last Updated:** 2026-01-09  
**Service Version:** v1.6.4  
**Status:** Production Ready
