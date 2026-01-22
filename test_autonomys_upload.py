#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Autonomys Upload with Real BM Data

This script processes real bandwidth miner data and tests the Autonomys
upload pipeline with res-5 redaction.
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding
if sys.platform == "win32":
    os.system('chcp 65001 > nul')

# Add measurements module to path
sys.path.insert(0, str(Path(__file__).parent / "measurements"))

try:
    from measurements.autonomys_orchestrator import process_daily_csv_to_autonomys
    from measurements.autonomys_redactor import get_redaction_level_info
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("\nMake sure you're running this from the project root directory.")
    sys.exit(1)

print("=" * 80)
print(" " * 20 + "AUTONOMYS UPLOAD TEST - RES-5 PRIVACY")
print("=" * 80)
print()

# Configuration
MINER_CODE = "BM"  # Bandwidth Miner
MEASUREMENT_TYPE = "bandwidth"

# Example hex_id - replace with your actual hex_id if you know it
# This is a res-7 hex for Manhattan, NYC area
HEX_ID = "871f90151ffffff"

# Test with yesterday's data (should be complete)
yesterday = "20260121"  # January 21, 2026

print("Test Configuration:")
print(f"  Miner Code: {MINER_CODE}")
print(f"  Measurement Type: {MEASUREMENT_TYPE}")
print(f"  Date: {yesterday}")
print(f"  Original Hex ID: {HEX_ID} (res-7, ~5 km²)")
print()

# Get redaction info
redaction_info = get_redaction_level_info('standard')
print("Redaction Settings (STANDARD):")
print(f"  Target Resolution: res-{redaction_info['hex_resolution']}")
print(f"  Area Coverage: ~252 km² (50× larger)")
print(f"  Noise Added: {redaction_info['noise_percent']}%")
print(f"  Identifiers Removed: {redaction_info['remove_identifiers']}")
print()

print("=" * 80)
print("STEP 1: LOCAL PROCESSING (No Upload)")
print("=" * 80)
print()
print("Processing data locally first to verify it works...")
print()

# First test: Process locally without uploading
success = process_daily_csv_to_autonomys(
    miner_code=MINER_CODE,
    measurement_type=MEASUREMENT_TYPE,
    hex_id=HEX_ID,
    date_str=yesterday,
    install_id=None,
    upload_to_cloud=False,  # Don't upload yet
    redaction_level='standard'
)

if success:
    print("[OK] SUCCESS: Data processed and redacted successfully!")
    print()
    print("Files created locally:")
    print(f"  C:\\ProgramData\\FryNetworks\\miner-{MINER_CODE}\\measurements\\hourly\\")
    print(f"    - 2026-01-21.parquet")
    print(f"    - 2026-01-21.meta.json")
    print()
    print("Note: Local files do NOT contain hexID in path (privacy preserved)")
    print()

    # Show what was redacted
    from measurements.autonomys_writer import get_hex_resolution, get_parent_hex
    try:
        redacted_hex = get_parent_hex(HEX_ID, 5)
        print(f"  Original hex: {HEX_ID} (res-7)")
        print(f"  Redacted hex: {redacted_hex} (res-5) - used for Autonomys upload path")
        print()
    except Exception as e:
        print(f"  Note: Could not calculate redacted hex: {e}")
        print()

    print("=" * 80)
    print("STEP 2: UPLOAD TO AUTONOMYS (OPTIONAL)")
    print("=" * 80)
    print()
    print("Would you like to upload to Autonomys now? (y/n)")
    response = input("> ").strip().lower()

    if response == 'y':
        print()
        print("Uploading redacted data to Autonomys...")
        print()

        success_upload = process_daily_csv_to_autonomys(
            miner_code=MINER_CODE,
            measurement_type=MEASUREMENT_TYPE,
            hex_id=HEX_ID,
            date_str=yesterday,
            install_id=None,
            upload_to_cloud=True,  # Upload now!
            redaction_level='standard'
        )

        if success_upload:
            print()
            print("[OK] SUCCESS: Data uploaded to Autonomys!")
            print()
            from measurements.autonomys_writer import get_parent_hex
            try:
                redacted_hex = get_parent_hex(HEX_ID, 5)
                print(f"Autonomys Drive Structure:")
                print(f"  {redacted_hex}/")
                print(f"    └── bandwidth/")
                print(f"         └── hourly/")
                print(f"              ├── 2026-01-21.parquet")
                print(f"              └── 2026-01-21.meta.json")
            except:
                pass
            print()
            print("Your data is now publicly visible on Autonomys with:")
            print("  - Location: res-5 (~252 km area)")
            print("  - Measurements: Rounded + 5% noise")
            print("  - Identifiers: Removed")
            print()
            print("Original data remains private on your local machine.")
        else:
            print()
            print("❌ FAILED: Upload to Autonomys failed.")
            print("Check logs for details.")
    else:
        print()
        print("Skipping upload. Your redacted data is ready locally.")
        print("Run this script again and choose 'y' when ready to upload.")

else:
    print("[FAIL] FAILED: Could not process data.")
    print()
    print("Possible issues:")
    print("  1. No data found for date:", yesterday)
    print("  2. CSV file path incorrect")
    print("  3. Dependencies missing (h3, pandas, pyarrow)")
    print()
    print("Check the measurements directory:")
    print(f"  C:\\ProgramData\\FryNetworks\\measurements\\bandwidth\\")
    print()
    print("Available dates:")
    import os
    meas_dir = Path("C:/ProgramData/FryNetworks/miner-BM/measurements")
    if meas_dir.exists():
        files = list(meas_dir.glob("bandwidth_real_*.csv"))
        for f in sorted(files):
            print(f"  - {f.name}")
    else:
        print("  (measurements directory not found)")

print()
print("=" * 80)
print("TEST COMPLETE")
print("=" * 80)
