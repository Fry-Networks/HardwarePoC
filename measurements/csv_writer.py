"""CSV writer for measurement data.

Handles append-only daily CSV files per sensor type.
Files rotate daily based on YYYYMMDD.
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("measurements.csv_writer")

# CSV schemas per sensor type
SCHEMAS = {
    "bandwidth": ["timestamp", "dl", "ul", "iface"],
    "satellite": ["timestamp", "sats", "fix", "hdop", "lat", "lon"],
    "radiation": ["timestamp", "cpm", "usv", "mr"],
    "decibel": ["timestamp", "dbfs"],
    "aem": ["timestamp", "poi", "tasks_completed", "last_activity_age_s", "mem_rss_mb", "proc_count"],
    "aem_live": ["timestamp", "olostep_running", "olostep_enabled", "status"],
    "mysterium": ["timestamp", "online", "earnings_usd", "sessions"],
    "honeygain": ["timestamp", "enabled", "earnings_usd", "status"],
    "bright": ["timestamp", "enabled", "status"],
    "presearch": ["timestamp", "online", "earnings_usd", "status"],
    "space_acres": ["timestamp", "node_healthy", "farmer_running", "status"],
    "diiisco": ["timestamp", "online", "ollama_healthy", "cpu_percent", "mem_usage_mb", "model", "status"],
}


def _measurements_dir(miner_code: str) -> Path:
    """Get measurements directory for this miner."""
    try:
        base = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
        return base / "FryNetworks" / f"miner-{miner_code}" / "measurements"
    except Exception:
        return Path("C:\\ProgramData\\FryNetworks\\miner-BM\\measurements")


def _csv_path(sensor_type: str, miner_code: str, dataset: str = "live") -> Path:
    """Get CSV file path for sensor on current date and dataset (live|real).

    Filenames:
      - live:   <sensor>_live_YYYYMMDD.csv
      - real:   <sensor>_real_YYYYMMDD.csv

    Defaults to "live" for immediate UI usage to preserve prior behavior.
    Uses UTC time to avoid timezone issues around midnight.
    """
    meas_dir = _measurements_dir(miner_code)
    today = datetime.utcnow().strftime("%Y%m%d")
    ds = (dataset or "live").strip().lower()
    if ds == "live":
        filename = f"{sensor_type}_live_{today}.csv"
    elif ds == "real":
        filename = f"{sensor_type}_real_{today}.csv"
    else:
        # Fallback to single-file legacy naming for unknown dataset
        filename = f"{sensor_type}_{today}.csv"
    return meas_dir / filename


def append_row(sensor_type: str, miner_code: str, row: Dict[str, Any], *, dataset: str = "live") -> bool:
    """Append a row to the daily CSV for a sensor.

    Supports writing to separate datasets:
      - dataset='live'  -> immediate/UI CSV (fast cadence)
      - dataset='real'  -> historical/real measurement CSV (10min cadence)
      - dataset='both'  -> write to both files

    Args:
        sensor_type: Type key (bandwidth, satellite, radiation, etc.)
        miner_code: Miner code (BM, ISM, etc.)
        row: Dict with data matching schema
        dataset: Which dataset to write ('live' | 'real' | 'both')

    Returns:
        True if successful for all targeted datasets, False otherwise
    """
    try:
        if sensor_type not in SCHEMAS:
            log.warning("Unknown sensor type: %s", sensor_type)
            return False

        schema = SCHEMAS[sensor_type]
        ds = (dataset or "live").strip().lower()
        targets = [ds] if ds != "both" else ["live", "real"]
        success = True

        for target in targets:
            csv_path = _csv_path(sensor_type, miner_code, target)
            # Ensure directory exists
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            # Check if file exists
            file_exists = csv_path.exists()
            # Build row values in schema order
            row_values: list[str] = []
            for col in schema:
                value = row.get(col, "")
                if value is None:
                    value = ""
                row_values.append(str(value))
            # Write CSV (append mode, add header if new file)
            try:
                with open(csv_path, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(schema)
                    writer.writerow(row_values)
                log.debug("Appended row to %s", csv_path.name)
            except Exception as e:
                log.error("Failed to append row to CSV %s: %s", csv_path.name if csv_path else "?", e)
                success = False
        return success

    except Exception as e:
        log.error("Failed to append row to CSV: %s", e)
        return False


def read_last_row(sensor_type: str, miner_code: str, *, dataset: str = "live") -> Optional[Dict[str, str]]:
    """Read the last row from today's CSV file for the given dataset.

    Defaults to `dataset='live'` (fast cadence) for UI consumption.
    """
    try:
        csv_path = _csv_path(sensor_type, miner_code, dataset)

        if not csv_path.exists():
            return None

        schema = SCHEMAS.get(sensor_type, [])
        if not schema:
            return None

        # Read all rows
        rows = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, fieldnames=schema)
            for row in reader:
                rows.append(row)

        # Return last row (skip header if present)
        if rows and len(rows) > 0:
            # Filter out header row (all values are field names)
            for row in reversed(rows):
                if row.get(schema[0]) != schema[0]:
                    return row

        return None

    except Exception as e:
        log.debug("Failed to read last row from CSV: %s", e)
        return None


def read_all_rows(sensor_type: str, miner_code: str, date_str: Optional[str] = None, *, dataset: str = "real") -> List[Dict[str, str]]:
    """Read all rows from CSV file for a date and dataset.

    Defaults to `dataset='real'` for history/charting.
    Uses UTC time to match file naming convention.
    """
    try:
        if date_str is None:
            date_str = datetime.utcnow().strftime("%Y%m%d")

        meas_dir = _measurements_dir(miner_code)
        # Use dataset-specific filename
        ds = (dataset or "real").strip().lower()
        if ds == "live":
            csv_path = meas_dir / f"{sensor_type}_live_{date_str}.csv"
        elif ds == "real":
            csv_path = meas_dir / f"{sensor_type}_real_{date_str}.csv"
        else:
            csv_path = meas_dir / f"{sensor_type}_{date_str}.csv"

        if not csv_path.exists():
            return []

        schema = SCHEMAS.get(sensor_type, [])
        if not schema:
            return []

        rows = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f, fieldnames=schema)
            for row in reader:
                # Skip header row
                if row.get(schema[0]) != schema[0]:
                    rows.append(row)

        return rows

    except Exception as e:
        log.debug("Failed to read CSV rows: %s", e)
        return []
