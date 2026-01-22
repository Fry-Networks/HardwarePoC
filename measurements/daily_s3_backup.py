"""Daily S3 backup script for original measurements.

This script should run once per day (e.g., via Windows Task Scheduler)
to upload the previous day's original measurement data to S3.

Unlike Autonomys uploads (which upload redacted data), this uploads
the original full-precision data for API server access.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from measurements.s3_uploader import get_s3_uploader
from measurements.csv_writer import _measurements_dir

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger("measurements.daily_s3_backup")


def get_original_measurements_path() -> Path:
    """Get path to original measurements directory.

    Returns:
        Path to C:\ProgramData\FryNetworks\measurements\
    """
    try:
        base = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
        return base / "FryNetworks" / "measurements"
    except Exception:
        return Path("C:\\ProgramData\\FryNetworks\\measurements")


def upload_yesterday_measurements(
    miner_code: str = "BM",
    measurement_types: List[str] = None
) -> Dict[str, Dict[str, bool]]:
    """Upload yesterday's measurements to S3.

    Args:
        miner_code: Miner code (used to determine measurement types if not specified)
        measurement_types: List of measurement types to upload (auto-detect if None)

    Returns:
        Dict mapping measurement_type -> {date: success}
    """
    # Get S3 uploader
    uploader = get_s3_uploader()
    if not uploader:
        log.error("S3 uploader not configured. Check config or environment variables.")
        return {}

    # Determine measurement types if not provided
    if not measurement_types:
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
        else:
            log.warning(f"Unknown miner code {miner_code}, attempting all types")
            measurement_types = ["bandwidth", "decibel", "radiation", "satellite", "aem"]

    # Get yesterday's date
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1))
    date_str = yesterday.strftime("%Y-%m-%d")

    log.info(f"Uploading measurements for {date_str}")

    # Get measurements base path
    measurements_base = get_original_measurements_path()

    results = {}

    for mtype in measurement_types:
        hourly_dir = measurements_base / mtype / "hourly"

        if not hourly_dir.exists():
            log.debug(f"Directory not found: {hourly_dir}, skipping")
            continue

        # Look for yesterday's file
        parquet_file = hourly_dir / f"{date_str}.parquet"

        if not parquet_file.exists():
            log.info(f"No data for {mtype} on {date_str}")
            continue

        # Check if already uploaded (skip if exists)
        if uploader.file_exists(mtype, date_str):
            log.info(f"{mtype}/{date_str} already uploaded, skipping")
            results.setdefault(mtype, {})[date_str] = True
            continue

        # Upload file
        log.info(f"Uploading {mtype}/{date_str}")
        success = uploader.upload_file(
            local_path=parquet_file,
            measurement_type=mtype,
            date_str=date_str,
            metadata={
                "miner_code": miner_code,
                "uploaded_by": "daily_s3_backup"
            }
        )

        results.setdefault(mtype, {})[date_str] = success

        if success:
            log.info(f"✓ {mtype}/{date_str} uploaded successfully")
        else:
            log.error(f"✗ {mtype}/{date_str} upload failed")

    return results


def upload_date_range(
    start_date: str,
    end_date: str,
    miner_code: str = "BM",
    measurement_types: List[str] = None,
    skip_existing: bool = True
) -> Dict[str, Dict[str, bool]]:
    """Upload measurements for a date range (backfill).

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        miner_code: Miner code
        measurement_types: List of types to upload (auto-detect if None)
        skip_existing: Skip files already uploaded (default True)

    Returns:
        Dict mapping measurement_type -> {date: success}
    """
    uploader = get_s3_uploader()
    if not uploader:
        log.error("S3 uploader not configured")
        return {}

    # Determine measurement types
    if not measurement_types:
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
        else:
            measurement_types = ["bandwidth", "decibel", "radiation", "satellite", "aem"]

    # Parse dates
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    measurements_base = get_original_measurements_path()
    results = {}

    current_dt = start_dt
    while current_dt <= end_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        log.info(f"Processing {date_str}")

        for mtype in measurement_types:
            hourly_dir = measurements_base / mtype / "hourly"
            parquet_file = hourly_dir / f"{date_str}.parquet"

            if not parquet_file.exists():
                log.debug(f"File not found: {parquet_file}")
                continue

            # Skip if already exists
            if skip_existing and uploader.file_exists(mtype, date_str):
                log.info(f"{mtype}/{date_str} already exists, skipping")
                results.setdefault(mtype, {})[date_str] = True
                continue

            # Upload
            success = uploader.upload_file(
                local_path=parquet_file,
                measurement_type=mtype,
                date_str=date_str,
                metadata={"miner_code": miner_code}
            )

            results.setdefault(mtype, {})[date_str] = success

            if success:
                log.info(f"✓ {mtype}/{date_str}")
            else:
                log.error(f"✗ {mtype}/{date_str}")

        current_dt += timedelta(days=1)

    return results


def update_miner_metadata(
    miner_code: str,
    hex_id: str,
    lat: float,
    lon: float
) -> bool:
    """Update miner metadata in S3.

    Args:
        miner_code: Miner code
        hex_id: Hex ID (res-7)
        lat: Latitude
        lon: Longitude

    Returns:
        True if successful
    """
    uploader = get_s3_uploader()
    if not uploader:
        log.error("S3 uploader not configured")
        return False

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

    # Get first and last upload dates
    first_date = None
    last_date = None

    for mtype in measurement_types:
        dates = uploader.list_uploaded_files(mtype)
        if dates:
            if not first_date or dates[0] < first_date:
                first_date = dates[0]
            if not last_date or dates[-1] > last_date:
                last_date = dates[-1]

    # Create metadata
    metadata = {
        "miner_id": uploader.miner_id,
        "miner_code": miner_code,
        "hex_id": hex_id,
        "location": {
            "lat": lat,
            "lon": lon,
            "resolution": 7
        },
        "measurement_types": measurement_types,
        "first_upload": first_date,
        "last_upload": last_date,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }

    return uploader.upload_miner_metadata(metadata)


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Upload original measurements to S3")
    parser.add_argument("--miner-code", default="BM", help="Miner code (BM, IDM, etc.)")
    parser.add_argument("--backfill", action="store_true", help="Backfill date range")
    parser.add_argument("--start-date", help="Start date for backfill (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date for backfill (YYYY-MM-DD)")
    parser.add_argument("--update-metadata", action="store_true", help="Update miner metadata")
    parser.add_argument("--hex-id", help="Hex ID (for metadata update)")
    parser.add_argument("--lat", type=float, help="Latitude (for metadata update)")
    parser.add_argument("--lon", type=float, help="Longitude (for metadata update)")

    args = parser.parse_args()

    if args.update_metadata:
        # Update metadata
        if not all([args.hex_id, args.lat, args.lon]):
            log.error("--hex-id, --lat, and --lon required for metadata update")
            sys.exit(1)

        success = update_miner_metadata(
            miner_code=args.miner_code,
            hex_id=args.hex_id,
            lat=args.lat,
            lon=args.lon
        )

        if success:
            log.info("Metadata updated successfully")
        else:
            log.error("Metadata update failed")
            sys.exit(1)

    elif args.backfill:
        # Backfill date range
        if not args.start_date or not args.end_date:
            log.error("--start-date and --end-date required for backfill")
            sys.exit(1)

        results = upload_date_range(
            start_date=args.start_date,
            end_date=args.end_date,
            miner_code=args.miner_code
        )

        # Summary
        total_success = sum(sum(dates.values()) for dates in results.values())
        total_files = sum(len(dates) for dates in results.values())

        log.info(f"Backfill complete: {total_success}/{total_files} files uploaded")

        if total_success < total_files:
            sys.exit(1)

    else:
        # Default: upload yesterday
        results = upload_yesterday_measurements(miner_code=args.miner_code)

        # Summary
        total_success = sum(sum(dates.values()) for dates in results.values())
        total_files = sum(len(dates) for dates in results.values())

        log.info(f"Daily backup complete: {total_success}/{total_files} files uploaded")

        if total_success < total_files:
            sys.exit(1)
