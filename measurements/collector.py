"""Measurement collector orchestrator for autonomous service.

Coordinates measurement collection across all miner types and writes to CSV.
"""

import os
import logging
import datetime as dt
import json
import time
from typing import Dict, Any, Optional
from pathlib import Path

log = logging.getLogger("measurements.collector")

# --- Upload rate limiting (safety guard) ---
# Ensure expensive backend uploads (real tests) do not happen more
# frequently than the configured minimum. This prevents accidental
# repeated uploads when the upload function is invoked too often.
_UPLOAD_LAST_TS: dict[str, float] = {}
_UPLOAD_MIN_INTERVAL: int = 600  # seconds (10 minutes)

def _should_skip_upload(measurement_type: str) -> bool:
    try:
        now_ts = time.time()
        last = _UPLOAD_LAST_TS.get(measurement_type, 0.0)
        if last and (now_ts - last) < _UPLOAD_MIN_INTERVAL:
            log.debug(
                "Skipping %s upload; last upload %.1fs ago (<%ss)",
                measurement_type,
                (now_ts - last),
                _UPLOAD_MIN_INTERVAL,
            )
            return True
    except Exception:
        pass
    return False


def _mark_upload_success(measurement_type: str) -> None:
    try:
        _UPLOAD_LAST_TS[measurement_type] = time.time()
    except Exception:
        pass

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
                "timestamp": dt.datetime.now().isoformat(),
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

    To ensure safety, we rate-limit actual uploads so that even if this
    function is invoked more frequently (e.g., due to a scheduler bug) we
    will not perform the expensive test or call the backend more often
    than _BANDWIDTH_UPLOAD_MIN_INTERVAL seconds.
    
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
        # Rate-limit guard (skip expensive upload if too-frequent)
        if _should_skip_upload('bandwidth'):
            return False

        bw = collect_bandwidth_measurement()
        if not bw:
            return False
        
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # If API client available, upload to backend first
        if api_client and hex_id and install_id:
            try:
                api_client._api.upload_measurement(
                    hex_id=hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="bandwidth",
                    value=bw
                )
                log.info("Bandwidth measurement uploaded to backend")
                uploaded = True
                # mark last upload time on success
                try:
                    _mark_upload_success('bandwidth')
                except Exception:
                    pass
            except Exception as e:
                log.error("Failed to upload bandwidth to backend: %s", e)
                # If backend upload fails, don't write CSV (failed measurement)
                return False



        # Write to CSV (only if backend upload succeeded, or if no API client available)
        if uploaded or not api_client:
            row = {
                "timestamp": timestamp,
                **bw
            }
            return append_row("bandwidth", miner_code, row)
        return False
        
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
            return False
        
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()

        # Rate-limit guard for satellite uploads
        if _should_skip_upload('satellite'):
            return False

        # If API client available, upload to backend first
        if api_client and hex_id and install_id:
            try:
                api_client._api.upload_measurement(
                    hex_id=hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="satellite",
                    value=sat
                )
                log.info("Satellite measurement uploaded to backend")
                uploaded = True
                try:
                    _mark_upload_success('satellite')
                except Exception:
                    pass
            except Exception as e:
                log.error("Failed to upload satellite to backend: %s", e)
                return False
        else:
            uploaded = False
        


        # Write to CSV (only if backend upload succeeded, or if no API client available)
        if uploaded or not api_client:
            row = {
                "timestamp": timestamp,
                **sat
            }
            return append_row("satellite", miner_code, row)
        return False
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
            return False
        
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # Rate-limit guard for radiation uploads
        if _should_skip_upload('radiation'):
            return False

        # If API client available, upload to backend first
        if api_client and hex_id and install_id:
            try:
                api_client._api.upload_measurement(
                    hex_id=hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="radiation",
                    value=rad
                )
                log.info("Radiation measurement uploaded to backend")
                uploaded = True
                try:
                    _mark_upload_success('radiation')
                except Exception:
                    pass
            except Exception as e:
                log.error("Failed to upload radiation to backend: %s", e)
                return False

        # Pod-status marking is handled by the measurement daemon via write_week_local() after
        # a successful collector upload. Collectors do not write local pod_status files.

        # Write to CSV (only if backend upload succeeded, or if no API client available)
        if uploaded or not api_client:
            row = {
                "timestamp": timestamp,
                "cpm": rad.get("cpm"),
                "usv": rad.get("usv"),
                "mr": rad.get("mr")
            }
            return append_row("radiation", miner_code, row)
        return False
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
            return False
        
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # Rate-limit guard for decibel uploads
        if _should_skip_upload('decibel'):
            return False

        # If API client available, upload to backend first
        if api_client and hex_id and install_id:
            try:
                # Ensure JSON-serializable native types (e.g., convert numpy scalars)
                val = dict(db) if isinstance(db, dict) else {"dbfs": db}
                try:
                    v = val.get("dbfs")
                    if v is not None:
                        val["dbfs"] = float(v)
                except Exception:
                    # Best-effort: leave as-is if conversion fails
                    pass

                api_client._api.upload_measurement(
                    hex_id=hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="decibel",
                    value=val
                )
                log.info("Decibel measurement uploaded to backend")
                uploaded = True
                try:
                    _mark_upload_success('decibel')
                except Exception:
                    pass
            except Exception as e:
                log.error("Failed to upload decibel to backend: %s", e)
                return False

        # Write to CSV (only if backend upload succeeded, or if no API client available)
        if uploaded or not api_client:
            # Ensure CSV gets native types as well
            try:
                csv_dbfs = float(db.get("dbfs")) if db and db.get("dbfs") is not None else None
            except Exception:
                csv_dbfs = None

            row = {
                "timestamp": timestamp,
                "dbfs": csv_dbfs
            }
            return append_row("decibel", miner_code, row)
        return False
    except Exception as e:
        log.error("Decibel collection failed: %s", e)
        return False


def collect_and_upload_aem(miner_code: str, api_client: Optional[Any] = None, hex_id: Optional[str] = None, install_id: Optional[str] = None, miner_key: Optional[str] = None) -> bool:
    """Collect AEM data and upload to backend.
    
    Sends directly to backend first, then records confirmation to CSV and writes an encrypted file.
    """
    try:
        aem = collect_aem_measurement()
        if not aem:
            return False
        
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        uploaded = False

        # Rate-limit guard for aem uploads
        if _should_skip_upload('aem'):
            return False

        # If API client available, upload to backend first
        if api_client and hex_id and install_id:
            try:
                api_client._api.upload_measurement(
                    hex_id=hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=timestamp,
                    measurement_type="aem",
                    value=aem
                )
                log.info("AEM measurement uploaded to backend")
                uploaded = True
                try:
                    _mark_upload_success('aem')
                except Exception:
                    pass
            except Exception as e:
                log.error("Failed to upload AEM to backend: %s", e)
                return False

        # Pod-status marking is handled by the measurement daemon via write_week_local() after
        # a successful collector upload. Collectors do not write local pod_status files.

        # Write to CSV (only if backend upload succeeded, or if no API client available)
        if uploaded or not api_client:
            row = {
                "timestamp": timestamp,
                "poi": aem.get("poi")
            }
            return append_row("aem", miner_code, row)
        return False
    except Exception as e:
        log.error("AEM collection failed: %s", e)
        return False


def collect_and_write_tool_stats(miner_code: str) -> bool:
    """Collect all tool stats and write to respective CSVs."""
    try:
        tool_stats = collect_all_tool_stats()
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
