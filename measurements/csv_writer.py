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
    "aem": ["timestamp", "poi"],
    "mysterium": ["timestamp", "online", "earnings_usd", "sessions"],
    "honeygain": ["timestamp", "enabled", "earnings_usd", "status"],
    "bright": ["timestamp", "enabled", "status"],
    "presearch": ["timestamp", "online", "earnings_usd", "status"],
    "space_acres": ["timestamp", "online", "earnings_usd", "status"],
    "diiisco": ["timestamp", "online", "earnings_usd", "status"],
}


def _measurements_dir(miner_code: str) -> Path:
    """Get measurements directory for this miner."""
    try:
        base = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
        return base / "FryNetworks" / f"miner-{miner_code}" / "measurements"
    except Exception:
        return Path("C:\\ProgramData\\FryNetworks\\miner-BM\\measurements")


def _csv_path(sensor_type: str, miner_code: str) -> Path:
    """Get CSV file path for sensor on current date."""
    meas_dir = _measurements_dir(miner_code)
    today = datetime.now().strftime("%Y%m%d")
    filename = f"{sensor_type}_{today}.csv"
    return meas_dir / filename


def append_row(sensor_type: str, miner_code: str, row: Dict[str, Any]) -> bool:
    """Append a row to the daily CSV for a sensor.
    
    Creates file with header if it doesn't exist.
    Appends row matching schema for the sensor type.
    
    Args:
        sensor_type: Type key (bandwidth, satellite, radiation, etc.)
        miner_code: Miner code (BM, ISM, etc.)
        row: Dict with data matching schema
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if sensor_type not in SCHEMAS:
            log.warning("Unknown sensor type: %s", sensor_type)
            return False
        
        schema = SCHEMAS[sensor_type]
        csv_path = _csv_path(sensor_type, miner_code)
        
        # Ensure directory exists
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Check if file exists
        file_exists = csv_path.exists()
        
        # Build row values in schema order
        row_values = []
        for col in schema:
            value = row.get(col, "")
            # Handle None and numeric types
            if value is None:
                value = ""
            row_values.append(str(value))
        
        # Write CSV (append mode, add header if new file)
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Write header if file is new
            if not file_exists:
                writer.writerow(schema)
            
            # Write data row
            writer.writerow(row_values)
        
        log.debug("Appended row to %s", csv_path.name)
        return True
        
    except Exception as e:
        log.error("Failed to append row to CSV: %s", e)
        return False


def read_last_row(sensor_type: str, miner_code: str) -> Optional[Dict[str, str]]:
    """Read the last row from today's CSV file.
    
    Used by GUI for Live Data display.
    
    Args:
        sensor_type: Type key
        miner_code: Miner code
    
    Returns:
        Dict with last row data, or None if file doesn't exist/is empty
    """
    try:
        csv_path = _csv_path(sensor_type, miner_code)
        
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


def read_all_rows(sensor_type: str, miner_code: str, date_str: Optional[str] = None) -> List[Dict[str, str]]:
    """Read all rows from CSV file for a date.
    
    Used by GUI Data History for charting.
    
    Args:
        sensor_type: Type key
        miner_code: Miner code
        date_str: Date string (YYYYMMDD). Defaults to today.
    
    Returns:
        List of row dicts, or empty list if file doesn't exist
    """
    try:
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")
        
        meas_dir = _measurements_dir(miner_code)
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
