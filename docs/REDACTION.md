# Data Redaction for Autonomys Auto Drive

## Problem & Solution

Autonomys Auto Drive is **publicly visible**. To protect competitive advantage, data is automatically redacted before upload. Full-resolution data stays local and is sold privately through tiered pricing.

## Redaction Pipeline

```
Raw CSV (10-min intervals)
  → Hourly Aggregation
  → Redaction Applied
  → Local Parquet (redacted hex folder)
  → Autonomys Auto Drive (public, redacted)
```

Original data (res-7) is **never uploaded**. Only redacted data (res-5/res-4) reaches Autonomys.

## Redaction Levels

| Level | Location | Measurements | Identifiers | Use Case |
|-------|----------|--------------|-------------|----------|
| **minimal** | res-7 (~5 km) | Rounded to 1 decimal | Kept | Low-sensitivity data |
| **standard** (default) | res-5 (~252 km) | +5% noise | Removed | Recommended for most data |
| **full** | res-4 (~1,300 km) | +10% noise, bucketed | Removed | Maximum privacy |

### Per-Measurement Type (Standard Level)

| Type | Redaction Applied |
|------|------------------|
| **Bandwidth** | 5% noise on dl/ul, interface name removed |
| **Satellite** | Lat/lon removed, sat count and HDOP kept |
| **Radiation** | 3% noise on CPM/uSv/mR |
| **Decibel** | Rounded to 1 decimal |
| **AEM** | Rounded to 1 decimal |

## Usage

### Default (Automatic Standard Redaction)

```python
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

results = process_yesterday_to_autonomys(
    miner_code="BM",
    hex_id="871f90151ffffff",
    upload_to_cloud=True
)
```

### Custom Level

```python
from measurements.autonomys_orchestrator import process_daily_csv_to_autonomys

process_daily_csv_to_autonomys(
    miner_code="BM",
    measurement_type="bandwidth",
    hex_id="871f90151ffffff",
    date_str="20260120",
    redaction_level='full',  # or 'minimal'
    upload_to_cloud=True
)
```

## File Structure

```
C:\ProgramData\FryNetworks\autonomys-measurements\
├── res-7\                         # ORIGINAL (stays local, never uploaded)
│   └── 871f90151ffffff\           # Exact hex (res-7)
│       └── bandwidth\hourly\
│           └── 2026-01-20.parquet
│
└── res-5\                         # REDACTED (uploaded to Autonomys)
    └── 851f901ffffffff\           # Coarsened hex (res-5, 50x larger area)
        ├── manifest.json          # Redacted manifest
        └── bandwidth\hourly\
            ├── 2026-01-20.parquet
            └── 2026-01-20.meta.json
```

## What Gets Protected

| Data | Before | After (Standard) |
|------|--------|------------------|
| Location | 871f90151ffffff (res-7) | 851f901ffffffff (res-5) |
| Download | 85.327 Mbps | 84.1 Mbps (+/-5%) |
| GPS | 40.712776, -74.005974 | 40.7, -74.0 |
| Interface | "wlan0" | "REDACTED" |
| Install ID | "550e8400-..." | Removed |

## Business Model

| Tier | Source | Redaction | Price |
|------|--------|-----------|-------|
| Free | Autonomys (public) | Standard (res-5) | $0 |
| Professional | Private delivery | Minimal (res-7) | $50-100/hex/month |
| Enterprise | Private delivery | None (exact GPS) | $500-1000/hex/month |

Autonomys serves as a **discovery platform** - buyers find redacted previews, then contact for full data.

## Testing

```bash
python test_redaction.py
```

## Implementation

- **Redactor:** [`measurements/autonomys_redactor.py`](../measurements/autonomys_redactor.py) - `DataRedactor` class, `REDACTION_LEVELS` config
- **Pipeline:** [`measurements/autonomys_orchestrator.py`](../measurements/autonomys_orchestrator.py) - Applies redaction after aggregation
- **Integration:** [`AUTONOMYS_INTEGRATION.md`](AUTONOMYS_INTEGRATION.md) - Full Autonomys technical reference
