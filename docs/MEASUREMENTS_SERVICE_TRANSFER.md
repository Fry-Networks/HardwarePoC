# Measurements Task Transfer to Service - Python Scripts List

This document lists the Python scripts that need to be transferred to the autonomous service to handle the "measurements" scheduled task responsibility, organized by miner type and function.

---

## Overview

**Current State:**  
- Service collection modules added (`measurement_sources.py`, `measurement_service.py`, `service_csv_writer.py`)
- Target architecture active: service writes CSV logs; GUI polls CSV (worker removed)
- LiveData panels now run on CSV polling timers; Data History reads service CSVs

**Goal:**  
Transfer ALL measurement collection and persistence to the autonomous service. GUI becomes pure display + configuration.

**GUI Responsibilities (DISPLAY + CONFIG ONLY):**
- ✅ **LiveData UI panels** (bandwidth, AEM POI, satellite, decibel, radiation graphs)
- ✅ **Config tools** (device selectors, port selectors, baud rate, tools settings)
- ✅ **Tools UI panels** (Mysterium, Bright, Honeygain, etc. config/control)
- ✅ **Poll service-written CSV files** (read last line every 2-10 seconds for live display)
- ✅ **Data History tab** (reads full CSV logs for historical charts)
- ✅ **"Open CSV" button** (opens service-written CSV files)
- ❌ **NO data collection** (no worker thread)
- ❌ **NO measurement writing** (service handles all I/O)

**Service Responsibilities (AUTONOMOUS COLLECTION + PERSISTENCE):**
- ✅ **Collect all sensor data** every 2-10 seconds (high-frequency for UI responsiveness)
- ✅ **Write daily CSV logs** (append-only, one file per sensor per day):
  - `logs/bm_YYYYMMDD.csv` (dl, ul every 2-10 sec)
  - `logs/satellite_YYYYMMDD.csv` (sats, fix, lat, lon, hdop every 10 sec)
  - `logs/radiation_YYYYMMDD.csv` (cpm, usv every 10 sec)
  - `logs/decibel_YYYYMMDD.csv` (dbfs every 2 sec)
  - `logs/aem_YYYYMMDD.csv` (poi events)
- ✅ **Send measurements to backend every 10 minutes with real bandwidth tests once by 10 minutes** (same behavior vs actual)
- ✅ **NO encryption**
- ✅ **Serial port communication** (GPS, Geiger)
- ✅ **API calls to tools** (Mysterium, Honeygain, etc.)
- ✅ **Runs independently** (survives GUI crash/restart)

---

## Core Infrastructure Modules

### 1. **GUI Utilities** (Display Only)
These files help GUI read service data and manage configuration:

| Module | Path | Purpose | Fate |
|--------|------|---------|------|
| `csv_reader.py` | `miner_GUI/utils/csv_reader.py` | **NEW** - Read last line from service CSV files for live display | ✅ Added |
| `device_config.py` | `miner_GUI/utils/device_config.py` | Manage device selections (ports, audio, etc.) | ✅ Keep |
| `data.py` | `miner_GUI/utils/data.py` | Data directory resolution, logging | ✅ Keep |
| `ops_queue_client.py` | `miner_GUI/utils/ops_queue_client.py` | IPC with service (config writes only) | ✅ Keep |
| ~~`measurement_reader.py`~~ | ~~`miner_GUI/utils/measurement_reader.py`~~ | ~~Read measurements/latest.json~~ | ❌ Remove (CSV replaces it) |
| ~~`encryption.py`~~ | ~~`miner_GUI/utils/encryption.py`~~ | ~~Encryption for measurements~~ | ❌ Remove (service sends to backend directly) |

### 1b. **Service Modules (New)**
| Module | Path | Purpose | Status |
|--------|------|---------|--------|
| `service_csv_writer.py` | `miner_GUI/services/service_csv_writer.py` | Append-only CSV writer for service logs | ✅ Added |
| `measurement_sources.py` | `miner_GUI/services/measurement_sources.py` | Qt-free sensor sampling helpers | ✅ Added |
| `measurement_service.py` | `miner_GUI/services/measurement_service.py` | Sensor loops + CSV logging + backend hook | ✅ Added |

---

## Measurement Collection Modules by Miner Type

**Note:** UI panels and config tools remain in the GUI. Only the data collection logic (methods like `_sample_bandwidth()`, `_real_bandwidth_test()`, etc.) transfers to the service.

### 2. **BM (Block Miner) / Bandwidth Miner**
Collects network bandwidth measurements (download/upload speeds):

| Component | Module | Path | Fate | Purpose |
|-----------|--------|------|------|---------|
| **UI Panel** | `bandwidth.py` | `miner_GUI/LiveData/bandwidth.py` | **KEEP in GUI** | Display DL/UL progress bars |
| **Collection Logic** | `measurement_service.py` + `measurement_sources.py` | `miner_GUI/services` | **TRANSFERRED to Service** | Bandwidth sampler + real test |

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
| **Collection Logic** | `measurement_service.py` + `measurement_sources.py` | `miner_GUI/services` | **TRANSFERRED to Service** | PoI sampler from status JSON |

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
| **Collection Logic** | `measurement_service.py` + `measurement_sources.py` | `miner_GUI/services` | **TRANSFERRED to Service** | GNSS sampler parses NMEA serial |

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
| **Collection Logic** | `measurement_service.py` + `measurement_sources.py` | `miner_GUI/services` | **TRANSFERRED to Service** | Geiger sampler reads serial |

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
| **Collection Logic** | `measurement_service.py` + `measurement_sources.py` | `miner_GUI/services` | **TRANSFERRED to Service** | Decibel sampler (audio) |

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

## Tool Controllers (Mining Apps)

These handle specific mining tool services and need measurement emission capability:

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
- All UI panels and widgets (`LiveData/`, `ui/widgets/`)
- All UI integration helpers (`ui/helpers/integrations/`)
- Config tools and UI elements
- `device_config.py` - User device configuration UI
- `status_week.py` - Display weekly status aggregation

### Transfer to Service:
- `encryption.py` - Removed from GUI; service sends directly without GUI encryption
- `ops_queue_client.py` - Service uses this for IPC responses
- Measurement collection logic formerly in `worker.py` now housed in `measurement_sources.py`/`measurement_service.py` (legacy worker removed)
- Polling logic from service controllers (mysterium.py, honeygain.py, etc.)

## Service Collection & Output by Type

| Sensor Type | Collection Method | Frequency | CSV Output | GUI Polling |
|------------|-------------------|-----------|------------|-------------|
| **BM** | Network adapter stats + real tests | Every 2-10 sec + 10 min for real tests | `logs/bm_YYYYMMDD.csv` | Read last line |
| **Satellite** | NMEA parsing from GPS serial | Every 10 sec | `logs/satellite_YYYYMMDD.csv` | Read last line |
| **Radiation** | Geiger counter serial reads | Every 10 sec | `logs/radiation_YYYYMMDD.csv` | Read last line |
| **Decibel** | Audio device sampling (dBFS) | Every 2 sec | `logs/decibel_YYYYMMDD.csv` | Read last line |
| **AEM** | Read PoI from service status | Every 10 min | `logs/aem_YYYYMMDD.csv` | Read last line |
| **Mysterium** | TequilAPI polling | Every 60 sec | `logs/mysterium_YYYYMMDD.csv` | Read last line |
| **Honeygain** | Honeygain API polling | Every 60 sec | `logs/honeygain_YYYYMMDD.csv` | Read last line |
| **Bright** | Bright API polling | Every 60 sec | `logs/bright_YYYYMMDD.csv` | Read last line |
| **Presearch** | Presearch API polling | Every 60 sec | `logs/presearch_YYYYMMDD.csv` | Read last line |
| **SpaceAcres** | Space Acres API polling | Every 60 sec | `logs/spaceacres_YYYYMMDD.csv` | Read last line |
| **Diiisco** | Diiisco API polling | Every 60 sec | `logs/diiisco_YYYYMMDD.csv` | Read last line |

---

## Implementation Notes

### Key Responsibilities for Service
- ✅ Run high-frequency collection (every 2-10 seconds for UI responsiveness)
- ✅ Write append-only CSV to `logs/{sensor}_YYYYMMDD.csv` after each collection
- ✅ Send to backend once every 10 minutes (same as actual behavior)
- ✅ Poll APIs for tools (Mysterium, Honeygain, etc.)
- ✅ Read serial ports for hardware sensors (GPS, Geiger)
- ✅ Sample audio devices for decibel measurements
- ✅ Handle failures gracefully (missing sensors, unreachable APIs, etc.)
- ✅ Rotate daily (new CSV file each day)

### File Output Locations:
- **Daily CSV Logs (Service writes, GUI reads):**
  - `%PROGRAMDATA%\FryNetworks\miner-{CODE}\logs\bm_YYYYMMDD.csv`
  - `%PROGRAMDATA%\FryNetworks\miner-{CODE}\logs\satellite_YYYYMMDD.csv`
  - `%PROGRAMDATA%\FryNetworks\miner-{CODE}\logs\radiation_YYYYMMDD.csv`
  - `%PROGRAMDATA%\FryNetworks\miner-{CODE}\logs\decibel_YYYYMMDD.csv`
  - `%PROGRAMDATA%\FryNetworks\miner-{CODE}\logs\aem_YYYYMMDD.csv`
  - `%PROGRAMDATA%\FryNetworks\miner-{CODE}\logs\{poc_app}_YYYYMMDD.csv`

### Service -> GUI Communication:
- ✅ GUI polls CSV files (reads last line) every 2-10 seconds for live display
- ✅ GUI reads full CSV for Data History tab
- ✅ Service uses `ops_queue` for receiving config changes from GUI
- ❌ No JSON files (CSV serves both live + history)
- ❌ No encryption 

### Dependencies to Transfer
- `sounddevice` - audio sampling (Decibel)
- `serial` (pyserial) - GPS and Geiger serial ports
- `psutil` - bandwidth sampling
- `requests` - HTTP API calls to tools applications
- `cryptography` - only if encryption is retained elsewhere (not used in CSV path)

## Next Steps - Service Implementation

### Phase 1: Extract Collection Logic (No Qt Dependencies)
1. ✅ Move sensor samplers into `measurement_sources.py` (no Qt)
2. ✅ Retire legacy `worker.py` after GUI polling cutover (file deleted)

### Phase 2: Service CSV Writer
1. ✅ Add `service_csv_writer.py` (append-only daily CSV)
2. ✅ Date-based rotation via file naming

### Phase 3: Service Collection Loop
1. ✅ Add `measurement_service.py` (threads + stop event)
2. ✅ Append to CSV after each collection
3. ✅ Backend sender hook (same as actual behavior)
4. 🔄 Reload device config when files change (currently read once)

### Phase 4: GUI Updates (Display-Only)
1. ✅ `csv_reader.py` for last-line polling
2. ✅ Replace worker thread with CSV polling in LiveData panels
3. ✅ Remove `worker.py` (legacy thread deleted)
4. ✅ Update Data History to reuse `csv_reader.read_full_csv()`
5. ✅ Remove `measurement_reader.py` (JSON) after cutover
6. ✅ Remove `encryption.py` from GUI (file deleted; service handles backend directly)

### Phase 5: Testing
1. ✅ Testing guide created: [PHASE_5_TESTING_GUIDE.md](PHASE_5_TESTING_GUIDE.md)
2. ✅ **Level 1: Sanity Checks COMPLETE**
   - ✅ Module compilation verified
   - ✅ CSV reader type mismatch fixed (`Union[str, Path]`)
   - ✅ services/__init__.py cleaned (worker import removed)
   - ✅ Mock CSV data created (BM, Satellite)
   - Tests: `test_imports_level1.py`, `test_csv_reader_level1.py`
3. ✅ **Level 2: GUI Module Imports COMPLETE**
   - ✅ measurement_display helpers import
   - ✅ DataHistoryWidget UI component imports
   - ✅ LiveData panel components (BandwidthPanel, SatellitePanel, etc.) import
   - ✅ PySide not instantiated (unit test safe)
   - Test: `test_gui_imports_level2.py`
4. ✅ **Level 3: LiveData Polling COMPLETE**
   - ✅ CSV polling without GUI event loop works
   - ✅ Data type conversions for display
   - ✅ Multi-sensor polling functional
   - ✅ Callback pattern ready for GUI integration
   - Test: `test_livedata_polling_level3.py`
5. ✅ **Level 4: Data History CSV Loading COMPLETE**
   - ✅ Full CSV data loads for charting
   - ✅ Timestamp parsing and ordering verified
   - ✅ Chart data format ready (min/max/count)
   - ✅ Date filtering capability verified
   - ✅ Data integrity confirmed (no missing fields)
   - Test: `test_datahistory_level4.py`
6. ✅ **Level 5: GUI Integration Tests (Next)**
   - ✅ Start actual GUI with mock CSV data
   - 🔄 Verify LiveData displays current values
   - ✅ Verify Data History shows chart
   - ✅ Test date navigation and filtering
7. ⬜ **Level 6-7: Resilience & Performance (Optional)**
   - [ ] Crash recovery scenarios
   - [ ] Large dataset handling
   - [ ] Concurrent polling

### Phase 6: Deployment
1. ⬜ Package service (NSSM/Task Scheduler) using new modules
2. ⬜ GUI runs on-demand (display only)
3. ⬜ Backend receives real-time measurements from service
4. ⬜ Add monitoring/log shipping for service errors
4. **Create service measurement writer** - emit `measurements/latest.json` after each collection
5. **Port serial communication** (GPS, Geiger) to service context (no Qt)
6. **Port audio sampling** (Decibel) to service context (sounddevice without Qt)
7. **Update GUI workflow** - remove measurement write operations, keep all UI display logic
8. **Test end-to-end** - Verify GUI reads from service-written measurements, all UI panels update correctly
9. **Deploy** - GUI and service run independently; GUI displays what service collects
