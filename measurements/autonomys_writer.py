"""Autonomys Auto Drive writer for measurement data.

Handles aggregation and writing of measurement data to Autonomys Auto Drive
in a structure optimized for data monetization by hex level and measurement type.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("measurements.autonomys_writer")

# H3 library will be imported when needed
try:
    import h3
    H3_AVAILABLE = True
except ImportError:
    H3_AVAILABLE = False
    log.warning("h3 library not available. Install with: pip install h3")

# Parquet support
try:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    PARQUET_AVAILABLE = True
except ImportError:
    PARQUET_AVAILABLE = False
    log.warning("Parquet support not available. Install with: pip install pandas pyarrow")


def autonomys_root_dir() -> Path:
    """Get root directory for Autonomys measurements storage."""
    try:
        base = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
        return base / "FryNetworks" / "autonomys-measurements"
    except Exception:
        return Path("C:\\ProgramData\\FryNetworks\\autonomys-measurements")


def ensure_autonomys_structure(hex_id: str, measurement_type: str) -> Tuple[Path, Path]:
    """Ensure the Autonomys folder structure exists for a hex and measurement type.

    Args:
        hex_id: H3 hex ID (e.g., "871f90151ffffff")
        measurement_type: Type of measurement (bandwidth, satellite, etc.)

    Returns:
        Tuple of (hourly_dir, daily_dir) paths
    """
    if not H3_AVAILABLE:
        raise RuntimeError("h3 library is required for Autonomys integration")

    # Get hex resolution from hex_id
    try:
        resolution = h3.h3_get_resolution(hex_id)
    except Exception as e:
        log.error("Invalid hex_id %s: %s", hex_id, e)
        raise ValueError(f"Invalid H3 hex_id: {hex_id}") from e

    # Build path: autonomys-measurements/res-{resolution}/{hex_id}/{measurement_type}/
    root = autonomys_root_dir()
    hex_dir = root / f"res-{resolution}" / hex_id / measurement_type

    hourly_dir = hex_dir / "hourly"
    daily_dir = hex_dir / "daily"

    # Create directories
    hourly_dir.mkdir(parents=True, exist_ok=True)
    daily_dir.mkdir(parents=True, exist_ok=True)

    return hourly_dir, daily_dir


def get_hex_center(hex_id: str) -> Optional[Tuple[float, float]]:
    """Get the center lat/lon for a hex cell.

    Args:
        hex_id: H3 hex ID

    Returns:
        Tuple of (lat, lon) or None if error
    """
    if not H3_AVAILABLE:
        return None

    try:
        lat, lon = h3.h3_to_geo(hex_id)
        return (lat, lon)
    except Exception as e:
        log.error("Failed to get center for hex %s: %s", hex_id, e)
        return None


def create_or_update_hex_manifest(
    hex_id: str,
    measurement_type: str,
    first_timestamp: datetime,
    last_timestamp: datetime,
    sample_count: int,
    install_id: Optional[str] = None,
    miner_code: Optional[str] = None
) -> bool:
    """Create or update the manifest.json file for a hex cell.

    Args:
        hex_id: H3 hex ID
        measurement_type: Type of measurement
        first_timestamp: First measurement timestamp
        last_timestamp: Last measurement timestamp
        sample_count: Total number of samples
        install_id: Installation UUID
        miner_code: Miner type code

    Returns:
        True if successful
    """
    if not H3_AVAILABLE:
        return False

    try:
        resolution = h3.h3_get_resolution(hex_id)
        root = autonomys_root_dir()
        manifest_path = root / f"res-{resolution}" / hex_id / "manifest.json"

        # Load existing manifest or create new
        if manifest_path.exists():
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
        else:
            # Create new manifest
            center = get_hex_center(hex_id)
            manifest = {
                "hex_id": hex_id,
                "resolution": resolution,
                "center": {
                    "lat": center[0] if center else None,
                    "lon": center[1] if center else None
                },
                "coverage": {
                    "start": None,
                    "end": None,
                    "total_days": 0
                },
                "install_ids": [],
                "miner_types": [],
                "measurements": {},
                "pricing_tier": "standard"
            }

        # Update measurement info
        if measurement_type not in manifest["measurements"]:
            manifest["measurements"][measurement_type] = {
                "hourly_files": 0,
                "daily_files": 0,
                "first_measurement": None,
                "last_measurement": None,
                "data_quality": 1.0,
                "total_samples": 0
            }

        meas = manifest["measurements"][measurement_type]

        # Update timestamps
        first_iso = first_timestamp.isoformat()
        last_iso = last_timestamp.isoformat()

        if meas["first_measurement"] is None or first_iso < meas["first_measurement"]:
            meas["first_measurement"] = first_iso
        if meas["last_measurement"] is None or last_iso > meas["last_measurement"]:
            meas["last_measurement"] = last_iso

        meas["total_samples"] += sample_count

        # Update coverage dates
        if manifest["coverage"]["start"] is None or first_iso < manifest["coverage"]["start"]:
            manifest["coverage"]["start"] = first_iso
        if manifest["coverage"]["end"] is None or last_iso > manifest["coverage"]["end"]:
            manifest["coverage"]["end"] = last_iso

        # Calculate total days
        if manifest["coverage"]["start"] and manifest["coverage"]["end"]:
            start_dt = datetime.fromisoformat(manifest["coverage"]["start"].replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(manifest["coverage"]["end"].replace('Z', '+00:00'))
            manifest["coverage"]["total_days"] = (end_dt - start_dt).days + 1

        # Add install_id and miner_code if provided
        if install_id and install_id not in manifest["install_ids"]:
            manifest["install_ids"].append(install_id)
        if miner_code and miner_code not in manifest["miner_types"]:
            manifest["miner_types"].append(miner_code)

        # Write updated manifest
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)

        log.debug("Updated manifest for hex %s, type %s", hex_id, measurement_type)
        return True

    except Exception as e:
        log.error("Failed to update hex manifest: %s", e)
        return False


def create_or_update_root_metadata() -> bool:
    """Create or update the root metadata.json file.

    Scans all hex directories and aggregates information.

    Returns:
        True if successful
    """
    try:
        root = autonomys_root_dir()
        metadata_path = root / "metadata.json"

        # Scan all hex directories
        total_hexagons = 0
        measurement_types_set = set()

        for res_dir in root.glob("res-*"):
            if not res_dir.is_dir():
                continue
            for hex_dir in res_dir.iterdir():
                if not hex_dir.is_dir():
                    continue
                total_hexagons += 1

                # Check manifest for measurement types
                manifest_path = hex_dir / "manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            manifest = json.load(f)
                            measurement_types_set.update(manifest.get("measurements", {}).keys())
                    except Exception:
                        pass

        metadata = {
            "network": "FryNetworks",
            "version": "2.0.0",
            "created": datetime.now(timezone.utc).isoformat(),
            "updated": datetime.now(timezone.utc).isoformat(),
            "hex_resolution": 7,  # Primary resolution
            "total_hexagons": total_hexagons,
            "measurement_types": sorted(list(measurement_types_set)),
            "pricing_tiers": {
                "hourly": {
                    "base_price_usd_per_hex_per_month": 50,
                    "description": "60-minute aggregated measurements"
                },
                "daily": {
                    "base_price_usd_per_hex_per_month": 15,
                    "description": "Daily min/max/avg summaries"
                }
            },
            "type_multipliers": {
                "bandwidth": 1.0,
                "satellite": 1.2,
                "radiation": 1.5,
                "decibel": 1.0,
                "aem": 2.0
            },
            "sample_interval_minutes": 10,
            "contact": "data-sales@frynetworks.com"
        }

        # Write metadata
        root.mkdir(parents=True, exist_ok=True)
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)

        log.info("Updated root metadata: %d hexagons, %d measurement types",
                 total_hexagons, len(measurement_types_set))
        return True

    except Exception as e:
        log.error("Failed to update root metadata: %s", e)
        return False


def check_dependencies() -> Dict[str, bool]:
    """Check if required dependencies are available.

    Returns:
        Dict with dependency availability status
    """
    return {
        "h3": H3_AVAILABLE,
        "parquet": PARQUET_AVAILABLE
    }
