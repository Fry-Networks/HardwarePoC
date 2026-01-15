# Measurements Collection Package

Autonomous measurement collection for all miner types. Transferred from GUI worker to service.

## Overview

This package contains measurement collection logic that runs autonomously in the service (not the GUI). Each module handles a specific miner type's sensor/API data collection.

## Architecture

```
Service (miner_online_simple.py)
  ↓ calls every 10 minutes
measurements.collector.write_latest_measurements(miner_code)
  ↓ collects from appropriate module
measurements/{bandwidth|satellite|radiation|decibel|aem}.py
  ↓ writes plaintext JSON
%PROGRAMDATA%\FryNetworks\miner-{CODE}\measurements\latest.json
  ↑ reads for display
GUI (measurement_reader.py)
```

## Modules

### Core

- **`collector.py`** - Orchestrates collection across all miner types, writes latest.json
- **`__init__.py`** - Package exports

### Miner-Specific Collectors

| Module | Miner Types | Measurement | Dependencies |
|--------|-------------|-------------|--------------|
| **`bandwidth.py`** | BM | Download/upload speeds (Mbps) | requests, psutil |
| **`satellite.py`** | ISM, OSM | GPS/GNSS satellite data | pyserial |
| **`radiation.py`** | IRM | Geiger counter CPM/dose | pyserial |
| **`decibel.py`** | IDM, ODM | Audio level (dBFS) | sounddevice, numpy |
| **`aem.py`** | AEM | Proof of Installation (PoI) | - |

## Usage

### In Service Main Loop

```python
from measurements.collector import write_latest_measurements

# Every 10 minutes
miner_code = "BM"  # or ISM, IRM, etc.
write_latest_measurements(miner_code)
```

### Output Format

**File:** `%PROGRAMDATA%\FryNetworks\miner-{CODE}\measurements\latest.json`

```json
{
  "timestamp": "2026-01-14T16:45:00Z",
  "miner_code": "BM",
  "group": "Bandwidth",
  "measurement": {
    "dl": 125.42,
    "ul": 23.15,
    "iface": "Ethernet"
  }
}
```

## Measurement Schemas

### BM (Bandwidth)
```json
{
  "dl": 125.42,        // Download Mbps
  "ul": 23.15,         // Upload Mbps
  "iface": "Ethernet"  // Network interface
}
```

### ISM/OSM (Satellite)
```json
{
  "sats": 8,           // Satellites in view
  "fix": "GPS",        // Fix type: NONE, GPS, DGPS
  "lat": 37.7749,      // Latitude (optional)
  "lon": -122.4194,    // Longitude (optional)
  "alt": 45.2,         // Altitude meters (optional)
  "hdop": 1.2          // Horizontal dilution (optional)
}
```

### IRM (Radiation)
```json
{
  "cpm": 28.0,         // Counts per minute
  "usv": 0.182,        // Microsieverts
  "usv_hour": 0.182,   // μSv/h
  "mr": 0.0182,        // Milliroentgen
  "cps": 1.2           // Counts per second
}
```

### IDM/ODM (Decibel)
```json
{
  "dbfs": -42.5        // Decibels full scale
}
```

### AEM (AI Edge)
```json
{
  "poi": true          // Proof of Installation
}
```

## Configuration

Collectors read device configuration from `%PROGRAMDATA%\FryNetworks\config\device_config.json`:

```json
{
  "gps_port": "COM3",
  "gps_baud": 9600,
  "geiger_port": "COM4",
  "geiger_baud": 9600,
  "audio_device_idx": 1
}
```

This file is written by the GUI when users configure devices.

## Error Handling

- All collectors return `None` on failure
- Failures are logged but don't crash the service
- Missing dependencies (pyserial, sounddevice) are handled gracefully
- Missing config files use safe defaults or skip collection

## Integration with Service

### Replace Old Stub Functions

**Before (in miner_online_simple.py):**
```python
def _collect_and_write_measurements() -> None:
    # Stub - measurements come from GUI
    pass
```

**After:**
```python
def _collect_and_write_measurements() -> None:
    """Collect measurements autonomously in service."""
    from measurements.collector import write_latest_measurements
    try:
        write_latest_measurements(MINER_CODE)
    except Exception as e:
        log.error("Measurement collection failed: %s", e)
```

### Remove GUI Dependencies

- No Qt/PySide6 imports
- No GUI signals/slots
- Pure Python data collection
- Standalone operation

## Testing

```python
# Test individual collectors
from measurements import collect_bandwidth_measurement
result = collect_bandwidth_measurement()
print(result)  # {"dl": 125.42, "ul": 23.15, "iface": "Ethernet"}

# Test full collection
from measurements.collector import collect_all_measurements
data = collect_all_measurements("BM")
print(data)
```

## Dependencies

**Required:**
- Python 3.8+

**Optional (per miner type):**
- `requests` - BM bandwidth testing
- `psutil` - BM network interface detection
- `pyserial` - ISM/OSM/IRM serial port communication
- `sounddevice` - IDM/ODM audio sampling
- `numpy` - IDM/ODM audio processing
- `cryptography` - Encrypted historical measurements

## Migration Notes

### What Moved from GUI to Service

✅ **Measurement collection logic** - All `_sample_*()` methods from worker.py  
✅ **Serial port communication** - GPS and Geiger reading  
✅ **Audio sampling** - Decibel measurement  
✅ **Bandwidth testing** - Real download/upload tests  
✅ **Scheduled collection** - 10-minute intervals  

### What Stays in GUI

❌ **UI panels** - LiveData display (bandwidth.py, satellite.py, etc.)  
❌ **Config tools** - Device selectors, port pickers  
❌ **Visualization** - Charts, graphs, progress bars  
❌ **User interaction** - Settings, enable/disable controls  

### GUI Now Reads from Service

The GUI uses `measurement_reader.py` to read `measurements/latest.json`:

```python
from utils.measurement_reader import read_latest_measurements
data = read_latest_measurements()
# Update UI panels with data
```

## Files Created

```
measurements/
├── __init__.py           # Package exports
├── README.md             # This file
├── collector.py          # Orchestrator and writer
├── bandwidth.py          # BM bandwidth collection
├── satellite.py          # ISM/OSM GPS collection
├── radiation.py          # IRM Geiger collection
├── decibel.py            # IDM/ODM audio collection
└── aem.py                # AEM PoI collection
```

## Future Enhancements

- [ ] PoC application polling (Mysterium, Bright, Honeygain, etc.)
- [ ] Historical encrypted measurement archival
- [ ] Measurement validation and sanitization
- [ ] Retry logic for transient failures
- [ ] Device auto-detection for serial ports
- [ ] Multi-sensor aggregation (multiple Geigers, GPS units, etc.)
