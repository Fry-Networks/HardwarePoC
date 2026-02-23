"""Hourly aggregation logic for Autonomys Auto Drive.

Reads CSV measurement data and aggregates it into hourly parquet files.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("measurements.autonomys_aggregator")

try:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    PARQUET_AVAILABLE = True
except Exception as _parquet_err:
    PARQUET_AVAILABLE = False
    log.warning("Parquet support not available. Install with: pip install pandas pyarrow (reason: %s)", _parquet_err)


def aggregate_bandwidth_hourly(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Aggregate bandwidth measurements into hourly data.

    Args:
        rows: List of measurement dicts with timestamp, dl, ul, iface

    Returns:
        DataFrame with hourly aggregates
    """
    if not PARQUET_AVAILABLE:
        raise RuntimeError("pandas and pyarrow are required for aggregation")

    if not rows:
        return pd.DataFrame()

    # Convert to DataFrame
    df = pd.DataFrame(rows)

    # Parse timestamps
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

    # Convert dl/ul to float
    df['dl'] = pd.to_numeric(df['dl'], errors='coerce')
    df['ul'] = pd.to_numeric(df['ul'], errors='coerce')

    # Extract hour
    df['hour'] = df['timestamp'].dt.hour

    # Group by hour and aggregate
    hourly = df.groupby('hour').agg({
        'dl': ['mean', 'min', 'max', 'count'],
        'ul': ['mean', 'min', 'max'],
        'iface': lambda x: x.mode()[0] if len(x) > 0 else ''
    }).reset_index()

    # Flatten column names
    hourly.columns = [
        'hour',
        'dl_avg_mbps', 'dl_min_mbps', 'dl_max_mbps', 'sample_count',
        'ul_avg_mbps', 'ul_min_mbps', 'ul_max_mbps',
        'iface'
    ]

    # Add date column (use first timestamp's date)
    date = df['timestamp'].iloc[0].date()
    hourly['date'] = date

    # Reorder columns
    hourly = hourly[[
        'date', 'hour',
        'dl_avg_mbps', 'dl_min_mbps', 'dl_max_mbps',
        'ul_avg_mbps', 'ul_min_mbps', 'ul_max_mbps',
        'sample_count', 'iface'
    ]]

    return hourly


def aggregate_satellite_hourly(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Aggregate satellite measurements into hourly data.

    Args:
        rows: List of measurement dicts with timestamp, sats, fix, hdop, lat, lon

    Returns:
        DataFrame with hourly aggregates
    """
    if not PARQUET_AVAILABLE:
        raise RuntimeError("pandas and pyarrow are required for aggregation")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['hour'] = df['timestamp'].dt.hour

    # Convert numeric fields
    for col in ['sats', 'fix', 'hdop', 'lat', 'lon']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Group by hour and aggregate
    hourly = df.groupby('hour').agg({
        'sats': ['mean', 'min', 'max', 'count'],
        'fix': lambda x: x.mode()[0] if len(x) > 0 else 0,
        'hdop': ['mean', 'min', 'max'],
        'lat': 'mean',
        'lon': 'mean'
    }).reset_index()

    hourly.columns = [
        'hour',
        'sats_avg', 'sats_min', 'sats_max', 'sample_count',
        'fix_mode',
        'hdop_avg', 'hdop_min', 'hdop_max',
        'lat_avg', 'lon_avg'
    ]

    date = df['timestamp'].iloc[0].date()
    hourly['date'] = date

    hourly = hourly[[
        'date', 'hour',
        'sats_avg', 'sats_min', 'sats_max',
        'hdop_avg', 'hdop_min', 'hdop_max',
        'lat_avg', 'lon_avg',
        'fix_mode', 'sample_count'
    ]]

    return hourly


def aggregate_radiation_hourly(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Aggregate radiation measurements into hourly data.

    Args:
        rows: List of measurement dicts with timestamp, cpm, usv, mr

    Returns:
        DataFrame with hourly aggregates
    """
    if not PARQUET_AVAILABLE:
        raise RuntimeError("pandas and pyarrow are required for aggregation")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['hour'] = df['timestamp'].dt.hour

    for col in ['cpm', 'usv', 'mr']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    hourly = df.groupby('hour').agg({
        'cpm': ['mean', 'min', 'max', 'count'],
        'usv': ['mean', 'min', 'max'],
        'mr': ['mean', 'min', 'max']
    }).reset_index()

    hourly.columns = [
        'hour',
        'cpm_avg', 'cpm_min', 'cpm_max', 'sample_count',
        'usv_avg', 'usv_min', 'usv_max',
        'mr_avg', 'mr_min', 'mr_max'
    ]

    date = df['timestamp'].iloc[0].date()
    hourly['date'] = date

    hourly = hourly[[
        'date', 'hour',
        'cpm_avg', 'cpm_min', 'cpm_max',
        'usv_avg', 'usv_min', 'usv_max',
        'mr_avg', 'mr_min', 'mr_max',
        'sample_count'
    ]]

    return hourly


def aggregate_decibel_hourly(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Aggregate decibel measurements into hourly data.

    Args:
        rows: List of measurement dicts with timestamp, dbfs

    Returns:
        DataFrame with hourly aggregates
    """
    if not PARQUET_AVAILABLE:
        raise RuntimeError("pandas and pyarrow are required for aggregation")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['hour'] = df['timestamp'].dt.hour
    df['dbfs'] = pd.to_numeric(df['dbfs'], errors='coerce')

    hourly = df.groupby('hour').agg({
        'dbfs': ['mean', 'min', 'max', 'count']
    }).reset_index()

    hourly.columns = [
        'hour',
        'dbfs_avg', 'dbfs_min', 'dbfs_max', 'sample_count'
    ]

    date = df['timestamp'].iloc[0].date()
    hourly['date'] = date

    hourly = hourly[[
        'date', 'hour',
        'dbfs_avg', 'dbfs_min', 'dbfs_max',
        'sample_count'
    ]]

    return hourly


def aggregate_aem_hourly(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Aggregate AEM Olostep Browser measurements into hourly data.

    Metrics:
    - poi mean -> uptime_ratio (0.0-1.0), fraction of interval Olostep was active
    - tasks_completed sum -> total Mellowtel tasks per hour
    - last_activity_age_s mean/min/max -> recency of Mellowtel work
    - mem_rss_mb mean/min/max -> browser resource usage
    - proc_count mean/min/max -> process stability

    Args:
        rows: List of measurement dicts with timestamp, poi, tasks_completed,
              last_activity_age_s, mem_rss_mb, proc_count

    Returns:
        DataFrame with hourly aggregates
    """
    if not PARQUET_AVAILABLE:
        raise RuntimeError("pandas and pyarrow are required for aggregation")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['hour'] = df['timestamp'].dt.hour

    # Convert all numeric columns (handles old-schema rows with missing columns)
    for col in ['poi', 'tasks_completed', 'last_activity_age_s', 'mem_rss_mb', 'proc_count']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        else:
            df[col] = 0

    hourly = df.groupby('hour').agg({
        'poi': 'mean',
        'tasks_completed': 'sum',
        'last_activity_age_s': ['mean', 'min', 'max'],
        'mem_rss_mb': ['mean', 'min', 'max'],
        'proc_count': ['mean', 'min', 'max', 'count'],
    }).reset_index()

    hourly.columns = [
        'hour',
        'uptime_ratio',
        'tasks_completed',
        'activity_age_avg_s', 'activity_age_min_s', 'activity_age_max_s',
        'mem_rss_avg_mb', 'mem_rss_min_mb', 'mem_rss_max_mb',
        'proc_count_avg', 'proc_count_min', 'proc_count_max',
        'sample_count',
    ]

    date = df['timestamp'].iloc[0].date()
    hourly['date'] = date

    hourly = hourly[[
        'date', 'hour',
        'uptime_ratio', 'tasks_completed',
        'activity_age_avg_s', 'activity_age_min_s', 'activity_age_max_s',
        'mem_rss_avg_mb', 'mem_rss_min_mb', 'mem_rss_max_mb',
        'proc_count_avg', 'proc_count_min', 'proc_count_max',
        'sample_count',
    ]]

    return hourly


# Map measurement types to aggregation functions
AGGREGATORS = {
    'bandwidth': aggregate_bandwidth_hourly,
    'satellite': aggregate_satellite_hourly,
    'radiation': aggregate_radiation_hourly,
    'decibel': aggregate_decibel_hourly,
    'aem': aggregate_aem_hourly,
}


def aggregate_measurement_hourly(
    measurement_type: str,
    rows: List[Dict[str, Any]]
) -> Optional[pd.DataFrame]:
    """Aggregate measurements into hourly data.

    Args:
        measurement_type: Type of measurement
        rows: List of measurement dicts

    Returns:
        DataFrame with hourly aggregates or None if error
    """
    if not PARQUET_AVAILABLE:
        log.error("Parquet support not available")
        return None

    if measurement_type not in AGGREGATORS:
        log.error("Unknown measurement type: %s", measurement_type)
        return None

    try:
        aggregator = AGGREGATORS[measurement_type]
        return aggregator(rows)
    except Exception as e:
        log.error("Failed to aggregate %s: %s", measurement_type, e)
        return None


def write_hourly_parquet(
    hourly_df: pd.DataFrame,
    output_path: Path
) -> bool:
    """Write hourly DataFrame to parquet file.

    Args:
        hourly_df: Hourly aggregated data
        output_path: Path to write parquet file

    Returns:
        True if successful
    """
    if not PARQUET_AVAILABLE:
        return False

    try:
        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to parquet with compression
        table = pa.Table.from_pandas(hourly_df)
        pq.write_table(table, output_path, compression='snappy')

        log.info("Wrote hourly parquet: %s", output_path.name)
        return True

    except Exception as e:
        log.error("Failed to write parquet %s: %s", output_path, e)
        return False


def create_hourly_metadata(
    hourly_df: pd.DataFrame,
    date_str: str,
    hex_id: Optional[str],
    measurement_type: str
) -> Dict[str, Any]:
    """Create metadata for hourly parquet file.

    Args:
        hourly_df: Hourly aggregated data
        date_str: Date string (YYYY-MM-DD)
        hex_id: H3 hex ID (optional, None to hide from metadata)
        measurement_type: Type of measurement

    Returns:
        Metadata dict
    """
    if hourly_df.empty:
        return {}

    metadata = {
        "date": date_str,
        "measurement_type": measurement_type,
        "aggregation": "hourly",
        "hours": len(hourly_df),
        "total_samples": int(hourly_df['sample_count'].sum()),
        "summary": {}
    }

    # Only include hex_id if provided (for privacy)
    if hex_id is not None:
        metadata["hex_id"] = hex_id

    # Add type-specific summaries
    if measurement_type == 'bandwidth':
        metadata["summary"] = {
            "dl_mbps": {
                "day_avg": float(hourly_df['dl_avg_mbps'].mean()),
                "day_min": float(hourly_df['dl_min_mbps'].min()),
                "day_max": float(hourly_df['dl_max_mbps'].max()),
                "peak_hour": int(hourly_df.loc[hourly_df['dl_avg_mbps'].idxmax(), 'hour'])
            },
            "ul_mbps": {
                "day_avg": float(hourly_df['ul_avg_mbps'].mean()),
                "day_min": float(hourly_df['ul_min_mbps'].min()),
                "day_max": float(hourly_df['ul_max_mbps'].max()),
                "peak_hour": int(hourly_df.loc[hourly_df['ul_avg_mbps'].idxmax(), 'hour'])
            }
        }
    elif measurement_type == 'radiation':
        metadata["summary"] = {
            "cpm": {
                "day_avg": float(hourly_df['cpm_avg'].mean()),
                "day_min": float(hourly_df['cpm_min'].min()),
                "day_max": float(hourly_df['cpm_max'].max())
            },
            "usv": {
                "day_avg": float(hourly_df['usv_avg'].mean()),
                "day_min": float(hourly_df['usv_min'].min()),
                "day_max": float(hourly_df['usv_max'].max())
            }
        }
    elif measurement_type == 'decibel':
        metadata["summary"] = {
            "dbfs": {
                "day_avg": float(hourly_df['dbfs_avg'].mean()),
                "day_min": float(hourly_df['dbfs_min'].min()),
                "day_max": float(hourly_df['dbfs_max'].max())
            }
        }
    elif measurement_type == 'aem':
        total_tasks = int(hourly_df['tasks_completed'].sum())
        metadata["summary"] = {
            "uptime_ratio": float(hourly_df['uptime_ratio'].mean()),
            "tasks_completed_total": total_tasks,
            "peak_tasks_hour": int(hourly_df.loc[hourly_df['tasks_completed'].idxmax(), 'hour']) if total_tasks > 0 else -1,
            "avg_memory_mb": float(hourly_df['mem_rss_avg_mb'].mean()),
        }

    return metadata


def write_hourly_metadata(
    metadata: Dict[str, Any],
    output_path: Path
) -> bool:
    """Write hourly metadata to JSON file.

    Args:
        metadata: Metadata dict
        output_path: Path to write JSON file

    Returns:
        True if successful
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        import json
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)

        log.debug("Wrote hourly metadata: %s", output_path.name)
        return True

    except Exception as e:
        log.error("Failed to write metadata %s: %s", output_path, e)
        return False
