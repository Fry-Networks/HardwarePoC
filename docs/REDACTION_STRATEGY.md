# Data Redaction Strategy for Autonomys Auto Drive

## Problem Statement

Autonomys Auto Drive is a **publicly accessible** decentralized storage system. Any data uploaded there can be viewed by anyone. This creates a challenge:

- **We want to use Autonomys** for decentralized storage and discoverability
- **We want to sell data** and maintain competitive advantage
- **Public visibility** would make the data worthless if unredacted

## Solution: Parallel Redacted Datasets

Upload **redacted versions** of data to Autonomys that:
1. ✅ Demonstrate data availability and coverage
2. ✅ Provide enough utility for some buyers (low-tier products)
3. ✅ Protect competitive advantage and exact measurements
4. ✅ Serve as marketing/lead generation for full dataset sales

Sell **unredacted data** through private channels for premium prices.

## How It Works

### Automatic Redaction Pipeline

```
Raw CSV Data (10-min intervals)
         ↓
   Hourly Aggregation
         ↓
   🔒 REDACTION APPLIED 🔒  ← Privacy protection happens here
         ↓
  Local Parquet Files (redacted hex folder)
         ↓
  Autonomys Auto Drive (publicly visible, redacted data)
```

### What Gets Redacted

| Data Type | Minimal | Standard (default) | Full |
|-----------|---------|-------------------|------|
| **Location** | Original (res-7, ~5 km²) | Coarsened to res-5 (~250 km²) | Coarsened to res-4 (~1,300 km²) |
| **Measurements** | Rounded to 1 decimal | +5% noise, interface removed | +10% noise, bucketed (5 Mbps/dB) |
| **GPS Coords** | Original | Rounded to ~10 km | Removed |
| **Install IDs** | Kept | Removed | Removed |
| **Miner Codes** | Kept | Removed | Removed |
| **Interface Names** | Kept | Removed | Removed |

### Redaction by Measurement Type

#### Bandwidth
- **Standard:** 5% noise on dl/ul speeds, interface name → "REDACTED"
- **Full:** 10% noise + round to 5 Mbps buckets, interface → "REDACTED"

#### Satellite/GNSS
- **Standard:** Remove exact lat/lon, keep satellite counts and HDOP
- **Full:** Same as standard (satellite counts are less sensitive)

#### Radiation
- **Standard:** 3% noise on CPM/µSv/mR values
- **Full:** 10% noise + round to integers

#### Decibel
- **Standard:** Round to 1 decimal place
- **Full:** Round to 5 dB buckets

#### AEM (Points of Interest)
- **Standard:** Round to 1 decimal place
- **Full:** 5% noise + round to integers

## Usage Examples

### Default (Standard Redaction)

```python
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

# Standard redaction is applied automatically
results = process_yesterday_to_autonomys(
    miner_code="BM",
    hex_id="871f90151ffffff",
    install_id="550e8400-e29b-41d4-a716-446655440000",
    upload_to_cloud=True
)
```

### Custom Redaction Level

```python
from measurements.autonomys_orchestrator import process_daily_csv_to_autonomys

# Minimal redaction (more data utility, less privacy)
process_daily_csv_to_autonomys(
    miner_code="BM",
    measurement_type="bandwidth",
    hex_id="871f90151ffffff",
    date_str="20260120",
    redaction_level='minimal',  # Override default
    upload_to_cloud=True
)

# Full redaction (maximum privacy, less utility)
process_daily_csv_to_autonomys(
    miner_code="IRM",
    measurement_type="radiation",
    hex_id="871f90151ffffff",
    date_str="20260120",
    redaction_level='full',  # Maximum privacy
    upload_to_cloud=True
)
```

## Business Model: Tiered Data Products

### Free Tier (Autonomys Public Data)
- **Source:** Autonomys Auto Drive (redacted data)
- **Redaction:** Standard level
- **Price:** Free (marketing/lead generation)
- **Use case:** Researchers, students, hobbyists

### Professional Tier
- **Source:** Private delivery (minimal redaction)
- **Redaction:** Minimal (rounded values, original res-7 hexes)
- **Price:** $50-100/hex/month
- **Use case:** Consultants, small businesses, app developers

### Enterprise Tier
- **Source:** Private delivery (no redaction)
- **Redaction:** None (raw measurements, exact locations)
- **Price:** $500-1000/hex/month or custom contracts
- **Use case:** Telecom companies, government agencies, large enterprises

### Real-Time Tier
- **Source:** Direct API access
- **Redaction:** None
- **Price:** Custom contracts ($5k-50k/month)
- **Use case:** Critical infrastructure, real-time applications

## Example Sales Flow

1. **Discovery:** Buyer searches Autonomys for "radiation data in Chicago"
2. **Preview:** Finds redacted data showing res-5 hex with ~30 CPM average
3. **Interest:** Wants higher resolution and exact measurements
4. **Contact:** Reaches out via contact info in manifest
5. **Quote:** You offer:
   - Professional: res-7 hex, minimal redaction, $75/month
   - Enterprise: res-7 hex, no redaction, exact location, $800/month
6. **Delivery:** Private transfer (not through Autonomys)

## Storage Organization

### Local Folder Structure

```
autonomys-measurements/
├── res-7/                           # High-res (original, NOT uploaded)
│   └── 871f90151ffffff/            # Exact location
│       └── bandwidth/
│           └── hourly/
│               └── 2026-01-20.parquet   # Unredacted data
│
├── res-5/                           # Coarsened for Autonomys (UPLOADED)
│   └── 851f901ffffffff/            # Covers 50× larger area
│       ├── manifest.json           # Redacted manifest
│       └── bandwidth/
│           └── hourly/
│               ├── 2026-01-20.parquet   # Redacted data
│               └── 2026-01-20.meta.json
```

### What Gets Uploaded to Autonomys

- ✅ Redacted parquet files (res-5 or res-4 folders)
- ✅ Redacted metadata files
- ✅ Redacted manifests (no install IDs, rounded GPS)
- ❌ Original resolution data (res-7 stays local)
- ❌ Install IDs or miner codes
- ❌ Interface names

## Privacy vs. Utility Tradeoff

### Minimal Redaction
- **Privacy:** ⭐⭐ (Low) - Still shows exact area, close to real values
- **Utility:** ⭐⭐⭐⭐⭐ (High) - Useful for analytics, planning
- **Sales potential:** ⭐⭐⭐ (Medium) - Less incentive to buy full data

### Standard Redaction (Recommended)
- **Privacy:** ⭐⭐⭐⭐ (Good) - 50× larger area, 5% noise
- **Utility:** ⭐⭐⭐⭐ (Good) - Still useful for trends, planning
- **Sales potential:** ⭐⭐⭐⭐⭐ (High) - Clear value in higher tiers

### Full Redaction
- **Privacy:** ⭐⭐⭐⭐⭐ (Maximum) - 260× larger area, 10% noise
- **Utility:** ⭐⭐⭐ (Medium) - Good for broad trends only
- **Sales potential:** ⭐⭐⭐⭐⭐ (High) - Strong incentive to upgrade

## Testing Redaction

Run the test script to see redaction in action:

```bash
python test_redaction.py
```

This will demonstrate:
- How measurement values change with different redaction levels
- How hex IDs are coarsened (res-7 → res-5 → res-4)
- How manifests are redacted
- What information is kept vs. removed

## Recommendations

### For Most Users: Standard Redaction
- Balances privacy and utility
- 50× larger location area (res-5)
- 5% noise protects exact values
- Removes identifying info (install IDs, interfaces)
- **Clear value proposition** for buyers to upgrade

### For Highly Sensitive Data: Full Redaction
- Maximum privacy (260× larger area)
- 10% noise + value bucketing
- Use for radiation in populated areas, military installations, etc.

### For Low-Sensitivity Data: Minimal Redaction
- When exact location doesn't matter (rural areas)
- When data is already semi-public (noise measurements in city centers)
- When you want to maximize Autonomys data utility

## Implementation Details

### Redaction Code Location
- Main redactor: [`measurements/autonomys_redactor.py`](../measurements/autonomys_redactor.py)
- Integration: [`measurements/autonomys_orchestrator.py`](../measurements/autonomys_orchestrator.py)
- Levels config: `REDACTION_LEVELS` dict in redactor module

### Key Functions
- `DataRedactor(redaction_level)` - Main redaction class
- `redactor.redact_bandwidth_data(df)` - Redact bandwidth measurements
- `redactor.redact_location(hex_id, target_res)` - Coarsen hex ID
- `redactor.redact_manifest(manifest, redacted_hex)` - Redact metadata

### Adding Custom Redaction
To customize redaction behavior, edit `autonomys_redactor.py`:

```python
# Example: Add custom noise level for radiation in urban areas
def redact_radiation_data(self, df: pd.DataFrame) -> pd.DataFrame:
    if self.level == 'urban':
        # Higher noise for populated areas
        for col in ['cpm_avg', 'cpm_min', 'cpm_max']:
            df_redacted[col] = df_redacted[col].apply(
                lambda x: self.add_noise_to_value(x, 15.0)  # 15% noise
            )
```

## FAQ

**Q: Why upload anything to Autonomys if it's public?**
A: Marketing and discoverability. Buyers can find your data and see what's available before purchasing full access.

**Q: Can someone reconstruct original values from redacted data?**
A: No. Random noise is irreversible, and coarsened hex IDs cannot be reversed to exact locations.

**Q: Should I use different redaction levels for different measurement types?**
A: Yes! Use full redaction for sensitive data (radiation near critical infrastructure) and minimal for less sensitive data (bandwidth in commercial areas).

**Q: What if a buyer wants raw 10-minute interval data?**
A: Autonomys only stores hourly aggregates. Sell raw CSV data separately for premium prices.

**Q: How do I advertise full data if only redacted data is public?**
A: Include contact information in manifests and root metadata. Buyers will reach out to inquire about higher-tier products.

## Security Considerations

### What's Protected
✅ Exact locations (hex coarsening)
✅ Precise measurement values (noise + rounding)
✅ Equipment identification (install IDs removed)
✅ Network details (interface names removed)
✅ Miner deployment info (miner codes removed)

### What's Not Protected (By Design)
❌ General area (city-level location visible at res-4/5)
❌ Measurement type (bandwidth, radiation, etc.)
❌ Time coverage (date ranges visible)
❌ Data quality (sample counts visible)

This is intentional - buyers need enough information to assess data value before purchasing.
