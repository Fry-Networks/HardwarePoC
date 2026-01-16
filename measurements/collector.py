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
    
    Called frequently (every 2s) for real-time UI display.
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


def collect_and_write_bandwidth(miner_code: str) -> bool:
    """Collect real bandwidth measurement (download/upload) and write to CSV.
    
    Called every 10 minutes for actual throughput testing.
    Does full 10MB download + 5MB upload test.
    
    Args:
        miner_code: Miner type code
    
    Returns:
        True if successful
    """
    try:
        bw = collect_bandwidth_measurement()
        if bw:
            row = {
                "timestamp": dt.datetime.now().isoformat(),
                **bw
            }
            return append_row("bandwidth", miner_code, row)
        return False
    except Exception as e:
        log.error("Bandwidth collection failed: %s", e)
        return False


def collect_and_write_satellite(miner_code: str) -> bool:
    """Collect satellite data and write to CSV."""
    try:
        sat = collect_satellite_measurement()
        if sat:
            row = {
                "timestamp": dt.datetime.now().isoformat(),
                **sat
            }
            return append_row("satellite", miner_code, row)
        return False
    except Exception as e:
        log.error("Satellite collection failed: %s", e)
        return False


def collect_and_write_radiation(miner_code: str) -> bool:
    """Collect radiation data and write to CSV."""
    try:
        rad = collect_radiation_measurement()
        if rad:
            # Extract only schema fields (cpm, usv, mr)
            row = {
                "timestamp": dt.datetime.now().isoformat(),
                "cpm": rad.get("cpm"),
                "usv": rad.get("usv"),
                "mr": rad.get("mr")
            }
            return append_row("radiation", miner_code, row)
        return False
    except Exception as e:
        log.error("Radiation collection failed: %s", e)
        return False


def collect_and_write_decibel(miner_code: str) -> bool:
    """Collect decibel data and write to CSV."""
    try:
        db = collect_decibel_measurement()
        if db:
            row = {
                "timestamp": dt.datetime.now().isoformat(),
                "dbfs": db.get("dbfs")
            }
            return append_row("decibel", miner_code, row)
        return False
    except Exception as e:
        log.error("Decibel collection failed: %s", e)
        return False


def collect_and_write_aem(miner_code: str) -> bool:
    """Collect AEM data and write to CSV."""
    try:
        aem = collect_aem_measurement()
        if aem:
            row = {
                "timestamp": dt.datetime.now().isoformat(),
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


def collect_all_by_miner_type(miner_code: str) -> bool:
    """Collect appropriate sensors for this miner type and write to CSV.
    
    Args:
        miner_code: Miner type code (BM, ISM, OSM, IDM, ODM, IRM, AEM)
    
    Returns:
        True if at least one collection succeeded
    """
    success = False
    
    if miner_code == "BM":
        # Bandwidth Miner
        if collect_and_write_bandwidth(miner_code):
            success = True
        # Tools may be enabled on BM
        if collect_and_write_tool_stats(miner_code):
            success = True
    
    elif miner_code in ("ISM", "OSM"):
        # Satellite Miners
        if collect_and_write_satellite(miner_code):
            success = True
    
    elif miner_code == "IRM":
        # Radiation Miner
        if collect_and_write_radiation(miner_code):
            success = True
    
    elif miner_code in ("IDM", "ODM"):
        # Decibel Miners
        if collect_and_write_decibel(miner_code):
            success = True
    
    elif miner_code == "AEM":
        # AI Edge Miner
        if collect_and_write_aem(miner_code):
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
