#!/usr/bin/env python3
"""Test script for Autonomys integration.

This script demonstrates and tests the Autonomys Auto Drive integration:
1. Checks dependencies
2. Processes sample data
3. Tests local aggregation and file generation
4. (Optionally) uploads to Autonomys Auto Drive
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

# Add measurements to path
sys.path.insert(0, str(Path(__file__).parent))

from measurements.autonomys_writer import check_dependencies, autonomys_root_dir
from measurements.autonomys_orchestrator import (
    process_today_to_autonomys,
    process_yesterday_to_autonomys,
    upload_manifest_files
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

log = logging.getLogger(__name__)


def main():
    """Main test function."""
    print("=" * 70)
    print("Autonomys Auto Drive Integration Test")
    print("=" * 70)
    print()

    # Step 1: Check dependencies
    print("Step 1: Checking dependencies...")
    deps = check_dependencies()

    print(f"  - h3 library: {'✓ Available' if deps['h3'] else '✗ Missing'}")
    print(f"  - parquet (pandas/pyarrow): {'✓ Available' if deps['parquet'] else '✗ Missing'}")
    print()

    if not all(deps.values()):
        print("Missing dependencies! Install with:")
        print("  pip install -r requirements-autonomys.txt")
        return 1

    # Step 2: Get configuration
    print("Step 2: Configuration")
    print("  You need to provide:")
    print("    - Miner code (e.g., BM, IRM, ISM)")
    print("    - H3 hex ID (e.g., 871f90151ffffff)")
    print("    - Installation ID (optional)")
    print()

    # Use defaults from config if available
    try:
        from config_profile import MINER_CODE
        miner_code = MINER_CODE
        print(f"  Using miner code from config: {miner_code}")
    except ImportError:
        miner_code = input("  Enter miner code (default: BM): ").strip() or "BM"

    hex_id = input("  Enter H3 hex ID (default: 871f90151ffffff): ").strip() or "871f90151ffffff"
    install_id = input("  Enter installation ID (optional, press Enter to skip): ").strip() or None

    print()

    # Step 3: Test local processing (today's data)
    print("Step 3: Testing local processing...")
    print(f"  Processing today's data for {miner_code} at hex {hex_id}")
    print("  This will aggregate CSV data into hourly parquet files")
    print()

    results = process_today_to_autonomys(
        miner_code=miner_code,
        hex_id=hex_id,
        install_id=install_id,
        upload_to_cloud=False  # Don't upload during test
    )

    print("  Results:")
    for measurement_type, success in results.items():
        status = "✓ Success" if success else "✗ Failed"
        print(f"    - {measurement_type}: {status}")
    print()

    # Step 4: Show generated files
    print("Step 4: Generated files")
    autonomys_dir = autonomys_root_dir()
    print(f"  Autonomys root directory: {autonomys_dir}")
    print()

    if autonomys_dir.exists():
        print("  Folder structure:")
        for item in sorted(autonomys_dir.rglob("*")):
            if item.is_file():
                rel_path = item.relative_to(autonomys_dir)
                size = item.stat().st_size
                print(f"    {rel_path} ({size:,} bytes)")
    else:
        print("  No files generated yet.")
    print()

    # Step 5: Optional upload
    print("Step 5: Upload to Autonomys Auto Drive (optional)")
    print("  To upload, you need to set the AUTONOMYS_API_KEY environment variable")
    print("  Get your API key at: https://ai3.storage")
    print()

    import os
    if os.environ.get("AUTONOMYS_API_KEY"):
        upload = input("  AUTONOMYS_API_KEY found. Upload to Auto Drive? (y/N): ").strip().lower()

        if upload == 'y':
            print("  Uploading manifests...")
            upload_manifest_files(hex_id)

            print("  Re-processing with upload enabled...")
            results = process_today_to_autonomys(
                miner_code=miner_code,
                hex_id=hex_id,
                install_id=install_id,
                upload_to_cloud=True
            )

            print("  Upload results:")
            for measurement_type, success in results.items():
                status = "✓ Success" if success else "✗ Failed"
                print(f"    - {measurement_type}: {status}")
        else:
            print("  Skipping upload.")
    else:
        print("  AUTONOMYS_API_KEY not set. Skipping upload.")
    print()

    # Step 6: Summary
    print("=" * 70)
    print("Test Complete!")
    print("=" * 70)
    print()
    print("Next steps:")
    print("  1. Review the generated files in:", autonomys_dir)
    print("  2. Set AUTONOMYS_API_KEY to enable cloud uploads")
    print("  3. Run daily job to process yesterday's data:")
    print("     from measurements.autonomys_orchestrator import process_yesterday_to_autonomys")
    print(f"     process_yesterday_to_autonomys('{miner_code}', '{hex_id}')")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
