#!/usr/bin/env python3
"""Test Autonomys REST API upload with actual data."""

import sys
import os
import logging
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding
if sys.platform == "win32":
    os.system('chcp 65001 > nul 2>&1')

# Enable debug logging to see request/response details
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Add measurements module to path
sys.path.insert(0, str(Path(__file__).parent / "measurements"))

try:
    from measurements.autonomys_uploader_rest import get_rest_uploader
    from measurements.autonomys_writer import get_parent_hex
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("\nMake sure you're running this from the project root directory.")
    sys.exit(1)

print("=" * 80)
print(" " * 20 + "AUTONOMYS REST API UPLOAD TEST")
print("=" * 80)
print()

# Configuration
MINER_CODE = "BM"
MEASUREMENT_TYPE = "bandwidth"
HEX_ID = "871f90151ffffff"  # res-7
DATE_STR = "20260121"

print("Test Configuration:")
print(f"  Miner Code: {MINER_CODE}")
print(f"  Measurement Type: {MEASUREMENT_TYPE}")
print(f"  Date: {DATE_STR}")
print(f"  Original Hex ID: {HEX_ID} (res-7)")
print()

# Get redacted hex
try:
    redacted_hex = get_parent_hex(HEX_ID, 5)
    print(f"  Redacted Hex ID: {redacted_hex} (res-5)")
except Exception as e:
    redacted_hex = "851f9017fffffff"
    print(f"  Redacted Hex ID: {redacted_hex} (res-5, fallback)")

print()
print("=" * 80)
print("STEP 1: Initialize REST Uploader")
print("=" * 80)
print()

# Get uploader
uploader = get_rest_uploader()
if not uploader:
    print("[FAIL] Could not initialize REST uploader")
    print("  Check that your API key is in 1Password (AutoDrive item)")
    sys.exit(1)

print("[OK] REST uploader initialized")
print(f"  Endpoint: {uploader.endpoint_url}")
print()

print("=" * 80)
print("STEP 2: Check for Local Files")
print("=" * 80)
print()

# Check for files
data_dir = Path(f"C:/ProgramData/FryNetworks/miner-{MINER_CODE}/measurements/hourly")
parquet_file = data_dir / "2026-01-21.parquet"
meta_file = data_dir / "2026-01-21.meta.json"

print(f"Looking for files in: {data_dir}")
print()

if not parquet_file.exists():
    print(f"[FAIL] Parquet file not found: {parquet_file}")
    print("  Run the data processing first to create this file")
    sys.exit(1)

if not meta_file.exists():
    print(f"[FAIL] Metadata file not found: {meta_file}")
    sys.exit(1)

print(f"[OK] Found parquet file: {parquet_file.name} ({parquet_file.stat().st_size} bytes)")
print(f"[OK] Found metadata file: {meta_file.name} ({meta_file.stat().st_size} bytes)")
print()

print("=" * 80)
print("STEP 3: Upload to Autonomys Auto Drive")
print("=" * 80)
print()

# Parse date
date_obj = datetime.strptime(DATE_STR, "%Y%m%d")

# Upload parquet
print(f"Uploading: {parquet_file.name}")
print(f"  Remote name: {redacted_hex}_{MEASUREMENT_TYPE}_{date_obj.strftime('%Y-%m-%d')}.parquet")
print()

cid_parquet = uploader.upload_daily_file(
    local_path=parquet_file,
    hex_id=redacted_hex,
    measurement_type=MEASUREMENT_TYPE,
    date=date_obj
)

if cid_parquet:
    print(f"[OK] Parquet uploaded successfully!")
    print(f"  CID: {cid_parquet}")
else:
    print("[FAIL] Parquet upload failed")
    print("  Check logs for error details")

print()

# Upload metadata
print(f"Uploading: {meta_file.name}")
print(f"  Remote name: {redacted_hex}_{MEASUREMENT_TYPE}_{date_obj.strftime('%Y-%m-%d')}.meta.json")
print()

cid_meta = uploader.upload_daily_file(
    local_path=meta_file,
    hex_id=redacted_hex,
    measurement_type=MEASUREMENT_TYPE,
    date=date_obj
)

if cid_meta:
    print(f"[OK] Metadata uploaded successfully!")
    print(f"  CID: {cid_meta}")
else:
    print("[FAIL] Metadata upload failed")

print()
print("=" * 80)
print("TEST SUMMARY")
print("=" * 80)
print()

if cid_parquet and cid_meta:
    print("[SUCCESS] Both files uploaded successfully!")
    print()
    print("Uploaded files:")
    print(f"  1. {redacted_hex}_{MEASUREMENT_TYPE}_{date_obj.strftime('%Y-%m-%d')}.parquet")
    print(f"  2. {redacted_hex}_{MEASUREMENT_TYPE}_{date_obj.strftime('%Y-%m-%d')}.meta.json")
    print()
    print("Data is now on Autonomys Auto Drive with:")
    print("  - Flat file naming (no folders)")
    print("  - HexID prefix for data organization")
    print("  - Privacy protected (res-5, 5% noise, identifiers removed)")
else:
    print("[PARTIAL] Some uploads failed")
    print(f"  Parquet: {'OK' if cid_parquet else 'FAILED'}")
    print(f"  Metadata: {'OK' if cid_meta else 'FAILED'}")

print()
print("=" * 80)
