#!/usr/bin/env python3
"""Verify the parquet file structure and content."""

import pandas as pd
from pathlib import Path

parquet_file = Path("C:/ProgramData/FryNetworks/miner-BM/measurements/hourly/2026-01-21.parquet")

if parquet_file.exists():
    print("Reading parquet file:", parquet_file)
    print("=" * 80)

    df = pd.read_parquet(parquet_file)

    print(f"\nShape: {df.shape[0]} rows x {df.shape[1]} columns")
    print(f"\nColumns: {list(df.columns)}")
    print(f"\nFirst 3 rows:")
    print(df.head(3).to_string())

    print("\n" + "=" * 80)
    print("VERIFICATION:")
    print("=" * 80)

    # Check for privacy-sensitive columns that should NOT be present
    sensitive_columns = ['install_id', 'device_id', 'uuid', 'mac_address', 'exact_location']
    found_sensitive = [col for col in sensitive_columns if col in df.columns]

    if found_sensitive:
        print(f"WARNING: Found sensitive columns: {found_sensitive}")
    else:
        print("[OK] No sensitive identifiers found in data")

    # Verify timestamp format
    if 'hour_start' in df.columns:
        print(f"[OK] hour_start column present")
        print(f"     Sample: {df['hour_start'].iloc[0]}")

    # Check measurement columns
    measurement_cols = [col for col in df.columns if col not in ['hour_start']]
    print(f"[OK] Measurement columns: {measurement_cols}")

else:
    print("ERROR: File not found:", parquet_file)
