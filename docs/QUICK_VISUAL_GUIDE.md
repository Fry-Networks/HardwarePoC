# Quick Visual Guide: Decibel Miner Data Redaction

## 📊 Data Flow (One Picture)

```
┌─────────────────────────────────────────────────────────────┐
│ Raw CSV Data (10-minute intervals)                         │
│ - 14:00: -42.3 dBFS                                        │
│ - 14:10: -38.7 dBFS                                        │
│ - 14:20: -45.1 dBFS                                        │
│ - 14:30: -41.2 dBFS                                        │
│ - 14:40: -39.8 dBFS                                        │
│ - 14:50: -43.5 dBFS                                        │
└──────────────────┬──────────────────────────────────────────┘
                   ↓
           ┌───────────────┐
           │  AGGREGATE    │
           │   HOURLY      │
           └───────┬───────┘
                   ↓
    ┌──────────────────────────────┐
    │  Hour 14 Summary:            │
    │  - Avg: -41.77 dBFS          │
    │  - Min: -45.1 dBFS           │
    │  - Max: -38.7 dBFS           │
    │  - Samples: 6                │
    └──────────────┬───────────────┘
                   │
        ┌──────────┴──────────┐
        ↓                     ↓
┌───────────────────┐  ┌─────────────────────┐
│   NO REDACTION    │  │  APPLY REDACTION    │
│   (Keep exact)    │  │  (Privacy protect)  │
└────────┬──────────┘  └──────────┬──────────┘
         ↓                         ↓
┌────────────────────┐   ┌────────────────────┐
│ ORIGINAL DATA      │   │ REDACTED DATA      │
│                    │   │                    │
│ measurements\      │   │ autonomys-         │
│   decibel\hourly\  │   │   measurements\    │
│                    │   │   res-5\...        │
│ -41.77 dBFS (exact)│   │ -41.8 dBFS (round) │
│ res-7 hex          │   │ res-5 hex          │
│ Install ID: YES    │   │ Install ID: NO     │
│                    │   │                    │
│ 🔒 STAYS LOCAL     │   │ ☁️ UPLOADED        │
│ Never uploaded     │   │ Autonomys Public   │
│                    │   │                    │
│ 💰 $50-1000/month  │   │ 💰 FREE (leads)    │
└────────────────────┘   └────────────────────┘
```

---

## 🗂️ Storage Structure (Side by Side)

### Original (Local Only) vs Redacted (Uploaded)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    C:\ProgramData\FryNetworks\                      │
└─────────────────────────────────────────────────────────────────────┘
            │
            ├─────────────────────┬──────────────────────────────┐
            ↓                     ↓                              ↓
    ┌──────────────────┐  ┌──────────────────────┐  ┌────────────────┐
    │  measurements\   │  │ autonomys-           │  │ Other files    │
    │  (ORIGINAL)      │  │   measurements\      │  │ (configs, etc) │
    │                  │  │ (REDACTED)           │  │                │
    │ 🔒 Never upload  │  │ ☁️ Upload to        │  │                │
    └──────────────────┘  │    Autonomys         │  └────────────────┘
            │              └──────────────────────┘
            │                      │
            ↓                      ↓
    ┌──────────────┐      ┌──────────────────┐
    │  decibel\    │      │  res-5\          │
    │  bandwidth\  │      │    851f901...\   │
    │  radiation\  │      │      decibel\    │
    │  satellite\  │      │      bandwidth\  │
    └──────────────┘      └──────────────────┘
            │                      │
            ↓                      ↓
    ┌──────────────┐      ┌──────────────────┐
    │  hourly\     │      │  hourly\         │
    │   2026-01-   │      │   2026-01-       │
    │   22.parquet │      │   22.parquet     │
    │              │      │   + meta.json    │
    │              │      │   manifest.json  │
    └──────────────┘      └──────────────────┘
```

---

## 📝 Data Comparison Table

| Field | Original | Redacted | Change |
|-------|----------|----------|--------|
| **dbfs_avg** | -41.77 | -41.8 | Rounded |
| **dbfs_min** | -45.1 | -45.1 | **Kept** |
| **dbfs_max** | -38.7 | -38.7 | **Kept** |
| **Hex ID** | 871f90151ffffff | 851f9017fffffff | Coarsened |
| **Area** | ~5 km² | ~250 km² | **50× larger** |
| **GPS** | 40.712776, -74.005974 | 40.7, -74.0 | Rounded |
| **Install ID** | 550e8400-... | [removed] | **Deleted** |
| **Miner Code** | IDM | [removed] | **Deleted** |
| **Uploaded?** | ❌ NO | ✅ YES | - |

---

## 💰 Business Model (One Miner)

```
           ┌─────────────────────────────────┐
           │  Autonomys Auto Drive (FREE)   │
           │  Redacted Data (res-5)         │
           │  1000 viewers/month            │
           └────────────┬────────────────────┘
                        │
                        │ 10% contact you
                        ↓
           ┌─────────────────────────────────┐
           │  100 Inquiries                  │
           │  "Can I get full data?"         │
           └────────────┬────────────────────┘
                        │
                        │ 10% convert
                        ↓
           ┌─────────────────────────────────┐
           │  10 Qualified Leads             │
           │  Request quotes/demos           │
           └────────────┬────────────────────┘
                        │
                ┌───────┴────────┐
                ↓                ↓
    ┌───────────────────┐  ┌──────────────────┐
    │ 3× Professional   │  │ 1× Enterprise    │
    │ $60/month each    │  │ $5000/month      │
    │ = $180/month      │  │ (10 hexes)       │
    └───────────────────┘  └──────────────────┘
                │                │
                └────────┬───────┘
                         ↓
                ┌─────────────────┐
                │ Total Revenue:  │
                │ $5,180/month    │
                │                 │
                │ Annual: $62,160 │
                └─────────────────┘
                         ↑
              Cost: ~$5/month (Autonomys)
              ROI: 1000×
```

---

## 🔐 Security Matrix

```
┌──────────────────┬──────────────┬──────────────┐
│                  │   ORIGINAL   │   REDACTED   │
├──────────────────┼──────────────┼──────────────┤
│ Exact Location   │     ✅       │     ❌       │
│ Exact Values     │     ✅       │     ❌       │
│ Install IDs      │     ✅       │     ❌       │
│ Miner Codes      │     ✅       │     ❌       │
│ Min/Max Values   │     ✅       │     ✅       │
│ Sample Counts    │     ✅       │     ✅       │
│ Time Range       │     ✅       │     ✅       │
│ Data Type        │     ✅       │     ✅       │
├──────────────────┼──────────────┼──────────────┤
│ Uploaded?        │     ❌       │     ✅       │
│ Public?          │     ❌       │     ✅       │
│ Sells for $$$?   │     ✅       │     ❌       │
└──────────────────┴──────────────┴──────────────┘
```

---

## 🎯 Quick Decision Guide

### When to Use Each Redaction Level

```
┌─────────────────────────────────────────────────────────┐
│ MINIMAL (res-7, light rounding)                         │
│ ✓ Low-sensitivity data                                  │
│ ✓ Rural areas                                           │
│ ✓ Non-critical measurements                             │
│ × Not recommended for production                        │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ STANDARD (res-5, 5% noise) ⭐ RECOMMENDED               │
│ ✓ Most commercial use cases                             │
│ ✓ Balanced privacy/utility                              │
│ ✓ Attracts buyers with useful preview                   │
│ ✓ 50× location privacy                                  │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ FULL (res-4, 10% noise, bucketed)                       │
│ ✓ Highly sensitive data                                 │
│ ✓ Critical infrastructure areas                         │
│ ✓ Government/military vicinity                          │
│ ✓ 260× location privacy                                 │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start Commands

### See the Demo
```bash
python example_decibel_redaction.py
```

### Test Redaction
```bash
python test_redaction.py
```

### Process Real Data (Local Test)
```python
from measurements.autonomys_orchestrator import process_today_to_autonomys

process_today_to_autonomys(
    miner_code="IDM",  # Your miner type
    hex_id="871f90151ffffff",  # Your location
    upload_to_cloud=False  # Test locally first!
)
```

### Check Results
```bash
# Original data (exact)
dir C:\ProgramData\FryNetworks\measurements\decibel\hourly\

# Redacted data (uploaded)
dir C:\ProgramData\FryNetworks\autonomys-measurements\res-5\
```

---

## 📚 Documentation Index

| Document | Purpose | When to Read |
|----------|---------|--------------|
| **This file** | Quick visual overview | Start here! |
| [REDACTION_QUICK_START.md](REDACTION_QUICK_START.md) | 5-minute guide | After visual overview |
| [STORAGE_STRUCTURE.md](STORAGE_STRUCTURE.md) | Complete storage details | Need deep dive on files |
| [REDACTION_STRATEGY.md](REDACTION_STRATEGY.md) | Business model & strategy | Planning monetization |
| [REDACTION_COMPARISON.md](../REDACTION_COMPARISON.md) | Detailed data comparison | Evaluating redaction |
| [AUTONOMYS_INTEGRATION.md](AUTONOMYS_INTEGRATION.md) | Full integration guide | Setting up system |

---

## ✅ Key Takeaways (Remember This!)

1. **Two Separate Folders**
   - `measurements\` = Original (never upload)
   - `autonomys-measurements\` = Redacted (upload)

2. **Min/Max Values Kept**
   - Trend analysis still possible
   - Buyers can assess data quality
   - Only average is rounded slightly

3. **Automatic by Default**
   - No code changes needed
   - Standard redaction applied automatically
   - Original data protected by default

4. **Revenue Model Works**
   - Free tier generates leads
   - Professional tier ($50-75/month)
   - Enterprise tier ($500-1000/month)
   - One miner = $60K+/year potential

5. **Security Through Separation**
   - Different paths = no accidental uploads
   - Original data never touches Autonomys
   - Clear visibility what's public vs private

---

**Next Step:** Run `python example_decibel_redaction.py` to see it in action!
