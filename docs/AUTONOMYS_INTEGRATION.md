# Autonomys Auto Drive Integration

This document explains the Autonomys Auto Drive integration for FryNetworks measurement data.

## Overview

The Autonomys integration enables decentralized storage and monetization of measurement data through a hierarchical folder structure optimized for different pricing tiers and data granularity levels.

## Architecture

### Data Flow

```
CSV Measurements (10min intervals)
         ↓
MongoDB Upload (existing) ← Dual Write → Autonomys Processing
         ↓                                        ↓
   Backend API                          Hourly Aggregation
                                                 ↓
                                        Local Parquet Files
                                                 ↓
                                        Autonomys Auto Drive
```

### Folder Structure

```
autonomys-measurements/
├── metadata.json                    # Network-wide manifest
├── res-7/                          # Resolution 7 hexagons
│   └── 871f90151ffffff/           # Individual hex
│       ├── manifest.json          # Hex metadata
│       ├── bandwidth/
│       │   ├── hourly/
│       │   │   ├── 2026-01-20.parquet
│       │   │   └── 2026-01-20.meta.json
│       │   └── daily/
│       │       └── 2026-01.parquet
│       ├── satellite/
│       ├── radiation/
│       ├── decibel/
│       └── aem/
└── index.json
```

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements-autonomys.txt
```

Required packages:
- `h3>=4.0.0` - H3 hexagon geospatial indexing
- `pandas>=2.0.0` - Data processing
- `pyarrow>=14.0.0` - Parquet file format
- `boto3>=1.34.0` - S3-compatible client for Autonomys

### 2. Get Autonomys API Key

1. Visit [https://ai3.storage](https://ai3.storage)
2. Sign in with Google, Discord, or GitHub
3. Generate an API key
4. Set environment variable:

```bash
# Windows
set AUTONOMYS_API_KEY=your-api-key-here

# Linux/Mac
export AUTONOMYS_API_KEY=your-api-key-here
```

## Usage

### Basic Usage - Process Yesterday's Data

```python
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

# Process yesterday's complete data
results = process_yesterday_to_autonomys(
    miner_code="IRM",
    hex_id="871f90151ffffff",
    install_id="550e8400-e29b-41d4-a716-446655440000",
    upload_to_cloud=True
)

print(results)  # {'radiation': True}
```

### Test with Today's Data

```python
from measurements.autonomys_orchestrator import process_today_to_autonomys

# Test with partial data (don't upload)
results = process_today_to_autonomys(
    miner_code="BM",
    hex_id="871f90151ffffff",
    upload_to_cloud=False  # Preview only
)
```

### Backfill Historical Data

```python
from measurements.autonomys_orchestrator import backfill_autonomys_data

# Backfill January 2026
results = backfill_autonomys_data(
    miner_code="BM",
    hex_id="871f90151ffffff",
    start_date="20260101",
    end_date="20260131",
    upload_to_cloud=True
)

print(results)  # {'bandwidth': 31}  # 31 days processed
```

### Interactive Test Script

```bash
python test_autonomys_integration.py
```

This script will:
1. Check dependencies
2. Process today's data locally
3. Show generated files
4. Optionally upload to Auto Drive

## Integration with Existing Code

### Option 1: Add to Measurement Daemon

Add a daily job to your measurement daemon:

```python
# In your measurement daemon's daily job (runs at 00:05 UTC)
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

def daily_autonomys_job():
    """Process yesterday's data to Autonomys."""
    results = process_yesterday_to_autonomys(
        miner_code=MINER_CODE,
        hex_id=config.get('hex_id'),
        install_id=config.get('install_id'),
        upload_to_cloud=True
    )
    log.info("Autonomys processing results: %s", results)
```

### Option 2: Scheduled Task

Create a Windows scheduled task:

```bash
# Run daily at 00:30 UTC
schtasks /create /tn "AutonomysUpload" /tr "python C:\path\to\autonomys_daily_job.py" /sc daily /st 00:30
```

Create `autonomys_daily_job.py`:

```python
#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from config_profile import MINER_CODE
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

# Read config
import json
config_path = Path("C:/ProgramData/FryNetworks/config.json")
with open(config_path) as f:
    config = json.load(f)

# Process yesterday's data
process_yesterday_to_autonomys(
    miner_code=MINER_CODE,
    hex_id=config['hex_id'],
    install_id=config.get('install_id'),
    upload_to_cloud=True
)
```

## Data Monetization

### Pricing Tiers

The folder structure supports multiple pricing tiers:

1. **Hourly Data** (`hourly/` folder)
   - 60-minute aggregates (avg, min, max)
   - Base price: $50/month per hex
   - Use case: Professional analytics, telecom planning

2. **Daily Data** (`daily/` folder)
   - Daily summaries
   - Base price: $15/month per hex
   - Use case: Researchers, urban planning

### Type Multipliers

Different measurement types have different values:

- Bandwidth: 1.0× (base)
- Satellite: 1.2×
- Decibel: 1.0×
- Radiation: 1.5×
- AEM: 2.0×

### Example Products

**Product: Urban Noise Mapping**
- Type: Decibel measurements
- Granularity: Daily aggregates
- Coverage: 50 hexes in downtown area
- Price: $15/month × 1.0 × 50 = $750/month

**Product: Telecom Bandwidth Analytics**
- Type: Bandwidth measurements
- Granularity: Hourly aggregates
- Coverage: 10 hexes in commercial district
- Price: $50/month × 1.0 × 10 = $500/month

## File Formats

### Parquet Files

Efficient columnar storage with compression. Example schema for bandwidth:

```
date               datetime64
hour               int64 (0-23)
dl_avg_mbps        float64
dl_min_mbps        float64
dl_max_mbps        float64
ul_avg_mbps        float64
ul_min_mbps        float64
ul_max_mbps        float64
sample_count       int64
iface              string
```

### Metadata Files

JSON sidecar files with daily summaries:

```json
{
  "date": "2026-01-20",
  "hex_id": "871f90151ffffff",
  "measurement_type": "bandwidth",
  "aggregation": "hourly",
  "hours": 24,
  "total_samples": 144,
  "summary": {
    "dl_mbps": {
      "day_avg": 85.3,
      "day_min": 12.1,
      "day_max": 124.7,
      "peak_hour": 14
    }
  }
}
```

### Manifest Files

Per-hex manifest tracking all measurements:

```json
{
  "hex_id": "871f90151ffffff",
  "resolution": 7,
  "center": {"lat": 40.7128, "lon": -74.0060},
  "coverage": {
    "start": "2025-06-01T00:00:00Z",
    "end": "2026-01-20T23:59:59Z",
    "total_days": 234
  },
  "measurements": {
    "bandwidth": {
      "hourly_files": 234,
      "first_measurement": "2025-06-01T00:00:00Z",
      "last_measurement": "2026-01-20T23:50:00Z",
      "total_samples": 33696
    }
  }
}
```

## Troubleshooting

### Missing Dependencies

```
ImportError: No module named 'h3'
```

**Solution:** Install dependencies:
```bash
pip install -r requirements-autonomys.txt
```

### Invalid Hex ID

```
ValueError: Invalid H3 hex_id
```

**Solution:** Verify your hex ID is valid H3 format. Check with:
```python
import h3
print(h3.h3_is_valid("871f90151ffffff"))  # Should return True
print(h3.h3_get_resolution("871f90151ffffff"))  # Should return 7
```

### No CSV Data Found

```
No data found for bandwidth on 20260120
```

**Solution:** Check that:
1. CSV files exist in `C:\ProgramData\FryNetworks\miner-{CODE}\measurements\`
2. Files are named `{type}_real_{YYYYMMDD}.csv`
3. Files contain data (not empty)

### Autonomys Upload Fails

```
Failed to upload to Autonomys: ClientError
```

**Solution:** Check:
1. `AUTONOMYS_API_KEY` environment variable is set
2. API key is valid (test at https://ai3.storage)
3. Network connectivity to `https://public.auto-drive.autonomys.xyz`

## API Reference

See the module docstrings for detailed API documentation:

- [autonomys_writer.py](../measurements/autonomys_writer.py) - Folder structure and manifests
- [autonomys_aggregator.py](../measurements/autonomys_aggregator.py) - Hourly aggregation logic
- [autonomys_uploader.py](../measurements/autonomys_uploader.py) - S3-compatible upload client
- [autonomys_orchestrator.py](../measurements/autonomys_orchestrator.py) - Main integration orchestrator

## Resources

- [Autonomys Auto Drive Documentation](https://develop.autonomys.xyz/sdk/auto-drive)
- [H3 Hexagon System](https://h3geo.org/)
- [Apache Parquet Format](https://parquet.apache.org/)
- [AI3 Storage Dashboard](https://ai3.storage)
