"""Satellite (GNSS/GPS) measurement collection for ISM/OSM.

Reads NMEA sentences from GPS serial port and extracts satellite data.
Transferred from GUI worker.py to autonomous service.
"""

import os
import json
import logging
from typing import Dict, Any, Optional

try:
    import serial  # type: ignore
except ImportError:
    serial = None  # type: ignore

log = logging.getLogger("measurements.satellite")


def collect_satellite_measurement() -> Optional[Dict[str, Any]]:
    """Collect GPS/GNSS satellite measurement from serial port.
    
    Returns:
        Dict with sats, fix, lat, lon, alt, hdop or None on failure
    """
    try:
        if not serial:
            log.warning("pyserial not available for GPS reading")
            return None
        
        # Get GPS port from config
        port, baud = _get_gps_config()
        
        if not port:
            log.debug("No GPS port configured")
            return None
        
        # Read NMEA sentences
        data = _read_nmea_data(port, baud)
        
        if not data:
            return None
        
        return {
            "sats": data.get("sats", 0),
            "fix": data.get("fix", "NONE"),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "alt": data.get("alt"),
            "hdop": data.get("hdop")
        }
        
    except Exception as e:
        log.error("Satellite measurement failed: %s", e)
        return None


def _get_gps_config() -> tuple[Optional[str], int]:
    """Get GPS serial port configuration from config file."""
    try:
        from pathlib import Path
        
        # Read from device_config.json (written by GUI)
        config_path = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "FryNetworks" / "config" / "device_config.json"
        
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                port = config.get("gps_port")
                baud = config.get("gps_baud", 9600)
                return port, baud
        
        return None, 9600
        
    except Exception as e:
        log.warning("Failed to read GPS config: %s", e)
        return None, 9600


def _read_nmea_data(port: str, baud: int, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Read and parse NMEA sentences from GPS serial port."""
    if not serial:
        return None
    
    try:
        ser = serial.Serial(port, baud, timeout=2)
        
        import time
        start = time.time()
        
        gga_data = None
        rmc_data = None
        
        while (time.time() - start) < timeout:
            try:
                line = ser.readline().decode('ascii', errors='ignore').strip()
                
                if line.startswith('$GPGGA') or line.startswith('$GNGGA'):
                    gga_data = _parse_gga(line)
                elif line.startswith('$GPRMC') or line.startswith('$GNRMC'):
                    rmc_data = _parse_rmc(line)
                
                # If we have enough data, break
                if gga_data and gga_data.get('sats', 0) > 0:
                    break
                    
            except Exception:
                continue
        
        ser.close()
        
        # Combine GGA and RMC data
        result = gga_data or {}
        if rmc_data:
            result.update(rmc_data)
        
        return result if result else None
        
    except Exception as e:
        log.warning("NMEA read failed: %s", e)
        return None


def _parse_gga(sentence: str) -> Dict[str, Any]:
    """Parse GGA sentence: $GPGGA,time,lat,N,lon,W,fix,sats,hdop,alt,M,..."""
    try:
        parts = sentence.split(',')
        if len(parts) < 15:
            return {}
        
        # Fix quality: 0=invalid, 1=GPS, 2=DGPS, 3=PPS, 4=RTK, 5=FLOAT RTK
        fix_quality = int(parts[6]) if parts[6] else 0
        sats = int(parts[7]) if parts[7] else 0
        hdop = float(parts[8]) if parts[8] else None
        alt = float(parts[9]) if parts[9] else None
        
        # Parse lat/lon
        lat = None
        lon = None
        
        # Latitude (ddmm.mmmm format)
        if len(parts) > 2 and parts[2] and parts[3]:
            lat_str = parts[2]
            lat_dir = parts[3]
            if lat_str and len(lat_str) >= 5:
                lat_deg = float(lat_str[:2])
                lat_min = float(lat_str[2:])
                lat = lat_deg + lat_min / 60.0
                if lat_dir == 'S':
                    lat = -lat
        
        # Longitude (dddmm.mmmm format)
        if len(parts) > 5 and parts[4] and parts[5]:
            lon_str = parts[4]
            lon_dir = parts[5]
            if lon_str and len(lon_str) >= 6:
                lon_deg = float(lon_str[:3])
                lon_min = float(lon_str[3:])
                lon = lon_deg + lon_min / 60.0
                if lon_dir == 'W':
                    lon = -lon
        
        fix_map = {0: 'NONE', 1: 'GPS', 2: 'DGPS', 3: 'PPS', 4: 'RTK', 5: 'FLOAT_RTK'}
        fix_type = fix_map.get(fix_quality, f'FIX{fix_quality}')
        
        return {
            "sats": sats,
            "fix": fix_type,
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "hdop": hdop
        }
        
    except Exception:
        return {}


def _parse_rmc(sentence: str) -> Dict[str, Any]:
    """Parse RMC sentence (minimal - mainly for status)."""
    try:
        parts = sentence.split(',')
        if len(parts) < 12:
            return {}
        
        # Status: A=active, V=void
        status = parts[2] if len(parts) > 2 else 'V'
        
        result = {}
        
        # Set fix status
        result['fix'] = 'GPS' if status == 'A' else 'NONE'
        
        # Parse coordinates if active
        if status == 'A':
            # Latitude
            if len(parts) > 3 and parts[3] and parts[4]:
                lat_str = parts[3]
                lat_dir = parts[4]
                if lat_str and len(lat_str) >= 5:
                    lat_deg = float(lat_str[:2])
                    lat_min = float(lat_str[2:])
                    lat = lat_deg + lat_min / 60.0
                    if lat_dir == 'S':
                        lat = -lat
                    result['lat'] = lat
            
            # Longitude
            if len(parts) > 5 and parts[5] and parts[6]:
                lon_str = parts[5]
                lon_dir = parts[6]
                if lon_str and len(lon_str) >= 6:
                    lon_deg = float(lon_str[:3])
                    lon_min = float(lon_str[3:])
                    lon = lon_deg + lon_min / 60.0
                    if lon_dir == 'W':
                        lon = -lon
                    result['lon'] = lon
        
        return result
        
    except Exception:
        return {}


def _parse_coord(value: str, direction: str) -> Optional[float]:
    """Deprecated: Kept for compatibility. Use inline parsing in _parse_gga/_parse_rmc."""
    try:
        if not value or not direction:
            return None
        
        # Determine if lat (ddmm.mmmm) or lon (dddmm.mmmm)
        if len(value) >= 9:  # Longitude (dddmm.mmmm)
            deg = float(value[:3])
            mins = float(value[3:])
        else:  # Latitude (ddmm.mmmm)
            deg = float(value[:2])
            mins = float(value[2:])
        
        decimal = deg + (mins / 60.0)
        
        # Apply direction
        if direction in ['S', 'W']:
            decimal = -decimal
        
        return round(decimal, 6)
        
    except Exception:
        return None
