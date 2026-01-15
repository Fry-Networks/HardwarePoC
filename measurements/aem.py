"""AEM (AI Edge Miner) Proof of Installation measurement collection.

Reads PoI status from daily status JSON file.
Transferred from GUI worker.py to autonomous service.
"""

import os
import json
import logging
import datetime as dt
from typing import Dict, Any, Optional
from pathlib import Path

log = logging.getLogger("measurements.aem")


def collect_aem_measurement() -> Optional[Dict[str, Any]]:
    """Collect AEM Proof of Installation (PoI) status.
    
    Returns:
        Dict with poi (boolean) or None on failure
    """
    try:
        # Get status file path
        status_path = _get_status_file_path()
        
        if not status_path or not status_path.exists():
            log.debug("AEM status file not found")
            return None
        
        # Read and parse status JSON
        with open(status_path, 'r', encoding='utf-8') as f:
            status = json.load(f)
        
        # Extract PoI value (case-insensitive)
        poi = _extract_poi(status)
        
        if poi is None:
            return None
        
        return {
            "poi": bool(poi)
        }
        
    except Exception as e:
        log.error("AEM measurement failed: %s", e)
        return None


def _get_status_file_path() -> Optional[Path]:
    """Get path to today's status JSON file."""
    try:
        # Status files are in ProgramData/FryNetworks/miner-AEM/status/
        base_dir = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData"))
        status_dir = base_dir / "FryNetworks" / "miner-AEM" / "status"
        
        # Today's file: status-YYYYMMDD.json
        today = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
        status_file = status_dir / f"status-{today}.json"
        
        return status_file
        
    except Exception as e:
        log.warning("Failed to determine status file path: %s", e)
        return None


def _extract_poi(status: Dict[str, Any]) -> Optional[bool]:
    """Extract PoI value from status dict (case-insensitive search)."""
    try:
        # Direct key lookup (case-sensitive first)
        if "PoI" in status:
            return bool(status["PoI"])
        
        if "poi" in status:
            return bool(status["poi"])
        
        # Case-insensitive search
        for key, value in status.items():
            if key.lower() == "poi":
                return bool(value)
        
        # Check nested structures
        if "gates" in status and isinstance(status["gates"], dict):
            for key, value in status["gates"].items():
                if key.lower() == "poi":
                    return bool(value)
        
        return None
        
    except Exception:
        return None
