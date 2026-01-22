#!/usr/bin/env python3
"""Test script to demonstrate data redaction for Autonomys uploads.

This script shows how redaction works at different levels and what
data is protected vs. what remains visible.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    import pandas as pd
    from measurements.autonomys_redactor import DataRedactor, get_redaction_level_info
except ImportError as e:
    print(f"Error: {e}")
    print("\nPlease install required dependencies:")
    print("  pip install pandas h3")
    sys.exit(1)


def demo_bandwidth_redaction():
    """Demonstrate bandwidth data redaction."""
    print("=" * 70)
    print("BANDWIDTH DATA REDACTION DEMO")
    print("=" * 70)

    # Sample bandwidth data (one hour)
    data = {
        'date': ['2026-01-20'] * 3,
        'hour': [14, 15, 16],
        'dl_avg_mbps': [85.3, 92.1, 78.4],
        'dl_min_mbps': [45.2, 51.3, 42.1],
        'dl_max_mbps': [124.7, 135.2, 118.9],
        'ul_avg_mbps': [12.5, 13.8, 11.2],
        'ul_min_mbps': [8.1, 9.2, 7.5],
        'ul_max_mbps': [18.3, 19.7, 16.8],
        'sample_count': [6, 6, 6],
        'iface': ['wlan0', 'wlan0', 'wlan0']
    }

    df_original = pd.DataFrame(data)

    print("\nORIGINAL DATA:")
    print(df_original.to_string(index=False))

    # Test each redaction level
    for level in ['minimal', 'standard', 'full']:
        print(f"\n{level.upper()} REDACTION:")
        info = get_redaction_level_info(level)
        print(f"  Description: {info['description']}")

        redactor = DataRedactor(redaction_level=level)
        df_redacted = redactor.redact_bandwidth_data(df_original.copy())

        print(df_redacted.to_string(index=False))


def demo_location_redaction():
    """Demonstrate location redaction (hex coarsening)."""
    print("\n\n" + "=" * 70)
    print("LOCATION REDACTION DEMO")
    print("=" * 70)

    original_hex = "871f90151ffffff"  # Resolution 7 (~5 km²)

    print(f"\nOriginal hex: {original_hex} (res-7, ~5 km² area)")

    for level in ['minimal', 'standard', 'full']:
        info = get_redaction_level_info(level)
        target_res = info['hex_resolution']

        redactor = DataRedactor(redaction_level=level)
        redacted_hex = redactor.redact_location(original_hex, target_resolution=target_res)

        # Calculate area increase
        area_multipliers = {7: 1, 5: 50, 4: 260}
        multiplier = area_multipliers.get(target_res, 1)

        print(f"\n{level.upper()} redaction:")
        print(f"  Redacted hex: {redacted_hex} (res-{target_res}, ~{5 * multiplier} km² area)")
        print(f"  Privacy gain: {multiplier}× larger area")


def demo_manifest_redaction():
    """Demonstrate manifest redaction."""
    print("\n\n" + "=" * 70)
    print("MANIFEST REDACTION DEMO")
    print("=" * 70)

    # Sample manifest
    manifest = {
        "hex_id": "871f90151ffffff",
        "resolution": 7,
        "center": {
            "lat": 40.712776,
            "lon": -74.005974
        },
        "coverage": {
            "start": "2025-06-01T00:00:00Z",
            "end": "2026-01-20T23:59:59Z",
            "total_days": 234
        },
        "install_ids": ["550e8400-e29b-41d4-a716-446655440000"],
        "measurements": {
            "bandwidth": {
                "hourly_files": 234,
                "total_samples": 33696
            }
        }
    }

    print("\nORIGINAL MANIFEST:")
    import json
    print(json.dumps(manifest, indent=2))

    redactor = DataRedactor(redaction_level='standard')
    redacted_hex = redactor.redact_location("871f90151ffffff", target_resolution=5)
    redacted_manifest = redactor.redact_manifest(manifest, redacted_hex)

    print("\nREDACTED MANIFEST (standard level):")
    print(json.dumps(redacted_manifest, indent=2))

    print("\nKEY CHANGES:")
    print("  * Hex ID coarsened (res-7 -> res-5)")
    print("  * GPS coordinates rounded (5+ decimals -> 1 decimal)")
    print("  * Install IDs removed (replaced with count)")
    print("  * Coverage dates kept (useful for buyers)")
    print("  * Sample counts kept (indicates data quality)")


def main():
    """Run all redaction demos."""
    print("\n")
    print("=" * 70)
    print(" " * 15 + "AUTONOMYS DATA REDACTION DEMO")
    print("=" * 70)

    demo_bandwidth_redaction()
    demo_location_redaction()
    demo_manifest_redaction()

    print("\n\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print()
    print("Redaction protects your competitive advantage while allowing you to:")
    print()
    print("1. SHARE: Post redacted data on Autonomys as a preview/sample")
    print("2. SELL: Offer full-resolution data privately for higher prices")
    print("3. COMPETE: Keep exact locations and measurements secret")
    print()
    print("Recommended approach:")
    print("- Use 'standard' redaction for Autonomys uploads (default)")
    print("- Sell unredacted data through private channels")
    print("- Treat Autonomys as marketing/lead generation")
    print()


if __name__ == "__main__":
    main()
