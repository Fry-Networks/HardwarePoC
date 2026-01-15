"""Measurement collector orchestrator for autonomous service.

Coordinates measurement collection across all miner types and writes results.
"""

import os
import json
import logging
import datetime as dt
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
from .tools import collect_all_tool_stats


def data_dir() -> Path:
    """Get ProgramData directory for this miner."""
    try:
        # Determine miner code from environment or config
        miner_code = os.environ.get("MINER_CODE", "BM")
        
        base = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
        return base / "FryNetworks" / f"miner-{miner_code}"
    except Exception:
        return Path("C:\\ProgramData\\FryNetworks\\miner-BM")


def collect_all_measurements(miner_code: str) -> Dict[str, Any]:
    """Collect all measurements for the given miner type.
    
    Args:
        miner_code: Miner type code (BM, ISM, OSM, IDM, ODM, IRM, AEM, etc.)
    
    Returns:
        Dict with timestamp, miner_code, and measurement data
    """
    measurements: Dict[str, Any] = {
        "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        "miner_code": miner_code
    }
    
    try:
        if miner_code == "BM":
            # Bandwidth Miner
            bw = collect_bandwidth_measurement()
            if bw:
                measurements["group"] = "Bandwidth"
                measurements["measurement"] = bw
        
        elif miner_code in ("ISM", "OSM"):
            # Satellite Miners
            sat = collect_satellite_measurement()
            if sat:
                measurements["group"] = "Satellite"
                measurements["measurement"] = sat
        
        elif miner_code == "IRM":
            # Radiation Miner
            rad = collect_radiation_measurement()
            if rad:
                measurements["group"] = "Radiation"
                measurements["measurement"] = rad
        
        elif miner_code in ("IDM", "ODM"):
            # Decibel Miners
            db = collect_decibel_measurement()
            if db:
                measurements["group"] = "Decibel"
                measurements["measurement"] = db
        
        elif miner_code == "AEM":
            # AI Edge Miner
            aem = collect_aem_measurement()
            if aem:
                measurements["group"] = "AEM"
                measurements["measurement"] = aem
        
        else:
            log.warning("Unknown miner code: %s", miner_code)
        
        # Collect tool stats (Mysterium, Bright, Honeygain, etc.)
        # These are common to BM miners but may be enabled on other types
        tool_stats = collect_all_tool_stats()
        if tool_stats:
            measurements["tools"] = tool_stats
    
    except Exception as e:
        log.exception("Measurement collection failed for %s: %s", miner_code, e)
    
    return measurements


def write_latest_measurements(miner_code: str) -> bool:
    """Collect and write latest measurements to latest.json.
    
    Args:
        miner_code: Miner type code
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Collect measurements
        measurements = collect_all_measurements(miner_code)
        
        # Determine output path
        measurements_dir = data_dir() / "measurements"
        measurements_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = measurements_dir / "latest.json"
        
        # Write atomically (temp file + rename)
        temp_path = output_path.with_suffix('.tmp')
        
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(measurements, f, indent=2)
        
        # Atomic rename
        temp_path.replace(output_path)
        
        log.debug("Wrote measurements to %s", output_path)
        return True
        
    except Exception as e:
        log.error("Failed to write measurements: %s", e)
        return False


def write_encrypted_measurement(miner_code: str, miner_key: str) -> bool:
    """Collect, encrypt, and write historical measurement file.
    
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
        
        # Collect measurements
        measurements = collect_all_measurements(miner_code)
        
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
        group = measurements.get("group", "unknown")
        output_path = measurements_dir / f"measurements-{group}-{timestamp}.json.enc"
        
        with open(output_path, 'wb') as f:
            f.write(encrypted)
        
        log.debug("Wrote encrypted measurement to %s", output_path)
        return True
        
    except Exception as e:
        log.error("Failed to write encrypted measurement: %s", e)
        return False
