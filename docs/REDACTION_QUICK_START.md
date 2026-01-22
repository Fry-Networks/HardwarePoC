# Quick Start: Redacted Data for Autonomys

## TL;DR

**Problem:** Autonomys is public. Everyone can see your data.

**Solution:** Automatically redact data before upload. Sell full data privately.

**Result:** Use Autonomys for marketing while protecting competitive advantage.

## How It Works (3 Steps)

### 1. Process Data (Same as Before)

```python
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

# Process yesterday's data - redaction happens automatically
results = process_yesterday_to_autonomys(
    miner_code="BM",
    hex_id="871f90151ffffff",
    upload_to_cloud=True
)
```

**What happens:**
- ✅ Data aggregated to hourly
- ✅ **Redaction applied automatically** (standard level)
- ✅ Saved to coarsened hex folder (res-5 instead of res-7)
- ✅ Uploaded to Autonomys (redacted version only)

### 2. Original Data Stays Local

Your full-resolution data is **never uploaded**:

```
C:\ProgramData\FryNetworks\autonomys-measurements\
├── res-7\                    # ORIGINAL (stays local)
│   └── 871f90151ffffff\      # Exact location (~5 km²)
│       └── bandwidth\
│           └── hourly\
│               └── 2026-01-20.parquet  # Unredacted
│
└── res-5\                    # REDACTED (uploaded to Autonomys)
    └── 851f901ffffffff\      # Coarsened location (~250 km²)
        └── bandwidth\
            └── hourly\
                └── 2026-01-20.parquet  # Redacted
```

### 3. Sell Both Versions

**Free (Autonomys):**
- Redacted data as preview
- Marketing and lead generation

**Paid (Private Channels):**
- Full resolution (res-7)
- Exact measurements
- Higher prices

## What Gets Redacted

| Item | Before | After (Standard) |
|------|--------|------------------|
| Location | res-7 (~5 km²) | res-5 (~250 km²) |
| Download speed | 85.327 Mbps | 84.1 Mbps (5% noise) |
| Interface | "wlan0" | "REDACTED" |
| Install ID | "550e8400-..." | Removed |
| GPS coords | 40.712776, -74.005974 | 40.7, -74.0 |

## Redaction Levels

### Standard (Default) ⭐ Recommended
```python
# Standard redaction (already the default)
process_yesterday_to_autonomys(...)
```

- Location: res-7 → res-5 (50× larger area)
- Measurements: +5% noise
- Identifiers: Removed (install IDs, interfaces)

### Minimal (More Utility, Less Privacy)
```python
process_daily_csv_to_autonomys(
    ...,
    redaction_level='minimal'
)
```

- Location: res-7 (original)
- Measurements: Rounded to 1 decimal
- Identifiers: Kept

### Full (Maximum Privacy)
```python
process_daily_csv_to_autonomys(
    ...,
    redaction_level='full'
)
```

- Location: res-7 → res-4 (260× larger area)
- Measurements: +10% noise, bucketed
- Identifiers: Removed

## Testing

Run the demo to see redaction in action:

```bash
python test_redaction.py
```

## Business Model

```
Autonomys (Free) → Discovery → Contact You → Sell Full Data ($$$)
     ↓                                              ↓
Redacted preview                          Unredacted, exact location
~30 CPM, ~250 km² area                   31.2 CPM, exact address
```

## Example Pricing

| Tier | Source | Redaction | Location | Price |
|------|--------|-----------|----------|-------|
| Free | Autonomys | Standard | res-5 | $0 |
| Pro | Private | Minimal | res-7 | $75/hex/month |
| Enterprise | Private | None | Exact GPS | $800/hex/month |

## Key Points

1. ✅ **Automatic** - Redaction happens by default, no extra code needed
2. ✅ **Safe** - Original data never uploaded to Autonomys
3. ✅ **Flexible** - Choose redaction level per dataset
4. ✅ **Marketing** - Free data on Autonomys generates leads
5. ✅ **Revenue** - Sell full data privately for higher prices

## Where to Learn More

- Full strategy: [`docs/REDACTION_STRATEGY.md`](REDACTION_STRATEGY.md)
- Integration docs: [`docs/AUTONOMYS_INTEGRATION.md`](AUTONOMYS_INTEGRATION.md)
- Code: [`measurements/autonomys_redactor.py`](../measurements/autonomys_redactor.py)

## FAQ

**Q: Is redaction secure?**
A: Yes. Random noise is irreversible and hex coarsening cannot be reversed.

**Q: Can I still sell data?**
A: Yes! Sell unredacted data privately. Autonomys is just for discovery.

**Q: What if I don't want any public data?**
A: Set `upload_to_cloud=False` to skip Autonomys upload entirely.

**Q: How do buyers find me?**
A: Contact info is in manifests (`contact: "data-sales@frynetworks.com"`).
