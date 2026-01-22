# Storage Structure Comparison

## What Users See Locally (Privacy Protected)

```
C:\ProgramData\FryNetworks\miner-BM\measurements\
│
├── bandwidth_real_20260121.csv     (Raw 10-min data)
├── bandwidth_real_20260122.csv
│
├── hourly\                         ← Redacted aggregated data
│   ├── 2026-01-21.parquet
│   └── 2026-01-21.meta.json
│
└── daily\                          ← Daily summaries (future)
    ├── 2026-01-21.parquet
    └── 2026-01-21.meta.json
```

**KEY POINT:** No hexID or res- folders visible locally!
Users don't see their location identifier in the file paths.

---

## What Appears on Autonomys Drive (Public Cloud)

```
851f9017fffffff/                    ← HexID as root folder
│
├── bandwidth/
│   ├── hourly/
│   │   ├── 2026-01-21.parquet
│   │   └── 2026-01-21.meta.json
│   └── daily/
│       ├── 2026-01-21.parquet
│       └── 2026-01-21.meta.json
│
├── satellite/                      ← Future ISM/OSM data
│   ├── hourly/
│   └── daily/
│
├── decibel/                        ← Future IDM/ODM data
│   ├── hourly/
│   └── daily/
│
└── radiation/                      ← Future IRM data
    ├── hourly/
    └── daily/
```

**KEY POINT:** HexID at root enables data buyers to purchase by location!

---

## Example: Multiple Measurement Types

When you have a bandwidth miner (BM) and a decibel miner (IDM) at the same location:

### Local Storage
```
miner-BM/measurements/hourly/2026-01-21.parquet
miner-IDM/measurements/hourly/2026-01-21.parquet
```
(Separate folders, no hexID visible)

### Autonomys Drive
```
851f9017fffffff/
   ├── bandwidth/hourly/2026-01-21.parquet    (from BM)
   └── decibel/hourly/2026-01-21.parquet      (from IDM)
```
(Combined under same hexID, organized by measurement type)

---

## Data Buyer's View

A data buyer interested in area `851f9017fffffff` can browse:

```
851f9017fffffff/                    ← "I want this location"
   ├── bandwidth/                   ← "I need bandwidth data"
   │   └── hourly/                  ← "Hourly resolution is fine"
   │       └── 2026-01-21.parquet   ← "Download this file"
   └── decibel/
       └── hourly/
```

They can:
1. **Browse by location** (hexID)
2. **Select measurement type** (bandwidth, satellite, etc.)
3. **Choose resolution** (hourly vs daily)
4. **Download specific dates**

---

## Privacy Summary

| Aspect | Local Storage | Autonomys Drive |
|--------|---------------|-----------------|
| **HexID visible?** | ❌ No | ✅ Yes (res-5) |
| **Resolution** | N/A | res-5 (~252 km²) |
| **Identifiers** | May contain | ✅ Removed |
| **Measurement noise** | None | ✅ 5% added |
| **Interface names** | Real | ✅ Redacted |
| **User knows location?** | No path hints | Only through upload logs |

---

## Summary

✓ **Local**: Privacy-first (no hexID in paths)
✓ **Cloud**: Monetization-ready (hexID-based structure)
✓ **Privacy**: Location generalized, data redacted
✓ **Organization**: Clean, scalable folder hierarchy
