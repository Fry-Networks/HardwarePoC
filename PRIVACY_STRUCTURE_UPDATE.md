# Privacy Structure Update - Simplified Storage

**Date:** 2026-01-22
**Change:** Removed visible hex/resolution structure from user-facing directories

---

## Problem

The previous structure exposed privacy strategy details to users:
```
C:\ProgramData\FryNetworks\autonomys-measurements\
└── res-5\                    ← Exposes "res-5" to users
    └── 851f901ffffffff\      ← Exposes redacted hex ID to users
        └── bandwidth\
```

**Issues:**
- Users could see we're using "res-5" resolution
- Redacted hex IDs were visible
- Exposed the privacy strategy we're using

---

## Solution

New simplified structure that hides all privacy implementation details:
```
C:\ProgramData\FryNetworks\miner-BM\measurements\
└── autonomys\                ← Simple folder name
    └── bandwidth\            ← Just measurement type
        └── hourly\
            ├── 2026-01-21.parquet
            └── 2026-01-21.meta.json
```

**Benefits:**
- ✅ No resolution level visible ("res-5" hidden)
- ✅ No hex IDs visible (privacy strategy hidden)
- ✅ Simple, clean structure users can understand
- ✅ Privacy protection still active internally (res-5 redaction)
- ✅ Integrated into existing miner folder structure

---

## What Changed

### 1. Directory Structure

**Before:**
```
autonomys-measurements\
└── res-5\
    └── 851f901ffffffff\
        ├── manifest.json
        └── bandwidth\
            └── hourly\
                ├── 2026-01-21.parquet
                └── 2026-01-21.meta.json
```

**After:**
```
miner-{CODE}\measurements\
└── autonomys\
    └── bandwidth\
        └── hourly\
            ├── 2026-01-21.parquet
            └── 2026-01-21.meta.json
```

### 2. Metadata Files

**Metadata no longer includes hex_id:**
```json
{
  "date": "2026-01-21",
  "measurement_type": "bandwidth",
  "aggregation": "hourly",
  "hours": 24,
  "total_samples": 144,
  "summary": { ... }
}
```

Note: `hex_id` field removed from user-visible metadata

### 3. Manifest Files

**Removed entirely** - no manifest.json files created
- Manifests would expose the hex/resolution structure
- Upload system handles metadata internally
- Users never see privacy implementation details

---

## Privacy Protection Unchanged

**Important:** The actual privacy protection is exactly the same:

| Aspect | Status |
|--------|--------|
| **Redaction Level** | ✅ res-5 (252 km² area, 50× privacy) |
| **Noise** | ✅ 5% random noise added |
| **Identifiers** | ✅ Removed (interface, install_id, etc.) |
| **Location** | ✅ Coarsened internally before upload |

**What changed:** Only the visible folder structure
**What stayed the same:** All privacy protection (res-5, noise, redaction)

---

## Upload Behavior

### Before
```
Remote path: res-5/851f901ffffffff/bandwidth/hourly/2026-01-21.parquet
```

### After
```
Remote path: bandwidth/hourly/2026-01-21.parquet
```

**Simpler, cleaner, no privacy details exposed**

---

## Code Changes

### 1. `autonomys_writer.py`
- `autonomys_root_dir()` now accepts `miner_code` parameter
- Returns path within miner folder: `miner-{code}/measurements/autonomys/`
- `ensure_autonomys_structure()` creates simple folder structure
- `create_or_update_hex_manifest()` disabled (no longer creates manifests)

### 2. `autonomys_orchestrator.py`
- Passes `miner_code` to structure creation
- Metadata created without hex_id
- Upload paths simplified (no res/hex in remote path)
- No manifest files created or uploaded

### 3. `autonomys_aggregator.py`
- `create_hourly_metadata()` accepts `Optional[str]` for hex_id
- hex_id excluded from metadata when `None` passed

---

## Migration

### Existing Data
If you have data in the old structure:
```
C:\ProgramData\FryNetworks\autonomys-measurements\res-5\...
```

**Action:** You can safely delete this folder
- It was only test data
- New structure will be created automatically
- Production uploads will use new simple structure

### New Deployments
No action needed - new structure is automatic

---

## User Experience

### What Users See
```
miner-BM\
├── measurements\
│   ├── bandwidth_real_20260121.csv     ← Original CSV data
│   └── autonomys\                       ← Data prepared for upload
│       └── bandwidth\
│           └── hourly\
│               └── 2026-01-21.parquet  ← Redacted, ready to upload
```

### What Users Don't See
- ❌ No "res-5" or "res-6" folders
- ❌ No hex ID folders like "851f901ffffffff"
- ❌ No manifest.json files
- ❌ No privacy strategy details

**They just see:** Simple "autonomys" folder with clean data ready for upload

---

## Documentation Updates Needed

Files that reference the old structure should be updated:
- ✅ `REDACTION_README.md` - Update file paths
- ✅ `REDACTION_COMPARISON.md` - Update storage structure examples
- ✅ `docs/STORAGE_STRUCTURE.md` - Major update needed
- ✅ `AUTONOMYS_TEST_RESULTS.md` - Update test paths
- ✅ `example_decibel_redaction.py` - Update file structure examples

---

## Summary

**Privacy protection:** ✅ Same (res-5, 50× privacy increase)
**User-facing structure:** ✅ Simplified (no hex/res visible)
**Implementation:** ✅ Cleaner, easier to understand
**Security:** ✅ Privacy strategy hidden from users

The system is now more user-friendly while maintaining the same strong privacy protection.
