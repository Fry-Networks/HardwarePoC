"""Autonomys orchestrator - integrates aggregation and upload with existing collectors.

This module provides the main entry point for dual-write functionality:
1. Continue existing MongoDB uploads (via hardwareapi)
2. Aggregate measurements hourly
3. Write to local Autonomys folder structure
4. Upload to Autonomys Auto Drive
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .csv_writer import read_all_rows, _measurements_dir
from .autonomys_writer import (
    ensure_autonomys_structure,
    create_or_update_hex_manifest,
    create_or_update_root_metadata,
    check_dependencies
)
from .autonomys_aggregator import (
    aggregate_measurement_hourly,
    write_hourly_parquet,
    create_hourly_metadata,
    write_hourly_metadata
)
from .autonomys_uploader import get_uploader
from .autonomys_redactor import DataRedactor, get_redaction_level_info

log = logging.getLogger("measurements.autonomys_orchestrator")


def process_daily_csv_to_autonomys(
    miner_code: str,
    measurement_type: str,
    hex_id: str,
    date_str: Optional[str] = None,
    install_id: Optional[str] = None,
    upload_to_cloud: bool = True,
    redaction_level: str = 'standard'
) -> bool:
    """Process daily CSV data and write to Autonomys structure.

    This function:
    1. Reads CSV data for the day
    2. Aggregates into hourly intervals
    3. **REDACTS sensitive information for public storage**
    4. Writes parquet files to local Autonomys folder
    5. Updates manifests
    6. Optionally uploads redacted data to Autonomys Auto Drive

    Args:
        miner_code: Miner type code (BM, ISM, etc.)
        measurement_type: Type of measurement (bandwidth, satellite, etc.)
        hex_id: H3 hex ID for this location
        date_str: Date string (YYYYMMDD), defaults to today
        install_id: Installation UUID
        upload_to_cloud: Whether to upload to Autonomys Auto Drive

    Returns:
        True if successful
    """
    # Check dependencies
    deps = check_dependencies()
    if not deps['h3']:
        log.error("h3 library required for Autonomys integration")
        return False
    if not deps['parquet']:
        log.error("pandas/pyarrow required for Autonomys integration")
        return False

    try:
        # Default to today
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

        # Read CSV data (from "real" dataset - 10min cadence historical data)
        rows = read_all_rows(measurement_type, miner_code, date_str, dataset="real")

        if not rows:
            log.debug("No data found for %s on %s", measurement_type, date_str)
            return False

        log.info("Processing %d rows for %s on %s", len(rows), measurement_type, date_str)

        # Aggregate to hourly
        hourly_df = aggregate_measurement_hourly(measurement_type, rows)

        if hourly_df is None or hourly_df.empty:
            log.warning("Aggregation produced no data for %s on %s", measurement_type, date_str)
            return False

        # Apply redaction to protect sensitive information
        redactor = DataRedactor(redaction_level=redaction_level)

        # Redact measurement data based on type
        if measurement_type == 'bandwidth':
            hourly_df = redactor.redact_bandwidth_data(hourly_df)
        elif measurement_type == 'satellite':
            hourly_df = redactor.redact_satellite_data(hourly_df)
        elif measurement_type == 'radiation':
            hourly_df = redactor.redact_radiation_data(hourly_df)
        elif measurement_type == 'decibel':
            hourly_df = redactor.redact_decibel_data(hourly_df)
        elif measurement_type == 'aem':
            hourly_df = redactor.redact_aem_data(hourly_df)

        log.info("Applied %s redaction to %s data", redaction_level, measurement_type)

        # Get redacted hex_id for folder structure
        redacted_hex_id = redactor.redact_location(hex_id,
            target_resolution=get_redaction_level_info(redaction_level)['hex_resolution'])

        # Ensure Autonomys folder structure exists (using redacted hex)
        hourly_dir, daily_dir = ensure_autonomys_structure(redacted_hex_id, measurement_type)

        # Format date for filenames (YYYY-MM-DD)
        date_obj = datetime.strptime(date_str, "%Y%m%d")
        formatted_date = date_obj.strftime("%Y-%m-%d")

        # Write hourly parquet file
        parquet_path = hourly_dir / f"{formatted_date}.parquet"
        if not write_hourly_parquet(hourly_df, parquet_path):
            log.error("Failed to write parquet for %s on %s", measurement_type, date_str)
            return False

        # Create and write hourly metadata (using redacted hex_id)
        metadata = create_hourly_metadata(hourly_df, formatted_date, redacted_hex_id, measurement_type)
        metadata_path = hourly_dir / f"{formatted_date}.meta.json"
        write_hourly_metadata(metadata, metadata_path)

        # Update hex manifest
        first_timestamp = datetime.fromisoformat(rows[0]['timestamp'])
        last_timestamp = datetime.fromisoformat(rows[-1]['timestamp'])

        # Create manifest with original hex_id first
        manifest_created = create_or_update_hex_manifest(
            hex_id=redacted_hex_id,  # Use redacted hex_id
            measurement_type=measurement_type,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
            sample_count=len(rows),
            install_id=None,  # Don't include install_id in public data
            miner_code=None   # Don't include miner_code in public data
        )

        # Apply manifest redaction
        if manifest_created:
            from .autonomys_writer import get_hex_resolution, autonomys_root_dir
            resolution = get_hex_resolution(redacted_hex_id)
            root = autonomys_root_dir()
            manifest_path = root / f"res-{resolution}" / redacted_hex_id / "manifest.json"

            if manifest_path.exists():
                import json
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)

                # Redact the manifest
                redacted_manifest = redactor.redact_manifest(manifest, redacted_hex_id)

                # Write back redacted version
                with open(manifest_path, 'w', encoding='utf-8') as f:
                    json.dump(redacted_manifest, f, indent=2)

        # Update root metadata
        create_or_update_root_metadata()

        # Upload to Autonomys Auto Drive if requested
        if upload_to_cloud:
            uploader = get_uploader()
            if uploader:
                try:
                    # Upload parquet file (using redacted hex_id)
                    from .autonomys_writer import get_hex_resolution
                    resolution = get_hex_resolution(redacted_hex_id)
                    remote_path = f"res-{resolution}/{redacted_hex_id}/{measurement_type}/hourly/{formatted_date}.parquet"
                    uploader.upload_file(parquet_path, remote_path)

                    # Upload metadata file
                    remote_meta_path = f"res-{resolution}/{redacted_hex_id}/{measurement_type}/hourly/{formatted_date}.meta.json"
                    uploader.upload_file(metadata_path, remote_meta_path)

                    # Upload redacted manifest
                    manifest_remote_path = f"res-{resolution}/{redacted_hex_id}/manifest.json"
                    uploader.upload_file(manifest_path, manifest_remote_path)

                    log.info("Uploaded redacted %s data to Autonomys Auto Drive (redaction: %s)",
                             measurement_type, redaction_level)
                except Exception as e:
                    log.error("Failed to upload to Autonomys: %s", e)
                    # Don't fail the whole operation if upload fails
            else:
                log.warning("Autonomys uploader not available, skipping cloud upload")

        return True

    except Exception as e:
        log.error("Failed to process CSV to Autonomys: %s", e)
        return False


def process_yesterday_to_autonomys(
    miner_code: str,
    hex_id: str,
    install_id: Optional[str] = None,
    upload_to_cloud: bool = True
) -> Dict[str, bool]:
    """Process yesterday's data for all measurement types.

    This is the main daily job that should run once per day to process
    the previous day's complete data.

    Args:
        miner_code: Miner type code
        hex_id: H3 hex ID
        install_id: Installation UUID
        upload_to_cloud: Whether to upload to Autonomys Auto Drive

    Returns:
        Dict mapping measurement_type to success status
    """
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")

    # Determine which measurement types to process based on miner code
    measurement_types = []

    if miner_code == "BM":
        measurement_types = ["bandwidth"]
    elif miner_code in ("ISM", "OSM"):
        measurement_types = ["satellite"]
    elif miner_code == "IRM":
        measurement_types = ["radiation"]
    elif miner_code in ("IDM", "ODM"):
        measurement_types = ["decibel"]
    elif miner_code == "AEM":
        measurement_types = ["aem"]
    elif miner_code in ("RDN", "SDN", "SVN"):
        # These miner types don't collect sensor measurements
        log.debug("Miner type %s does not collect measurements", miner_code)
        return {}  # No measurements to process
    else:
        log.warning("Unknown miner code: %s", miner_code)
        return {}

    results = {}

    for measurement_type in measurement_types:
        log.info("Processing %s for %s", measurement_type, yesterday)
        success = process_daily_csv_to_autonomys(
            miner_code=miner_code,
            measurement_type=measurement_type,
            hex_id=hex_id,
            date_str=yesterday,
            install_id=install_id,
            upload_to_cloud=upload_to_cloud
        )
        results[measurement_type] = success

    return results


def process_today_to_autonomys(
    miner_code: str,
    hex_id: str,
    install_id: Optional[str] = None,
    upload_to_cloud: bool = False
) -> Dict[str, bool]:
    """Process today's data for all measurement types (for testing/preview).

    This can be called during the day to see partial results.
    By default, doesn't upload to cloud since data is incomplete.

    Args:
        miner_code: Miner type code
        hex_id: H3 hex ID
        install_id: Installation UUID
        upload_to_cloud: Whether to upload to Autonomys Auto Drive

    Returns:
        Dict mapping measurement_type to success status
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    # Determine measurement types
    measurement_types = []

    if miner_code == "BM":
        measurement_types = ["bandwidth"]
    elif miner_code in ("ISM", "OSM"):
        measurement_types = ["satellite"]
    elif miner_code == "IRM":
        measurement_types = ["radiation"]
    elif miner_code in ("IDM", "ODM"):
        measurement_types = ["decibel"]
    elif miner_code == "AEM":
        measurement_types = ["aem"]
    elif miner_code in ("RDN", "SDN", "SVN"):
        # These miner types don't collect sensor measurements
        log.debug("Miner type %s does not collect measurements", miner_code)
        measurement_types = []
    else:
        log.warning("Unknown miner code: %s", miner_code)
        measurement_types = []

    results = {}

    for measurement_type in measurement_types:
        log.info("Processing %s for today (%s)", measurement_type, today)
        success = process_daily_csv_to_autonomys(
            miner_code=miner_code,
            measurement_type=measurement_type,
            hex_id=hex_id,
            date_str=today,
            install_id=install_id,
            upload_to_cloud=upload_to_cloud
        )
        results[measurement_type] = success

    return results


def backfill_autonomys_data(
    miner_code: str,
    hex_id: str,
    start_date: str,
    end_date: str,
    install_id: Optional[str] = None,
    upload_to_cloud: bool = True
) -> Dict[str, int]:
    """Backfill historical data to Autonomys structure.

    Processes all days between start_date and end_date.

    Args:
        miner_code: Miner type code
        hex_id: H3 hex ID
        start_date: Start date (YYYYMMDD)
        end_date: End date (YYYYMMDD)
        install_id: Installation UUID
        upload_to_cloud: Whether to upload to Autonomys Auto Drive

    Returns:
        Dict with success counts per measurement type
    """
    # Determine measurement types
    measurement_types = []

    if miner_code == "BM":
        measurement_types = ["bandwidth"]
    elif miner_code in ("ISM", "OSM"):
        measurement_types = ["satellite"]
    elif miner_code == "IRM":
        measurement_types = ["radiation"]
    elif miner_code in ("IDM", "ODM"):
        measurement_types = ["decibel"]
    elif miner_code == "AEM":
        measurement_types = ["aem"]
    elif miner_code in ("RDN", "SDN", "SVN"):
        # These miner types don't collect sensor measurements
        log.debug("Miner type %s does not collect measurements", miner_code)
        measurement_types = []
    else:
        log.warning("Unknown miner code: %s", miner_code)
        measurement_types = []

    # Parse dates
    start_dt = datetime.strptime(start_date, "%Y%m%d")
    end_dt = datetime.strptime(end_date, "%Y%m%d")

    # Count successes per measurement type
    results = {mt: 0 for mt in measurement_types}

    # Iterate through dates
    current_dt = start_dt
    while current_dt <= end_dt:
        date_str = current_dt.strftime("%Y%m%d")
        log.info("Backfilling %s", date_str)

        for measurement_type in measurement_types:
            success = process_daily_csv_to_autonomys(
                miner_code=miner_code,
                measurement_type=measurement_type,
                hex_id=hex_id,
                date_str=date_str,
                install_id=install_id,
                upload_to_cloud=upload_to_cloud
            )

            if success:
                results[measurement_type] += 1

        current_dt += timedelta(days=1)

    log.info("Backfill complete: %s", results)
    return results


def upload_manifest_files(hex_id: str) -> bool:
    """Upload manifest files for a hex to Autonomys Auto Drive.

    Args:
        hex_id: H3 hex ID

    Returns:
        True if successful
    """
    uploader = get_uploader()
    if not uploader:
        log.error("Autonomys uploader not available")
        return False

    try:
        from .autonomys_writer import get_hex_resolution, autonomys_root_dir
        resolution = get_hex_resolution(hex_id)

        root = autonomys_root_dir()

        # Upload hex manifest
        hex_manifest = root / f"res-{resolution}" / hex_id / "manifest.json"
        if hex_manifest.exists():
            remote_path = f"res-{resolution}/{hex_id}/manifest.json"
            uploader.upload_file(hex_manifest, remote_path)

        # Upload root metadata
        root_metadata = root / "metadata.json"
        if root_metadata.exists():
            uploader.upload_file(root_metadata, "metadata.json")

        log.info("Uploaded manifest files for hex %s", hex_id)
        return True

    except Exception as e:
        log.error("Failed to upload manifests: %s", e)
        return False
