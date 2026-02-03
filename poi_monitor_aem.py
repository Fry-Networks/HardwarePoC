import json
import os
from pathlib import Path

import psutil


def _process_running(process_name: str) -> bool:
    """Return True if any running process name contains process_name."""
    name_norm = process_name.replace("-", "").lower()
    for proc in psutil.process_iter(attrs=["name"]):
        try:
            normalized = (proc.info.get("name") or "").replace("-", "").lower()
            if name_norm and name_norm in normalized:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _olostep_status_enabled() -> bool:
    """Return True if Olostep Browser status toggle is enabled."""
    try:
        config_path = Path(os.environ.get("APPDATA", "")) / "Olostep-Browser" / "config.json"
        if not config_path.exists():
            return False
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config.get("mellowtel_opt_in_status", False)
    except Exception:
        return False


def monitor_poi_for_aem() -> bool:
    """Return True when Olostep-Browser is running AND status is enabled."""
    return _process_running("Olostep") and _olostep_status_enabled()
