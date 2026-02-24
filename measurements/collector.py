"""Measurement collector orchestrator for autonomous service.

Coordinates measurement collection across all miner types and writes to CSV.
"""

import os
import logging
import datetime as dt
import json
from typing import Dict, Any, Optional
from pathlib import Path

log = logging.getLogger("measurements.collector")

# Import measurement collection functions
from . import (
    collect_bandwidth_measurement,
    collect_satellite_measurement,
    collect_radiation_measurement,
    collect_decibel_measurement,
    collect_aem_measurement
)
from .bandwidth import collect_bandwidth_live
from .tools import collect_all_tool_stats
from .csv_writer import append_row


def data_dir() -> Path:
    """Get ProgramData directory for this miner."""
    try:
        # Determine miner code from environment or config
        miner_code = os.environ.get("MINER_CODE", "BM")
        
        base = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
        return base / "FryNetworks" / f"miner-{miner_code}"
    except Exception:
        return Path("C:\\ProgramData\\FryNetworks\\miner-BM")


def collect_and_write_bandwidth_live(miner_code: str) -> bool:
    """Collect live bandwidth from system stats and write to CSV.
    
    Called frequently (every 10s) for real-time UI display (CSV for GUI).
    Does NOT do actual downloads/uploads.
    
    Args:
        miner_code: Miner type code
    
    Returns:
        True if successful
    """
    try:
        bw = collect_bandwidth_live()
        if bw:
            row = {
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                **bw
            }
            return append_row("bandwidth", miner_code, row)
        return False
    except Exception as e:
        log.debug("Live bandwidth collection failed: %s", e)
        return False





def collect_and_upload_bandwidth(miner_code: str, api_client: Optional[Any] = None, hex_id: Optional[str] = None, install_id: Optional[str] = None, miner_key: Optional[str] = None) -> bool:
    """Collect real bandwidth measurement and upload to backend.

    Called every 10 minutes for actual throughput testing.
    Does full 10MB download + 5MB upload test.
    Sends directly to backend first, then records confirmation to CSV and writes an encrypted file so legacy uploader can discover it.

    Args:
        miner_code: Miner type code
        api_client: API client for uploading (if None, only writes to CSV/encrypted file)
        hex_id: Registered hex cell ID for backend upload
        install_id: Installation UUID for backend upload
        miner_key: Miner key used to encrypt local measurement file (optional)

    Returns:
        True if successful (uploaded or saved locally)
    """
    try:
        bw = collect_bandwidth_measurement()
        if not bw:
            log.warning("Bandwidth measurement failed (internet down or test servers unreachable)")
            return False

        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # Use "unknown" as fallback hex_id if not registered yet
        effective_hex_id = hex_id or "unknown"

        # If API client available, upload to backend
        if api_client and install_id:
            try:
                api_client._api.upload_measurement(
                    hex_id=effective_hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="bandwidth",
                    value=bw
                )
                log.info("Bandwidth measurement uploaded to backend" + ("" if hex_id else " (hex_id=unknown)"))
                uploaded = True
            except Exception as e:
                log.error("Failed to upload bandwidth to backend: %s", e)
                # Continue to write CSV even if upload fails - measurement was successful

        # Always write to CSV if measurement succeeded (PoD = measurement taken, not upload success)
        row = {
            "timestamp": timestamp,
            **bw
        }
        # Real/historical measurement CSV (10min cadence)
        if append_row("bandwidth", miner_code, row, dataset="real"):
            return True
        log.error("Failed to write bandwidth measurement to CSV")
        return uploaded  # Return True if at least upload succeeded
        
    except Exception as e:
        log.error("Bandwidth collection failed: %s", e)
        return False


def collect_and_upload_satellite(miner_code: str, api_client: Optional[Any] = None, hex_id: Optional[str] = None, install_id: Optional[str] = None, miner_key: Optional[str] = None) -> bool:
    """Collect satellite data and upload to backend.
    
    Sends directly to backend first, then records confirmation to CSV.
    
    Args:
        miner_code: Miner type code
        api_client: API client for uploading (if None, only writes to CSV)
        hex_id: Registered hex cell ID for backend upload
        install_id: Installation UUID for backend upload
    
    Returns:
        True if successful (uploaded or saved locally)
    """
    try:
        sat = collect_satellite_measurement()
        if not sat:
            log.warning("Satellite measurement failed (sensor not connected or not responding)")
            return False

        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # Use "unknown" as fallback hex_id if not registered yet
        effective_hex_id = hex_id or "unknown"

        # If API client available, upload to backend
        if api_client and install_id:
            try:
                api_client._api.upload_measurement(
                    hex_id=effective_hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="satellite",
                    value=sat
                )
                log.info("Satellite measurement uploaded to backend" + ("" if hex_id else " (hex_id=unknown)"))
                uploaded = True
            except Exception as e:
                log.error("Failed to upload satellite to backend: %s", e)
                # Continue to write CSV even if upload fails

        # Always write to CSV if measurement succeeded
        row = {
            "timestamp": timestamp,
            **sat
        }
        # Real/historical measurement CSV (10min cadence)
        if append_row("satellite", miner_code, row, dataset="real"):
            return True
        log.error("Failed to write satellite measurement to CSV")
        return uploaded
    except Exception as e:
        log.error("Satellite collection failed: %s", e)
        return False


def collect_and_upload_radiation(miner_code: str, api_client: Optional[Any] = None, hex_id: Optional[str] = None, install_id: Optional[str] = None, miner_key: Optional[str] = None) -> bool:
    """Collect radiation data and upload to backend.
    
    Sends directly to backend first, then records confirmation to CSV and writes an encrypted file.
    """
    try:
        rad = collect_radiation_measurement()
        if not rad:
            log.warning("Radiation measurement failed (sensor not connected or not responding)")
            return False

        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # Use "unknown" as fallback hex_id if not registered yet
        effective_hex_id = hex_id or "unknown"

        # If API client available, upload to backend
        if api_client and install_id:
            try:
                api_client._api.upload_measurement(
                    hex_id=effective_hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="radiation",
                    value=rad
                )
                log.info("Radiation measurement uploaded to backend" + ("" if hex_id else " (hex_id=unknown)"))
                uploaded = True
            except Exception as e:
                log.error("Failed to upload radiation to backend: %s", e)
                # Continue to write CSV even if upload fails

        # Always write to CSV if measurement succeeded
        row = {
            "timestamp": timestamp,
            "cpm": rad.get("cpm"),
            "usv": rad.get("usv"),
            "mr": rad.get("mr")
        }
        # Real/historical measurement CSV (10min cadence)
        if append_row("radiation", miner_code, row, dataset="real"):
            return True
        log.error("Failed to write radiation measurement to CSV")
        return uploaded
    except Exception as e:
        log.error("Radiation collection failed: %s", e)
        return False


def collect_and_upload_decibel(miner_code: str, api_client: Optional[Any] = None, hex_id: Optional[str] = None, install_id: Optional[str] = None, miner_key: Optional[str] = None) -> bool:
    """Collect decibel data and upload to backend.
    
    Sends directly to backend first, then records confirmation to CSV and writes an encrypted file.
    """
    try:
        db = collect_decibel_measurement()
        if not db:
            log.warning("Decibel measurement failed (microphone not connected or not responding)")
            return False

        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # Use "unknown" as fallback hex_id if not registered yet
        effective_hex_id = hex_id or "unknown"

        # If API client available, upload to backend
        if api_client and install_id:
            try:
                # Ensure JSON-serializable native types (e.g., convert numpy scalars)
                val: dict[str, Any] = dict(db) if isinstance(db, dict) else {"dbfs": db}
                try:
                    v = val.get("dbfs")
                    if v is not None:
                        val["dbfs"] = float(v)  # type: ignore[assignment]
                except Exception:
                    # Best-effort: leave as-is if conversion fails
                    pass

                api_client._api.upload_measurement(
                    hex_id=effective_hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="decibel",
                    value=val
                )
                log.info("Decibel measurement uploaded to backend" + ("" if hex_id else " (hex_id=unknown)"))
                uploaded = True
            except Exception as e:
                log.error("Failed to upload decibel to backend: %s", e)
                # Continue to write CSV even if upload fails

        # Always write to CSV if measurement succeeded
        # Ensure CSV gets native types as well
        csv_dbfs: Any = None
        try:
            dbfs_val = db.get("dbfs") if db else None
            if dbfs_val is not None:
                csv_dbfs = float(dbfs_val)
        except Exception:
            csv_dbfs = None

        row = {
            "timestamp": timestamp,
            "dbfs": csv_dbfs
        }
        # Real/historical measurement CSV (10min cadence)
        if append_row("decibel", miner_code, row, dataset="real"):
            return True
        log.error("Failed to write decibel measurement to CSV")
        return uploaded
    except Exception as e:
        log.error("Decibel collection failed: %s", e)
        return False


def collect_and_upload_aem(miner_code: str, api_client: Optional[Any] = None, hex_id: Optional[str] = None, install_id: Optional[str] = None, miner_key: Optional[str] = None) -> bool:
    """Collect AEM Olostep Browser metrics and upload to backend.

    Sends rich activity data (task count, memory, process stats) to backend,
    then records to CSV.
    """
    try:
        aem = collect_aem_measurement()
        if not aem:
            log.warning("AEM measurement failed (Olostep Browser not responding)")
            return False

        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # Use "unknown" as fallback hex_id if not registered yet
        effective_hex_id = hex_id or "unknown"

        # If API client available, upload to backend
        if api_client and install_id:
            try:
                api_client._api.upload_measurement(
                    hex_id=effective_hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="aem",
                    value=aem
                )
                log.info("AEM measurement uploaded to backend" + ("" if hex_id else " (hex_id=unknown)"))
                uploaded = True
            except Exception as e:
                log.error("Failed to upload AEM to backend: %s", e)
                # Continue to write CSV even if upload fails

        # Always write to CSV if measurement succeeded
        row = {
            "timestamp": timestamp,
            "poi": aem.get("poi", 0),
            "tasks_completed": aem.get("tasks_completed", 0),
            "last_activity_age_s": aem.get("last_activity_age_s", -1),
            "mem_rss_mb": aem.get("mem_rss_mb", 0.0),
            "proc_count": aem.get("proc_count", 0),
        }
        # Real/historical measurement CSV (10min cadence)
        if append_row("aem", miner_code, row, dataset="real"):
            return True
        log.error("Failed to write AEM measurement to CSV")
        return uploaded
    except Exception as e:
        log.error("AEM collection failed: %s", e)
        return False


def collect_and_write_tool_stats(miner_code: str, presearch_api_key: str = "") -> bool:
    """Collect all tool stats and write to respective CSVs."""
    try:
        tool_stats = collect_all_tool_stats(presearch_api_key=presearch_api_key, miner_code=miner_code)
        if not tool_stats:
            return True
        
        success = True
        
        # Write each tool's stats
        for tool_name, stats in tool_stats.items():
            if not stats:
                continue
            
            row = {
                "timestamp": dt.datetime.now().isoformat(),
                **stats
            }
            
            # Map tool name to schema key
            tool_key = tool_name.lower()
            if not append_row(tool_key, miner_code, row):
                success = False
                log.warning("Failed to write %s stats", tool_name)
        
        return success
        
    except Exception as e:
        log.error("Tool stats collection failed: %s", e)
        return False


# --------------------------
# Live-only writers (write CSV for UI at configured intervals)
# These do NOT attempt expensive backend uploads.
# --------------------------

def collect_and_write_satellite_live(miner_code: str) -> bool:
    """Collect satellite measurement and write to CSV for live UI display."""
    try:
        sat = collect_satellite_measurement()
        if not sat:
            return False
        row = {
            "timestamp": dt.datetime.now().isoformat(),
            **sat
        }
        return append_row("satellite", miner_code, row)
    except Exception as e:
        log.debug("Live satellite collection failed: %s", e)
        return False


def collect_and_write_radiation_live(miner_code: str) -> bool:
    """Collect radiation measurement and write to CSV for live UI display."""
    try:
        rad = collect_radiation_measurement()
        if not rad:
            return False
        row = {
            "timestamp": dt.datetime.now().isoformat(),
            "cpm": rad.get("cpm"),
            "usv": rad.get("usv"),
            "mr": rad.get("mr"),
        }
        return append_row("radiation", miner_code, row)
    except Exception as e:
        log.debug("Live radiation collection failed: %s", e)
        return False


def collect_and_write_decibel_live(miner_code: str) -> bool:
    """Collect decibel measurement and write to CSV for live UI display."""
    try:
        db = collect_decibel_measurement()
        if not db:
            return False
        csv_dbfs: Any = None
        try:
            dbfs_val = db.get("dbfs") if db else None
            if dbfs_val is not None:
                csv_dbfs = float(dbfs_val)
        except Exception:
            csv_dbfs = None
        row = {
            "timestamp": dt.datetime.now().isoformat(),
            "dbfs": csv_dbfs,
        }
        return append_row("decibel", miner_code, row)
    except Exception as e:
        log.debug("Live decibel collection failed: %s", e)
        return False


def collect_all_by_miner_type(miner_code: str, api_client: Optional[Any] = None, hex_id: Optional[str] = None, install_id: Optional[str] = None, miner_key: Optional[str] = None) -> bool:
    """Collect appropriate sensors for this miner type and upload/write to CSV.
    
    Args:
        miner_code: Miner type code (BM, ISM, OSM, IDM, ODM, IRM, AEM)
        api_client: API client for backend upload (optional)
        hex_id: Registered hex cell ID for backend upload (optional)
        install_id: Installation UUID for backend upload (optional)
    
    Returns:
        True if at least one collection succeeded
    """
    success = False
    
    if miner_code == "BM":
        # Bandwidth Miner
        if collect_and_upload_bandwidth(miner_code, api_client, hex_id, install_id, miner_key):
            success = True
        # Tools may be enabled on BM
        if collect_and_write_tool_stats(miner_code):
            success = True
    
    elif miner_code in ("ISM", "OSM"):
        # Satellite Miners
        if collect_and_upload_satellite(miner_code, api_client, hex_id, install_id, miner_key):
            success = True
    
    elif miner_code == "IRM":
        # Radiation Miner
        if collect_and_upload_radiation(miner_code, api_client, hex_id, install_id, miner_key):
            success = True
    
    elif miner_code in ("IDM", "ODM"):
        # Decibel Miners
        if collect_and_upload_decibel(miner_code, api_client, hex_id, install_id, miner_key):
            success = True
    
    elif miner_code == "AEM":
        # AI Edge Miner
        if collect_and_upload_aem(miner_code, api_client, hex_id, install_id, miner_key):
            success = True

    elif miner_code == "SVN":
        # SVN collects tool stats (presearch, diiisco)
        if collect_and_write_tool_stats(miner_code):
            success = True
        else:
            success = True  # Not an error if tools aren't running yet

    elif miner_code == "RDN":
        # RDN collects tool stats (presearch, diiisco)
        if collect_and_write_tool_stats(miner_code):
            success = True
        else:
            success = True  # Not an error if tools aren't running yet

    elif miner_code == "SDN":
        # SDN doesn't collect sensor measurements
        log.debug("Miner type %s does not collect measurements", miner_code)
        success = True  # Not an error condition

    else:
        log.warning("Unknown miner code: %s", miner_code)
    
    return success


def write_latest_measurements(miner_code: str) -> bool:
    """Collect and write measurements for this miner type to CSV.
    
    This is the main entry point for the service scheduler.
    Collects sensor data appropriate for the miner type and appends to daily CSV files.
    
    Args:
        miner_code: Miner type code
    
    Returns:
        True if successful, False otherwise
    """
    try:
        return collect_all_by_miner_type(miner_code)
    except Exception as e:
        log.exception("Measurement write failed: %s", e)
        return False


def write_encrypted_measurement(miner_code: str, miner_key: str) -> bool:
    """Collect and encrypt measurements (legacy, for backward compatibility).
    
    Note: Service primarily writes to CSV. This function is kept for compatibility
    but is not used in the main measurement loop.
    
    Args:
        miner_code: Miner type code
        miner_key: Miner key for encryption
    
    Returns:
        True if successful, False otherwise
    """
    try:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        from cryptography.fernet import Fernet
        import base64
        
        # Build measurements from collected data
        # Note: This is a simplified version for backward compatibility
        measurements = {
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
            "miner_code": miner_code,
        }
        
        # Derive Fernet key from miner_key
        salt = b'measurements_key_v1'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(miner_key.encode('utf-8')))
        fernet = Fernet(key)
        
        # Encrypt measurements
        plaintext = json.dumps(measurements).encode('utf-8')
        encrypted = fernet.encrypt(plaintext)
        
        # Write encrypted file
        measurements_dir = data_dir() / "measurements"
        measurements_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d_%H%M%S")
        output_path = measurements_dir / f"measurements-{timestamp}.json.enc"
        
        with open(output_path, 'wb') as f:
            f.write(encrypted)
        
        log.debug("Wrote encrypted measurement to %s", output_path)
        return True
        
    except Exception as e:
        log.error("Failed to write encrypted measurement: %s", e)
        return False
