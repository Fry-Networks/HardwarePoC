#!/usr/bin/env python3
"""
Example: Decibel Miner Data Redaction

This demonstrates how a decibel (noise) miner's data is transformed
through the redaction process before being uploaded to Autonomys.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

try:
    import pandas as pd
    from measurements.autonomys_redactor import DataRedactor, get_redaction_level_info
except ImportError as e:
    print(f"Error: {e}")
    print("\nPlease install: pip install pandas h3")
    sys.exit(1)

print("=" * 80)
print(" " * 20 + "DECIBEL MINER DATA REDACTION EXAMPLE")
print("=" * 80)
print()
print("Scenario: Indoor Decibel Miner (IDM) in downtown office building")
print("Location: 871f90151ffffff (res-7, Manhattan, NYC)")
print("Date: 2026-01-22")
print()

# ============================================================================
# ORIGINAL RAW DATA (10-minute intervals from CSV)
# ============================================================================

print("=" * 80)
print("STEP 1: ORIGINAL RAW DATA (10-minute intervals)")
print("=" * 80)
print()
print("This is what gets collected every 10 minutes:")
print()

raw_data = [
    {"timestamp": "2026-01-22T14:00:00Z", "dbfs": -42.3},
    {"timestamp": "2026-01-22T14:10:00Z", "dbfs": -38.7},
    {"timestamp": "2026-01-22T14:20:00Z", "dbfs": -45.1},
    {"timestamp": "2026-01-22T14:30:00Z", "dbfs": -41.2},
    {"timestamp": "2026-01-22T14:40:00Z", "dbfs": -39.8},
    {"timestamp": "2026-01-22T14:50:00Z", "dbfs": -43.5},
]

df_raw = pd.DataFrame(raw_data)
print(df_raw.to_string(index=False))
print()
print(f"Total samples: {len(raw_data)}")
print(f"Exact location: 871f90151ffffff (covers ~5 km² area)")
print(f"Approximate coordinates: 40.712776°, -74.005974° (exact)")
print()

# ============================================================================
# AGGREGATED HOURLY DATA (what goes into parquet files)
# ============================================================================

print("=" * 80)
print("STEP 2: HOURLY AGGREGATION (before redaction)")
print("=" * 80)
print()
print("Raw data is aggregated into hourly summaries:")
print()

# Simulate hourly aggregation for hour 14
hourly_data = {
    'date': ['2026-01-22'],
    'hour': [14],
    'dbfs_avg': [-41.77],  # Average of raw values
    'dbfs_min': [-45.1],   # Minimum
    'dbfs_max': [-38.7],   # Maximum
    'sample_count': [6]
}

df_hourly_original = pd.DataFrame(hourly_data)
print(df_hourly_original.to_string(index=False))
print()
print("Key points:")
print("  • Average noise: -41.77 dBFS (exact)")
print("  • Min/Max range: -45.1 to -38.7 dBFS")
print("  • 6 samples in this hour")
print()

# ============================================================================
# REDACTION APPLIED
# ============================================================================

print("=" * 80)
print("STEP 3: REDACTION (what gets uploaded to Autonomys)")
print("=" * 80)
print()

# Show all three redaction levels
for level in ['minimal', 'standard', 'full']:
    print("-" * 80)
    print(f"{level.upper()} REDACTION")
    print("-" * 80)

    info = get_redaction_level_info(level)
    print(f"Description: {info['description']}")
    print()

    # Apply redaction
    redactor = DataRedactor(redaction_level=level)
    df_redacted = redactor.redact_decibel_data(df_hourly_original.copy())

    print("Data after redaction:")
    print(df_redacted.to_string(index=False))
    print()

    # Location redaction
    original_hex = "871f90151ffffff"
    target_res = info['hex_resolution']
    redacted_hex = redactor.redact_location(original_hex, target_resolution=target_res)

    area_sizes = {7: 5, 5: 252, 4: 1300}
    area = area_sizes.get(target_res, 5)
    multiplier = area / 5

    print(f"Location redaction:")
    print(f"  Original hex: {original_hex} (res-7, ~5 km²)")
    print(f"  Redacted hex: {redacted_hex} (res-{target_res}, ~{area} km²)")
    print(f"  Privacy gain: {multiplier:.0f}× larger area")
    print()

    # Show what changed
    if level == 'minimal':
        print("What changed:")
        print("  • dBFS values: Rounded to 1 decimal place")
        print("  • Location: No change (original precision)")
        print("  • Identifiers: Kept")
    elif level == 'standard':
        print("What changed:")
        print("  • dBFS values: Rounded to 1 decimal place")
        print("  • Location: Coarsened to res-5 (~252 km² area)")
        print("  • Identifiers: Install IDs and miner codes removed")
        print("  • GPS coordinates: Rounded (40.712776 -> 40.7)")
    elif level == 'full':
        orig_avg = df_hourly_original['dbfs_avg'].iloc[0]
        redacted_avg = df_redacted['dbfs_avg'].iloc[0]
        print("What changed:")
        print(f"  • dBFS values: Bucketed to nearest 5 dB ({orig_avg:.1f} -> {redacted_avg:.1f})")
        print("  • Location: Coarsened to res-4 (260× larger area)")
        print("  • Identifiers: All removed")
        print("  • GPS coordinates: Removed entirely")

    print()

# ============================================================================
# FILE STRUCTURE COMPARISON
# ============================================================================

print("=" * 80)
print("STEP 4: FILE STORAGE")
print("=" * 80)
print()

print("Local file structure (STANDARD REDACTION):")
print()
print("C:\\ProgramData\\FryNetworks\\")
print("|")
print("|-- measurements\\                             <- ORIGINAL (never uploaded)")
print("|   +-- decibel\\")
print("|       +-- hourly\\")
print("|           +-- 2026-01-22.parquet")
print("|               - dbfs_avg: -41.77 (exact)")
print("|               - dbfs_min: -45.1 (exact)")
print("|               - dbfs_max: -38.7 (exact)")
print("|               - sample_count: 6")
print("|               - hex_id: 871f90151ffffff (res-7)")
print("|               - install_id: 550e8400-...")
print("|               - miner_code: IDM")
print("|")
print("+-- autonomys-measurements\\                  <- REDACTED (uploaded to Autonomys)")
print("    +-- res-5\\")
print("        +-- 851f901ffffffff\\")
print("            |-- manifest.json")
print("            |   - hex_id: 851f901ffffffff (redacted)")
print("            |   - center: {lat: 40.7, lon: -74.0} (rounded)")
print("            |   - install_ids: [] (removed)")
print("            +-- decibel\\")
print("                +-- hourly\\")
print("                    |-- 2026-01-22.parquet")
print("                    |   - dbfs_avg: -41.8 (rounded)")
print("                    |   - dbfs_min: -45.1")
print("                    |   - dbfs_max: -38.7")
print("                    |   - sample_count: 6")
print("                    |   - hex_id: 851f901ffffffff (res-5)")
print("                    |   - install_id: [removed]")
print("                    +-- 2026-01-22.meta.json")
print()

# ============================================================================
# METADATA COMPARISON
# ============================================================================

print("=" * 80)
print("STEP 5: METADATA FILES")
print("=" * 80)
print()

print("Original metadata (res-7, NOT uploaded):")
print("-" * 40)
original_metadata = {
    "date": "2026-01-22",
    "hex_id": "871f90151ffffff",
    "measurement_type": "decibel",
    "aggregation": "hourly",
    "hours": 1,
    "total_samples": 6,
    "summary": {
        "dbfs": {
            "day_avg": -41.77,
            "day_min": -45.1,
            "day_max": -38.7
        }
    }
}

import json
print(json.dumps(original_metadata, indent=2))
print()

print("Redacted metadata (res-5, UPLOADED to Autonomys):")
print("-" * 40)
redacted_metadata = {
    "date": "2026-01-22",
    "hex_id": "851f901ffffffff",  # Coarsened
    "measurement_type": "decibel",
    "aggregation": "hourly",
    "hours": 1,
    "total_samples": 6,  # Kept (indicates data quality)
    "summary": {
        "dbfs": {
            "day_avg": -41.8,  # Rounded
            "day_min": -45.1,  # Rounded
            "day_max": -38.7   # Rounded
        }
    }
}

print(json.dumps(redacted_metadata, indent=2))
print()

# ============================================================================
# BUSINESS MODEL
# ============================================================================

print("=" * 80)
print("STEP 6: MONETIZATION STRATEGY")
print("=" * 80)
print()

print("FREE TIER (Autonomys Auto Drive - Public):")
print("-" * 40)
print("  What buyer sees:")
print("    • Location: Somewhere in ~252 km² area around Manhattan")
print("    • Noise level: ~-42 dBFS (rounded)")
print("    • Sample count: 6 per hour")
print("    • Coverage: January 2026")
print("    • Contact: data-sales@frynetworks.com")
print()
print("  Use case: Discovery, lead generation")
print("  Price: FREE")
print()

print("PROFESSIONAL TIER (Private Delivery):")
print("-" * 40)
print("  What buyer gets:")
print("    • Location: Exact res-7 hex (871f90151ffffff, ~5 km²)")
print("    • Noise level: -41.77 dBFS (exact)")
print("    • Hourly data: Full precision")
print("    • Install ID: Included")
print("    • API access: Read-only")
print()
print("  Use case: Noise mapping, urban planning consultants")
print("  Price: $50-75/hex/month")
print()

print("ENTERPRISE TIER (Private Delivery):")
print("-" * 40)
print("  What buyer gets:")
print("    • Location: Exact GPS coordinates (40.712776°, -74.005974°)")
print("    • Raw 10-minute intervals: All 6 samples per hour")
print("    • Real-time access: WebSocket or polling API")
print("    • Historical data: Complete archive")
print("    • Custom alerts: Threshold notifications")
print()
print("  Use case: City government, research institutions")
print("  Price: $500-1000/hex/month or custom contract")
print()

# ============================================================================
# EXAMPLE SALES SCENARIO
# ============================================================================

print("=" * 80)
print("EXAMPLE SALES SCENARIO")
print("=" * 80)
print()

print("1. DISCOVERY (Buyer browses Autonomys)")
print("   -> Finds decibel data in Manhattan area (res-5)")
print("   -> Sees average noise levels around -42 dBFS")
print("   -> Notices 6 samples/hour, good data quality")
print()

print("2. INTEREST (Buyer wants more detail)")
print("   -> Contacts: data-sales@frynetworks.com")
print("   -> Asks: 'Can you provide exact location and higher precision?'")
print()

print("3. QUOTE")
print("   -> Professional: Res-7 hex, hourly data - $60/month")
print("   -> Enterprise: Exact GPS, 10-min intervals, API - $750/month")
print()

print("4. DELIVERY (Private, not through Autonomys)")
print("   -> S3 bucket with credentials")
print("   -> API key for real-time access")
print("   -> Invoice via Stripe/PayPal")
print()

# ============================================================================
# KEY TAKEAWAYS
# ============================================================================

print("=" * 80)
print("KEY TAKEAWAYS")
print("=" * 80)
print()

print("* PRIVACY PROTECTED:")
print("  - Original data (res-7) never uploaded to Autonomys")
print("  - Exact location obscured (50-260x larger area)")
print("  - Install IDs and miner codes removed")
print()

print("* DATA STILL VALUABLE:")
print("  - Buyers can assess coverage and quality")
print("  - General area and time range visible")
print("  - Enough detail to determine if full data worth buying")
print()

print("* BUSINESS MODEL ENABLED:")
print("  - Autonomys = Free preview + lead generation")
print("  - Private sales = Premium pricing for exact data")
print("  - Multiple tiers for different buyer needs")
print()

print("* COMPETITIVE ADVANTAGE MAINTAINED:")
print("  - Competitors can't see exact sensor locations")
print("  - Precise measurements remain secret")
print("  - Installation details protected")
print()

print("=" * 80)
print("Run: python test_redaction.py for interactive demo")
print("=" * 80)
