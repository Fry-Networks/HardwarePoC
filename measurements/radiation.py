"""Radiation (Geiger counter) measurement collection for IRM.

Reads radiation data from Geiger counter serial port.
Transferred from GUI worker.py to autonomous service.
"""

import os
import json
import logging
import time
from typing import Dict, Any, Optional

try:
    import serial  # type: ignore
except ImportError:
    serial = None  # type: ignore

log = logging.getLogger("measurements.radiation")

# Conversion factors
CPM_TO_USV = 0.00812  # CPM to μSv/h (device-specific, adjust as needed)


def collect_radiation_measurement() -> Optional[Dict[str, Any]]:
    """Collect radiation measurement from Geiger counter.
    
    Returns:
        Dict with cpm, usv, usv_hour, mr, cps or None on failure
    """
    try:
        if not serial:
            log.warning("pyserial not available for Geiger reading")
            return None
        
        # Get Geiger port from config
        port, baud = _get_geiger_config()
        
        if not port:
            log.debug("No Geiger port configured")
            return None
        
        # Read CPM from serial
        cpm = _read_geiger_cpm(port, baud)
        
        if cpm is None:
            return None
        
        # Calculate dose rates
        usv_hour = cpm * CPM_TO_USV
        usv = usv_hour
        mr = usv_hour / 10.0  # Convert to milliroentgen
        cps = cpm / 60.0
        
        return {
            "cpm": round(cpm, 1),
            "usv": round(usv, 3),
            "usv_hour": round(usv_hour, 3),
            "mr": round(mr, 4),
            "cps": round(cps, 2)
        }
        
    except Exception as e:
        log.error("Radiation measurement failed: %s", e)
        return None


def _get_geiger_config() -> tuple[Optional[str], int]:
    """Get Geiger serial port configuration from config file."""
    try:
        from pathlib import Path

        miner_code = os.environ.get("MINER_CODE", "BM")
        base = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
        config_path = base / "FryNetworks" / f"miner-{miner_code}" / "config" / "device_config.json"

        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                port = config.get("geiger_port") or config.get("radiation_port") or config.get("serial_port")
                baud = int(config.get("geiger_baud") or config.get("radiation_baud") or config.get("baud_rate") or 9600)
                return port, baud

        return None, 9600

    except Exception as e:
        log.warning("Failed to read Geiger config: %s", e)
        return None, 9600


def _read_geiger_cpm(port: str, baud: int, timeout: float = 10.0) -> Optional[float]:
    """Read CPM value from Geiger counter serial port.
    
    Supports multiple protocols:
    - GQ GMC-300S binary protocol (57600 baud)
    - Text-based output (CPM/μSv/h formats)
    """
    try:
        import struct
        import re
        
        # Defensive open with retries (driver instability can cause issues)
        if not serial:
            return None
        
        ser = None
        for attempt in range(2):
            try:
                ser = serial.Serial(port, baud, timeout=1.0)
                break
            except Exception as e:
                if attempt == 1:
                    log.warning("Geiger port open failed: %s", e)
                    return None
                time.sleep(0.15)
        
        if not ser:
            return None
        
        cpm_val = None
        
        try:
            # Binary protocol branch (GQ GMC series at 57600 baud)
            if baud == 57600:
                try:
                    ser.reset_input_buffer()
                    ser.write(b"<GETCPM>>")
                    ser.flush()
                    time.sleep(0.08)
                    
                    waiting = 0
                    try:
                        waiting = ser.in_waiting
                    except Exception:
                        waiting = 0
                    
                    if waiting >= 2:
                        response = ser.read(2)
                        if len(response) == 2:
                            cpm = struct.unpack('>H', response)[0]
                            if 0 <= cpm <= 10000:
                                cpm_val = float(cpm)
                                return cpm_val
                except Exception:
                    # Fall through to text parsing
                    pass
            
            # Text parsing branch
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            
            for _ in range(8):  # Read up to 8 lines
                try:
                    line = ser.readline()
                except Exception:
                    break
                
                if not line:
                    continue
                
                try:
                    line_str = line.decode('utf-8', errors='ignore').strip()
                except Exception:
                    continue
                
                if not line_str:
                    continue
                
                # Try CPM format
                cpm_match = re.search(r'(\d+\.?\d*)\s*(cpm|counts?)', line_str, re.IGNORECASE)
                if cpm_match:
                    try:
                        cpm_val = float(cpm_match.group(1))
                        if cpm_val is not None:
                            return cpm_val
                    except Exception:
                        pass
                
                # Try μSv/h format (convert to CPM)
                usv_match = re.search(r'(\d+\.?\d*)\s*[uµ]s?v(/h)?', line_str, re.IGNORECASE)
                if usv_match:
                    try:
                        usv_val = float(usv_match.group(1))
                        # Convert to CPM using standard conversion (device-specific, adjust as needed)
                        cpm_val = usv_val * 333.0
                        if cpm_val is not None:
                            return cpm_val
                    except Exception:
                        pass
            
            return cpm_val
            
        finally:
            try:
                ser.close()
            except Exception:
                pass
        
    except Exception as e:
        log.warning("Geiger CPM read failed: %s", e)
        return None
