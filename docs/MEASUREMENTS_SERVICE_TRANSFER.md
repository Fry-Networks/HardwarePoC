# Measurements Task Transfer to Service - Python Scripts List

This document lists the Python scripts that need to be transferred to the autonomous service to handle the "measurements" scheduled task responsibility, organized by miner type and function.

---

## Overview

**Current State:**  
- The GUI Worker (worker.py) collects sensor data and emits measurements via Qt signals
- The service should autonomously read service-written measurements from `ProgramData/measurements/latest.json`
- Measurement collection responsibility transitioning from GUI to service

**Goal:**  
Transfer measurement collection logic to the autonomous service so it operates independently of the GUI.

**GUI Responsibilities (KEPT):**
- LiveData UI panels (bandwidth, AEM POI, satellite, decibel, radiation graphs)
- Config tools (decibel device selector, serial port selector, radiation device selector)
- PoC application UI panels (Mysterium, Bright, Honeygain, Presearch, Space Acres, Diiisco config/status)
- Reading and displaying measurements from service-written `measurements/latest.json`

**Service Responsibilities (TRANSFERRED):**
- Autonomous collection of measurement data from all sensors and APIs
- Writing plaintext measurements to `measurements/latest.json`
- Scheduled polling (10-minute intervals for periodic tasks)
- Serial port communication (GPS, Geiger)
- API calls to PoC applications

---

## Core Service Measurement Modules

### 1. **Service Infrastructure** (All Miner Types)
These files handle service communication, configuration, and measurement I/O:

| Module | Path | Purpose |
|--------|------|---------|
| `measurement_reader.py` | `miner_GUI/utils/measurement_reader.py` | Read plaintext measurements written by service from `measurements/latest.json` |
| `data.py` | `miner_GUI/utils/data.py` | Utility functions for data directory resolution, config reading |
| `encryption.py` | `miner_GUI/utils/encryption.py` | Key derivation and encryption utilities for measurement encryption |
| `ops_queue_client.py` | `miner_GUI/utils/ops_queue_client.py` | IPC communication with service via ops_queue (write_measurement operation) |

---

## Measurement Collection Modules by Miner Type

**Note:** UI panels and config tools remain in the GUI. Only the data collection logic (methods like `_sample_bandwidth()`, `_real_bandwidth_test()`, etc.) transfers to the service.

### 2. **BM (Block Miner) / Bandwidth Miner**
Collects network bandwidth measurements (download/upload speeds):

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `bandwidth.py` | `miner_GUI/LiveData/bandwidth.py` | **KEEP in GUI** | Display DL/UL progress bars |
| **Collection Logic** | `worker.py` methods | `miner_GUI/services/worker.py` | **TRANSFER to Service** | `_sample_bandwidth()` and `_real_bandwidth_test()` |

**Measurement Fields:**
```json
{
  "dl": 125.42,        // Download Mbps
  "ul": 23.15,         // Upload Mbps
  "iface": "Ethernet"  // Interface name
}
```

**Collection Logic:**  
- Real bandwidth test every 10 minutes
- Logs to daily CSV: `logs/bm_realtest_YYYYMMDD.csv`

---

### 3. **AEM (Asset Extraction Mining)**
Collects Proof of Installation (PoI) status from daily status JSON:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `aem.py` | `miner_GUI/LiveData/aem.py` | **KEEP in GUI** | Display AEM/PoI status |
| **Collection Logic** | `worker.py` method | `miner_GUI/services/worker.py` (lines 168-201) | **TRANSFER to Service** | `_sample_aem()` reads PoI from status JSON |

**Measurement Fields:**
```json
{
  "poi": true          // Proof of Installed state (boolean)
}
```

**Collection Logic:**  
- Reads from service-written `ProgramData/status/status-YYYYMMDD.json`
- Extracts 'PoI' key (case-insensitive)

---

### 4. **Satellite (GNSS)**
Collects GPS/GNSS satellite data from serial port:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `satellite.py` | `miner_GUI/LiveData/satellite.py` | **KEEP in GUI** | Display satellite count and GPS fix |
| **Config Tool** | Port selector in UI | `miner_GUI/ui/main_window.py` | **KEEP in GUI** | User selects GPS serial port |
| **Collection Logic** | `worker.py` method | `miner_GUI/services/worker.py` (lines 91-92, 337-391) | **TRANSFER to Service** | `_sample_gnss()` parses NMEA from serial port |

**Measurement Fields:**
```json
{
  "sats": 8,           // Satellites in view
  "fix": "GPS",        // Fix type: NONE, GPS, DGPS, etc.
  "lat": 37.7749,      // Latitude (optional)
  "lon": -122.4194,    // Longitude (optional)
  "alt": 45.2,         // Altitude meters (optional)
  "hdop": 1.2          // Horizontal dilution (optional)
}
```

**Collection Logic:**  
- Parses NMEA sentences (GGA, RMC) from serial port
- Extracts satellite count, fix quality, coordinates

---

### 5. **Radiation (Geiger Counter)**
Collects radiation measurements from serial port:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `geiger.py` | `miner_GUI/LiveData/geiger.py` | **KEEP in GUI** | Display CPM and dose rate bar graphs |
| **Config Tool** | Device selector in UI | `miner_GUI/ui/main_window.py` | **KEEP in GUI** | User selects Geiger serial port and baud rate |
| **Collection Logic** | `worker.py` method | `miner_GUI/services/worker.py` (lines 93-94, 392-435) | **TRANSFER to Service** | `_sample_geiger()` reads Geiger counter from serial port |

**Measurement Fields:**
```json
{
  "cpm": 28.0,         // Counts per minute
  "usv": 0.182,        // Microsieverts
  "usv_hour": 0.182,   // μSv/h
  "mr": 0.0182,        // Milliroentgen
  "cps": 1.2           // Counts per second (optional)
}
```

**Collection Logic:**  
- Reads Geiger counter data from serial port (various protocols)
- Calculates dose rates from CPM

---

### 6. **Decibel (Audio)**
Collects audio level measurements from sound device:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `decibel.py` | `miner_GUI/LiveData/decibel.py` | **KEEP in GUI** | Display audio level dBFS bar graph |
| **Config Tool** | Audio device selector | `miner_GUI/ui/main_window.py` | **KEEP in GUI** | User selects audio input device |
| **Collection Logic** | `worker.py` methods | `miner_GUI/services/worker.py` (lines 75-86, 436-495) | **TRANSFER to Service** | `_sample_decibel()` and `_run_decibel_stream()` sample audio |

**Measurement Fields:**
```json
{
  "dbfs": -42.5        // Decibels full scale
}
```

**Collection Logic:**  
- Persistent audio stream to avoid device blink on each sample
- Falls back to periodic sampling on error

---

## PoC Application Controllers (Mining Apps)

These handle specific mining application services and need measurement emission capability:

**Note:** UI panels and config tools remain in GUI. Only the collection/polling logic transfers to service.

### 7. **Mysterium (VPN/Proxy)**
Collects earnings and status from Mysterium API:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `mysterium_panel.py` | `miner_GUI/ui/widgets/mysterium_panel.py` | **KEEP in GUI** | Display Mysterium status and earnings |
| **Config/Status** | `mysterium.py` controller | `miner_GUI/services/mysterium.py` | **KEEP UI methods, TRANSFER collection** | UI keeps install/start/stop/config methods; service takes `refresh_status()` polling |
| **UI Integration** | `mysterium.py` helpers | `miner_GUI/ui/helpers/integrations/mysterium.py` | **KEEP in GUI** | Helper functions for Mysterium UI operations |

---

### 8. **Honeygain (Bandwidth Sharing)**
Collects earnings and status from Honeygain:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `honeygain_panel.py` | `miner_GUI/ui/widgets/honeygain_panel.py` | **KEEP in GUI** | Display Honeygain status and earnings |
| **Config/Status** | `honeygain.py` controller | `miner_GUI/services/honeygain.py` | **KEEP UI methods, TRANSFER collection** | UI keeps config methods; service takes polling logic |
| **UI Integration** | `honeygain.py` helpers | `miner_GUI/ui/helpers/integrations/honeygain.py` | **KEEP in GUI** | Helper functions for Honeygain UI operations |

---

### 9. **Bright (Residential Proxy)**
Collects earnings and status from Bright:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `bright_panel.py` | `miner_GUI/ui/widgets/bright_panel.py` | **KEEP in GUI** | Display Bright status and earnings |
| **Config/Status** | `bright.py` controller | `miner_GUI/services/bright.py` | **KEEP UI methods, TRANSFER collection** | UI keeps config methods; service takes polling logic |
| **UI Integration** | `bright.py` helpers | `miner_GUI/ui/helpers/integrations/bright.py` | **KEEP in GUI** | Helper functions for Bright UI operations |

---

### 10. **Presearch**
Collects earnings and status from Presearch:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `presearch_panel.py` | `miner_GUI/ui/widgets/presearch_panel.py` | **KEEP in GUI** | Display Presearch status and earnings |
| **Config/Status** | `presearch.py` controller | `miner_GUI/services/presearch.py` | **KEEP UI methods, TRANSFER collection** | UI keeps config methods; service takes polling logic |
| **UI Integration** | `presearch.py` helpers | `miner_GUI/ui/helpers/integrations/presearch.py` | **KEEP in GUI** | Helper functions for Presearch UI operations |

---

### 11. **Space Acres (Storage)**
Collects earnings and status from Space Acres:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `space_acres_panel.py` | `miner_GUI/ui/widgets/space_acres_panel.py` | **KEEP in GUI** | Display Space Acres status and earnings |
| **Config/Status** | `space_acres.py` controller | `miner_GUI/services/space_acres.py` | **KEEP UI methods, TRANSFER collection** | UI keeps config methods; service takes polling logic |
| **UI Integration** | `space_acres.py` helpers | `miner_GUI/ui/helpers/integrations/space_acres.py` | **KEEP in GUI** | Helper functions for Space Acres UI operations |

---

### 12. **Diiisco**
Collects earnings and status from Diiisco:

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `diiisco_panel.py` | `miner_GUI/ui/widgets/diiisco_panel.py` | **KEEP in GUI** | Display Diiisco status and earnings |
| **Config/Status** | `diiisco.py` controller | `miner_GUI/services/diiisco.py` | **KEEP UI methods, TRANSFER collection** | UI keeps config methods; service takes polling logic |
| **UI Integration** | `diiisco.py` helpers | `miner_GUI/ui/helpers/integrations/diiisco.py` | **KEEP in GUI** | Helper functions for Diiisco UI operations |

---

## Core Infrastructure & Configuration

### Keep in GUI:
- `measurement_reader.py` - **READ** measurements from service-written `latest.json`
- All UI panels and widgets (`LiveData/`, `ui/widgets/`)
- All UI integration helpers (`ui/helpers/integrations/`)
- Config tools and UI elements
- `device_config.py` - User device configuration UI
- `status_week.py` - Display weekly status aggregation

### Transfer to Service:
- `encryption.py` - Service-side encryption for historical measurements
- `ops_queue_client.py` - Service uses this for IPC responses
- Measurement collection logic from `worker.py` methods
- Polling logic from service controllers (mysterium.py, honeygain.py, etc.)

---

## Measurement Collection Summary by Type

| Miner Type | Collection Method | Interval | Source Module |
|------------|-------------------|----------|----------------|
| **BM** | Real bandwidth test (DL/UL Mbps) | 10 minutes | `worker.py` |
| **AEM** | Read PoI from status JSON | On-demand | `worker.py` |
| **Satellite** | NMEA parsing from GPS serial | Continuous | `worker.py` |
| **Radiation** | Geiger counter serial reads | Continuous | `worker.py` |
| **Decibel** | Audio device sampling (dBFS) | Continuous | `worker.py` |
| **Mysterium** | TequilAPI polling | Periodic | `mysterium.py` |
| **Honeygain** | Honeygain API polling | Periodic | `honeygain.py` |
| **Bright** | Bright API polling | Periodic | `bright.py` |
| **Presearch** | Presearch node API polling | Periodic | `presearch.py` |
| **SpaceAcres** | Space Acres API polling | Periodic | `space_acres.py` |
| **Diiisco** | Diiisco API polling | Periodic | `diiisco.py` |

---

## Implementation Notes

### Key Responsibilities for Service:
1. **Run scheduled collection task** for each miner type
2. **Write plaintext JSON** to `ProgramData/measurements/latest.json` after each collection cycle
3. **Maintain encryption** for historical measurements if needed
4. **Poll APIs** for PoC applications (Mysterium, Honeygain, etc.)
5. **Read serial ports** for hardware sensors (GPS, Geiger, audio)
6. **Calculate measurements** and aggregate into standard format
7. **Handle failures gracefully** - missing sensors, unreachable APIs, etc.

### File Output Locations:
- **Plaintext Current:** `%PROGRAMDATA%\FryNetworks\miner-{CODE}\measurements\latest.json`
- **Encrypted Historical:** `%PROGRAMDATA%\FryNetworks\miner-{CODE}\measurements\measurements_*.enc`
- **Daily CSV Logs:** `%APPDATA%\FryNetworks\miner-{CODE}\logs\{device}_realtest_YYYYMMDD.csv`

### Service -> GUI Communication:
- GUI reads `measurements/latest.json` via `read_latest_measurements()` from `measurement_reader.py`
- Service uses `ops_queue` for config changes via `write_config` operation
- No GUI -> Service measurement writes; service is autonomous

---

## Dependencies to Transfer

### Python Packages:
- `PySide6` (Qt framework) - for UI panels, may not be needed in headless service
- `sounddevice` - for audio sampling (Decibel)
- `serial` (pyserial) - for GPS and Geiger serial port communication
- `cryptography` - for encryption key derivation and measurement encryption
- `requests` - for HTTP API calls to PoC applications

### Environment:
- Windows: NSSM or Task Scheduler for background service
- Configuration: `miner_config.json`, service access to ProgramData directories

---

## Next Steps

1. **Extract collection methods** from `worker.py` into service-compatible modules (no Qt dependencies)
2. **Implement service measurement collection loop** - run every 10 minutes
3. **Extract API polling logic** from Mysterium, Honeygain, Bright, Presearch, Space Acres, Diiisco
4. **Create service measurement writer** - emit `measurements/latest.json` after each collection
5. **Port serial communication** (GPS, Geiger) to service context (no Qt)
6. **Port audio sampling** (Decibel) to service context (sounddevice without Qt)
7. **Update GUI workflow** - remove measurement write operations, keep all UI display logic
8. **Test end-to-end** - Verify GUI reads from service-written measurements, all UI panels update correctly
9. **Deploy** - GUI and service run independently; GUI displays what service collects
