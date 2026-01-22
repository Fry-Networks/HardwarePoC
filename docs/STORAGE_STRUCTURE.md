# Storage Structure: Original vs Redacted Data

## Overview

The system maintains **two separate storage locations**:

1. **Original measurements** - Full precision, never uploaded
2. **Redacted measurements** - Privacy-protected, uploaded to Autonomys

---

## Storage Paths

### Path 1: Original Measurements (Local Only)

**Location:** `C:\ProgramData\FryNetworks\measurements\`

**Structure:**
```
C:\ProgramData\FryNetworks\measurements\
|
+-- decibel\
|   +-- hourly\
|       +-- 2026-01-22.parquet
|       +-- 2026-01-23.parquet
|       +-- ...
|
+-- bandwidth\
|   +-- hourly\
|       +-- 2026-01-22.parquet
|
+-- radiation\
|   +-- hourly\
|       +-- 2026-01-22.parquet
|
+-- satellite\
    +-- hourly\
        +-- 2026-01-22.parquet
```

**Characteristics:**
- ✅ **Full precision** measurements (dbfs_avg: -41.77, not -41.8)
- ✅ **Min and max values** for each hour
- ✅ **Exact location** (res-7 hex ID stored in metadata)
- ❌ **Never uploaded** to Autonomys
- 💰 Used for **Professional/Enterprise tier sales**

---

### Path 2: Redacted Measurements (Autonomys Upload)

**Location:** `C:\ProgramData\FryNetworks\autonomys-measurements\`

**Structure:**
```
C:\ProgramData\FryNetworks\autonomys-measurements\
|
+-- res-5\                          <- Coarsened resolution
    +-- 851f901ffffffff\            <- Redacted hex (50x larger area)
        |
        |-- manifest.json           <- Redacted metadata
        |
        +-- decibel\
        |   +-- hourly\
        |       +-- 2026-01-22.parquet
        |       +-- 2026-01-22.meta.json
        |
        +-- bandwidth\
        |   +-- hourly\
        |       +-- 2026-01-22.parquet
        |
        +-- radiation\
            +-- hourly\
                +-- 2026-01-22.parquet
```

**Characteristics:**
- ✅ **Rounded** measurements (dbfs_avg: -41.8)
- ✅ **Min and max values** (kept for trend analysis)
- ✅ **Coarsened location** (res-5, 50× larger area)
- ❌ **No exact GPS** coordinates
- ✅ **Uploaded to Autonomys** (publicly visible)
- 💰 Used for **lead generation** (free tier)

---

## Data Comparison: Decibel Example

### Original Data (measurements\decibel\hourly\2026-01-22.parquet)

```python
{
  "date": "2026-01-22",
  "hour": 14,
  "dbfs_avg": -41.77,      # Exact average
  "dbfs_min": -45.1,       # Exact minimum
  "dbfs_max": -38.7,       # Exact maximum
  "sample_count": 6,
  "metadata": {
    "hex_id": "871f90151ffffff",  # res-7, ~5 km²
    "gps": {"lat": 40.712776, "lon": -74.005974}
  }
}
```

**Status:** 🔒 **Local only** - Never leaves your machine

---

### Redacted Data (autonomys-measurements\res-5\...\decibel\hourly\2026-01-22.parquet)

```python
{
  "date": "2026-01-22",
  "hour": 14,
  "dbfs_avg": -41.8,       # Rounded to 0.1
  "dbfs_min": -45.1,       # Kept (useful for analysis)
  "dbfs_max": -38.7,       # Kept (useful for analysis)
  "sample_count": 6,       # Kept (shows data quality)
  "metadata": {
    "hex_id": "851f901ffffffff",  # res-5, ~252 km²
    "gps": {"lat": 40.7, "lon": -74.0}  # Rounded
  }
}
```

**Status:** 🌐 **Uploaded to Autonomys** - Publicly visible

---

## Why Two Separate Locations?

### Separation of Concerns

1. **Original data** = Your valuable asset
   - Full precision for paying customers
   - Exact locations for professional services

2. **Redacted data** = Marketing/discovery
   - Privacy-protected preview
   - Enough detail to attract buyers
   - Safe for public distribution

### Security Benefits

- **No accidental uploads** - Original data in different path
- **Clear separation** - Easy to identify what's public vs private
- **Backup strategy** - Can backup each separately
- **Access control** - Can set different permissions

---

## File Size Comparison

### Original Data (Per Day)

```
measurements\decibel\hourly\2026-01-22.parquet
  - Size: ~100-200 KB
  - Rows: 24 (one per hour)
  - Columns: 8-10 (including metadata)
  - Compression: Snappy
```

### Redacted Data (Per Day)

```
autonomys-measurements\res-5\...\decibel\hourly\2026-01-22.parquet
  - Size: ~80-150 KB (slightly smaller, less metadata)
  - Rows: 24 (one per hour)
  - Columns: 6-8 (redacted metadata)
  - Compression: Snappy

+ manifest.json (~2 KB)
+ 2026-01-22.meta.json (~1 KB)
```

**Total storage per miner per month:**
- Original: ~3-6 MB/month
- Redacted: ~2.5-5 MB/month + manifests

---

## Access Patterns

### Original Data Access

**Who accesses:**
- You (data owner)
- Professional tier customers (via API/S3)
- Enterprise tier customers (via real-time API)

**How:**
- Direct file system access (you)
- S3 bucket credentials (paying customers)
- REST/WebSocket API (enterprise)

**Security:**
- File permissions (local)
- IAM policies (S3)
- API authentication (enterprise)

---

### Redacted Data Access

**Who accesses:**
- Anyone browsing Autonomys network
- Potential buyers (free preview)
- Researchers, students, hobbyists

**How:**
- Autonomys Auto Drive public URLs
- IPFS gateway (underlying storage)
- Direct download from Autonomys network

**Security:**
- Public (by design)
- No authentication required
- Read-only access

---

## Workflow: Data Flow

```
CSV Raw Data (10-min intervals)
        ↓
   Aggregation
    (hourly)
        ↓
    ┌───────────────────┐
    │  Split into two   │
    │   destinations    │
    └───────────────────┘
           ↓
    ┌──────────────────────────┐
    ↓                          ↓
ORIGINAL                   REDACTED
(no redaction)          (apply redaction)
    ↓                          ↓
measurements\              autonomys-measurements\
  decibel\                   res-5\851f901...\
    hourly\                    decibel\hourly\
      2026-01-22.parquet         2026-01-22.parquet
    ↓                          ↓
LOCAL ONLY              UPLOAD TO AUTONOMYS
(never uploaded)        (publicly visible)
    ↓                          ↓
Professional/           Lead generation
Enterprise sales        (free tier)
```

---

## Configuration

### Current Implementation

The system automatically:
1. Writes original data to `measurements\`
2. Applies redaction
3. Writes redacted data to `autonomys-measurements\`
4. Uploads only redacted data to Autonomys

No configuration needed - it's automatic!

### Customization Options

If you want to change behavior:

```python
# In autonomys_orchestrator.py
def process_daily_csv_to_autonomys(
    ...
    redaction_level: str = 'standard'  # Change default here
):
```

Or per-call:

```python
process_daily_csv_to_autonomys(
    miner_code="IDM",
    measurement_type="decibel",
    hex_id="871f90151ffffff",
    redaction_level='full',  # Override per call
    upload_to_cloud=True
)
```

---

## Backup Strategy

### Original Data (Critical)

**Backup:** YES, frequently
**Why:** This is your revenue source
**How:**
- Daily automated backups to S3/cloud
- Offsite backup recommended
- Keep at least 90 days

### Redacted Data (Optional)

**Backup:** Not critical
**Why:** Can be regenerated from original
**How:**
- Autonomys network serves as distributed backup
- Can skip local backups
- Regenerate if needed from original

---

## Cleanup/Retention

### Original Data

**Keep:** Indefinitely (or per business requirements)
**Reason:** Revenue source, historical sales

### Redacted Data (Local Copy)

**Keep:** 30-60 days
**Reason:** Already on Autonomys network, can regenerate

### Autonomys Network

**Keep:** According to Autonomys retention policy
**Reason:** Public availability for marketing

---

## Summary

| Aspect | Original (measurements\) | Redacted (autonomys-measurements\) |
|--------|-------------------------|-----------------------------------|
| **Location** | C:\ProgramData\FryNetworks\measurements\ | C:\ProgramData\FryNetworks\autonomys-measurements\ |
| **Precision** | Full (e.g., -41.77 dBFS) | Rounded (e.g., -41.8 dBFS) |
| **Min/Max** | ✅ Exact values | ✅ Kept for analysis |
| **Location** | Exact res-7 hex (~5 km²) | Coarsened res-5 (~252 km²) |
| **Upload** | ❌ Never | ✅ To Autonomys |
| **Access** | Private (you + paid customers) | Public (anyone) |
| **Use Case** | Revenue (paid tiers) | Lead generation (free) |
| **Backup** | Critical | Optional |

---

## Quick Reference

**Where is my exact data?**
```
C:\ProgramData\FryNetworks\measurements\decibel\hourly\*.parquet
```

**Where is the public preview data?**
```
C:\ProgramData\FryNetworks\autonomys-measurements\res-5\*\decibel\hourly\*.parquet
```

**What gets uploaded?**
```
Only: autonomys-measurements\res-5\*\**\*.parquet
Never: measurements\**\*.parquet
```

**How do I sell my exact data?**
```
Give customers access to: measurements\decibel\hourly\
(via S3, API, or direct file transfer)
```
