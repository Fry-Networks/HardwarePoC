import json
import os
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import psutil

log = logging.getLogger("poi_monitor_aem")

# Module-level state for cumulative counter delta tracking
_prev_count_m: Optional[int] = None


def _process_running(process_name: str) -> bool:
    """Return True if any running process name contains process_name."""
    name_norm = process_name.replace("-", "").lower()
    for proc in psutil.process_iter(attrs=["name"]):
        try:
            normalized = (proc.info.get("name") or "").replace("-", "").lower()
            if name_norm and name_norm in normalized:
                log.debug("Found process: %s", proc.info.get("name"))
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    log.debug("Process '%s' not found", process_name)
    return False


def _olostep_status_enabled() -> bool:
    """Return True if Olostep Browser status toggle is enabled.
    
    Checks all user profiles since service runs as SYSTEM but Olostep runs as user.
    """
    try:
        # First check current user's APPDATA (for dev/testing)
        config_path = Path(os.environ.get("APPDATA", "")) / "Olostep-Browser" / "config.json"
        log.debug("Checking config at: %s", config_path)
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            status = config.get("mellowtel_opt_in_status", False)
            log.debug("mellowtel_opt_in_status = %s (current user)", status)
            if status:
                return True
        
        # Check all user profiles (service runs as SYSTEM, Olostep runs as user)
        users_dir = Path(r"C:\Users")
        if users_dir.exists():
            for user_dir in users_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                olostep_config = user_dir / "AppData" / "Roaming" / "Olostep-Browser" / "config.json"
                if olostep_config.exists():
                    try:
                        log.debug("Checking config at: %s", olostep_config)
                        with open(olostep_config, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                        status = config.get("mellowtel_opt_in_status", False)
                        log.debug("mellowtel_opt_in_status = %s (user: %s)", status, user_dir.name)
                        if status:
                            return True
                    except Exception as e:
                        log.debug("Failed to read %s: %s", olostep_config, e)
                        continue
        
        log.debug("No Olostep config with status enabled found in any user profile")
        return False
    except Exception as e:
        log.warning("Failed to check Olostep status: %s", e)
        return False


def monitor_poi_for_aem() -> bool:
    """Return True when Olostep-Browser is running AND status is enabled."""
    process_ok = _process_running("Olostep")
    status_ok = _olostep_status_enabled()
    result = process_ok and status_ok
    log.debug("PoI check: process=%s, status=%s, result=%s", process_ok, status_ok, result)
    return result


def get_olostep_status_detailed() -> dict:
    """Return detailed Olostep status for live CSV writing.

    Returns:
        dict with keys:
            - olostep_running: bool - is Olostep-Browser process running
            - olostep_enabled: bool - is mellowtel_opt_in_status enabled
            - poi: bool - combined result (running AND enabled)
    """
    process_ok = _process_running("Olostep")
    status_ok = _olostep_status_enabled()
    return {
        "olostep_running": process_ok,
        "olostep_enabled": status_ok,
        "poi": process_ok and status_ok,
    }


def _get_olostep_process_stats() -> Tuple[int, float]:
    """Collect aggregate stats across all Olostep Browser processes.

    Returns:
        (proc_count, mem_rss_mb)
    """
    proc_count = 0
    mem_rss_bytes = 0
    for proc in psutil.process_iter(attrs=["name", "memory_info"]):
        try:
            name = (proc.info.get("name") or "").replace("-", "").lower()
            if "olostep" in name:
                proc_count += 1
                mem = proc.info.get("memory_info")
                if mem:
                    mem_rss_bytes += mem.rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return proc_count, round(mem_rss_bytes / (1024 * 1024), 1)


def _read_olostep_config_metrics() -> Tuple[bool, int, float]:
    """Read Mellowtel activity metrics from Olostep config.json.

    Scans current user APPDATA and all user profiles (for SYSTEM service).

    Returns:
        (enabled, count_m, timestamp_m_epoch_s)
        - enabled: mellowtel_opt_in_status
        - count_m: cumulative Mellowtel task count (highest across profiles)
        - timestamp_m_epoch_s: last activity epoch seconds (most recent across profiles), 0 if unavailable
    """
    enabled = False
    best_count = 0
    best_ts = 0.0

    config_paths = []

    # Current user APPDATA (dev/testing)
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        config_paths.append(Path(appdata) / "Olostep-Browser" / "config.json")

    # All user profiles (service runs as SYSTEM)
    users_dir = Path(r"C:\Users")
    if users_dir.exists():
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            p = user_dir / "AppData" / "Roaming" / "Olostep-Browser" / "config.json"
            if p not in config_paths:
                config_paths.append(p)

    for config_path in config_paths:
        if not config_path.exists():
            continue
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            if config.get("mellowtel_opt_in_status", False):
                enabled = True
            count = config.get("count_m", 0)
            if isinstance(count, (int, float)) and count > best_count:
                best_count = int(count)
            ts = config.get("timestamp_m", 0)
            if isinstance(ts, (int, float)):
                # timestamp_m is in milliseconds
                ts_s = ts / 1000.0
                if ts_s > best_ts:
                    best_ts = ts_s
        except Exception as e:
            log.debug("Failed to read config metrics from %s: %s", config_path, e)
            continue

    return enabled, best_count, best_ts


def get_olostep_metrics() -> Dict[str, Any]:
    """Collect rich Olostep Browser activity metrics for AEM measurement upload.

    Returns dict with:
        - poi (int): 1 if Olostep running AND enabled, 0 otherwise
        - tasks_completed (int): Mellowtel tasks completed since last reading
        - last_activity_age_s (float): seconds since last Mellowtel activity, -1 if unknown
        - mem_rss_mb (float): total RSS memory of Olostep processes in MB
        - proc_count (int): number of Olostep processes
    """
    global _prev_count_m

    try:
        # Process stats
        proc_count, mem_rss_mb = _get_olostep_process_stats()

        # Config metrics
        enabled, count_m, timestamp_m_s = _read_olostep_config_metrics()

        # POI: backward compat
        poi = 1 if (proc_count > 0 and enabled) else 0

        # Tasks delta
        tasks_completed = 0
        if _prev_count_m is not None:
            if count_m >= _prev_count_m:
                tasks_completed = count_m - _prev_count_m
            else:
                # count_m decreased (reinstall/reset) -- report 0, reset baseline
                log.info("count_m decreased (%d -> %d), resetting baseline", _prev_count_m, count_m)
                tasks_completed = 0
        _prev_count_m = count_m

        # Last activity age
        if timestamp_m_s > 0:
            last_activity_age_s = round(time.time() - timestamp_m_s, 1)
        else:
            last_activity_age_s = -1.0

        result = {
            "poi": poi,
            "tasks_completed": tasks_completed,
            "last_activity_age_s": last_activity_age_s,
            "mem_rss_mb": mem_rss_mb,
            "proc_count": proc_count,
        }
        log.debug("Olostep metrics: %s", result)
        return result

    except Exception as e:
        log.error("Failed to collect Olostep metrics: %s", e)
        return {
            "poi": 0,
            "tasks_completed": 0,
            "last_activity_age_s": -1.0,
            "mem_rss_mb": 0.0,
            "proc_count": 0,
        }
