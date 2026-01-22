# Decibel Miner: Original vs Redacted Data Comparison

## Quick Visual Comparison

### Original Data (Stays Local - Never Uploaded)

```
Location: 871f90151ffffff (res-7)
Area: ~5 km² (exact location)
GPS: 40.712776°, -74.005974° (precise)

Hourly Data (2026-01-22, Hour 14):
┌──────────────┬──────────┬──────────┬──────────┐
│ dbfs_avg     │ dbfs_min │ dbfs_max │ samples  │
├──────────────┼──────────┼──────────┼──────────┤
│ -41.77 dBFS  │ -45.1    │ -38.7    │ 6        │
└──────────────┴──────────┴──────────┴──────────┘

Metadata:
  • Exact timestamps: All 6 measurements
  • Location: Exact building/floor (res-7)
```

### Redacted Data (Uploaded to Autonomys - Public)

```
Location: 851f901ffffffff (res-5)
Area: ~252 km² (50× larger, covers broader NYC area)
GPS: 40.7°, -74.0° (rounded to ~10 km precision)

Hourly Data (2026-01-22, Hour 14):
┌──────────────┬──────────┬──────────┬──────────┬──────────────┐
│ dbfs_avg     │ dbfs_min │ dbfs_max │ samples  │ interface    │
├──────────────┼──────────┼──────────┼──────────┼──────────────┤
│ -41.8 dBFS   │ -45.1    │ -38.7    │ 6        │ REDACTED     │
└──────────────┴──────────┴──────────┴──────────┴──────────────┘

Metadata:
  • Install ID: [removed]
  • Miner Code: [removed]
  • Exact timestamps: Hour-level only
  • Location: General area
```

---

## Detailed Field-by-Field Comparison

| Field | Original (Local) | Standard Redaction (Autonomys) | Change |
|-------|-----------------|-------------------------------|--------|
| **Location Hex** | `871f90151ffffff` | `851f901ffffffff` | Coarsened res-7 → res-5 |
| **Area Coverage** | ~5 km² | ~252 km² | **50× larger** |
| **GPS Latitude** | 40.712776° | 40.7° | Rounded to 1 decimal |
| **GPS Longitude** | -74.005974° | -74.0° | Rounded to 1 decimal |
| **dbfs_avg** | -41.77 dBFS | -41.8 dBFS | Rounded to 1 decimal |
| **dbfs_min** | -45.1 dBFS | -45.1 dBFS | Kept (already 1 decimal) |
| **dbfs_max** | -38.7 dBFS | -38.7 dBFS | Kept (already 1 decimal) |
| **Sample Count** | 6 | 6 | **Kept** (shows data quality) |
| **Raw Samples** | All 6 × 10-min | Aggregated only | Only hourly summary |

---

## What Buyers See on Autonomys vs What You Sell Privately

### Free Tier (Autonomys - Public)

**What's Visible:**
- Broader NYC area (~252 km²)
- Average noise: ~-42 dBFS (rounded)
- Data availability: January 2026
- Sample quality: 6 samples/hour
- Contact: data-sales@frynetworks.com

**What's Hidden:**
- Exact building/street location
- Precise measurements (-41.77 vs -41.8)

**Purpose:** Lead generation, discovery

---

### Professional Tier ($50-75/month)

**What You Provide:**
```json
{
  "location": {
    "hex_id": "871f90151ffffff",
    "resolution": 7,
    "area_km2": 5,
    "center": {"lat": 40.712776, "lon": -74.005974}
  },
  "measurements": {
    "2026-01-22T14:00:00Z": {
      "dbfs_avg": -41.77,
      "dbfs_min": -45.1,
      "dbfs_max": -38.7,
      "sample_count": 6
    }
  },
  "metadata": {
    "aggregation": "hourly"
  }
}
```

**Delivery:** S3 bucket, API key (read-only)

---

### Enterprise Tier ($500-1000/month)

**What You Provide:**
```json
{
  "location": {
    "hex_id": "871f90151ffffff",
    "gps_exact": {"lat": 40.712776, "lon": -74.005974},
    "address": "123 Broadway, Floor 15, NYC"
  },
  "raw_measurements": [
    {"timestamp": "2026-01-22T14:00:00Z", "dbfs": -42.3},
    {"timestamp": "2026-01-22T14:10:00Z", "dbfs": -38.7},
    {"timestamp": "2026-01-22T14:20:00Z", "dbfs": -45.1},
    {"timestamp": "2026-01-22T14:30:00Z", "dbfs": -41.2},
    {"timestamp": "2026-01-22T14:40:00Z", "dbfs": -39.8},
    {"timestamp": "2026-01-22T14:50:00Z", "dbfs": -43.5}
  ],
  "real_time": {
    "websocket_url": "wss://api.frynetworks.com/ws",
    "api_key": "ent_..."
  }
}
```

**Delivery:** Real-time API, WebSocket, S3, Custom integration

---

## Privacy Protection Analysis

### What's Protected by Redaction

✅ **Exact Location**
- Original: Specific building at 40.712776°, -74.005974°
- Redacted: Somewhere in ~252 km² broader NYC area
- **Protection Level:** Cannot determine exact building, street, or even neighborhood

✅ **Precise Measurements**
- Original: -41.77 dBFS (to 0.01 precision)
- Redacted: -41.8 dBFS (to 0.1 precision)
- **Protection Level:** Minor, but prevents exact reconstruction

✅ **Temporal Granularity**
- Original: 6 individual 10-minute measurements
- Redacted: 1 hourly aggregate
- **Protection Level:** Cannot see moment-to-moment variations

### What Remains Visible (By Design)

❌ **General Area**
- Purpose: Buyers need to know city/region
- Example: "NYC area"
- Trade-off: Acceptable for lead generation

❌ **Data Quality**
- Purpose: Buyers assess if data is worth purchasing
- Example: "6 samples/hour" indicates good coverage
- Trade-off: Necessary for sales

❌ **Measurement Type**
- Purpose: Buyers search for specific data types
- Example: "decibel" measurements
- Trade-off: Required for discoverability

❌ **Time Range**
- Purpose: Buyers need to know data freshness
- Example: "January 2026"
- Trade-off: Necessary for sales value

---

## Use Cases by Tier

### Free (Autonomys)
**Who Uses It:**
- Students doing research projects
- Hobbyists exploring noise data
- Researchers doing preliminary analysis
- Potential buyers evaluating before purchase

**What They Can Do:**
- See general trends
- Identify coverage areas
- Assess data quality
- Contact you for full data

**Revenue:** $0 (marketing cost)

---

### Professional ($50-75/month)
**Who Uses It:**
- Environmental consultants
- Urban planning firms
- App developers (noise map apps)
- Small research labs

**What They Can Do:**
- Create noise maps (res-7 precision)
- Analyze specific neighborhoods
- Build commercial applications
- Generate reports for clients

**Revenue:** $50-75/hex/month × N hexes

---

### Enterprise ($500-1000+/month)
**Who Uses It:**
- City/government agencies
- Major telecom companies
- Academic institutions (funded)
- Real estate developers

**What They Can Do:**
- Real-time monitoring
- Building-specific analysis
- Regulatory compliance
- Infrastructure planning

**Revenue:** $500-1000/hex/month or custom contracts

---

## Example Sales Funnel

```
1000 Autonomys Viewers (Free)
      ↓
    100 Inquiries (Contact form)
      ↓
     10 Demo Requests (Professional tier sample)
      ↓
      3 Professional Subscriptions ($60/month × 3 = $180/month)
      ↓
      1 Enterprise Contract ($5000/month for 10 hexes)
      ↓
Total Monthly Revenue: $5,180 from one deployed miner

Annual Value: $62,160 per miner location
```

**Cost of Autonomys (free tier):** Storage + bandwidth (~$5/month)

**ROI:** 1000×+ return on Autonomys investment

---

## File Storage Comparison

### Local Storage (Full Data - NOT Uploaded)

```
C:\ProgramData\FryNetworks\measurements\decibel\hourly\

Structure:
  decibel\
    +-- hourly\
        +-- 2026-01-22.parquet
            - dbfs_avg: -41.77 (exact)
            - dbfs_min: -45.1 (exact)
            - dbfs_max: -38.7 (exact)
            - sample_count: 6
            - hex_id: 871f90151ffffff (res-7)

Size: ~2 MB per day
Status: NEVER uploaded, stays on local machine only
Use: Professional/Enterprise tier sales
```

### Autonomys Storage (Redacted Data - UPLOADED)

```
Autonomys: /res-5/851f901ffffffff/decibel/hourly/

Structure:
  res-5\
    +-- 851f901ffffffff\
        +-- manifest.json (redacted)
        +-- decibel\
            +-- hourly\
                +-- 2026-01-22.parquet
                    - dbfs_avg: -41.8 (rounded)
                    - dbfs_min: -45.1
                    - dbfs_max: -38.7
                    - sample_count: 6
                    - hex_id: 851f901ffffffff (res-5, coarsened)

Size: ~1.5 MB per day
Status: Publicly visible on Autonomys network
Use: Lead generation, discovery
```

---

## Key Takeaways

1. **Redaction is Automatic** - No code changes needed, works by default
2. **Original Data Protected** - Never uploaded to Autonomys
3. **Business Model Enabled** - Free preview → Paid full data
4. **Privacy Preserved** - 50-260× larger area
5. **Data Still Valuable** - Enough detail for buyers to assess worth
6. **Competitive Advantage** - Exact locations and measurements secret

---

## Testing

Run the example:
```bash
python example_decibel_redaction.py
```

See interactive demo:
```bash
python test_redaction.py
```
