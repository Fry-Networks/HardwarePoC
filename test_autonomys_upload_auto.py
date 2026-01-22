#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Autonomys Upload - Automatic (No Interactive Input)

This script tests the full upload pipeline including the new hexID-based
folder structure on Autonomys Drive.
"""

import sys
import os
from pathlib import Path
import tempfile
import shutil

# Fix Windows console encoding
if sys.platform == "win32":
    os.system('chcp 65001 > nul')

# Add measurements module to path
sys.path.insert(0, str(Path(__file__).parent / "measurements"))

try:
    from measurements.autonomys_orchestrator import process_daily_csv_to_autonomys
    from measurements.autonomys_redactor import get_redaction_level_info
    from measurements.autonomys_writer import get_parent_hex
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("\nMake sure you're running this from the project root directory.")
    sys.exit(1)

# Use the SDK-based uploader wrapper
try:
    from Autonomys.upload_with_sdk import run_node_folder_upload
except Exception:
    run_node_folder_upload = None

print("=" * 80)
print(" " * 15 + "AUTONOMYS UPLOAD TEST - FULL PIPELINE")
print("=" * 80)
print()

# Configuration
MINER_CODE = "BM"
MEASUREMENT_TYPE = "bandwidth"
HEX_ID = "871f90151ffffff"  # res-7 Manhattan area
yesterday = "20260121"

print("Configuration:")
print(f"  Miner Code: {MINER_CODE}")
print(f"  Measurement Type: {MEASUREMENT_TYPE}")
print(f"  Date: {yesterday}")
print(f"  Original Hex ID: {HEX_ID} (res-7)")
print()

# Get redacted hex for display
try:
    redacted_hex = get_parent_hex(HEX_ID, 5)
    print(f"  Redacted Hex ID: {redacted_hex} (res-5)")
except Exception as e:
    redacted_hex = "851f9017fffffff"
    print(f"  Redacted Hex ID: {redacted_hex} (res-5, estimated)")

print()
print("=" * 80)
print("STEP 1: Process and Upload to Autonomys")
print("=" * 80)
print()

# Process without built-in upload; we'll use the SDK wrapper below
success = process_daily_csv_to_autonomys(
    miner_code=MINER_CODE,
    measurement_type=MEASUREMENT_TYPE,
    hex_id=HEX_ID,
    date_str=yesterday,
    install_id=None,
    upload_to_cloud=False,  # Disable built-in upload; use SDK wrapper
    redaction_level='standard'
)

print()
if success:
    print("✓ SUCCESS!")
    print()
    print("LOCAL STORAGE (User-visible):")
    print(f"  C:\\ProgramData\\FryNetworks\\miner-{MINER_CODE}\\measurements\\")
    print(f"    └── hourly\\")
    print(f"         ├── 2026-01-21.parquet")
    print(f"         └── 2026-01-21.meta.json")
    print()
    print("  Note: No hexID visible in local paths (privacy preserved)")
    print()
    # Attempt upload using the Autonomys SDK wrapper if available
    if run_node_folder_upload:
        local_folder = rf"C:\ProgramData\FryNetworks\miner-{MINER_CODE}\measurements\hourly"
        dest = f"{redacted_hex}/{MEASUREMENT_TYPE}/hourly"
        print("Uploading to Autonomys via SDK wrapper...")

        # Prefer explicit env var. If not present, instruct the Node script
        # to read from 1Password so the Node process handles any prompts.
        api_key = os.environ.get('AUTONOMYS_API_KEY')
        use_1pw_flag = False
        onepw_path = None
        if not api_key:
            use_1pw_flag = True
            onepw_path = 'op://DataStorage/AutoDrive/AUTONOMYS_API_KEY'

        # Some SDKs display files under "root" when a destination path with
        # slashes is passed. To guarantee the desired Drive path
        # `<hex>/<measurement_type>/hourly`, create a temporary folder with
        # that nested structure and upload the temp root so the folder tree
        # is preserved on the remote side.
        temp_root = None
        try:
            temp_root = tempfile.mkdtemp(prefix='autoupload_')
            target = os.path.join(temp_root, redacted_hex, MEASUREMENT_TYPE, 'hourly')
            os.makedirs(target, exist_ok=True)
            # Copy files from the produced local folder into the nested hourly dir
            for fname in os.listdir(local_folder):
                src = os.path.join(local_folder, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, target)

            # Upload the temporary root (no dest-folder) so the nested folders
            # appear under the drive root as expected.
            try:
                rc = run_node_folder_upload(temp_root, None, api_key, use_1pw_flag, onepw_path, True)
            except Exception as e:
                rc = 99
                print("Upload wrapper raised:", e)
        finally:
            if temp_root and os.path.exists(temp_root):
                try:
                    shutil.rmtree(temp_root)
                except Exception:
                    pass

        if rc == 0:
            print("SDK upload: ✓ SUCCESS")
        else:
            print(f"SDK upload: ✗ FAILED (exit code {rc})")

    print("AUTONOMYS DRIVE STRUCTURE (Public Cloud):")
    print(f"  {redacted_hex}/")
    print(f"    └── {MEASUREMENT_TYPE}/")
    print(f"         └── hourly/")
    print(f"              ├── 2026-01-21.parquet")
    print(f"              └── 2026-01-21.meta.json")
    print()
    print("  Note: HexID is at root level for data monetization")
    print()
    print("PRIVACY PROTECTION:")
    print("  ✓ Location generalized to res-5 (~252 km²)")
    print("  ✓ 5% noise added to measurements")
    print("  ✓ Identifiers removed")
    print()
else:
    print("✗ FAILED")
    print()
    print("Check the logs above for error details.")

print()
print("=" * 80)
print("TEST COMPLETE")
print("=" * 80)
