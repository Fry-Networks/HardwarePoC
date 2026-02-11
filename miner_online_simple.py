#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import json, os, sys, time, tempfile, logging, re, hashlib, argparse, uuid, platform, pathlib, threading, subprocess
from poi_monitor_aem import monitor_poi_for_aem
from typing import Dict, Any, Optional, Tuple, List, Callable, Iterable, cast

# Use timezone-aware UTC. `datetime.UTC` exists on Python 3.11+; fall back otherwise.
UTC = getattr(dt, "UTC", dt.timezone.utc)

import requests

from external_api import ExternalApiClient, ApiError

from mongo_api_proxy import MongoProxyClient

try:
    import h3.api.basic_str as h3  # type: ignore
    HAVE_H3 = True
except Exception:  # pragma: no cover
    try:
        import h3  # type: ignore
        HAVE_H3 = True
    except Exception:
        HAVE_H3 = False
        h3 = None  # type: ignore

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

try:
    from shapely.geometry import Polygon, shape  # type: ignore
    HAVE_SHAPELY = True
except Exception:  # pragma: no cover
    Polygon = shape = None  # type: ignore
    HAVE_SHAPELY = False

try:
    from geoip2.database import Reader as GeoIP2Reader  # type: ignore
    HAVE_GEOIP2 = True
except Exception:  # pragma: no cover
    GeoIP2Reader = Any  # type: ignore
    HAVE_GEOIP2 = False

COUNTRY_CACHE_DIR = pathlib.Path(os.getenv("H3_COUNTRY_CACHE_DIR", str(pathlib.Path.home() / ".cache" / "miner-country")))
try:
    COUNTRY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
NE_FILE = COUNTRY_CACHE_DIR / "ne_110m_admin_0_countries.geojson"
IPINFO_TOKEN = os.getenv("IPINFO_TOKEN", "")
try:
    COUNTRY_AREA_THRESHOLD = float(os.getenv("H3_COUNTRY_THRESHOLD", "0.60"))
except Exception:
    COUNTRY_AREA_THRESHOLD = 0.60

_COUNTRIES: Optional[List[Tuple[str, Any]]] = None
_GEOIP_READER: Optional[Any] = None
_GEOIP_LOG_STATE: Optional[str] = None

from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

import config_profile as _cfg


def _shared_geoip_default_path() -> pathlib.Path:
    base_dir = pathlib.Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    try:
        sub = getattr(_cfg, "GEOIP_SHARED_SUBPATH", "")
    except Exception:
        sub = ""

    try:
        normalized = [part for part in str(sub).replace("\\", "/").split("/") if part]
    except Exception:
        normalized = []

    if not normalized:
        normalized = ["FryNetworks", "GeoLite2", "GeoLite2-Country.mmdb"]

    for part in normalized:
        base_dir = base_dir / part

    return base_dir


_SHARED_GEOIP_PATH = _shared_geoip_default_path()
if not os.environ.get("MAXMIND_DB_PATH"):
    os.environ["MAXMIND_DB_PATH"] = str(_SHARED_GEOIP_PATH)

def _guess_gui_version_from_fs() -> str:
    """Best-effort: infer GUI/agent version from local binaries.
    Windows: FRY_<CODE>_vX.Y.Z*.exe in app_dir or miner_GUI/.
    Linux: FRY_<CODE>_vX.Y.Z in app_dir or a release/<CODE>/ sibling.
    """
    try:
        base_path = pathlib.Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
        pattern_win = re.compile(r"FRY_[A-Z]{2,3}_v(\d+\.\d+\.\d+).*\.exe$", re.IGNORECASE)
        pattern_lin = re.compile(r"FRY_[A-Z]{2,3}_v(\d+\.\d+\.\d+)$", re.IGNORECASE)
        candidates: list[str] = []
        search_roots = {base_path, base_path / "miner_GUI"}
        # Also consider sibling release/<CODE> on Linux installs
        release_dir = base_path / "release"
        if release_dir.exists():
            for sub in release_dir.iterdir():
                if sub.is_dir():
                    search_roots.add(sub)
        for search_root in search_roots:
            if not search_root.exists():
                continue
            for p in search_root.iterdir():
                m = pattern_win.match(p.name) or pattern_lin.match(p.name)
                if m:
                    candidates.append(m.group(1))
        if not candidates:
            return ""
        def _v_tuple(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return (0, 0, 0)
        return max(candidates, key=_v_tuple)
    except Exception:
        return ""

MINER_CODE = getattr(_cfg, "MINER_CODE", "")
VERSION = getattr(_cfg, "VERSION", "")
GUI_VERSION = _guess_gui_version_from_fs()
SOFTWARE_VERSION = GUI_VERSION or getattr(_cfg, "SOFTWARE_VERSION", VERSION)
POC_VERSION = getattr(_cfg, "POC_VERSION", VERSION)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("miner-online")
_CRIT_FAILURES: dict[str, int] = {}

SOFTWARE_VERSION_REFRESH_SECONDS = 600
_SOFTWARE_VERSION_LAST_REFRESH = 0.0

# Firewall port constants for privileged operations
MYST_TEQUILAPI_PORT = 4050
MYST_WIREGUARD_PORT = 51820
PRESEARCH_API_PORT = 80
DIIISCO_API_PORT = 8080
DEFAULT_RPC_PORT = 9944  # Default Substrate RPC port (Space Acres)

def refresh_software_version(force: bool = False) -> str:
    """Re-check GUI executable to detect updated software versions at runtime."""
    global SOFTWARE_VERSION, GUI_VERSION, _SOFTWARE_VERSION_LAST_REFRESH
    now = time.time()
    if not force:
        last = _SOFTWARE_VERSION_LAST_REFRESH
        if last and (now - last) < SOFTWARE_VERSION_REFRESH_SECONDS:
            return SOFTWARE_VERSION
    new_guess = _guess_gui_version_from_fs()
    fallback = getattr(_cfg, "SOFTWARE_VERSION", VERSION)
    new_version = new_guess or fallback or SOFTWARE_VERSION
    updated = False
    if new_guess:
        GUI_VERSION = new_guess
    if new_version and new_version != SOFTWARE_VERSION:
        SOFTWARE_VERSION = new_version
        updated = True
    _SOFTWARE_VERSION_LAST_REFRESH = now
    if updated:
        try:
            log.info("Detected updated software version %s", SOFTWARE_VERSION)
        except Exception:
            pass
    return SOFTWARE_VERSION


def _record_critical_failure(key: str, msg: str, *, threshold: int = 2, exit_code: int = 12, miner_key: Optional[str] = None) -> None:
    """Count a critical failure; exit once threshold is reached to signal GUI restart."""
    count = _CRIT_FAILURES.get(key, 0) + 1
    _CRIT_FAILURES[key] = count
    try:
        if count >= threshold:
            log.error("%s (hit %d/%d, exiting)", msg, count, threshold)
            try:
                _write_stop_reason(msg, miner_key=miner_key)
            except Exception:
                pass
            sys.exit(exit_code)
        else:
            log.warning("%s (hit %d/%d)", msg, count, threshold)
    except SystemExit:
        raise
    except Exception:
        pass

def _reset_critical_failure(key: str) -> None:
    _CRIT_FAILURES.pop(key, None)

def _write_stop_reason(reason: str, *, miner_key: Optional[str] = None) -> None:
    """Persist last stop reason for GUI consumption."""
    try:
        payload = {
            "reason": str(reason),
            "miner_key": miner_key,
            "timestamp": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        path = os.path.join(data_dir(), "status", "last_stop.json")
        atomic_write_json(path, payload)
    except Exception:
        pass

def _init_service_file_logging() -> None:
    """Attach a file handler under the ProgramData logs folder for service diagnostics.

    Note: When running as a Windows service, the service wrapper typically redirects
    stderr to the log file already. We skip adding a FileHandler to avoid duplicates.
    """
    try:
        import sys

        # Skip file handler setup if stderr is not a TTY (likely redirected by service wrapper)
        # This prevents duplicate log entries when running as a Windows service
        try:
            if not sys.stderr.isatty():
                return
        except (AttributeError, ValueError):
            # If isatty() fails or doesn't exist, proceed with normal setup
            pass

        log_dir = os.path.join(data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        file_path = os.path.join(log_dir, "service.err.log")

        root = logging.getLogger()

        # Check if a FileHandler for this file already exists
        for handler in root.handlers:
            if isinstance(handler, logging.FileHandler):
                try:
                    handler_path = os.path.abspath(handler.baseFilename)
                    target_path = os.path.abspath(file_path)
                    if handler_path == target_path:
                        # Handler already exists for this file
                        return
                except (AttributeError, OSError):
                    pass

        # Add new FileHandler only if one doesn't exist
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        root.addHandler(fh)
    except Exception:
        # Do not block startup if logging cannot initialize
        pass

def log_step(step: str, data: Dict[str, Any]) -> None:
    """Log a structured step with associated data for diagnostics.
    
    This provides structured logging for key service events, particularly
    for IPC queue operations and privileged operations handling.
    
    Args:
        step: Event identifier (e.g., "ops_daemon_start", "privileged_config_file_written")
        data: Additional context data for the event
    """
    try:
        log.info("STEP: %s | %s", step, json.dumps(data, default=str))
    except Exception:
        try:
            log.info("STEP: %s | %s", step, str(data))
        except Exception:
            pass


# ====================================================================
# SERVICE CONFIGURATION MANAGEMENT
# ====================================================================

_service_config_cache: Dict[str, Any] = {}
_service_config_lock = threading.Lock()

def _read_service_config() -> Dict[str, Any]:
    """Read all configuration files from config/ directory into memory.
    
    Reads:
    - config/miner_config.json - Main miner configuration (enable/disable flags)
    - config/presearch_config.json - Presearch tool settings
    - config/diiisco_config.json - Diiisco tool settings
    - config/brd_config.json - Bright tool settings
    - config/honeygain.json - Honeygain tool settings
    - config/honeygain.enc - Honeygain API key (encrypted)
    
    Critical credentials (API keys, payout addresses) are embedded at build time
    from 1Password and available via load_config() -> tool_credentials.
    
    Returns:
        Dictionary with all configuration data
    """
    config = {}
    
    try:
        config_dir = os.path.join(data_dir(), "config")
        
        # Load encrypted miner_config.enc; required in production
        decrypted_miner_config = _decrypt_config_file(
            "miner_config.enc",
            salt=b"miner_config_salt_v1",
            password="miner_config_encryption_key_v1",
        )
        if isinstance(decrypted_miner_config, dict):
            miner_config = decrypted_miner_config.get("miner_config")
            if isinstance(miner_config, dict):
                config["miner_config"] = miner_config
            else:
                config["miner_config"] = {
                    k: v for k, v in decrypted_miner_config.items()
                    if k not in ("measurement_intervals",)
                }

            intervals = decrypted_miner_config.get("measurement_intervals")
            if isinstance(intervals, dict):
                config["measurement_intervals"] = intervals

            if "miner_key" in decrypted_miner_config:
                config.setdefault("miner_key", decrypted_miner_config.get("miner_key"))
        else:
            log.warning("Encrypted miner_config.enc missing or failed to decrypt; measurement intervals and miner config unavailable")
        
        # Read tool-specific config files (written by GUI)
        tool_configs = {
            'presearch_config': 'presearch_config.json',
            'diiisco_config': 'diiisco_config.json',
            'brd_config': 'brd_config.json',
            'honeygain_config': 'honeygain.json'
        }
        
        for config_key, config_filename in tool_configs.items():
            config_path = os.path.join(config_dir, config_filename)
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        tool_config = json.load(f)
                        if isinstance(tool_config, dict):
                            config[config_key] = tool_config
                            log.debug("Loaded %s", config_filename)
                except Exception as e:
                    log.warning("Failed to load %s: %s", config_filename, e)
    
    except Exception as e:
        log.error("Error reading service configuration: %s", e)
    
    return config


def _load_service_config() -> Dict[str, Any]:
    """Load service configuration with caching and thread safety.
    
    Returns:
        Cached configuration dictionary
    """
    with _service_config_lock:
        return dict(_service_config_cache)


def _get_tool_credentials() -> Dict[str, Any]:
    """Get tool credentials from embedded config.
    
    Credentials are embedded at build time from 1Password and include:
    - mysterium_api_key, mysterium_identity
    - presearch_registration_code
    - diiisco_api_key
    - spaceacres_farmer_key, spaceacres_reward_address
    - bright_api_token
    - honeygain_api_key
    
    Returns:
        Dictionary of tool credentials (empty if not embedded)
    """
    try:
        cfg = load_config()
        return cfg.get('tool_credentials', {})
    except Exception as e:
        log.warning("Failed to load tool credentials: %s", e)
        return {}


def _get_presearch_api_key() -> str:
    """Resolve Presearch API key from embedded credentials or service config."""
    try:
        creds = _get_tool_credentials()
        api_key = creds.get("presearch_api_key", "")
        if isinstance(api_key, str) and api_key:
            return api_key
    except Exception:
        pass
    try:
        cfg = _load_service_config()
        pre_cfg = cfg.get("presearch_config", {})
        if isinstance(pre_cfg, dict):
            api_key = pre_cfg.get("api_key") or pre_cfg.get("presearch_api_key")
            if isinstance(api_key, str) and api_key:
                return api_key
    except Exception:
        pass
    return ""


def _reload_service_config() -> None:
    """Reload configuration from disk (called when GUI updates config)."""
    try:
        new_config = _read_service_config()
        with _service_config_lock:
            _service_config_cache.clear()
            _service_config_cache.update(new_config)
        
        log_step("service_config_reloaded", {
            "configs_loaded": list(new_config.keys()),
            "timestamp": now_utc().isoformat()
        })
        log.info("Service configuration reloaded")
    except Exception as e:
        log.error("Failed to reload service configuration: %s", e)


def _get_measurement_intervals() -> Dict[str, int]:
    """Get per-sensor measurement intervals (seconds).
    
    Default intervals:
    - Bandwidth: 10 seconds
    - Satellite: 10 seconds
    - Radiation: 10 seconds
    - Decibel: 2 seconds
    - AEM: 600 seconds (10 minutes)
    - Tools: 60 seconds
    """
    defaults = {
        "bandwidth": 10,
        "satellite": 10,
        "radiation": 10,
        "decibel": 2,
        "aem": 600,
        "tools": 60,
    }

    try:
        config = _load_service_config()
        intervals = config.get('measurement_intervals', {})
        if isinstance(intervals, dict):
            # Only accept positive numeric overrides; keep defaults otherwise
            for key, value in intervals.items():
                if key in defaults and isinstance(value, (int, float)) and value > 0:
                    defaults[key] = int(value)
    except Exception:
        pass
    
    return defaults



def _get_enabled_tools() -> List[str]:
    """Get list of enabled tools from configuration.
    
    Only returns tools that are:
    1. Enabled in the configuration
    2. Supported by this miner type (BM only supports mysterium, etc.)
    
    Returns:
        List of enabled tool names (e.g., ['mysterium'])
    """
    enabled = []
    
    try:
        config = _load_service_config()
        miner_config = config.get('miner_config', {})
        
        if not isinstance(miner_config, dict):
            return enabled
        
        # Define which PoCs are supported by each miner type
        supported_pocs = {
            'BM': ['mysterium', 'bright', 'honeygain'],     # Bandwidth Miner
            'SDN': ['spaceacres'],                           # Storage/Data Node
            'SVN': ['presearch', 'diiisco'],                 # Storage Verification Node
            'AEM': [],                                       # Agent/Edge Miner (if any)
        }
        
        # Get supported PoCs for this miner
        allowed = supported_pocs.get(MINER_CODE, [])
        
        # Check each PoC type
        poc_types = {
            'mysterium': ['mysterium_enabled', 'enable_mysterium'],
            'presearch': ['presearch_enabled', 'enable_presearch'],
            'diiisco': ['diiisco_enabled', 'enable_diiisco'],
            'spaceacres': ['spaceacres_enabled', 'enable_spaceacres'],
            'bright': ['bright_enabled', 'enable_bright'],
            'honeygain': ['honeygain_enabled', 'enable_honeygain'],
        }
        
        for poc_type, config_keys in poc_types.items():
            # Only include if supported by this miner type
            if poc_type not in allowed:
                continue

            # Docker-based tools require Docker to be installed and running
            if poc_type in DOCKER_TOOLS and not _is_docker_available():
                log.warning("Tool %s is enabled but Docker is not available; skipping", poc_type)
                continue

            for key in config_keys:
                if miner_config.get(key) is True:
                    enabled.append(poc_type)
                    break
    
    except Exception as e:
        log.warning("Error determining enabled PoCs: %s", e)
    
    return enabled


def _is_running_as_service() -> bool:
    """Return True if this process is running as a managed service.

    Windows: checks for Session 0 (all services run there).
    Linux/aarch64: checks for INVOCATION_ID env var set by systemd.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            session_id = ctypes.c_ulong()
            if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
                return session_id.value == 0
        else:
            # systemd sets INVOCATION_ID for every service unit it manages
            return bool(os.environ.get("INVOCATION_ID"))
    except Exception:
        pass
    return True  # Detection failed: fail-open


def _detect_virtual_machine() -> Dict[str, Any]:
    """Best-effort virtual machine detection across major platforms."""
    info: Dict[str, Any] = {"vm": None, "evidence": [], "method": "heuristic"}
    try:
        sys_name = platform.system()
        sys_lower = sys_name.lower()

        vm_markers = [
            "virtual", "vmware", "hyper-v", "xen", "qemu", "kvm",
            "parallels", "virtualbox", "vbox", "bochs"
        ]

        # --- Windows ---
        if sys_lower == "windows":
            try:
                wmi_cmd = ["powershell", "-NoLogo", "-WindowStyle", "Hidden", "-NoProfile", "-Command",
                           "Get-CimInstance Win32_ComputerSystem | Select Manufacturer,Model"]
                r = subprocess.run(wmi_cmd, capture_output=True, text=True, timeout=5)
                out = (r.stdout + r.stderr).lower()
                if any(m in out for m in vm_markers):
                    info["vm"] = True
                    info["evidence"].append(f"WMI Manufacturer/Model: {out.strip()[:120]}")
                    info["method"] = "wmi"
                    return info
            except Exception:
                pass
            # BIOS vendor / product via wmic (fallback)
            try:
                r = subprocess.run(["wmic", "computersystem", "get", "manufacturer,model"],
                                   capture_output=True, text=True, timeout=5)
                out = (r.stdout + r.stderr).lower()
                if any(m in out for m in vm_markers):
                    info["vm"] = True
                    info["evidence"].append(f"WMIC manufacturer/model: {out.strip()[:120]}")
                    info["method"] = "wmic"
                    return info
            except Exception:
                pass
            # MAC OUI heuristic
            try:
                mac = uuid.getnode()
                oui = f"{(mac >> 40) & 0xff:02x}:{(mac >> 32) & 0xff:02x}:{(mac >> 24) & 0xff:02x}"
                vm_ouis = {"00:05:69", "00:0c:29", "00:1c:14", "00:50:56"}  # VMware ranges
                if oui.lower() in vm_ouis:
                    info["vm"] = True
                    info["evidence"].append(f"VM OUI prefix detected: {oui}")
                    info["method"] = "mac_oui"
            except Exception:
                pass
            if info["vm"] is None:
                info["vm"] = False  # Default to physical if no evidence
            return info

        # --- Linux ---
        if sys_lower == "linux":
            # systemd-detect-virt
            try:
                r = subprocess.run(["systemd-detect-virt"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout.strip() and r.stdout.strip() != "none":
                    info["vm"] = True
                    info["evidence"].append(f"systemd-detect-virt: {r.stdout.strip()}")
                    info["method"] = "systemd-detect-virt"
                    return info
            except Exception:
                pass
            # DMI product name / sys vendor
            dmi_paths = [
                "/sys/class/dmi/id/product_name",
                "/sys/class/dmi/id/sys_vendor",
                "/sys/class/dmi/id/bios_vendor"
            ]
            for p in dmi_paths:
                try:
                    if os.path.exists(p):
                        content = pathlib.Path(p).read_text(encoding="utf-8", errors="ignore").lower()
                        if any(m in content for m in vm_markers):
                            info["vm"] = True
                            info["evidence"].append(f"DMI {os.path.basename(p)}: {content.strip()[:80]}")
                            info["method"] = "dmi"
                            return info
                except Exception:
                    pass
            # /proc/cpuinfo hypervisor flag
            try:
                cpuinfo = pathlib.Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").lower()
                if "hypervisor" in cpuinfo:
                    info["vm"] = True
                    info["evidence"].append("CPU hypervisor flag present")
                    info["method"] = "cpuinfo"
                    return info
            except Exception:
                pass
            if info["vm"] is None:
                info["vm"] = False
            return info

        # --- macOS ---
        if sys_lower == "darwin":
            try:
                r = subprocess.run(["sysctl", "-a"], capture_output=True, text=True, timeout=8)
                out = r.stdout.lower()
                if any(m in out for m in vm_markers):
                    info["vm"] = True
                    info["evidence"].append("sysctl hypervisor indicators present")
                    info["method"] = "sysctl"
                    return info
            except Exception:
                pass
            # Apple Silicon / hardware model seldom virtualized publicly; default False if no evidence
            info["vm"] = False if info["vm"] is None else info["vm"]
            return info

        # Other/unknown OS
        info["vm"] = None
        return info
    except Exception as e:
        info["vm"] = None
        info["evidence"].append(f"vm-detect-error: {e}")
        return info

DEFAULT_H3_RES = 4
MAX_POC_DAYS = 14
DEFAULT_POI_POLL_SECONDS = 15  # Fast PoI refresh for local cache/UI

# Shared PoI state across main loop and PoI monitor thread
_POI_STATE_LOCK = threading.Lock()
_POI_STATE_READY = threading.Event()
_POI_STATE: Dict[str, Any] = {
    "status": "offline",
    "pod_status": None,
    "mac_registered": None,
    "mac_mismatch": None,
    "interval": 600,
    "last_poi": None,
}

def _update_poi_state(**kwargs: Any) -> None:
    with _POI_STATE_LOCK:
        _POI_STATE.update(kwargs)
    _POI_STATE_READY.set()

def _get_poi_state_snapshot(wait: bool = False, timeout: Optional[float] = None) -> Dict[str, Any]:
    if wait:
        _POI_STATE_READY.wait(timeout=timeout)
    with _POI_STATE_LOCK:
        return dict(_POI_STATE)

# Expected measurement groups per miner type (drives Proof of Data checks).
MEASUREMENT_EXPECTATIONS: dict[str, List[str]] = {
    "BM": ["Bandwidth"],
    "IRM": ["Radiation"],
    "ISM": ["Satellite"],
    "OSM": ["Satellite"],
    "IDM": ["Decibel"],
    "ODM": ["Decibel"],
}
SDK_NAMES: Tuple[str, ...] = ("bright", "honeygain", "mysterium")
_CONFIG_FILE_TO_SDK: Dict[str, str] = {
    "brd_config.json": "bright",
    "honeygain.json": "honeygain",
    "mysterium.json": "mysterium",
}
_TRUE_SET = {"1", "true", "yes", "y", "on", "approved", "allow", "allowed", "enabled"}
_FALSE_SET = {"0", "false", "no", "n", "off", "deny", "denied", "blocked"}

# Allowed Windows service names that can be managed via IPC ops_queue
ALLOWED_SERVICE_NAMES: frozenset = frozenset({"MysteriumNode"})

# Docker container definitions: image, volumes, and credential injection per container
DOCKER_CONTAINER_DEFS: Dict[str, Dict[str, Any]] = {
    "presearch-node": {
        "image": "presearch/node",
        "volumes": ["presearch-node-storage:/app/node"],
        "cred_key": "presearch_registration_code",   # embedded 1Password credential
        "cred_env": "REGISTRATION_CODE",              # env var name for docker run
    },
    "diiisco-node": {
        "image": "diiisco/node",
        "volumes": ["diiisco-node-storage:/app/data"],
        "cred_key": "diiisco_api_key",
        "cred_env": "API_KEY",
    },
}

# Allowed Docker container names that can be managed via IPC ops_queue
ALLOWED_DOCKER_CONTAINERS: frozenset = frozenset(DOCKER_CONTAINER_DEFS.keys())

# Docker-based tools that require Docker to be installed
DOCKER_TOOLS: frozenset = frozenset({"presearch", "diiisco"})

# Docker availability cache
_docker_available: Optional[bool] = None
_docker_available_ts: float = 0.0
_DOCKER_CACHE_TTL: float = 60.0  # seconds


def _is_docker_available() -> bool:
    """Return True if Docker CLI is on PATH and the daemon is responding.

    Used to gate Presearch and other Docker-based tools.
    Result is cached for 60 seconds to avoid repeated subprocess calls.
    """
    global _docker_available, _docker_available_ts
    now = time.time()
    if _docker_available is not None and (now - _docker_available_ts) < _DOCKER_CACHE_TTL:
        return _docker_available
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
        _docker_available = result.returncode == 0
    except FileNotFoundError:
        _docker_available = False
    except Exception:
        _docker_available = False
    _docker_available_ts = now
    return _docker_available


class ApiHealthBackoff:
    """Simple backoff controller that pauses API traffic while the health endpoint fails."""

    def __init__(self, delays: Optional[Iterable[int]] = None) -> None:
        seq = list(delays) if delays is not None else [60, 600, 3600]
        self.delays: List[int] = seq if seq else [60, 600, 3600]
        self.level: int = -1
        self.next_check_epoch: Optional[float] = None

    def reset(self) -> None:
        self.level = -1
        self.next_check_epoch = None

    def record_failure(self) -> int:
        if self.level < len(self.delays) - 1:
            self.level += 1
        delay = self.delays[self.level]
        self.next_check_epoch = time.time() + delay
        return delay

    def wait_for_health(self, base_url: str, *, timeout: float = 5.0) -> None:
        """Block until the health endpoint responds healthy, backing off between probes."""
        while self.level >= 0:
            now_ts = time.time()
            if self.next_check_epoch and self.next_check_epoch > now_ts:
                time.sleep(min(5.0, self.next_check_epoch - now_ts))
                continue
            try:
                if api_health_ok(base_url, timeout=timeout):
                    self.reset()
                    return
            except Exception:
                pass
            delay = self.record_failure()
            try:
                log.warning("Hardware API health check failed; next probe in %ss", delay)
            except Exception:
                pass

def api_health_ok(base_url: str, *, timeout: float = 5.0) -> bool:
    """Return True if the /health endpoint responds with 2xx JSON or text."""
    url = f"{base_url.rstrip('/')}/health"
    try:
        resp = requests.get(url, timeout=timeout)
        if 200 <= resp.status_code < 300:
            return True
    except Exception:
        return False
    return False

def _get_existing_hardware_doc(coll, miner_key: str) -> dict[str, Any]:
    try:
        doc = coll.find_one({"miner_key": miner_key})
        if isinstance(doc, dict):
            return doc
    except Exception:
        pass
    return {}

def _extract_mac_fields(doc: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {"miner_mac": "", "mac_registered": "", "mac_match": False}
    if not isinstance(doc, dict):
        return info
    miner_mac_val = doc.get("miner_mac")
    if isinstance(miner_mac_val, str) and miner_mac_val:
        info["miner_mac"] = miner_mac_val
    mac_registered_val = doc.get("mac_registered") or doc.get("hexID_registered") or doc.get("hexId")
    if isinstance(mac_registered_val, str) and mac_registered_val:
        info["mac_registered"] = mac_registered_val
    mac_match_val = doc.get("mac_match")
    if isinstance(mac_match_val, bool):
        info["mac_match"] = mac_match_val
    elif isinstance(doc.get("mac_mismatch"), bool):
        info["mac_match"] = not doc.get("mac_mismatch")
    mac_obj = doc.get("mac")
    if isinstance(mac_obj, dict):
        if isinstance(mac_obj.get("miner_mac"), str) and mac_obj.get("miner_mac"):
            info["miner_mac"] = mac_obj["miner_mac"]
        if isinstance(mac_obj.get("mac_registered"), str) and mac_obj.get("mac_registered"):
            info["mac_registered"] = mac_obj["mac_registered"]
        if isinstance(mac_obj.get("mac_match"), bool):
            info["mac_match"] = mac_obj["mac_match"]
        elif isinstance(mac_obj.get("mac_mismatch"), bool):
            info["mac_match"] = not mac_obj["mac_mismatch"]
    def _norm(value: Optional[str]) -> str:
        try:
            return re.sub(r"[^0-9a-f]", "", (value or "").lower())
        except Exception:
            return ""
    norm_miner = _norm(info.get("miner_mac"))
    norm_registered = _norm(info.get("mac_registered"))
    if norm_miner and norm_registered:
        info["mac_match"] = (norm_miner == norm_registered)
    return info

def _extract_software_fields(doc: dict[str, Any]) -> dict[str, Any]:
    host_os = "windows" if platform.system().lower().startswith("win") else "linux"
    info: dict[str, Any] = {
        "software_version_installed": SOFTWARE_VERSION,
        "software_version_needed": None,
        "software_uptodate": None,
        "os": host_os,
        "poc_version_installed": POC_VERSION,
        "poc_version_needed": None,
        "poc_uptodate": None,
        "is_uptodate": None,
    }
    if not isinstance(doc, dict):
        return info

    def _ingest(source: Optional[dict[str, Any]]) -> None:
        if not isinstance(source, dict):
            return
        val = source.get("software_version_installed")
        if isinstance(val, str) and val:
            info["software_version_installed"] = val
        val = source.get("software_version_needed")
        if isinstance(val, str) and val:
            info["software_version_needed"] = val
        val = source.get("software_uptodate")
        if isinstance(val, bool):
            info["software_uptodate"] = val
        val = source.get("software_outdated")
        if isinstance(val, bool):
            info["software_outdated"] = val
            if info["software_uptodate"] is None:
                info["software_uptodate"] = not val
        val = source.get("os")
        if isinstance(val, str) and val:
            info["os"] = val.lower()
        val = source.get("poc_version_installed")
        if isinstance(val, str) and val:
            info["poc_version_installed"] = val
        val = source.get("poc_version_needed")
        if isinstance(val, str) and val:
            info["poc_version_needed"] = val
        val = source.get("poc_uptodate")
        if isinstance(val, bool):
            info["poc_uptodate"] = val
        val = source.get("poc_outdated")
        if isinstance(val, bool):
            info["poc_outdated"] = val
            if info["poc_uptodate"] is None:
                info["poc_uptodate"] = not val
        val = source.get("is_uptodate")
        if isinstance(val, bool):
            info["is_uptodate"] = val
        val = source.get("is_outdated")
        if isinstance(val, bool):
            info["is_outdated"] = val

    _ingest(doc)
    _ingest(doc.get("software"))
    return info

def _compose_hardware_doc(
    miner_key: str,
    *,
    miner_type: Optional[str],
    software: dict[str, Any],
    last_updated: dt.datetime,
    poi: Optional[bool] = None,
    poi_slots: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    doc["miner_key"] = miner_key
    doc["miner_type"] = (miner_type or MINER_CODE)

    host_os = "windows" if platform.system().lower().startswith("win") else "linux"
    doc["software"] = {
        "os": software.get("os") if isinstance(software.get("os"), str) and software.get("os") else host_os,
        "software_version_installed": software.get("software_version_installed")
        if isinstance(software.get("software_version_installed"), str) and software.get("software_version_installed")
        else SOFTWARE_VERSION,
        "software_version_needed": software.get("software_version_needed")
        if isinstance(software.get("software_version_needed"), str) and software.get("software_version_needed")
        else None,
        "software_uptodate": software.get("software_uptodate") if isinstance(software.get("software_uptodate"), bool) else None,
        "poc_version_installed": software.get("poc_version_installed") or POC_VERSION,
        "poc_version_needed": software.get("poc_version_needed")
        if isinstance(software.get("poc_version_needed"), str) and software.get("poc_version_needed")
        else None,
        "poc_uptodate": software.get("poc_uptodate") if isinstance(software.get("poc_uptodate"), bool) else None,
        "is_uptodate": software.get("is_uptodate") if isinstance(software.get("is_uptodate"), bool) else None,
    }

    # IMPORTANT: lastUpdated = slot activity only (write_status controls this)
    doc["lastUpdated"] = last_updated.isoformat()

    if (doc.get("miner_type") == "AEM" or miner_type == "AEM") and poi is not None:
        doc["PoI"] = bool(poi)
    if poi_slots is not None:
        doc["PoI_slots"] = poi_slots

    return doc

def _host_norms() -> tuple[str, str]:
    """Return (full_norm, base_norm) for the current host.
    full_norm is lowercase hostname; base_norm strips domain suffix after first dot.
    """
    try:
        hn = platform.node() or ""
        full_norm = hn.lower()
        base_norm = full_norm.split(".")[0]
        return full_norm, base_norm
    except Exception:
        return "", ""

def app_dir() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def read_text_optional(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return "".join(f.read().splitlines()).strip()
    except Exception:
        return ""

def _config_file_candidates(filename: str) -> List[str]:
    """Return possible locations for encrypted config files."""
    candidates: List[str] = []
    try:
        candidates.append(os.path.join(data_dir(), "config", filename))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(data_dir(), filename))
    except Exception:
        pass
    # Development fallback: repo-local config
    try:
        candidates.append(os.path.join(app_dir(), "config", filename))
    except Exception:
        pass
    # Deduplicate while preserving order
    seen: set[str] = set()
    uniq: List[str] = []
    for path in candidates:
        if path and path not in seen:
            seen.add(path)
            uniq.append(path)
    return uniq

def _decrypt_config_file(filename: str, salt: bytes, password: str) -> Optional[Dict[str, Any]]:
    """Decrypt a JSON config file using fixed PBKDF2/Fernet settings."""
    try:
        encrypted: Optional[dict[str, Any]] = None
        for path in _config_file_candidates(filename):
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                encrypted = json.load(f)
            break
        if not isinstance(encrypted, dict) or 'data' not in encrypted:
            return None

        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        decrypted = Fernet(key).decrypt(encrypted['data'].encode())
        return json.loads(decrypted)
    except Exception:
        return None

def read_encrypted_miner_config() -> Optional[str]:
    """Read miner key from encrypted config file if present."""
    try:
        config_data = _decrypt_config_file(
            "miner_config.enc",
            salt=b"miner_config_salt_v1",
            password="miner_config_encryption_key_v1",
        )
        if isinstance(config_data, dict):
            return config_data.get("miner_key")
        return None
    except Exception:
        return None

def read_encrypted_install_config() -> Optional[str]:
    """Read install_id from encrypted install config file created by installer."""
    try:
        encrypted_data: Optional[dict[str, Any]] = None
        for config_path in _config_file_candidates("install_config.enc"):
            if not os.path.exists(config_path):
                continue
            with open(config_path, 'r') as f:
                enc = json.load(f)
            encrypted_data = enc
            break
        if not isinstance(encrypted_data, dict):
            return None
        # Use same encryption scheme as miner_config
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64
        # Use fixed salt for install config
        salt = b'install_config_salt_v1'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        password = "install_config_encryption_key_v1".encode()
        key = base64.urlsafe_b64encode(kdf.derive(password))
        # Decrypt
        f = Fernet(key)
        decrypted_data = f.decrypt(encrypted_data['data'].encode())
        config_data = json.loads(decrypted_data)
        return config_data.get('install_id')
    except Exception:
        return None

def read_miner_key() -> str:
    """Read miner key from encrypted config file."""
    # Read from encrypted config file
    miner_key = read_encrypted_miner_config()
    if miner_key:
        return miner_key
    
    raise RuntimeError("Miner key not found in config/miner_config.enc file")

def expected_measurement_groups() -> List[str]:
    """Return normalized list of measurement groups required for PoD for this miner."""
    groups = MEASUREMENT_EXPECTATIONS.get(MINER_CODE, [])
    cleaned: List[str] = []
    for g in groups:
        if isinstance(g, str):
            val = g.strip()
            if val:
                cleaned.append(val)
    return cleaned

def _parse_measurement_timestamp(value: Any) -> Optional[dt.datetime]:
    """Parse a measurement timestamp (ISO string, datetime, or epoch) into UTC."""
    try:
        if isinstance(value, dt.datetime):
            ts = value
        elif isinstance(value, (int, float)):
            ts = dt.datetime.fromtimestamp(float(value), UTC)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            ts = dt.datetime.fromisoformat(text)
        else:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        else:
            ts = ts.astimezone(UTC)
        return ts.replace(microsecond=0)
    except Exception:
        return None

def _normalize_measurement_group(name: Optional[str]) -> str:
    try:
        if not isinstance(name, str):
            return ""
        return name.strip().lower()
    except Exception:
        return ""

def derive_measurement_key(miner_key: str) -> bytes:
    """Derive Fernet key from miner_key for measurement encryption.
    
    Uses PBKDF2-HMAC-SHA256 with fixed salt (same as GUI worker).
    This allows GUI and service to encrypt/decrypt measurements using the same key.
    """
    try:
        import base64
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        
        salt = b'measurements_key_v1'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        derived = kdf.derive(miner_key.encode('utf-8'))
        return base64.urlsafe_b64encode(derived)
    except Exception:
        return b''

def read_measurement_files() -> List[Dict[str, Any]]:
    """Read and decrypt all measurement files from the measurements directory.
    
    Returns list of decrypted measurement data dicts.
    Each dict contains: timestamp, miner_key, group, measurement.
    """
    measurements = []
    try:
        from cryptography.fernet import Fernet
        
        # Get miner key for decryption
        miner_key = read_miner_key()
        if not miner_key:
            return measurements
        
        # Derive Fernet key
        fernet_key = derive_measurement_key(miner_key)
        if not fernet_key:
            return measurements
        
        fernet = Fernet(fernet_key)
        
        # Get measurements directory
        measurements_dir = os.path.join(data_dir(), "measurements")
        if not os.path.exists(measurements_dir):
            return measurements
        
        # Read all .enc files
        for filename in os.listdir(measurements_dir):
            if not filename.endswith('.json.enc'):
                continue
            
            filepath = os.path.join(measurements_dir, filename)
            try:
                with open(filepath, 'rb') as f:
                    encrypted = f.read()
                
                # Decrypt
                decrypted = fernet.decrypt(encrypted)
                data = json.loads(decrypted)
                measurements.append(data)
            except Exception:
                # Skip corrupted or unreadable files
                pass
    
    except Exception:
        pass
    
    return measurements

def upload_measurements_for_slot(
    client: MongoProxyClient,
    miner_key: str,
    install_id: str,
    *,
    expected_groups: Optional[List[str]] = None,
    slot_ts: Optional[dt.datetime] = None,
    interval_seconds: int = 0,
) -> Tuple[bool, List[str]]:
    """Upload local measurement files and return (pod_met, delivered_groups) for this slot."""
    delivered_groups: List[str] = []
    expected_set = {_normalize_measurement_group(g) for g in (expected_groups or []) if g}
    pod_required = bool(expected_set)
    slot_end = slot_ts if isinstance(slot_ts, dt.datetime) else now_utc()
    if slot_end.tzinfo is None:
        slot_end = slot_end.replace(tzinfo=UTC)
    else:
        slot_end = slot_end.astimezone(UTC)
    slot_length = max(1, int(interval_seconds)) if interval_seconds else 1
    slot_start = slot_end - dt.timedelta(seconds=slot_length)
    if MINER_CODE == "AEM":
        # AEM uses PoI; PoD not enforced.
        return True, delivered_groups
    try:
        hex_registered = registered_hexid_from_devices(client, miner_key)
    except Exception:
        hex_registered = None
    else:
        if hex_registered:
            _reset_critical_failure(f"pod-no-hex-{miner_key}")
            _reset_critical_failure(f"pod-upload-error-{miner_key}")
    if not hex_registered:
        _record_critical_failure(f"pod-no-hex-{miner_key}", f"Skipping measurement upload; no registered hexId for {miner_key}", miner_key=miner_key)
        return (False if pod_required else True), delivered_groups
    # First, check local pod_status.json entries (written by collectors after successful upload)
    try:
        pod_status_path = os.path.join(data_dir(), "status", "pod_status.json")
        if os.path.exists(pod_status_path):
            try:
                with open(pod_status_path, "r", encoding="utf-8") as f:
                    pod_data = json.load(f)
                entries: List[Any] = []
                if isinstance(pod_data, dict) and isinstance(pod_data.get("measurements"), list):
                    entries = pod_data.get("measurements") or []
                elif isinstance(pod_data, list):
                    entries = pod_data
                for e in entries:
                    try:
                        ets = e.get("timestamp")
                        mtype = e.get("type") or e.get("measurement_type")
                        mts = _parse_measurement_timestamp(ets)
                        if mts and mtype:
                            in_slot = (slot_start <= mts < slot_end)
                            norm = _normalize_measurement_group(mtype)
                            if in_slot and norm and norm in expected_set and norm not in delivered_groups:
                                delivered_groups.append(norm)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    # Check CSV files for measurements in this slot
    # Measurements are uploaded immediately when collected, so we just verify they exist in CSV
    try:
        from measurements.csv_writer import read_all_rows

        # Map expected groups to sensor types
        sensor_type_map = {
            "bandwidth": "bandwidth",
            "satellite": "satellite",
            "radiation": "radiation",
            "decibel": "decibel",
            "aem": "aem",
        }

        # Determine which dates to check - if slot spans midnight, check both days
        slot_start_date = slot_start.strftime("%Y%m%d")
        slot_end_date = slot_end.strftime("%Y%m%d")
        dates_to_check = [slot_end_date]
        if slot_start_date != slot_end_date:
            dates_to_check.append(slot_start_date)

        for expected_group in expected_set:
            sensor_type = sensor_type_map.get(expected_group)
            if not sensor_type:
                continue

            try:
                # Read rows from CSV for all relevant dates (handles midnight boundary)
                rows = []
                for date_str in dates_to_check:
                    rows.extend(read_all_rows(sensor_type, MINER_CODE, date_str=date_str, dataset="real"))

                # Check if any measurement falls within the slot time range
                for row in rows:
                    try:
                        timestamp_str = row.get("timestamp", "")
                        if not timestamp_str:
                            continue

                        measurement_ts = _parse_measurement_timestamp(timestamp_str)
                        if measurement_ts and slot_start <= measurement_ts < slot_end:
                            # Found a measurement in this slot for this sensor type
                            if expected_group not in delivered_groups:
                                delivered_groups.append(expected_group)
                            break
                    except Exception:
                        continue
            except Exception as e:
                log.debug("Failed to check CSV for %s: %s", sensor_type, e)
                continue

    except Exception as e:
        log.error("Failed to check CSV measurements: %s", e)

    if not pod_required:
        return True, delivered_groups
    delivered_set = set(delivered_groups)
    pod_met = delivered_set.issuperset(expected_set)

    # Log if PoD check failed
    if not pod_met:
        missing_groups = expected_set - delivered_set
        log.info("PoD incomplete for slot %s: missing %s", slot_end, missing_groups)

    return (pod_met, delivered_groups)

def owen_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    nonce, ct = ciphertext[:16], ciphertext[16:]
    cipher = Cipher(algorithms.ChaCha20(key, nonce), mode=None, backend=default_backend())
    dec = cipher.decryptor()
    return dec.update(ct) + dec.finalize()

def decrypt_config() -> Dict[str, Any]:
    # placeholders ? use tools/make_encrypted_config.py for production
    dlt=b''; dlp=b''; knt=b''; knp=b''
    if not dlt or not dlp or not knt or not knp:
        return {}
    kas = owen_decrypt(dlt, dlp)
    ec = owen_decrypt(knt, knp)
    return json.loads(Fernet(kas).decrypt(ec))

def _dotenv_overrides() -> Dict[str, str]:
    """Load optional .env next to the executable for local/dev overrides."""
    exe_path = pathlib.Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    env_path = exe_path.parent / ".env"
    overrides: Dict[str, str] = {}
    try:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                overrides[key.strip()] = value.strip()
    except Exception:
        pass
    return overrides

def load_config() -> Dict[str, Any]:
    # Use embedded encrypted config (credentials from 1Password at build time)
    cfg: Dict[str, Any] = {}
    try:
        embedded_cfg = decrypt_config()
        if isinstance(embedded_cfg, dict):
            # Flatten nested external_api structure for backward compatibility
            if "external_api" in embedded_cfg:
                ext_api = embedded_cfg.get("external_api", {})
                if ext_api.get("base_url"):
                    embedded_cfg["api_base_url"] = ext_api["base_url"]
                if ext_api.get("bearer_token"):
                    embedded_cfg["api_token"] = ext_api["bearer_token"]
            
            # Return if we have valid config
            if embedded_cfg.get("api_base_url") or embedded_cfg.get("api_url"):
                cfg = embedded_cfg
    except Exception:
        pass
    # Allow local .env to override API base URL for developer/test installs
    try:
        overrides = _dotenv_overrides()
        env_base = overrides.get("API_BASE_URL") or os.environ.get("API_BASE_URL")
        if isinstance(env_base, str) and env_base.strip():
            cfg["api_base_url"] = env_base.strip()
    except Exception:
        pass
    
    # No fallback - production builds must have embedded config from 1Password
    if cfg.get("api_base_url") or cfg.get("api_url"):
        return cfg
    return {}

def required_versions_from_db(client) -> Dict[str, Any]:
    """Return software_version_needed, poc_version_needed, and rewards multiplier
    parameters for this miner code from PoC.versions.
    Returns dict with 'software_version', 'poc_version', 'multiplier_base',
    and 'multiplier_per_tool' keys (may be empty if unavailable)."""
    try:
        doc = client["PoC"]["versions"].find_one(
            {"miner_code": MINER_CODE},
            {"software_version_needed": 1, "poc_version_needed": 1,
             "multiplier_base": 1, "multiplier_per_tool": 1, "_id": 0}
        )
        result: Dict[str, Any] = {}
        if isinstance(doc, dict):
            software = doc.get("software_version_needed")
            poc = doc.get("poc_version_needed")
            if isinstance(software, str) and software:
                result["software_version"] = software
            if isinstance(poc, str) and poc:
                result["poc_version"] = poc
            mb = doc.get("multiplier_base")
            if isinstance(mb, (int, float)):
                result["multiplier_base"] = float(mb)
            mpt = doc.get("multiplier_per_tool")
            if isinstance(mpt, (int, float)):
                result["multiplier_per_tool"] = float(mpt)
        return result
    except Exception:
        return {}

# --- Local cache integrity (anti-tamper) ---
def _local_signing_key() -> Optional[bytes]:
    """Return the local signing key (bytes) from config if present.
    Looks for 'local_signing_key_hex' or 'local_signing_key' (hex string).
    """
    try:
        cfg = load_config() or {}
        key_hex = cfg.get("local_signing_key_hex") or cfg.get("local_signing_key")
        if isinstance(key_hex, str) and key_hex:
            key_hex = re.sub(r"[^0-9a-fA-F]", "", key_hex)
            if len(key_hex) >= 16:
                return bytes.fromhex(key_hex)
    except Exception:
        pass
    return None

from cache_integrity import compute_cache_signature as _compute_sig_generic, verify_cache_signature as _verify_sig_generic

def compute_cache_signature(payload: Dict[str, Any]) -> Optional[str]:
    """Compute hex HMAC-SHA256 over payload with the local signing key. Returns None if no key."""
    key = _local_signing_key()
    if not key:
        return None
    return _compute_sig_generic(payload, key)

def verify_cache_signature(doc: Dict[str, Any]) -> bool:
    key = _local_signing_key()
    return _verify_sig_generic(doc, key, allow_if_no_key=False)

def now_utc() -> dt.datetime:
    """Current UTC time as a timezone-aware datetime without microseconds."""
    return dt.datetime.now(UTC).replace(microsecond=0)

def day_bucket(ts: dt.datetime) -> dt.datetime:
    """Start of the UTC day for the given timestamp, preserving tzinfo."""
    tz = ts.tzinfo or UTC
    return dt.datetime(ts.year, ts.month, ts.day, tzinfo=tz)

def day_iso(ts: dt.datetime) -> str:
    """UTC date string YYYY-MM-DD (no hours)."""
    d = day_bucket(ts)
    return d.strftime("%Y-%m-%d")

def hour_and_slot(ts: dt.datetime, interval_seconds: int) -> tuple[int, int]:
    """Return (hour, slotIndex) for a given interval size.
    slotIndex is 0..slots_per_hour-1 where slots_per_hour = 3600/interval_seconds.
    """
    sec_in_hour = ts.minute * 60 + ts.second
    return ts.hour, sec_in_hour // max(1, interval_seconds)

def _compute_rewards_multiplier(
    miner_type: str,
    mac_match: bool,
    poc_ok: bool,
    pod_ok: bool,
    bright_active: bool,
    honeygain_active: bool,
    mysterium_active: bool,
    multiplier_base: float = 1.0,
    multiplier_per_tool: float = 0.1,
    *,
    presearch_active: bool = False,
    diiisco_active: bool = False,
) -> float:
    """Compute per-slot rewards multiplier based on gating and active tools.

    For pod_ok:
    - BM: PoD (Proof of Data) - data upload succeeded
    - AEM: PoI (Proof of Installation) - Olostep running and enabled

    RDN and BM use the same parametric formula:
        multiplier_base + multiplier_per_tool * tool_count
    """
    # SVN, SDN: only check mac_match and poc_ok (no pod_ok or tools required)
    if miner_type in ("SVN", "SDN"):
        return 1.0 if (mac_match and poc_ok) else 0.0

    # RDN: parametric like BM but gates only on mac_match + poc_ok (no pod_ok)
    if miner_type == "RDN":
        if not (mac_match and poc_ok):
            return 0.0
        tool_count = int(bool(presearch_active)) + int(bool(diiisco_active))
        return multiplier_base + multiplier_per_tool * tool_count

    # Other miners require mac_match, poc_ok, and pod_ok
    if not (mac_match and poc_ok and pod_ok):
        return 0.0
    if miner_type == "AEM":
        return 1.0  # All gates passed
    if miner_type == "BM":
        tool_count = (
            int(bool(bright_active))
            + int(bool(honeygain_active))
            + int(bool(mysterium_active))
        )
        return multiplier_base + multiplier_per_tool * tool_count
    # Non-BM/AEM/RDN: gate passed -> full multiplier
    return 1.0

def _get_boot_time_iso() -> Optional[str]:
    """Return system boot time as ISO string in UTC if available."""
    try:
        import psutil  # type: ignore
        bt = psutil.boot_time()
        return dt.datetime.fromtimestamp(bt, UTC).replace(microsecond=0).isoformat()
    except Exception:
        return None

def get_miner_type_offset() -> int:
    """Return the time offset in seconds for this miner type to stagger API calls.
    
    Spreads different miner types across 10 minutes (one per minute):
    - BM:  0 seconds (xx:00)
    - IDM: 60 seconds (xx:01)
    - ODM: 120 seconds (xx:02)
    - ISM: 180 seconds (xx:03)
    - OSM: 240 seconds (xx:04)
    - RDN: 300 seconds (xx:05)
    - SDN: 360 seconds (xx:06)
    - SVN: 420 seconds (xx:07)
    - AEM: 480 seconds (xx:08)
    - IRM: 540 seconds (xx:09)
    """
    offsets = {
        "BM": 0,
        "IDM": 60,
        "ODM": 120,
        "ISM": 180,
        "OSM": 240,
        "RDN": 300,
        "SDN": 360,
        "SVN": 420,
        "AEM": 480,
        "IRM": 540,
    }
    return offsets.get(MINER_CODE, 0)

def next_boundary(ts: dt.datetime, interval_seconds: int) -> dt.datetime:
    """Calculate next interval boundary with miner-type-specific offset.
    
    Adds a fixed offset based on miner type to stagger API load across different miner types.
    """
    base = day_bucket(ts)
    elapsed = (ts - base).seconds
    
    # Get the miner type offset (0-540 seconds depending on type)
    offset = get_miner_type_offset()
    
    # Calculate elapsed time relative to our offset
    elapsed_from_offset = elapsed - offset
    if elapsed_from_offset < 0:
        elapsed_from_offset += 86400  # Handle wrap-around at midnight
    
    # Find next multiple of interval from our offset point
    next_multiple = ((elapsed_from_offset // interval_seconds) + 1) * interval_seconds
    next_wake_seconds = (offset + next_multiple) % 86400
    
    # If we wrapped past midnight, add a day
    if next_wake_seconds < elapsed:
        base += dt.timedelta(days=1)
    
    return base + dt.timedelta(seconds=next_wake_seconds)

def hour_key(ts: dt.datetime) -> str:
    return ts.strftime("%Y%m%d%H")

def target_slot_for_hour(miner_key: str, ts: dt.datetime, slots_per_hour: int) -> int:
    s = f"{miner_key}|{hour_key(ts)}".encode("utf-8")
    h = int(hashlib.sha256(s).hexdigest(), 16)
    return h % max(1, slots_per_hour)

def is_internet_up(timeout: int = 4) -> bool:
    try:
        r = requests.get("http://clients3.google.com/generate_204", timeout=timeout)
        return r.status_code == 204
    except requests.RequestException:
        return False

def connect_mongo(uri: str, tlsCAFile: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None) -> MongoProxyClient:

    cfg = cfg or load_config()

    base_url = cfg.get("api_base_url") or cfg.get("api_url")

    if not base_url and isinstance(uri, str) and uri.startswith("http"):

        base_url = uri

    if not base_url:
        raise RuntimeError("api_base_url missing - executable not built with embedded 1Password credentials")

    token_val = cfg.get("api_token") or cfg.get("api_key")

    token = token_val.strip() if isinstance(token_val, str) and token_val.strip() else None
    
    if not token:
        raise RuntimeError("api_token missing - executable not built with embedded 1Password credentials")

    timeout_val = cfg.get("api_timeout") or cfg.get("api_timeout_seconds") or cfg.get("timeout_seconds")

    try:

        timeout = float(timeout_val) if timeout_val is not None else 10.0

    except Exception:

        timeout = 10.0

    api = ExternalApiClient(base_url, token=token, timeout=timeout)

    client = MongoProxyClient(api)

    client.admin.command("ping")

    return client

def ensure_device_is_installed_flag(client: MongoProxyClient, miner_key: str):
    """No-op: install tracking now stored in main.installations.
    Intentionally avoid writing to main.devices (exe may lack permissions).
    """
    try:
        return
    except Exception:
        pass

def ensure_device_installed_version(client: MongoProxyClient, miner_key: str, version: str):
    """No-op: version tracking lives in main.installations.
    Intentionally avoid writing to main.devices (exe may lack permissions).
    """
    try:
        return
    except Exception:
        pass

def parse_ver(s: str):
    try:
        return [int(x) for x in re.split(r"[^0-9]+", s or "") if x]
    except Exception:
        return [0]

def cmp_ver(a: str, b: str) -> int:
    A, B = parse_ver(a), parse_ver(b)
    L = max(len(A), len(B))
    A += [0]*(L-len(A)); B += [0]*(L-len(B))
    return (A > B) - (A < B)

def _lease_doc_id(miner_key: str) -> str:
    """Return a deterministic _id for the global lease record of a miner_key.
    Using a singleton document per miner_key allows atomic upsert semantics without races.
    """
    return f"lease:{miner_key}"

def upsert_installation_record(
    client: MongoProxyClient,
    miner_key: str,
    install_id: str,
    *,
    software_version_needed: Optional[str] = None,
    poc_version_needed: Optional[str] = None,
    is_uptodate: Optional[bool] = None,
    is_outdated: Optional[bool] = None,
):
    """Track per-machine installation in main.installations.
    Keyed by (miner_key, install_id).
    Records software_version_installed and poc_version_installed, and optionally version requirements and status flags.
    """
    try:
        coll = client["main"]["installations"]
        hn = platform.node(); hn_norm, hn_base = _host_norms()

        payload_set = {
            "miner_key": miner_key,
            "install_id": install_id,
            "minerCode": MINER_CODE,
            "software_version_installed": SOFTWARE_VERSION,
            "poc_version_installed": POC_VERSION,
            "hostname": hn,
            "os": platform.platform(),
            "last_seen_at": now_utc(),
            "is_installed": True,
        }
        if isinstance(software_version_needed, str) and software_version_needed:
            payload_set["software_version_needed"] = software_version_needed
        if isinstance(poc_version_needed, str) and poc_version_needed:
            payload_set["poc_version_needed"] = poc_version_needed
        if is_uptodate is not None:
            payload_set["is_uptodate"] = bool(is_uptodate)
        if is_outdated is not None:
            payload_set["is_outdated"] = bool(is_outdated)
        payload_set_on_insert = {
            "first_installed_at": now_utc(),
        }
        coll.update_one(  # type: ignore[attr-defined]
            {"miner_key": miner_key, "install_id": install_id},
            {"$set": payload_set, "$setOnInsert": payload_set_on_insert},
            upsert=True,
        )
    except Exception:
        pass

def other_active_installation_exists(client: MongoProxyClient, miner_key: str, install_id: str, window_seconds: int = 30) -> bool:
    """Return True if the global lease record is held by another install_id and not expired."""
    try:
        coll = client["PoC"]["installations"]
        now = now_utc()
        q = {
            "_id": _lease_doc_id(miner_key),
            "lease_install_id": {"$exists": True, "$ne": install_id},
            "lease_expires_at": {"$gt": now},
        }
        doc = coll.find_one(q, {"_id": 1})  # type: ignore[attr-defined]
        return doc is not None
    except Exception:
        return False

# Removed: verify_lease_ownership() now handled directly by ExternalApiClient.verify_lease_ownership()

def acquire_installation_lease(client: MongoProxyClient, miner_key: str, install_id: str, lease_seconds: int = 900) -> bool:
    """Best-effort global single-instance lease per miner_key.
    Grants a time-limited lease if no unexpired lease exists or if we already hold it.
    Returns True on success. Safe across machines (uses atomic filter + upsert).
    Schema fields used: lease_install_id, lease_expires_at (UTC ISODate).
    """
    try:
        coll = client["PoC"]["installations"]
        now = now_utc()
        expiry = now + dt.timedelta(seconds=max(60, lease_seconds))
        this_host = platform.node(); this_host_norm, this_base = _host_norms()
        # Singleton lease document identified by _id to avoid races/upserts creating duplicates
        or_terms = [
            {"lease_install_id": install_id},
            {"lease_expires_at": {"$lte": now}},
            {"lease_expires_at": {"$exists": False}},
        ]

        filt = {"_id": _lease_doc_id(miner_key), "$or": or_terms}
        update = {
            "$set": {
                "_id": _lease_doc_id(miner_key),
                "miner_key": miner_key,
                "install_id": install_id,
                "lease_install_id": install_id,
                "lease_expires_at": expiry,
                "last_seen_at": now,
                "is_installed": True,
                "minerCode": MINER_CODE,
                "version_installed": SOFTWARE_VERSION,
                "hostname": this_host,
                "os": platform.platform(),
                "_lease": True,
            },
            "$setOnInsert": {"first_installed_at": now},
        }
        try:
            log.debug("lease-acquire attempt | miner_key=%s install_id=%s filt=%s", miner_key, install_id, filt)
        except Exception:
            pass
        res = coll.update_one(filt, update, upsert=True)  # type: ignore[attr-defined]
        ok = bool(res.matched_count or res.upserted_id)
        try:
            log.debug("lease-acquire result | matched=%s upserted=%s modified=%s ok=%s", res.matched_count, res.upserted_id, getattr(res, 'modified_count', '?'), ok)
        except Exception:
            pass
        return ok
    except Exception as e:
        try:
            log.error("lease-acquire error: %s", e)
        except Exception:
            pass
        return False

def renew_installation_lease(client: MongoProxyClient, miner_key: str, install_id: str, lease_seconds: int = 900) -> bool:
    """Extend our lease if we still hold it. Returns False if we lost it."""
    try:
        coll = client["PoC"]["installations"]
        now = now_utc()
        expiry = now + dt.timedelta(seconds=max(60, lease_seconds))
        res = coll.update_one(  # type: ignore[attr-defined]
            {"_id": _lease_doc_id(miner_key), "lease_install_id": install_id},
            {"$set": {"lease_expires_at": expiry, "last_seen_at": now, "install_id": install_id, "miner_key": miner_key}},
        )
        return bool(res.matched_count)
    except Exception:
        return True  # don't fail hard on transient DB errors

def verify_or_acquire_installation_lease(client: MongoProxyClient, miner_key: str, install_id: str, lease_seconds: int = 900, max_retries: int = 10) -> bool:
    """Verify that a valid lease exists, or acquire it if the API was unreachable during install.
    
    This handles the case where:
    1. Installer acquired lease, but API went down before service started
    2. Service tries to verify lease, gets 502/timeout
    3. Service should wait for API to recover, then verify or reacquire
    
    Args:
        client: MongoDB proxy client with API access
        miner_key: The miner key to verify/acquire lease for
        install_id: This installation's unique ID
        lease_seconds: Lease duration if we need to acquire
        max_retries: Maximum retry attempts with exponential backoff
    
    Returns:
        True if lease is verified or successfully acquired, False otherwise
    """
    api_client = client._api
    
    for attempt in range(max_retries):
        try:
            # First, try to verify existing lease
            if api_client.verify_lease_ownership(miner_key, install_id):
                if attempt > 0:
                    log.info("Lease verified after %d retries", attempt)
                return True
            
            # Lease doesn't exist or is held by someone else - try to acquire it
            log.info("No valid lease found, attempting to acquire...")
            if api_client.acquire_installation_lease(miner_key, install_id, lease_seconds):
                log.info("Successfully acquired installation lease")
                return True
            
            # Someone else holds it - this is a real conflict
            try:
                status = api_client.lease_status(miner_key)
                holder = status.get("holder_install_id")
                if holder and holder != install_id:
                    log.error("Lease is held by different installation: %s", holder)
                    log.error("Only one instance per miner_key can run at a time.")
                    return False
            except Exception:
                pass
            
            return False
            
        except Exception as e:
            error_msg = str(e)
            is_api_down = any(x in error_msg.lower() for x in ["502", "503", "504", "bad gateway", "timeout", "connection"])
            
            if is_api_down and attempt < max_retries - 1:
                # API is down - wait with exponential backoff before retry
                wait_seconds = min(60, 2 ** attempt)  # 1, 2, 4, 8, 16, 32, 60, 60...
                log.warning("External API unreachable (attempt %d/%d): %s", attempt + 1, max_retries, error_msg)
                log.info("Waiting %d seconds for API to recover...", wait_seconds)
                time.sleep(wait_seconds)
                continue
            else:
                # Non-recoverable error or max retries reached
                log.error("Lease verification/acquisition failed: %s", e)
                return False
    
    log.error("Failed to verify or acquire lease after %d attempts", max_retries)
    return False

def _as_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}

def _as_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []

def write_status(
    coll,
    miner_key: str,
    ts: dt.datetime,
    status: str,
    interval_seconds: int,
    software_version_needed: Optional[str] = None,
    poc_version_needed: Optional[str] = None,
    miner_mac: Optional[str] = None,
    mac_registered: Optional[str] = None,
    poi_data: Optional[bool] = None,
    hex_registered: Optional[str] = None,
    pol_override: Optional[Dict[str, Any]] = None,
    pod_status: Optional[bool] = None,
    multiplier_base: float = 1.0,
    multiplier_per_tool: float = 0.1,
    presearch_active: bool = False,
    diiisco_active: bool = False,
) -> None:
    # --- time / slot basics ---
    day = day_iso(ts)  # "YYYY-MM-DD"
    hour, slot = hour_and_slot(ts, interval_seconds)
    slots_per_hour = max(1, 3600 // max(1, interval_seconds))
    day_total_full = 24 * slots_per_hour

    checked_at_iso = ts.isoformat()

    # --- read existing doc safely ---
    existing_doc_raw = _get_existing_hardware_doc(coll, miner_key)
    existing_doc: Dict[str, Any] = existing_doc_raw if isinstance(existing_doc_raw, dict) else {}

    miner_type_val = existing_doc.get("miner_type") if isinstance(existing_doc.get("miner_type"), str) else MINER_CODE

    # --- local cache ---
    local_doc: dict[str, Any] = {}
    try:
        cache_doc = read_local_cache(cache_path_for(ts))
        if isinstance(cache_doc, dict) and cache_doc.get("date") == day:
            local_doc = cache_doc
    except Exception:
        local_doc = {}

    weekly_cache_doc = _read_weekly_cache_doc(ts)
    weekly_day_doc = _weekly_day_doc(ts, weekly_cache_doc)
    weekly_slot_entry = _weekly_slot_entry(weekly_day_doc, hour, slot, slots_per_hour)
    weekly_online_counts = _weekly_online_counts(weekly_day_doc, hour, slot, slots_per_hour)

    # --- compute elapsed slots + uptime counters (Option A: so-far-today) ---
    slots_elapsed = hour * slots_per_hour + (slot + 1)
    slots_elapsed = max(1, min(day_total_full, slots_elapsed))

    day_online_so_far = 0
    local_online = local_doc.get("onlineCountDay") if isinstance(local_doc, dict) else None
    local_total = local_doc.get("totalSlotsDay") if isinstance(local_doc, dict) else None

    if isinstance(local_online, int) and isinstance(local_total, int) and local_total > 0:
        slots_elapsed = max(1, min(day_total_full, local_total))
        day_online_so_far = max(0, min(local_online, slots_elapsed))
    elif isinstance(local_online, int):
        day_online_so_far = max(0, min(local_online, slots_elapsed))
    elif weekly_online_counts:
        weekly_online, weekly_counted = weekly_online_counts
        slots_elapsed = max(1, min(day_total_full, weekly_counted))
        day_online_so_far = max(0, min(weekly_online, slots_elapsed))

    # --- AEM PoI slots (keep existing format) ---
    poi_slots: Optional[dict[str, Any]] = None
    if miner_type_val == "AEM":
        # For AEM, pod_slot_ok represents PoI (Proof of Installation - Olostep)
        if poi_data is not None:
            pod_slot_ok = bool(poi_data)

        poi_count = local_doc.get("poiCountDay") if isinstance(local_doc, dict) else None
        poi_total = local_doc.get("poiTotalSlotsDay") if isinstance(local_doc, dict) else None
        poi_percent_local = local_doc.get("poiPercentDay") if isinstance(local_doc, dict) else None

        if isinstance(poi_percent_local, (int, float)):
            poi_percent = round(float(poi_percent_local), 1)
        elif isinstance(poi_count, int) and isinstance(poi_total, int) and poi_total > 0:
            poi_percent = round(100.0 * max(0, min(poi_count, poi_total)) / max(1, poi_total), 1)
        else:
            poi_percent = None

        poi_hours_clean: dict[str, list[bool]] = {}
        poi_hours_raw = local_doc.get("poiHours") if isinstance(local_doc, dict) else None
        if isinstance(poi_hours_raw, dict):
            for h in range(24):
                key = str(h)
                entry = poi_hours_raw.get(key)
                if not isinstance(entry, dict):
                    continue
                slots_val = entry.get("slots") or []
                if not isinstance(slots_val, list):
                    continue
                clean: list[bool] = []
                for v in slots_val[:slots_per_hour]:
                    if isinstance(v, bool):
                        clean.append(bool(v))
                if clean:
                    poi_hours_clean[key] = clean

        if poi_percent is not None or poi_hours_clean:
            poi_slots = {
                "percentDay": float(poi_percent) if poi_percent is not None else None,
                "hours": poi_hours_clean,
            }

    # --- MAC: compute mac_match + build mac_block (new structure) ---
    mac_info = _extract_mac_fields(existing_doc)
    if isinstance(miner_mac, str) and miner_mac:
        mac_info["miner_mac"] = miner_mac
    if isinstance(mac_registered, str) and mac_registered:
        mac_info["mac_registered"] = mac_registered

    def _norm_mac(value: Optional[str]) -> str:
        try:
            return re.sub(r"[^0-9a-f]", "", (value or "").lower())
        except Exception:
            return ""

    norm_miner = _norm_mac(cast(Optional[str], mac_info.get("miner_mac")))
    norm_registered = _norm_mac(cast(Optional[str], mac_info.get("mac_registered")))
    mac_match = bool(norm_miner and norm_registered and norm_miner == norm_registered)

    existing_mac_raw = existing_doc.get("mac")
    existing_mac: dict[str, Any] = existing_mac_raw if isinstance(existing_mac_raw, dict) else {}

    mac_status_old = existing_mac.get("status") if isinstance(existing_mac.get("status"), bool) else None
    last_changed_at = existing_mac.get("last_changed_at") if isinstance(existing_mac.get("last_changed_at"), str) else None
    if mac_status_old is None or mac_status_old != mac_match:
        last_changed_at = checked_at_iso
    elif not last_changed_at:
        last_changed_at = checked_at_iso

    mac_block = {
        "status": mac_match,
        "last_changed_at": last_changed_at,
        "last_checked_at": checked_at_iso,
        "evidence": {
            "miner_mac": mac_info.get("miner_mac"),
            "registered_mac": mac_info.get("mac_registered"),
        },
    }

    # --- PoL: carry forward from write_location_daily() ---
    existing_pol_raw = existing_doc.get("pol")
    pol_block: dict[str, Any] = existing_pol_raw if isinstance(existing_pol_raw, dict) else {
        "status": False,
        "last_changed_at": checked_at_iso,
        "last_checked_at": checked_at_iso,
        "evidence": {},
    }
    pol_evidence = pol_block.get("evidence")
    if not isinstance(pol_evidence, dict):
        pol_evidence = {}
    if hex_registered and not pol_evidence.get("hexID_registered"):
        pol_evidence["hexID_registered"] = hex_registered
    pol_block["evidence"] = pol_evidence
    if isinstance(pol_override, dict):
        try:
            status_override = pol_override.get("status")
            if isinstance(status_override, bool):
                pol_block["status"] = status_override
            lc_over = pol_override.get("last_changed_at")
            if isinstance(lc_over, str) and lc_over:
                pol_block["last_changed_at"] = lc_over
            lchk_over = pol_override.get("last_checked_at")
            if isinstance(lchk_over, str) and lchk_over:
                pol_block["last_checked_at"] = lchk_over
            evid_over = pol_override.get("evidence")
            if isinstance(evid_over, dict):
                pol_evidence.update(evid_over)
        except Exception:
            pass
    pol_status = pol_block.get("status") if isinstance(pol_block.get("status"), bool) else False

    # --- software info (same logic as before, but no legacy maps) ---
    software_info = _extract_software_fields(existing_doc)

    needed_value = (
        software_version_needed.strip()
        if isinstance(software_version_needed, str) and software_version_needed.strip()
        else software_info.get("software_version_needed")
    )
    poc_needed_value = (
        poc_version_needed.strip()
        if isinstance(poc_version_needed, str) and poc_version_needed.strip()
        else software_info.get("poc_version_needed")
    )

    software_info["software_version"] = SOFTWARE_VERSION
    software_info["software_version_installed"] = SOFTWARE_VERSION
    software_info["software_version_needed"] = needed_value

    if isinstance(needed_value, str) and needed_value:
        try:
            cmp_res = cmp_ver(SOFTWARE_VERSION, needed_value)
            software_info["software_uptodate"] = (cmp_res >= 0)
            software_info["software_outdated"] = (cmp_res < 0)
        except Exception:
            software_info["software_uptodate"] = None
            software_info["software_outdated"] = None
    else:
        if not isinstance(software_info.get("software_uptodate"), bool):
            software_info["software_uptodate"] = None
        software_info["software_outdated"] = None

    software_info["poc_version_installed"] = POC_VERSION
    software_info["poc_version_needed"] = poc_needed_value if isinstance(poc_needed_value, str) and poc_needed_value else None

    if isinstance(poc_needed_value, str) and poc_needed_value:
        try:
            poc_cmp = cmp_ver(POC_VERSION, poc_needed_value)
            software_info["poc_uptodate"] = (poc_cmp >= 0)
            software_info["poc_outdated"] = (poc_cmp < 0)
        except Exception:
            software_info["poc_uptodate"] = None
            software_info["poc_outdated"] = None
    else:
        if not isinstance(software_info.get("poc_uptodate"), bool):
            software_info["poc_uptodate"] = None
        software_info["poc_outdated"] = None

    has_requirements = bool((software_info.get("software_version_needed") or software_info.get("poc_version_needed")))
    if has_requirements:
        any_outdated = bool((software_info.get("software_outdated") is True) or (software_info.get("poc_outdated") is True))
        software_info["is_outdated"] = any_outdated
        software_info["is_uptodate"] = not any_outdated
    else:
        software_info["is_outdated"] = None
        software_info["is_uptodate"] = None

    # --- slot-level PoC from local cache ---
    poc_slot_ok: Optional[bool] = None
    try:
        hours_local_raw = local_doc.get("hours")
        hours_local: dict[str, Any] = hours_local_raw if isinstance(hours_local_raw, dict) else {}

        h_entry_raw = hours_local.get(str(hour))
        h_entry: dict[str, Any] = h_entry_raw if isinstance(h_entry_raw, dict) else {}

        slots_list_raw = h_entry.get("slots")
        slots_list: list[Any] = slots_list_raw if isinstance(slots_list_raw, list) else []

        if slot < len(slots_list):
            poc_slot_ok = (slots_list[slot] == "online")
    except Exception:
        poc_slot_ok = None

    if poc_slot_ok is None and isinstance(weekly_slot_entry, dict):
        gates = _safe_dict(weekly_slot_entry.get("gates"))
        poc_slot_ok = bool(gates.get("online")) if gates else None

    if poc_slot_ok is None:
        poc_slot_ok = (status == "online")

    # --- slot-level PoD: use provided value or fall back to cache ---
    pod_slot_ok: Optional[bool] = pod_status
    
    # If not provided, try local cache
    if pod_slot_ok is None:
        try:
            pod_hours_local_raw = local_doc.get("podHours")
            pod_hours_local: dict[str, Any] = pod_hours_local_raw if isinstance(pod_hours_local_raw, dict) else {}

            pod_entry_raw = pod_hours_local.get(str(hour))
            pod_entry: dict[str, Any] = pod_entry_raw if isinstance(pod_entry_raw, dict) else {}

            pod_slots_list_raw = pod_entry.get("slots")
            pod_slots_list: list[Any] = pod_slots_list_raw if isinstance(pod_slots_list_raw, list) else []

            if slot < len(pod_slots_list):
                pod_slot_ok = bool(pod_slots_list[slot])
        except Exception:
            pod_slot_ok = None

    # Fall back to weekly cache
    if pod_slot_ok is None and isinstance(weekly_slot_entry, dict):
        gates = _safe_dict(weekly_slot_entry.get("gates"))
        pod_slot_ok = bool(gates.get("data")) if gates else None

    # Final fallback
    if pod_slot_ok is None:
        pod_slot_ok = False

    # --- BM bandwidth tools ---
    bright_active = False
    honeygain_active = False
    mysterium_active = False
    selected_tools: list[str] = []

    if miner_type_val == "BM":
        try:
            bright_active = bool(sdk_approved("bright"))
        except Exception:
            bright_active = False
        try:
            honeygain_active = bool(sdk_approved("honeygain"))
        except Exception:
            honeygain_active = False
        try:
            mysterium_active = bool(sdk_approved("mysterium"))
        except Exception:
            mysterium_active = False

        selected_tools = [name for name, active in (
            ("bright", bright_active),
            ("honeygain", honeygain_active),
            ("mysterium", mysterium_active),
        ) if active]

    # --- SVN Docker-based tools ---
    elif miner_type_val == "SVN":
        selected_tools = [name for name, active in (
            ("presearch", presearch_active),
            ("diiisco", diiisco_active),
        ) if active]

    # --- compute multiplier ---
    rewards_multiplier_value = _compute_rewards_multiplier(
        miner_type_val if isinstance(miner_type_val, str) and miner_type_val else MINER_CODE,
        mac_match,
        bool(poc_slot_ok),
        bool(pod_slot_ok),  # BM: PoD, AEM: PoI
        bright_active,
        honeygain_active,
        mysterium_active,
        multiplier_base=multiplier_base,
        multiplier_per_tool=multiplier_per_tool,
        presearch_active=presearch_active,
        diiisco_active=diiisco_active,
    )

    # --- unified slot snapshot for the graph ---
    slot_obj: dict[str, Any] = {
        "gates": {
            "data": bool(pod_slot_ok),
            "online": (status == "online"),
            "mac_match": mac_match,
        },
        "tools_active": selected_tools,
        "tools_count": len(selected_tools),
        "multiplier": rewards_multiplier_value,
    }

    # --- Update rewards structure: rewards[day][hour].slots[slot] ---
    rewards_root_raw = existing_doc.get("rewards")
    rewards_root: dict[str, Any] = rewards_root_raw if isinstance(rewards_root_raw, dict) else {}

    day_bucket_raw = rewards_root.get(day)
    day_bucket: dict[str, Any] = day_bucket_raw if isinstance(day_bucket_raw, dict) else {}

    hour_key = str(hour)
    hour_bucket_raw = day_bucket.get(hour_key)
    hour_bucket: dict[str, Any] = hour_bucket_raw if isinstance(hour_bucket_raw, dict) else {}

    slots_list_raw = hour_bucket.get("slots")
    slots_list: list[Any] = slots_list_raw if isinstance(slots_list_raw, list) else []
    if len(slots_list) < slots_per_hour:
        slots_list = slots_list + [None] * (slots_per_hour - len(slots_list))
    slots_list[slot] = slot_obj

    hour_bucket["slots"] = slots_list
    day_bucket[hour_key] = hour_bucket
    rewards_root[day] = day_bucket

    # --- keep only last 30 days of rewards ---
    MAX_REWARDS_DAYS = 30
    reward_days = sorted([k for k in rewards_root.keys() if isinstance(k, str)])
    if len(reward_days) > MAX_REWARDS_DAYS:
        drop = reward_days[: len(reward_days) - MAX_REWARDS_DAYS]
        for d in drop:
            rewards_root.pop(d, None)

    # --- Compute Option A daily avg (so far today): only count filled slots ---
    total = 0.0
    count = 0
    for _, h_val in day_bucket.items():
        if not isinstance(h_val, dict):
            continue
        h_slots = h_val.get("slots")
        if not isinstance(h_slots, list):
            continue
        for s in h_slots:
            if isinstance(s, dict):
                m = s.get("multiplier")
                if isinstance(m, (int, float)):
                    total += float(m)
                    count += 1

    rewards_multiplier_day = (total / count) if count > 0 else 0.0

    # --- rewards_multiplier_history (array, last 30 days) ---
    hist_raw = existing_doc.get("rewards_multiplier_history")
    hist = _as_list(hist_raw)

    entry = {
        "day": day,
        "avg": float(round(rewards_multiplier_day, 6)),
        "counted_slots": int(count),
    }

    updated = False
    new_hist: list[dict[str, Any]] = []
    for item in hist:
        it = _as_dict(item)
        if it.get("day") == day:
            new_hist.append(entry)
            updated = True
        else:
            if isinstance(it.get("day"), str) and isinstance(it.get("avg"), (int, float)) and isinstance(it.get("counted_slots"), int):
                new_hist.append(it)

    if not updated:
        new_hist.append(entry)

    new_hist_sorted = sorted(new_hist, key=lambda x: cast(str, x.get("day", "")))
    if len(new_hist_sorted) > 30:
        new_hist_sorted = new_hist_sorted[-30:]

    # --- uptime info (Option A: so far today) ---
    uptime_raw = existing_doc.get("uptime")
    uptime_existing: Dict[str, Any] = uptime_raw if isinstance(uptime_raw, dict) else {}
    prev_status = uptime_existing.get("status") if isinstance(uptime_existing.get("status"), str) else None

    last_online_at = uptime_existing.get("last_online_at") if isinstance(uptime_existing.get("last_online_at"), str) else None
    last_offline_at = uptime_existing.get("last_offline_at") if isinstance(uptime_existing.get("last_offline_at"), str) else None
    current_run_started_at = uptime_existing.get("current_run_started_at") if isinstance(uptime_existing.get("current_run_started_at"), str) else None

    if status == "online":
        last_online_at = checked_at_iso
        if prev_status != "online" or not current_run_started_at:
            current_run_started_at = checked_at_iso
    else:
        last_offline_at = checked_at_iso

    uptime_seconds_24h = max(0, int(day_online_so_far * interval_seconds))
    downtime_seconds_24h = max(0, int(max(0, slots_elapsed - day_online_so_far) * interval_seconds))

    uptime_info_new = {
        "status": status,
        "last_online_at": last_online_at,
        "last_offline_at": last_offline_at,
        "current_run_started_at": current_run_started_at,
        "uptime_seconds_24h": uptime_seconds_24h,
        "downtime_seconds_24h": downtime_seconds_24h,
    }

    boot_time_existing = existing_doc.get("boot_time")
    boot_time_val = _get_boot_time_iso() or (boot_time_existing if isinstance(boot_time_existing, str) else None)

    # --- compose base doc (NEW signature) ---
    new_doc = _compose_hardware_doc(
        miner_key,
        miner_type=miner_type_val,
        software=software_info,
        last_updated=now_utc(),  # lastUpdated = slot activity only
        poi=poi_data if (miner_type_val == "AEM") else None,
        poi_slots=poi_slots if (miner_type_val == "AEM") else None,
    )

    # --- inject simplified fields ---
    new_doc["mac"] = mac_block
    new_doc["pol"] = pol_block
    new_doc["rewards"] = rewards_root
    new_doc["rewards_multiplier_day"] = float(round(rewards_multiplier_day, 6))
    new_doc["rewards_multiplier_day_counted_slots"] = int(count)
    new_doc["rewards_multiplier_history"] = new_hist_sorted
    new_doc["uptime"] = uptime_info_new

    if boot_time_val:
        new_doc["boot_time"] = boot_time_val

    # Optional query helpers
    new_doc["tz"] = "UTC"
    new_doc["day"] = day

    try:
        coll.replace_one({"miner_key": miner_key}, new_doc, upsert=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    try:
        coll.delete_many({"miner_key": miner_key, "date": {"$exists": True}})
    except Exception:
        pass

def data_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return os.path.join(base, "FryNetworks", f"miner-{MINER_CODE}")
    else:
        return f"/var/lib/frynetworks/miner-{MINER_CODE}"

def read_selected_miner_mac() -> str:
    p = os.path.join(data_dir(), "miner_mac.txt")
    return read_text_optional(p)

def _parse_bool_flag(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        val = value.strip().lower()
        if not val:
            return None
        if val in _TRUE_SET:
            return True
        if val in _FALSE_SET:
            return False
    return None

def read_encrypted_gui_config() -> Dict[str, Any]:
    """Read GUI config (SDK approvals + rewards params) from encrypted file."""
    try:
        config_path = os.path.join(data_dir(), "config", "gui_config.enc")
        if not os.path.exists(config_path):
            return {}
        with open(config_path, "r", encoding="utf-8") as f:
            encrypted_data = json.load(f)
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64

        salt = b'sdk_config_salt_v1'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        password = "sdk_config_encryption_key_v1".encode()
        key = base64.urlsafe_b64encode(kdf.derive(password))
        f = Fernet(key)
        decrypted = f.decrypt(encrypted_data['data'].encode())
        payload = json.loads(decrypted)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}

def _write_encrypted_gui_config(payload: Dict[str, Any]) -> bool:
    """Write GUI config (SDK approvals + rewards params) to encrypted file."""
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64

        salt = b'gui_config_salt_v1'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        password = "gui_config_encryption_key_v1".encode()
        key = base64.urlsafe_b64encode(kdf.derive(password))
        f = Fernet(key)
        encrypted = f.encrypt(json.dumps(payload).encode()).decode()

        config_path = os.path.join(data_dir(), "config", "gui_config.enc")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        temp_path = f"{config_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump({"data": encrypted}, fh)
        os.replace(temp_path, config_path)
        return True
    except Exception:
        log.exception("Failed to write gui_config.enc")
        return False

def _update_sdk_approval(sdk_name: str, approved: bool) -> bool:
    """Read current gui_config.enc, update one SDK's approval, write back."""
    payload = read_encrypted_gui_config()
    payload[sdk_name] = approved
    ok = _write_encrypted_gui_config(payload)
    if ok:
        log_step("sdk_approval_updated", {"sdk": sdk_name, "approved": approved})
    return ok

def _update_sdk_rewards_params(base: float, per_tool: float) -> bool:
    """Write base_reward and per_tool_reward into gui_config.enc for GUI."""
    payload = read_encrypted_gui_config()
    old_base = payload.get("base_reward")
    old_per_tool = payload.get("per_tool_reward")
    changed = (old_base != base or old_per_tool != per_tool)
    payload["base_reward"] = base
    payload["per_tool_reward"] = per_tool
    ok = _write_encrypted_gui_config(payload)
    if ok and changed:
        log_step("sdk_rewards_params_updated", {"base_reward": base, "per_tool_reward": per_tool})
    return ok

def read_sdk_approval_state() -> Dict[str, bool]:
    """Read SDK approval flags from encrypted config only."""
    state: Dict[str, bool] = {}

    def _ingest_payload(payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        entries = payload
        # Allow {"approvals": {...}} structure
        if "approvals" in payload and isinstance(payload["approvals"], dict):
            entries = payload["approvals"]
        for key, value in entries.items():
            name = str(key).strip().lower()
            if not name:
                continue
            maybe = _parse_bool_flag(value)
            if maybe is None and isinstance(value, dict):
                for inner_key in ("approved", "allow", "allowed", "value", "enabled", "status"):
                    if inner_key in value:
                        maybe = _parse_bool_flag(value[inner_key])
                        if maybe is not None:
                            break
            if maybe is not None:
                state[name] = maybe

    try:
        encrypted_payload = read_encrypted_gui_config()
        _ingest_payload(encrypted_payload)
    except Exception:
        pass
    return state

def sdk_approved(name: str) -> bool:
    """Return True if the given SDK (Bright, Honeygain, etc.) is approved by the user."""
    if not isinstance(name, str) or not name:
        return False
    key = name.strip().lower()
    env_keys = [
        f"FRY_SDK_{key.upper()}_APPROVED",
        f"{key.upper()}_SDK_APPROVED",
    ]
    for env_key in env_keys:
        try:
            env_val = os.getenv(env_key)
        except Exception:
            env_val = None
        parsed = _parse_bool_flag(env_val)
        if parsed is not None:
            return parsed
    state = read_sdk_approval_state()
    return bool(state.get(key, False))

def apply_bm_sdk_cap(percent: float) -> float:
    """Cap BM PoC depending on SDK approvals (Bright, Olostep)."""
    if MINER_CODE != "BM":
        return percent
    try:
        bright_ok = sdk_approved("bright")
    except Exception:
        bright_ok = False
    try:
        honeygain_ok = sdk_approved("honeygain")
    except Exception:
        honeygain_ok = False
    is_windows = sys.platform.startswith("win")
    cap = 100.0
    if is_windows:
        approved_count = int(bool(bright_ok)) + int(bool(honeygain_ok))
        if approved_count <= 0:
            cap = 50.0
        elif approved_count == 1:
            cap = 75.0
        else:
            cap = 100.0
    else:
        cap = 75.0
        if honeygain_ok:
            cap = 100.0
    if percent > cap:
        try:
            log.debug(
                "BM PoC capped at %.1f%% (requested %.1f%%, bright=%s, honeygain=%s)",
                cap,
                percent,
                bright_ok,
                honeygain_ok,
            )
        except Exception:
            pass
        return cap
    return percent

def detect_local_mac() -> str:
    """Try to detect a sensible local MAC address.
    Prefer psutil.net_if_addrs() when available, otherwise fall back to uuid.getnode().
    Returns colon-separated upper-case MAC or empty string.
    """
    try:
        try:
            import psutil  # type: ignore[import-not-found]
        except Exception:
            psutil = None
        if psutil:
            # Prefer interface that owns the default route (active internet path)
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.connect(("8.8.8.8", 53))
                    local_ip = sock.getsockname()[0]
                finally:
                    try:
                        sock.close()
                    except Exception:
                        pass
                if isinstance(local_ip, str) and local_ip:
                    addrs = psutil.net_if_addrs()
                    for ifname, lst in addrs.items():
                        ip_match = False
                        for addr in lst:
                            ip = getattr(addr, "address", None)
                            if isinstance(ip, str) and ip == local_ip:
                                ip_match = True
                                break
                        if not ip_match:
                            continue
                        for addr in lst:
                            mac = getattr(addr, "address", None) or ""
                            if not isinstance(mac, str) or not mac:
                                continue
                            norm = re.sub(r"[^0-9A-Fa-f]", "", mac)
                            if len(norm) == 12 and norm != "000000000000":
                                return ":".join(norm[i:i+2].upper() for i in range(0, 12, 2))
            except Exception:
                pass

            addrs = psutil.net_if_addrs()
            for ifname, lst in addrs.items():
                for addr in lst:
                    mac = getattr(addr, "address", None) or ""
                    if not isinstance(mac, str) or not mac:
                        continue
                    norm = re.sub(r"[^0-9A-Fa-f]", "", mac)
                    if len(norm) == 12 and norm != "000000000000":
                        return ":".join(norm[i:i+2].upper() for i in range(0, 12, 2))
    except Exception:
        pass
    try:
        node = uuid.getnode()
        if isinstance(node, int) and node != 0:
            s = ":".join(f"{(node >> ele) & 0xff:02X}" for ele in range(40, -1, -8))
            if re.match(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$", s):
                if s != "00:00:00:00:00:00":
                    return s
    except Exception:
        pass
    return ""

def ensure_dirs():
    path = data_dir()
    os.makedirs(path, exist_ok=True)
    if not sys.platform.startswith("win"):
        try: os.chmod(path, 0o755)
        except Exception: pass
    
    # Create IPC queue directories for privileged operations
    try:
        os.makedirs(os.path.join(path, "ops_queue"), exist_ok=True)
        os.makedirs(os.path.join(path, "ops_processed"), exist_ok=True)
        os.makedirs(os.path.join(path, "config"), exist_ok=True)
        os.makedirs(os.path.join(path, "measurements"), exist_ok=True)
    except Exception:
        pass

def cache_path_for(ts: dt.datetime) -> str:
    return os.path.join(data_dir(), "status", f"status-{ts.strftime('%Y%m%d')}.json")

def read_local_cache(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        # Verify using our local signing key if available; if not, accept.
        if isinstance(doc, dict):
            k = _local_signing_key()
            if k is None:
                return doc
            if _verify_sig_generic(doc, k, allow_if_no_key=False):
                return doc
        return {}
    except Exception:
        return {}

def require_api_base(cfg: Dict[str, Any]) -> str:
    """Extract API base URL from config. Use embedded config in production builds."""
    cfg = cfg or load_config()
    
    # Get base URL from config (embedded or file)
    base_url = cfg.get("api_base_url") or cfg.get("api_url")
    
    if not base_url:
        # Fallback to canonical URL if no config available
        base_url = "https://hardwareapi.frynetworks.com"
    
    return base_url

def atomic_write_json(path: str, payload: dict):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except Exception: pass
        raise

def cache_lock_path_for(ts: dt.datetime) -> str:
    """Return the lock file path for the given day's cache."""
    return os.path.join(data_dir(), "status", f"status-{ts.strftime('%Y%m%d')}.lock")




class _CacheLock:
    """Lightweight advisory lock for a day's cache file.
    Uses msvcrt.locking on Windows; best-effort no-op elsewhere.
    """
    def __init__(self, lock_path: str, *, block_ms: int = 500):
        self.lock_path = lock_path
        self.block_ms = max(0, int(block_ms))
        self._fh = None

    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
            fh = open(self.lock_path, "a+b")
            self._fh = fh
            try:
                import msvcrt  # type: ignore
                # Try to acquire non-blocking; retry a few times up to block_ms
                deadline = time.time() + (self.block_ms / 1000.0)
                while True:
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except Exception:
                        if time.time() >= deadline:
                            break  # best-effort: proceed without lock to avoid deadlock
                        time.sleep(0.05)
                try:
                    fh.seek(0)
                    fh.write(str(os.getpid()).encode("ascii", "ignore"))
                    fh.truncate()
                    fh.flush()
                except Exception:
                    pass
            except Exception:
                # Non-Windows: optional fcntl if available
                if fcntl is not None:
                    fcntl_mod = cast(Any, fcntl)
                    deadline = time.time() + (self.block_ms / 1000.0)
                    while True:
                        try:
                            fcntl_mod.flock(fh.fileno(), fcntl_mod.LOCK_EX | fcntl_mod.LOCK_NB)
                            break
                        except Exception:
                            if time.time() >= deadline:
                                break
                            time.sleep(0.05)
        except Exception:
            self._fh = None
        return self

    def __exit__(self, exc_type, exc, tb):
        fh = self._fh
        self._fh = None
        if not fh:
            return False
        try:
            try:
                import msvcrt  # type: ignore
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                if fcntl is not None:
                    fcntl_mod = cast(Any, fcntl)
                    try:
                        fcntl_mod.flock(fh.fileno(), fcntl_mod.LOCK_UN)
                    except Exception:
                        pass
        finally:
            try:
                fh.close()
            except Exception:
                pass

def _week_bounds_for_rewards(ts: dt.datetime) -> Tuple[dt.datetime, dt.datetime]:
    t = ts.astimezone(UTC)
    start_of_day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_friday = (start_of_day.weekday() - 4) % 7
    week_start = start_of_day - dt.timedelta(days=days_since_friday)
    week_end = week_start + dt.timedelta(days=7)
    return week_start, week_end

def _iso_z(x: dt.datetime) -> str:
    return x.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

def _date_iso(x: dt.datetime) -> str:
    return x.astimezone(UTC).strftime("%Y-%m-%d")

def _week_file_path(week_start: dt.datetime) -> str:
    # one file per week, keyed by Friday date
    # e.g. status-week-20251212.json
    fname = f"status-week-{week_start.astimezone(UTC).strftime('%Y%m%d')}.json"
    return os.path.join(data_dir(), "status", fname)

def _safe_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}

def _safe_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []

def _read_weekly_cache_doc(ts: dt.datetime) -> Dict[str, Any]:
    week_start, _ = _week_bounds_for_rewards(ts)
    path = _week_file_path(week_start)
    doc = read_local_cache(path)
    return doc if isinstance(doc, dict) else {}

def _weekly_day_doc(ts: dt.datetime, weekly_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = weekly_doc or _read_weekly_cache_doc(ts)
    days = _safe_dict(doc.get("days"))
    return _safe_dict(days.get(_date_iso(ts)))

def _weekly_slot_entry(
    day_doc: Dict[str, Any],
    hour: int,
    slot: int,
    slots_per_hour: int,
) -> Optional[Dict[str, Any]]:
    if not day_doc:
        return None
    hours = _safe_dict(day_doc.get("hours"))
    hour_doc = _safe_dict(hours.get(str(hour)))
    slots_list = _safe_list(hour_doc.get("slots"))
    if not slots_list:
        return None
    if len(slots_list) < slots_per_hour:
        slots_list = slots_list + [None] * (slots_per_hour - len(slots_list))
    if 0 <= slot < len(slots_list):
        entry = slots_list[slot]
        if isinstance(entry, dict):
            return entry
    return None

def _weekly_online_counts(
    day_doc: Dict[str, Any],
    hour: int,
    slot: int,
    slots_per_hour: int,
) -> Optional[Tuple[int, int]]:
    if not day_doc:
        return None
    hours = _safe_dict(day_doc.get("hours"))
    online = 0
    counted = 0
    for h in range(0, hour + 1):
        hour_doc = _safe_dict(hours.get(str(h)))
        slots_list = _safe_list(hour_doc.get("slots"))
        if not slots_list:
            continue
        if len(slots_list) < slots_per_hour:
            slots_list = slots_list + [None] * (slots_per_hour - len(slots_list))
        limit = slots_per_hour if h < hour else min(slot + 1, slots_per_hour)
        limit = min(limit, len(slots_list))
        for idx in range(limit):
            entry = slots_list[idx]
            if isinstance(entry, dict):
                counted += 1
                gates = _safe_dict(entry.get("gates"))
                if gates.get("online") is True:
                    online += 1
    if counted == 0:
        return None
    return online, counted

def _tool_states_for_slot() -> Tuple[bool, bool, bool]:
    bright_active = False
    honeygain_active = False
    mysterium_active = False
    try:
        bright_active = bool(sdk_approved("bright"))
    except Exception:
        bright_active = False
    try:
        honeygain_active = bool(sdk_approved("honeygain"))
    except Exception:
        honeygain_active = False
    try:
        mysterium_active = bool(sdk_approved("mysterium"))
    except Exception:
        mysterium_active = False
    return bright_active, honeygain_active, mysterium_active


def _rdn_tool_states_for_slot() -> Tuple[bool, bool]:
    """Return (presearch_active, diiisco_active) for RDN reward calculation.

    Checks whether each tool is enabled in config and its Docker container
    is running.
    """
    presearch_active = False
    diiisco_active = False

    try:
        enabled_tools = _get_enabled_tools()
    except Exception:
        enabled_tools = []

    if "presearch" in enabled_tools:
        try:
            from measurements.tools import poll_presearch
            creds = _get_tool_credentials()
            api_key = creds.get("presearch_api_key", "")
            stats = poll_presearch(api_key=api_key)
            presearch_active = bool(stats.get("running", False))
        except Exception:
            presearch_active = False

    if "diiisco" in enabled_tools:
        try:
            from measurements.tools import poll_diiisco
            stats = poll_diiisco()
            diiisco_active = bool(stats.get("running", False))
        except Exception:
            diiisco_active = False

    return presearch_active, diiisco_active


def _compute_day_aggregates(day_doc: Dict[str, Any]) -> None:
    """
    Fill/refresh:
      - avg_multiplier
      - counted_slots
      - issue_counts (tools/data/online/mac)
      - tools_active (union)
    based on hours[*].slots[*]
    """
    total = 0.0
    count = 0

    issue_counts = {"tools": 0, "data": 0, "online": 0, "mac": 0}
    tools_union: set[str] = set()

    hours = _safe_dict(day_doc.get("hours"))
    for _, hdoc_any in hours.items():
        hdoc = _safe_dict(hdoc_any)
        slots = _safe_list(hdoc.get("slots"))
        for s_any in slots:
            s = _safe_dict(s_any)
            m = s.get("multiplier")
            if isinstance(m, (int, float)):
                total += float(m)
                count += 1

            gates = _safe_dict(s.get("gates"))
            # count failures PER SLOT
            if gates.get("tools") is False:
                issue_counts["tools"] += 1
            if gates.get("data") is False:
                issue_counts["data"] += 1
            if gates.get("online") is False:
                issue_counts["online"] += 1
            if gates.get("mac") is False:
                issue_counts["mac"] += 1

            ta = s.get("tools_active")
            if isinstance(ta, list):
                for name in ta:
                    if isinstance(name, str) and name:
                        tools_union.add(name)

    day_doc["counted_slots"] = int(count)
    day_doc["avg_multiplier"] = float(round((total / count) if count > 0 else 0.0, 6))
    day_doc["issue_counts"] = issue_counts
    day_doc["tools_active"] = sorted(tools_union)

def _compute_week_so_far(doc: Dict[str, Any]) -> Dict[str, Any]:
    days = _safe_dict(doc.get("days"))
    total = 0.0
    count = 0
    issue_counts = {"tools": 0, "data": 0, "online": 0, "mac": 0}

    for _, day_doc_any in days.items():
        day_doc = _safe_dict(day_doc_any)
        # prefer the stored aggregates if present; else compute from slots
        avg = day_doc.get("avg_multiplier")
        c = day_doc.get("counted_slots")
        if isinstance(avg, (int, float)) and isinstance(c, int) and c > 0:
            total += float(avg) * float(c)
            count += int(c)
        else:
            # fall back to scan
            _compute_day_aggregates(day_doc)
            avg2 = day_doc.get("avg_multiplier")
            c2 = day_doc.get("counted_slots")
            if isinstance(avg2, (int, float)) and isinstance(c2, int) and c2 > 0:
                total += float(avg2) * float(c2)
                count += int(c2)

        ic = _safe_dict(day_doc.get("issue_counts"))
        for k in ("tools", "data", "online", "mac"):
            v = ic.get(k)
            if isinstance(v, int):
                issue_counts[k] += int(v)

    return {
        "avg_multiplier": float(round((total / count) if count > 0 else 0.0, 6)),
        "counted_slots": int(count),
        "issue_counts": issue_counts,
    }

def _hour_summary_from_slots(hour_slots: List[Any]) -> Dict[str, Any]:
    """
    Build the per-hour object the GUI expects:
      - gates: bools (True only if ALL known slots pass)
      - tools_active: union across slots
      - number_of_tools: len(tools_active)
      - multiplier: avg across slots with numeric multiplier
    Keeps it robust with partial/missing slots.
    """
    gates_all = {"online": True, "mac": True, "data": True, "tools": True}
    tools_union: set[str] = set()
    total = 0.0
    count = 0

    seen_any_slot = False
    for s_any in hour_slots:
        if not isinstance(s_any, dict):
            continue
        seen_any_slot = True

        gates = s_any.get("gates")
        if isinstance(gates, dict):
            for k in ("online", "mac", "data", "tools"):
                v = gates.get(k)
                if isinstance(v, bool):
                    gates_all[k] = gates_all[k] and v

        ta = s_any.get("tools_active")
        if isinstance(ta, list):
            for name in ta:
                if isinstance(name, str) and name:
                    tools_union.add(name)

        m = s_any.get("multiplier")
        if isinstance(m, (int, float)):
            total += float(m)
            count += 1

    # If hour has no known slots yet, don’t claim it’s “all green”
    if not seen_any_slot:
        gates_all = {"online": False, "mac": False, "data": False, "tools": False}

    tools_sorted = sorted(tools_union)
    avg = (total / count) if count > 0 else 0.0

    return {
        "gates": gates_all,
        "tools_active": tools_sorted,
        "number_of_tools": int(len(tools_sorted)),
        "multiplier": float(round(avg, 6)),
    }

def write_week_local(
    miner_key: str,
    ts: dt.datetime,
    status: str,
    interval_seconds: int,
    *,
    pod_status: Optional[bool] = None,     # "data" gate for BM
    mac_registered: Optional[str] = None,
    mac_mismatch: Optional[bool] = None,
    poi_data: Optional[bool] = None,       # AEM only
    pol_status: Optional[bool] = None,     # PoL (Proof of Location)
    verified: Optional[bool] = None,       # Overall verified gate
    gui_version: Optional[str] = None,
    skip_slot: bool = False,
    multiplier_base: float = 1.0,
    multiplier_per_tool: float = 0.1,
    presearch_active: bool = False,
    diiisco_active: bool = False,
    api_available: Optional[bool] = None,  # Hardware API availability
) -> None:
    """
    One file per rewards-week (Fri 00:00 UTC → next Fri 00:00 UTC).
    Keeps slot objects so GUI can render detailed view from slot data.
    """

    ensure_dirs()

    ts_utc = ts.astimezone(UTC)
    week_start, week_end = _week_bounds_for_rewards(ts_utc)
    path = _week_file_path(week_start)
    lock_path = path + ".lock"

    def _commit(doc_obj: Dict[str, Any]) -> None:
        doc_obj["lastUpdated"] = _iso_z(now_utc())
        try:
            sig_val = compute_cache_signature(doc_obj)
            if isinstance(sig_val, str) and sig_val:
                doc_obj["sig"] = sig_val
        except Exception:
            pass
        with _CacheLock(lock_path):
            atomic_write_json(path, doc_obj)

    # Load existing weekly doc
    with _CacheLock(lock_path):
        doc_raw = read_local_cache(path)
    doc: Dict[str, Any] = doc_raw if isinstance(doc_raw, dict) else {}

    # Initialize if wrong week / empty
    if doc.get("week_start") != _iso_z(week_start) or doc.get("week_end") != _iso_z(week_end):
        doc = {}

    # Base fields (match your schema)
    doc["miner_key"] = miner_key
    doc["minerCode"] = MINER_CODE

    if isinstance(mac_registered, str) and mac_registered.strip():
        doc["mac_registered"] = mac_registered.strip()
    else:
        doc["mac_registered"] = doc.get("mac_registered", "")

    doc["mac_mismatch"] = bool(mac_mismatch) if mac_mismatch is not None else bool(doc.get("mac_mismatch", False))

    # PoL (Proof of Location) - update when provided, otherwise preserve existing
    if isinstance(pol_status, bool):
        doc["pol_status"] = bool(pol_status)
        doc["pol_last_updated"] = _iso_z(ts_utc)
    elif "pol_status" not in doc:
        doc["pol_status"] = None
        doc["pol_last_updated"] = None

    # Verified gate - update when explicitly passed, default to False if never set
    if isinstance(verified, bool):
        doc["verified"] = verified
    elif "verified" not in doc:
        doc["verified"] = False

    # Always populate GUI_version (prefer explicit arg, fallback to global)
    gv = gui_version.strip() if isinstance(gui_version, str) and gui_version.strip() else ""
    if not gv:
        gv = GUI_VERSION.strip() if isinstance(GUI_VERSION, str) and GUI_VERSION.strip() else ""
    doc["GUI_version"] = gv

    # Docker availability (cached 60s, checked by service running as SYSTEM)
    doc["docker"] = _is_docker_available()

    # Hardware API availability (for GUI warnings)
    if isinstance(api_available, bool):
        doc["api_available"] = api_available
        doc["api_last_updated"] = _iso_z(ts_utc)
    elif "api_available" not in doc:
        doc["api_available"] = True  # Default to true if never set
        doc["api_last_updated"] = None

    doc["date"] = _date_iso(ts_utc)
    doc["week_start"] = _iso_z(week_start)
    doc["week_end"] = _iso_z(week_end)

    # Ensure days dict exists
    days = _safe_dict(doc.get("days"))
    doc["days"] = days

    if skip_slot:
        _commit(doc)
        return

    day_key = _date_iso(ts_utc)
    day_doc = _safe_dict(days.get(day_key))
    if not day_doc:
        day_doc = {
            "avg_multiplier": 0.0,
            "counted_slots": 0,
            "issue_counts": {"tools": 0, "data": 0, "online": 0, "mac": 0},
            "tools_active": [],
            "hours": {},
        }

    hours = _safe_dict(day_doc.get("hours"))
    day_doc["hours"] = hours

    # Slot address
    hour, slot = hour_and_slot(ts_utc, interval_seconds)
    slots_per_hour = max(1, 3600 // max(1, interval_seconds))

    hour_key = str(hour)
    hour_doc = _safe_dict(hours.get(hour_key))
    if not hour_doc:
        hour_doc = {}

    slots_list = _safe_list(hour_doc.get("slots"))
    if len(slots_list) < slots_per_hour:
        slots_list = slots_list + [None] * (slots_per_hour - len(slots_list))
    else:
        slots_list = slots_list[:slots_per_hour]

    # Gates + tools
    online_ok = (status == "online")
    mac_ok = not bool(doc.get("mac_mismatch"))
    
    # "data" gate: 
    # - BM: pod_status (PoD - Proof of Data: upload succeeded)
    # - AEM: poi_data (PoI - Proof of Installation: Olostep running and enabled)
    if MINER_CODE == "AEM":
        data_ok = bool(poi_data) if poi_data is not None else False
    else:
        data_ok = bool(pod_status) if pod_status is not None else False

    bright_active, honeygain_active, mysterium_active = _tool_states_for_slot()
    tools_active: list[str] = []
    if MINER_CODE == "BM":
        if bright_active:
            tools_active.append("bright")
        if honeygain_active:
            tools_active.append("honeygain")
        if mysterium_active:
            tools_active.append("mysterium")
    elif MINER_CODE == "RDN":
        if presearch_active:
            tools_active.append("presearch")
        if diiisco_active:
            tools_active.append("diiisco")

    # Tools gate policy (BM / RDN):
    # - If BM or RDN and zero tools selected => fail tools gate
    tools_ok: Optional[bool]
    if MINER_CODE in ("BM", "RDN"):
        tools_ok = (len(tools_active) > 0)
    else:
        tools_ok = None

    multiplier: Optional[float] = None
    try:
        multiplier = _compute_rewards_multiplier(
            MINER_CODE,
            bool(mac_ok),
            bool(online_ok),      # PoC (Proof of Connectivity)
            bool(data_ok),        # BM: PoD, AEM: PoI
            bright_active,
            honeygain_active,
            mysterium_active,
            multiplier_base=multiplier_base,
            multiplier_per_tool=multiplier_per_tool,
            presearch_active=presearch_active,
            diiisco_active=diiisco_active,
        )
    except Exception:
        multiplier = None

    # Build slot object with appropriate gates per miner type
    slot_obj: Dict[str, Any] = {
        "gates": {
            "online": bool(online_ok),  # PoC (Proof of Connectivity)
            "mac": bool(mac_ok),
            "data": bool(data_ok),  # BM: PoD (upload), AEM: PoI (Olostep)
            "tools": (bool(tools_ok) if tools_ok is not None else None),  # BM only
        },
        "tools_active": tools_active,
        "number_of_tools": len(tools_active),
        "multiplier": float(multiplier) if isinstance(multiplier, (int, float)) else None,
    }

    # Write slot
    if 0 <= slot < slots_per_hour:
        slots_list[slot] = slot_obj

    # Store slots (optional but recommended for detailed view)
    hour_doc["slots"] = slots_list

    # Compute hour summary fields the GUI expects
    hour_doc.update(_hour_summary_from_slots(slots_list))
    hours[hour_key] = hour_doc

    # Store back day
    day_doc["hours"] = hours
    days[day_key] = day_doc
    doc["days"] = days

    # Refresh aggregates for THIS day + week so far
    _compute_day_aggregates(days[day_key])
    doc["week_so_far"] = _compute_week_so_far(doc)

    # Update timestamps
    # lastSlotWritten = floored boundary
    secs = ts_utc.hour * 3600 + ts_utc.minute * 60 + ts_utc.second
    floored = (secs // max(1, interval_seconds)) * max(1, interval_seconds)
    slot_floor = ts_utc.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(seconds=floored)

    doc["lastSlotWritten"] = _iso_z(slot_floor)
    _commit(doc)

def _start_poi_local_loop(miner_key: str, interval_seconds: int, poll_seconds: int) -> None:
    """Launch a background loop that refreshes PoI in the WEEKLY local cache more frequently (AEM only).

    Also writes to aem_live CSV every poll_seconds for GUI status display.
    """
    if poll_seconds <= 0:
        return

    def _loop() -> None:
        _POI_STATE_READY.wait(timeout=5)
        while True:
            # Get detailed Olostep status for live CSV
            olostep_details = None
            try:
                from poi_monitor_aem import get_olostep_status_detailed
                olostep_details = get_olostep_status_detailed()
                installed = olostep_details.get("poi", False)
            except Exception:
                installed = None
                try:
                    installed = monitor_poi_for_aem()
                except Exception:
                    pass

            try:
                snap = _get_poi_state_snapshot()

                # For weekly cache we only need enough to write the current slot
                status = snap.get("status", "offline")
                interval = int(snap.get("interval", interval_seconds))
                mac_registered = snap.get("mac_registered")
                mac_mismatch = snap.get("mac_mismatch")

                # Fetch current PoL status from hardware doc if available
                pol_status_val: Optional[bool] = None
                try:
                    from mongo_api_proxy import MongoProxyClient
                    from external_api import ExternalApiClient
                    api = ExternalApiClient()
                    client_temp = MongoProxyClient(api)
                    coll_temp = client_temp["PoC"]["hardware"]
                    hw_doc = coll_temp.find_one({"miner_key": miner_key})
                    if isinstance(hw_doc, dict):
                        pol_block = hw_doc.get("pol")
                        if isinstance(pol_block, dict):
                            pol_status_val = pol_block.get("status") if isinstance(pol_block.get("status"), bool) else None
                except Exception:
                    pol_status_val = None

                # AEM: "data" gate isn't relevant; pass pod_status=None
                write_week_local(
                    miner_key,
                    now_utc(),
                    status,
                    interval,
                    pod_status=None,
                    mac_registered=mac_registered,
                    mac_mismatch=mac_mismatch,
                    poi_data=installed,
                    pol_status=pol_status_val,
                    gui_version=GUI_VERSION,
                )

                _update_poi_state(last_poi=installed)

                # Write to aem_live CSV for GUI quick refresh
                if olostep_details:
                    try:
                        from measurements.csv_writer import append_row
                        # Check internet status directly for fresh value (main loop only updates every 10 min)
                        live_status = "online" if is_internet_up(timeout=2) else "offline"
                        append_row(
                            "aem_live",
                            MINER_CODE,
                            {
                                "timestamp": now_utc().isoformat(),
                                "olostep_running": olostep_details.get("olostep_running", False),
                                "olostep_enabled": olostep_details.get("olostep_enabled", False),
                                "status": live_status,
                            },
                            dataset="gui",  # Use simple filename: aem_live_YYYYMMDD.csv
                        )
                    except Exception:
                        pass
            except Exception:
                pass

            time.sleep(max(1, poll_seconds))

    threading.Thread(target=_loop, name="poi-week-monitor", daemon=True).start()

def _maxmind_db_path() -> pathlib.Path:
    env = os.getenv("MAXMIND_DB_PATH")
    if isinstance(env, str) and env.strip():
        candidate = pathlib.Path(env.strip()).expanduser()
        if candidate.exists():
            return candidate
        # Return the provided path even if missing so the caller reports it
        return candidate

    candidates: list[pathlib.Path] = []
    shared_candidate = _SHARED_GEOIP_PATH
    if isinstance(shared_candidate, pathlib.Path):
        try:
            if shared_candidate.exists():
                return shared_candidate
        except Exception:
            pass
        candidates.append(shared_candidate)

    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            try:
                candidates.append(pathlib.Path(getattr(sys, "_MEIPASS")) / "GeoLite2-Country.mmdb")
            except Exception:
                pass
        try:
            candidates.append(pathlib.Path(os.path.dirname(sys.executable)) / "GeoLite2-Country.mmdb")
        except Exception:
            pass

    candidates.append(pathlib.Path(app_dir()) / "GeoLite2-Country.mmdb")
    candidates.append(pathlib.Path.cwd() / "GeoLite2-Country.mmdb")

    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue

    # Fall back to the app directory even if the file is missing; caller will raise
    return pathlib.Path(app_dir()) / "GeoLite2-Country.mmdb"

def _download_once(url: str, dest: pathlib.Path) -> None:
    if dest.exists():
        return
    tmp = dest.with_suffix(".tmp")
    try:
        resp = requests.get(url, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        tmp.write_bytes(resp.content)
        data = json.loads(tmp.read_text(encoding="utf-8"))
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list) or not features:
            raise RuntimeError("GeoJSON payload missing features")
        tmp.replace(dest)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise

def _ensure_countries() -> List[Tuple[str, Any]]:
    global _COUNTRIES
    if _COUNTRIES is not None:
        return _COUNTRIES
    if not (HAVE_SHAPELY and HAVE_H3):
        _COUNTRIES = []
        return _COUNTRIES
    try:
        _download_once(NE_URL, NE_FILE)
        payload = json.loads(NE_FILE.read_text(encoding="utf-8"))
    except Exception:
        _COUNTRIES = []
        return _COUNTRIES
    out: List[Tuple[str, Any]] = []
    for feature in payload.get("features", []) if isinstance(payload, dict) else []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            props = {}
        raw_iso_candidates = [
            props.get("iso_a2"), props.get("ISO_A2"), props.get("iso_a2_eh"), props.get("ISO_A2_EH"),
            props.get("wb_a2"), props.get("WB_A2"), props.get("adm0_a3"), props.get("ADM0_A3"),
        ]
        iso = None
        for candidate in raw_iso_candidates:
            if isinstance(candidate, str) and candidate:
                candidate = candidate.strip().upper()
                if candidate and candidate not in {"-99", "ZZ", "--"}:
                    iso = candidate
                    break
        if not iso:
            continue
        if len(iso) == 3:
            iso = iso[:2]
        geometry = feature.get("geometry")
        try:
            geom = shape(geometry)  # type: ignore[arg-type]
            if not geom.is_valid:
                geom = geom.buffer(0)
        except Exception:
            continue
        out.append((iso.upper(), geom))
    _COUNTRIES = out
    return _COUNTRIES

def _get_geoip_reader():
    global _GEOIP_READER, _GEOIP_LOG_STATE
    if _GEOIP_READER is not None:
        return _GEOIP_READER
    if not HAVE_GEOIP2:
        if _GEOIP_LOG_STATE != "no-geoip2":
            log.info("geoip2 package not available; skipping GeoLite2 country lookups")
            _GEOIP_LOG_STATE = "no-geoip2"
        return None
    path = _maxmind_db_path()
    if not path.exists():
        state = f"missing:{path}"
        if _GEOIP_LOG_STATE != state:
            log.warning("GeoIP2 country database not found at %s; set MAXMIND_DB_PATH or bundle GeoLite2-Country.mmdb", path)
            _GEOIP_LOG_STATE = state
        return None
    try:
        _GEOIP_READER = GeoIP2Reader(str(path))  # type: ignore[call-arg]
        state = f"loaded:{path}"
        if _GEOIP_LOG_STATE != state:
            log.info("GeoIP2 country database loaded from %s", path)
            _GEOIP_LOG_STATE = state
    except Exception as exc:
        state = f"error:{path}"
        if _GEOIP_LOG_STATE != state:
            log.warning("Failed to open GeoIP2 database at %s: %s", path, exc)
            _GEOIP_LOG_STATE = state
        _GEOIP_READER = None
    return _GEOIP_READER

def _public_ip(timeout: float = 4.0) -> Optional[str]:
    try:
        resp = requests.get("https://api.ipify.org", params={"format": "json"}, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            ip_value = data.get("ip") if isinstance(data, dict) else None
            if isinstance(ip_value, str) and ip_value:
                return ip_value
    except Exception:
        pass
    return None

def _ip_to_country(ip: str) -> Tuple[Optional[str], Optional[str], bool]:
    reader = _get_geoip_reader()
    if reader is not None:
        try:
            resp = reader.country(ip)
            code = resp.country.iso_code or resp.registered_country.iso_code
            if isinstance(code, str) and code:
                return code.upper(), "maxmind", True
        except Exception:
            pass
    if IPINFO_TOKEN:
        try:
            resp = requests.get(f"https://ipinfo.io/{ip}", params={"token": IPINFO_TOKEN}, timeout=4.0)
            if resp.status_code == 200:
                payload = resp.json()
                code = payload.get("country") if isinstance(payload, dict) else None
                if isinstance(code, str) and code:
                    return code.upper(), "ipinfo", False
        except Exception:
            pass
    return None, None, False

def _h3_to_country(h3_index: Optional[str], area_threshold: float = COUNTRY_AREA_THRESHOLD) -> Tuple[Optional[str], Optional[float], bool]:
    if not (h3_index and HAVE_H3 and HAVE_SHAPELY and h3):
        return None, None, False
    h3_mod = cast(Any, h3)
    try:
        boundary = h3_mod.cell_to_boundary(h3_index)
    except Exception:
        return None, None, False
    if not boundary:
        return None, None, False
    if not callable(Polygon):
        return None, None, False
    polygon_factory = cast(Callable[[Iterable[Tuple[float, float]]], Any], Polygon)
    try:
        hex_poly = polygon_factory([(lon, lat) for (lat, lon) in boundary])
    except Exception:
        return None, None, False
    if hex_poly.is_empty:
        return None, None, False
    if not hex_poly.is_valid:
        hex_poly = hex_poly.buffer(0)
    hex_area = hex_poly.area or 1e-12
    countries = _ensure_countries()
    if not countries:
        return None, None, False
    hb = hex_poly.bounds
    best_iso: Optional[str] = None
    best_share = -1.0
    best_area = -1.0
    for iso, geom in countries:
        try:
            gb = geom.bounds
        except Exception:
            continue
        if (gb[2] < hb[0]) or (gb[0] > hb[2]) or (gb[3] < hb[1]) or (gb[1] > hb[3]):
            continue
        try:
            inter = geom.intersection(hex_poly)
        except Exception:
            continue
        if inter.is_empty:
            continue
        area_val = inter.area
        share = area_val / hex_area if hex_area else 0.0
        if (share > best_share) or (share == best_share and area_val > best_area):
            best_iso = iso
            best_share = share
            best_area = area_val
    if best_iso and best_share >= area_threshold:
        return best_iso, best_share, True
    if best_iso:
        return None, best_share, True
    return None, None, True

def check_country_once(h3_index: Optional[str], *, area_threshold: float = COUNTRY_AREA_THRESHOLD, ip: Optional[str] = None) -> Dict[str, Any]:
    ip_value = ip if isinstance(ip, str) and ip else None
    if not ip_value:
        ip_value = _public_ip()
    ip_country = None
    ip_source = None
    geoip_used = False
    if ip_value:
        ip_country, ip_source, geoip_used = _ip_to_country(ip_value)
    h3_country, share, dataset_ready = _h3_to_country(h3_index, area_threshold=area_threshold)
    if ip_country and h3_country:
        same_country: Optional[bool] = (ip_country == h3_country)
    elif ip_country is None and h3_country is None:
        same_country = None
    else:
        same_country = None
    return {
        "ip": ip_value,
        "ip_country": ip_country,
        "ip_country_source": ip_source,
        "h3_country": h3_country,
        "h3_share": share,
        "same_country": same_country,
        "geoip_used": geoip_used,
        "country_dataset_ready": dataset_ready,
    }

def registered_hexid_from_devices(client: MongoProxyClient, miner_key: str) -> Optional[str]:
    """Return hexId (res7) from main.devices for this miner_key."""
    try:
        coll = client["main"]["devices"]
        if hasattr(coll, "find_one"):
            doc = coll.find_one({"miner_key": miner_key}, {"hexId": 1, "_id": 0})  # type: ignore[attr-defined]
        else:
            doc = None
        if not isinstance(doc, dict) and hasattr(client, "_api"):
            try:
                doc = client._api.get_miner_profile(miner_key)
            except Exception:
                doc = None
        if isinstance(doc, dict):
            v = doc.get("hexId") or (doc.get("position", {}) if isinstance(doc.get("position"), dict) else {}).get("hexId")
            if isinstance(v, str) and v:
                _reset_critical_failure(f"hexid-missing-{miner_key}")
                return v
        msg = f"No hexId returned for {miner_key} from /credentials"
        _record_critical_failure(f"hexid-missing-{miner_key}", msg, miner_key=miner_key)
    except Exception as e:
        _record_critical_failure(f"hexid-error-{miner_key}", f"Failed to fetch hexId for {miner_key}: {e}", miner_key=miner_key)
    return None

def write_location_daily(
    coll,
    client: MongoProxyClient,
    miner_key: str,
    ts: dt.datetime,
) -> Optional[Dict[str, Any]]:
    """
    Evaluate Proof of Location once per day and store:
    - pol.status
    - pol.last_changed_at
    - pol.last_checked_at
    - pol.evidence (latest)

    IMPORTANT:
    - Must NOT update lastUpdated (slot activity only)
    - Must NOT create a partial doc on upsert
    """

    area_threshold = COUNTRY_AREA_THRESHOLD
    hex7 = registered_hexid_from_devices(client, miner_key)

    try:
        country_check = check_country_once(hex7, area_threshold=area_threshold)
    except Exception:
        country_check = {}

    country_check = country_check if isinstance(country_check, dict) else {}
    checked_at_iso = now_utc().isoformat()

    ip_value = country_check.get("ip") if isinstance(country_check.get("ip"), str) else None
    ip_country = country_check.get("ip_country") if isinstance(country_check.get("ip_country"), str) else None
    hex_country = country_check.get("h3_country") if isinstance(country_check.get("h3_country"), str) else None

    same_country_val = country_check.get("same_country")
    country_match = same_country_val if isinstance(same_country_val, bool) else None
    pol_status_new = bool(country_match) if isinstance(country_match, bool) else False

    existing_doc_raw = _get_existing_hardware_doc(coll, miner_key)
    existing_doc: Dict[str, Any] = existing_doc_raw if isinstance(existing_doc_raw, dict) else {}

    existing_pol_raw = existing_doc.get("pol")
    existing_pol: Dict[str, Any] = existing_pol_raw if isinstance(existing_pol_raw, dict) else {}

    pol_status_old = existing_pol.get("status") if isinstance(existing_pol.get("status"), bool) else None
    last_changed_at = existing_pol.get("last_changed_at") if isinstance(existing_pol.get("last_changed_at"), str) else None

    if pol_status_old is None or pol_status_old != pol_status_new:
        last_changed_at = checked_at_iso
    elif not last_changed_at:
        last_changed_at = checked_at_iso

    pol_block: Dict[str, Any] = {
        "status": pol_status_new,
        "last_changed_at": last_changed_at,
        "last_checked_at": checked_at_iso,
        "evidence": {
            "ip": ip_value,
            "hexID_registered": hex7 if isinstance(hex7, str) and hex7 else None,
            "ipCountry": ip_country,
            "hexCountry": hex_country,
            "country_match": country_match,
        },
    }

    # On insert only, initialize a consistent doc skeleton (but do not “refresh” lastUpdated here).
    miner_type_val = existing_doc.get("miner_type") if isinstance(existing_doc.get("miner_type"), str) else MINER_CODE
    software_info = _extract_software_fields(existing_doc)

    base_on_insert = _compose_hardware_doc(
        miner_key,
        miner_type=miner_type_val,
        software=software_info,
        last_updated=now_utc(),   # only used if doc does not exist yet
        poi=None,
        poi_slots=None,
    )

    doc_to_write: Dict[str, Any]
    if existing_doc:
        doc_to_write = existing_doc
    else:
        doc_to_write = base_on_insert

    if not isinstance(doc_to_write, dict):
        doc_to_write = {}

    if not doc_to_write:
        doc_to_write = base_on_insert

    if not isinstance(doc_to_write, dict):
        doc_to_write = {"miner_key": miner_key}

    if isinstance(doc_to_write, dict):
        doc_to_write.pop("_id", None)
        doc_to_write.setdefault("miner_key", miner_key)
        if "lastUpdated" not in doc_to_write and isinstance(base_on_insert.get("lastUpdated"), str):
            doc_to_write["lastUpdated"] = base_on_insert["lastUpdated"]
        doc_to_write["pol"] = pol_block

    try:
        coll.replace_one(
            {"miner_key": miner_key},
            doc_to_write,
            upsert=True,
        )
        return pol_block
    except Exception:
        pass
    return None

def registered_mac_from_devices(client: MongoProxyClient, miner_key: str) -> Optional[str]:
    """Return the registered MAC for a miner_key from creds.hardware.miner_mac.
    Returns None if not present or on error."""
    try:
        coll = client["creds"]["hardware"]
        if hasattr(coll, "find_one"):
            dev = coll.find_one({"miner_key": miner_key}, {"miner_mac": 1, "_id": 0})  # type: ignore[attr-defined]
        else:
            dev = None
        if not isinstance(dev, dict) and hasattr(client, "_api"):
            try:
                dev = client._api.get_miner_profile(miner_key)
            except Exception:
                dev = None
        if isinstance(dev, dict):
            v = dev.get("miner_mac") or (dev.get("credentials", {}) if isinstance(dev.get("credentials"), dict) else {}).get("mac_address")
            if isinstance(v, str):
                v = v.strip()
                if v:
                    _reset_critical_failure(f"mac-missing-{miner_key}")
                    return v
        msg = f"No registered MAC returned for {miner_key} from /credentials"
        _record_critical_failure(f"mac-missing-{miner_key}", msg, miner_key=miner_key)
    except Exception as e:
        _record_critical_failure(f"mac-error-{miner_key}", f"Failed to fetch registered MAC for {miner_key}: {e}", miner_key=miner_key)
    return None

def group_lock_path() -> str:
    base = data_dir()
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "service.lock")

# Simple interlock: service holds an exclusive lock on a small file while running.
# On exit, the lock is released and the file is removed.
_LOCK_FH = None  # type: ignore[var-annotated]

def acquire_service_lock() -> None:
    global _LOCK_FH
    path = group_lock_path()
    try:
        # Open or create the lock file and try to acquire a 1-byte non-blocking lock
        fh = open(path, "w+b")
        try:
            import msvcrt  # type: ignore
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except Exception:
            # If we cannot lock, leave without exiting (relaxed semantics)
            try:
                fh.close()
            except Exception:
                pass
            return
        # Write our PID for visibility and keep handle open to hold the lock
        try:
            fh.seek(0)
            fh.write(str(os.getpid()).encode("ascii", "ignore"))
            fh.truncate()
            fh.flush()
        except Exception:
            pass
        _LOCK_FH = fh
    except Exception as e:
        try:
            log.warning("Service lock acquire warning: %s", e)
        except Exception:
            pass

def _release_service_lock() -> None:
    global _LOCK_FH
    if not _LOCK_FH:
        return
    try:
        try:
            import msvcrt  # type: ignore
            _LOCK_FH.seek(0)
            msvcrt.locking(_LOCK_FH.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        try:
            _LOCK_FH.close()
        except Exception:
            pass
        try:
            os.remove(group_lock_path())
        except Exception:
            pass
    finally:
        _LOCK_FH = None

# --- IPC Queue Processing (Privileged Operations Daemon) ---

def _sanitize_relative_path(relative_path: str) -> Optional[str]:
    """Validate and sanitize a relative path to prevent directory traversal.
    
    Returns normalized path if safe, None otherwise.
    """
    if not relative_path or not isinstance(relative_path, str):
        return None
    
    # Normalize and check for directory traversal attempts
    try:
        normalized = os.path.normpath(relative_path)
        # Block absolute paths and parent directory references
        if os.path.isabs(normalized) or normalized.startswith("..") or ".." in normalized.split(os.sep):
            return None
        return normalized
    except Exception:
        return None

def _write_config_file(relative_path: str, content: str) -> bool:
    """Write a config file to ProgramData config directory.
    
    Args:
        relative_path: Path relative to config/ directory (e.g., "bright.json")
        content: JSON string content to write
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Validate path
        safe_path = _sanitize_relative_path(relative_path)
        if not safe_path:
            log.error("Invalid config file path: %s", relative_path)
            return False
        
        # Build full path under config/
        target_path = os.path.join(data_dir(), "config", safe_path)
        
        # Ensure parent directory exists
        parent_dir = os.path.dirname(target_path)
        os.makedirs(parent_dir, exist_ok=True)
        
        # Atomic write: temp file + rename
        temp_path = f"{target_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(temp_path, target_path)
            
            log_step("privileged_config_file_written", {
                "path": safe_path,
                "size": len(content)
            })
            return True
        except Exception as e:
            log.error("Failed to write config file %s: %s", safe_path, e)
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            log_step("privileged_config_file_write_failed", {
                "path": safe_path,
                "error": str(e)
            })
            return False
    except Exception as e:
        log.exception("Config file write error: %s", e)
        return False

def _write_measurement_file(tool: str, encrypted_bytes: bytes) -> bool:
    """Write an encrypted measurement file to ProgramData measurements directory."""
    try:
        tool_safe = "".join(c for c in tool if c.isalnum() or c in "_ -")
        if not tool_safe:
            log.error("Invalid measurement tool: %s", tool)
            return False
        
        filename = f"measurements-{tool_safe}-latest.json.enc"
        target_path = os.path.join(data_dir(), "measurements", filename)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        
        # Atomic write: temp file + rename
        temp_path = f"{target_path}.tmp"
        try:
            with open(temp_path, "wb") as f:
                f.write(encrypted_bytes)
            os.replace(temp_path, target_path)
            
            log_step("privileged_measurement_file_written", {
                "tool": tool_safe,
                "size": len(encrypted_bytes)
            })
            return True
        except Exception as e:
            log.error("Failed to write measurement file %s: %s", filename, e)
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            log_step("privileged_measurement_file_write_failed", {
                "tool": tool_safe,
                "error": str(e)
            })
            return False
    except Exception as e:
        log.exception("Measurement file write error: %s", e)
        return False

def _detect_linux_firewall() -> Optional[str]:
    """Detect which firewall system is active on Linux.
    
    Returns:
        'ufw', 'firewalld', 'iptables', or None if none detected
    """
    try:
        # Check for ufw
        result = subprocess.run(["which", "ufw"], capture_output=True, timeout=2)
        if result.returncode == 0:
            # Verify ufw is active
            result = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=2)
            if "Status: active" in result.stdout or "Status: inactive" in result.stdout:
                return "ufw"
        
        # Check for firewalld
        result = subprocess.run(["which", "firewall-cmd"], capture_output=True, timeout=2)
        if result.returncode == 0:
            # Verify firewalld is running
            result = subprocess.run(["firewall-cmd", "--state"], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                return "firewalld"
        
        # Check for iptables (fallback)
        result = subprocess.run(["which", "iptables"], capture_output=True, timeout=2)
        if result.returncode == 0:
            return "iptables"
        
        return None
    except Exception:
        return None

def _add_firewall_rule_linux_ufw(port: int, protocol: str, direction: str) -> bool:
    """Add firewall rule using ufw."""
    try:
        proto = protocol.lower()
        cmd = ["ufw", "allow", f"{port}/{proto}"]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False

def _add_firewall_rule_linux_firewalld(port: int, protocol: str, direction: str) -> bool:
    """Add firewall rule using firewalld."""
    try:
        proto = protocol.lower()
        cmd = ["firewall-cmd", f"--add-port={port}/{proto}", "--permanent"]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            # Reload to apply changes
            subprocess.run(["firewall-cmd", "--reload"], capture_output=True, timeout=10)
            return True
        return False
    except Exception:
        return False

def _add_firewall_rule_linux_iptables(port: int, protocol: str, direction: str) -> bool:
    """Add firewall rule using iptables."""
    try:
        proto = protocol.upper()
        chain = "INPUT" if direction == "in" else "OUTPUT"
        
        # Check if rule already exists
        check_cmd = [
            "iptables", "-C", chain,
            "-p", proto, "--dport", str(port),
            "-j", "ACCEPT"
        ]
        result = subprocess.run(check_cmd, capture_output=True, timeout=10)
        if result.returncode == 0:
            # Rule already exists
            return True
        
        # Add rule
        cmd = [
            "iptables", "-A", chain,
            "-p", proto, "--dport", str(port),
            "-j", "ACCEPT"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            # Try to save (varies by distro)
            for save_cmd in [["iptables-save"], ["service", "iptables", "save"], ["netfilter-persistent", "save"]]:
                try:
                    subprocess.run(save_cmd, capture_output=True, timeout=5)
                    break
                except Exception:
                    continue
            return True
        return False
    except Exception:
        return False

def _remove_firewall_rule_linux_ufw(port: int, protocol: str) -> bool:
    """Remove firewall rule using ufw."""
    try:
        proto = protocol.lower()
        cmd = ["ufw", "delete", "allow", f"{port}/{proto}"]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False

def _remove_firewall_rule_linux_firewalld(port: int, protocol: str) -> bool:
    """Remove firewall rule using firewalld."""
    try:
        proto = protocol.lower()
        cmd = ["firewall-cmd", f"--remove-port={port}/{proto}", "--permanent"]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            # Reload to apply changes
            subprocess.run(["firewall-cmd", "--reload"], capture_output=True, timeout=10)
            return True
        return False
    except Exception:
        return False

def _remove_firewall_rule_linux_iptables(port: int, protocol: str, direction: str) -> bool:
    """Remove firewall rule using iptables."""
    try:
        proto = protocol.upper()
        chain = "INPUT" if direction == "in" else "OUTPUT"
        
        cmd = [
            "iptables", "-D", chain,
            "-p", proto, "--dport", str(port),
            "-j", "ACCEPT"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            # Try to save
            for save_cmd in [["iptables-save"], ["service", "iptables", "save"], ["netfilter-persistent", "save"]]:
                try:
                    subprocess.run(save_cmd, capture_output=True, timeout=5)
                    break
                except Exception:
                    continue
            return True
        return False
    except Exception:
        return False

def _add_firewall_rule(rule_name: str, port: int, protocol: str = "TCP", direction: str = "in", 
                       program: Optional[str] = None) -> bool:
    """Add a firewall rule (Windows or Linux).
    
    Args:
        rule_name: Name for the firewall rule (Windows only, used for logging on Linux)
        port: Port number (or 0 if program-based rule)
        protocol: TCP or UDP
        direction: in (inbound) or out (outbound)
        program: Optional program path for program-based rules (Windows only)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Validate inputs
        protocol = protocol.upper()
        if protocol not in ("TCP", "UDP"):
            log.error("Invalid protocol: %s (must be TCP or UDP)", protocol)
            return False
        
        direction = direction.lower()
        if direction not in ("in", "out"):
            log.error("Invalid direction: %s (must be in or out)", direction)
            return False
        
        # Windows implementation
        if sys.platform.startswith("win"):
            # Build netsh command
            cmd = [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={rule_name}",
                f"dir={direction}",
                "action=allow"
            ]
            
            if program:
                # Program-based rule
                if not os.path.isabs(program):
                    log.error("Program path must be absolute: %s", program)
                    return False
                cmd.append(f"program={program}")
            elif port > 0:
                # Port-based rule
                cmd.append(f"protocol={protocol}")
                cmd.append(f"localport={port}")
            else:
                log.error("Either port or program must be specified")
                return False
            
            # Execute command
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                log_step("privileged_firewall_rule_added", {
                    "rule_name": rule_name,
                    "port": port,
                    "protocol": protocol,
                    "direction": direction,
                    "program": program,
                    "platform": "windows"
                })
                return True
            else:
                log.error("Failed to add firewall rule %s: %s", rule_name, result.stderr)
                log_step("privileged_firewall_rule_add_failed", {
                    "rule_name": rule_name,
                    "error": result.stderr.strip(),
                    "platform": "windows"
                })
                return False
        
        # Linux implementation
        else:
            if program:
                log.warning("Program-based firewall rules not supported on Linux")
                return False
            
            if port <= 0:
                log.error("Port must be specified for Linux firewall rules")
                return False
            
            # Detect firewall system
            firewall = _detect_linux_firewall()
            if not firewall:
                log.error("No supported firewall system detected on Linux (ufw/firewalld/iptables)")
                return False
            
            # Add rule based on detected system
            success = False
            if firewall == "ufw":
                success = _add_firewall_rule_linux_ufw(port, protocol, direction)
            elif firewall == "firewalld":
                success = _add_firewall_rule_linux_firewalld(port, protocol, direction)
            elif firewall == "iptables":
                success = _add_firewall_rule_linux_iptables(port, protocol, direction)
            
            if success:
                log_step("privileged_firewall_rule_added", {
                    "rule_name": rule_name,
                    "port": port,
                    "protocol": protocol,
                    "direction": direction,
                    "platform": "linux",
                    "firewall": firewall
                })
            else:
                log_step("privileged_firewall_rule_add_failed", {
                    "rule_name": rule_name,
                    "error": f"Failed using {firewall}",
                    "platform": "linux"
                })
            
            return success
            
    except Exception as e:
        log.exception("Firewall rule add error: %s", e)
        log_step("privileged_firewall_rule_add_failed", {
            "rule_name": rule_name,
            "error": str(e)
        })
        return False

def _remove_firewall_rule(rule_name: str, port: int = 0, protocol: str = "TCP", direction: str = "in") -> bool:
    """Remove a firewall rule (Windows or Linux).
    
    Args:
        rule_name: Name of the firewall rule to remove (Windows) or identifier for logging (Linux)
        port: Port number (required for Linux)
        protocol: TCP or UDP (required for Linux)
        direction: in or out (required for Linux iptables)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Windows implementation
        if sys.platform.startswith("win"):
            cmd = [
                "netsh", "advfirewall", "firewall", "delete", "rule",
                f"name={rule_name}"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                log_step("privileged_firewall_rule_removed", {
                    "rule_name": rule_name,
                    "platform": "windows"
                })
                return True
            else:
                log.error("Failed to remove firewall rule %s: %s", rule_name, result.stderr)
                log_step("privileged_firewall_rule_remove_failed", {
                    "rule_name": rule_name,
                    "error": result.stderr.strip(),
                    "platform": "windows"
                })
                return False
        
        # Linux implementation
        else:
            if port <= 0:
                log.error("Port must be specified for Linux firewall rule removal")
                return False
            
            # Detect firewall system
            firewall = _detect_linux_firewall()
            if not firewall:
                log.error("No supported firewall system detected on Linux")
                return False
            
            # Remove rule based on detected system
            success = False
            if firewall == "ufw":
                success = _remove_firewall_rule_linux_ufw(port, protocol)
            elif firewall == "firewalld":
                success = _remove_firewall_rule_linux_firewalld(port, protocol)
            elif firewall == "iptables":
                success = _remove_firewall_rule_linux_iptables(port, protocol, direction)
            
            if success:
                log_step("privileged_firewall_rule_removed", {
                    "rule_name": rule_name,
                    "port": port,
                    "protocol": protocol,
                    "platform": "linux",
                    "firewall": firewall
                })
            else:
                log_step("privileged_firewall_rule_remove_failed", {
                    "rule_name": rule_name,
                    "error": f"Failed using {firewall}",
                    "platform": "linux"
                })
            
            return success
            
    except Exception as e:
        log.exception("Firewall rule remove error: %s", e)
        log_step("privileged_firewall_rule_remove_failed", {
            "rule_name": rule_name,
            "error": str(e)
        })
        return False

def _setup_mysterium_firewall() -> bool:
    """Setup firewall rules for Mysterium node.
    
    Returns:
        True if all rules added successfully, False otherwise
    """
    success = True
    
    # Tequilapi port (API)
    if not _add_firewall_rule("Mysterium_Tequilapi_TCP", MYST_TEQUILAPI_PORT, "TCP", "in"):
        success = False
    
    # WireGuard port (VPN)
    if not _add_firewall_rule("Mysterium_WireGuard_UDP", MYST_WIREGUARD_PORT, "UDP", "in"):
        success = False
    
    log_step("privileged_mysterium_firewall_setup", {"success": success})
    return success

def _setup_presearch_firewall() -> bool:
    """Setup firewall rules for Presearch node.
    
    Returns:
        True if rule added successfully, False otherwise
    """
    success = _add_firewall_rule("Presearch_API_TCP", PRESEARCH_API_PORT, "TCP", "in")
    log_step("privileged_presearch_firewall_setup", {"success": success})
    return success

def _setup_diiisco_firewall() -> bool:
    """Setup firewall rules for Diiisco node.
    
    Returns:
        True if rule added successfully, False otherwise
    """
    success = _add_firewall_rule("Diiisco_API_TCP", DIIISCO_API_PORT, "TCP", "in")
    log_step("privileged_diiisco_firewall_setup", {"success": success})
    return success

def _setup_spaceacres_firewall() -> bool:
    """Setup firewall rules for Space Acres (Substrate RPC).

    Returns:
        True if rule added successfully, False otherwise
    """
    success = _add_firewall_rule("SpaceAcres_RPC_TCP", DEFAULT_RPC_PORT, "TCP", "in")
    log_step("privileged_spaceacres_firewall_setup", {"success": success})
    return success


def _start_windows_service(service_name: str) -> Tuple[bool, str]:
    """Start a Windows service via nssm or sc.

    Args:
        service_name: Name of the Windows service (must be in ALLOWED_SERVICE_NAMES)

    Returns:
        Tuple of (success, message)
    """
    if not service_name:
        return False, "service_name required"

    if service_name not in ALLOWED_SERVICE_NAMES:
        return False, f"service {service_name} not in allowed list"

    # Try NSSM first (if available), then fall back to sc
    try:
        nssm_path = os.path.join(data_dir(), "nssm.exe")
        if os.path.exists(nssm_path):
            result = subprocess.run(
                [nssm_path, "start", service_name],
                capture_output=True,
                timeout=30,
                encoding="utf-8",
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            if result.returncode == 0:
                log_step("privileged_service_started", {"service": service_name, "method": "nssm"})
                return True, f"Service {service_name} started via nssm"
            # Log but continue to try sc
            log_step("privileged_service_start_nssm_failed", {
                "service": service_name,
                "returncode": result.returncode,
                "stderr": (result.stderr or "")[:200],
            })
    except Exception as exc:
        log_step("privileged_service_start_nssm_exception", {"service": service_name, "error": str(exc)})

    # Fallback to sc
    try:
        result = subprocess.run(
            ["sc", "start", service_name],
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        if result.returncode == 0:
            log_step("privileged_service_started", {"service": service_name, "method": "sc"})
            return True, f"Service {service_name} started via sc"
        log_step("privileged_service_start_failed", {
            "service": service_name,
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[:200],
            "stderr": (result.stderr or "")[:200],
        })
        return False, f"Failed to start service: {(result.stderr or result.stdout or '')[:200]}"
    except Exception as exc:
        log_step("privileged_service_start_exception", {"service": service_name, "error": str(exc)})
        return False, f"Exception starting service: {exc}"


def _stop_windows_service(service_name: str) -> Tuple[bool, str]:
    """Stop a Windows service via nssm or sc.

    Args:
        service_name: Name of the Windows service (must be in ALLOWED_SERVICE_NAMES)

    Returns:
        Tuple of (success, message)
    """
    if not service_name:
        return False, "service_name required"

    if service_name not in ALLOWED_SERVICE_NAMES:
        return False, f"service {service_name} not in allowed list"

    # Try NSSM first, then fall back to sc
    try:
        nssm_path = os.path.join(data_dir(), "nssm.exe")
        if os.path.exists(nssm_path):
            result = subprocess.run(
                [nssm_path, "stop", service_name],
                capture_output=True,
                timeout=30,
                encoding="utf-8",
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            if result.returncode == 0:
                log_step("privileged_service_stopped", {"service": service_name, "method": "nssm"})
                return True, f"Service {service_name} stopped via nssm"
            # Log but continue to try sc
            log_step("privileged_service_stop_nssm_failed", {
                "service": service_name,
                "returncode": result.returncode,
                "stderr": (result.stderr or "")[:200],
            })
    except Exception as exc:
        log_step("privileged_service_stop_nssm_exception", {"service": service_name, "error": str(exc)})

    # Fallback to sc
    try:
        result = subprocess.run(
            ["sc", "stop", service_name],
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        if result.returncode == 0:
            log_step("privileged_service_stopped", {"service": service_name, "method": "sc"})
            return True, f"Service {service_name} stopped via sc"
        log_step("privileged_service_stop_failed", {
            "service": service_name,
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[:200],
            "stderr": (result.stderr or "")[:200],
        })
        return False, f"Failed to stop service: {(result.stderr or result.stdout or '')[:200]}"
    except Exception as exc:
        log_step("privileged_service_stop_exception", {"service": service_name, "error": str(exc)})
        return False, f"Exception stopping service: {exc}"


# ---------------------------------------------------------------------------
# Docker container lifecycle (for Presearch, Diiisco, etc.)
# ---------------------------------------------------------------------------

def _start_docker_container(container_name: str) -> Tuple[bool, str]:
    """Start a Docker container.  If it exists but is stopped, start it.
    If it doesn't exist, create it with ``docker run`` using
    ``DOCKER_CONTAINER_DEFS`` for image, volumes, and credentials.

    Args:
        container_name: Container name (must be in DOCKER_CONTAINER_DEFS)

    Returns:
        Tuple of (success, message)
    """
    cdef = DOCKER_CONTAINER_DEFS.get(container_name)
    if cdef is None:
        return False, f"container {container_name} not in allowed list"

    if not _is_docker_available():
        return False, "Docker is not installed or daemon not running"

    _cflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

    # Check if container already exists
    try:
        inspect = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container_name],
            capture_output=True, timeout=10, encoding="utf-8", errors="ignore",
            creationflags=_cflags,
        )
        if inspect.returncode == 0:
            state = inspect.stdout.strip()
            if state == "running":
                return True, f"Container {container_name} is already running"
            # Container exists but stopped -- start it
            result = subprocess.run(
                ["docker", "start", container_name],
                capture_output=True, timeout=30, encoding="utf-8", errors="ignore",
                creationflags=_cflags,
            )
            if result.returncode == 0:
                log_step("docker_container_started", {"container": container_name, "method": "start"})
                return True, f"Container {container_name} started"
            return False, f"docker start failed: {(result.stderr or result.stdout or '')[:200]}"
    except Exception:
        pass

    # Container doesn't exist -- docker run (first time)
    image = cdef.get("image", "")
    if not image:
        return False, f"No image defined for container {container_name}"

    # Build env vars from embedded credentials
    run_env: Dict[str, str] = {}
    cred_key = cdef.get("cred_key", "")
    cred_env = cdef.get("cred_env", "")
    if cred_key and cred_env:
        try:
            creds = _get_tool_credentials()
            val = creds.get(cred_key, "")
            if val:
                run_env[cred_env] = val
        except Exception:
            pass

    cmd: List[str] = [
        "docker", "run", "-d",
        "--name", container_name,
        "--restart", "unless-stopped",
    ]
    for vol in cdef.get("volumes", []):
        cmd.extend(["-v", vol])
    for k, v in run_env.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.append(image)

    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=120, encoding="utf-8", errors="ignore",
            creationflags=_cflags,
        )
        if result.returncode == 0:
            log_step("docker_container_created", {"container": container_name, "image": image})
            
            return True, f"Container {container_name} created and started"
        return False, f"docker run failed: {(result.stderr or result.stdout or '')[:200]}"
    except Exception as exc:
        log_step("docker_container_create_exception", {"container": container_name, "error": str(exc)})
        return False, f"Exception creating container: {exc}"


def _stop_docker_container(container_name: str) -> Tuple[bool, str]:
    """Stop a Docker container.

    Args:
        container_name: Container name (must be in ALLOWED_DOCKER_CONTAINERS)

    Returns:
        Tuple of (success, message)
    """
    if container_name not in ALLOWED_DOCKER_CONTAINERS:
        return False, f"container {container_name} not in allowed list"

    if not _is_docker_available():
        return False, "Docker is not installed or daemon not running"

    _cflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

    try:
        result = subprocess.run(
            ["docker", "stop", container_name],
            capture_output=True, timeout=30, encoding="utf-8", errors="ignore",
            creationflags=_cflags,
        )
        if result.returncode == 0:
            log_step("docker_container_stopped", {"container": container_name})
            return True, f"Container {container_name} stopped"
        return False, f"docker stop failed: {(result.stderr or result.stdout or '')[:200]}"
    except Exception as exc:
        log_step("docker_container_stop_exception", {"container": container_name, "error": str(exc)})
        return False, f"Exception stopping container: {exc}"


# IPC helper: write result marker for processed ops
def _write_ops_result(request_id: str, op: Optional[str], success: bool, error_msg: Optional[str] = None) -> None:
    try:
        result = {
            "id": request_id,
            "op": op,
            "success": success,
            "processed_at": dt.datetime.now(UTC).isoformat(),
        }
        if error_msg:
            result["error"] = error_msg
        result_path = os.path.join(data_dir(), "ops_processed", f"{request_id}.done.json")
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f)
        
        # Debug: log that result was written
        log_step("ops_result_written", {
            "request_id": request_id,
            "op": op,
            "success": success,
            "result_path": result_path
        })
        
        # Debug: write marker file to confirm execution
        try:
            marker_path = os.path.join(data_dir(), "ops_processed", f"{request_id}.result_written")
            with open(marker_path, "w", encoding="utf-8") as m:
                m.write(f"done.json written at {result_path}")
        except Exception:
            pass
    except Exception as e:
        # Debug: log write failure
        try:
            log_step("ops_result_write_failed", {
                "request_id": request_id,
                "op": op,
                "error": str(e)
            })
        except Exception:
            pass
        
        # Debug: write error marker
        try:
            error_marker_path = os.path.join(data_dir(), "ops_processed", f"{request_id}.result_error")
            with open(error_marker_path, "w", encoding="utf-8") as m:
                m.write(str(e))
        except Exception:
            pass

def _process_ops_queue_request(request_path: str) -> None:
    """Process a single IPC queue request file.
    
    Args:
        request_path: Full path to the request JSON file
    """
    request_id = None
    op: Optional[str] = None
    
    # DEBUG: Write marker file to confirm function is called
    debug_marker = os.path.join(data_dir(), "ops_processed", "DEBUG_handler_called.txt")
    try:
        with open(debug_marker, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now(UTC).isoformat()} handler called with path={request_path}\n")
    except Exception:
        pass
    
    try:
        # Read request (try UTF-8 first, fall back to utf-8-sig if BOM present)
        try:
            with open(request_path, "r", encoding="utf-8") as f:
                request = json.load(f)
        except json.JSONDecodeError as e:
            if "BOM" in str(e):
                # Retry with UTF-8-sig to handle BOM
                with open(request_path, "r", encoding="utf-8-sig") as f:
                    request = json.load(f)
            else:
                raise
        
        # Validate required fields
        if not isinstance(request, dict):
            raise ValueError("Request must be a JSON object")
        
        request_id = request.get("id")
        op = request.get("op")
        
        if not request_id or not op:
            raise ValueError("Missing required fields: id, op")
        
        # DEBUG: Log that we received a valid request
        log_step("ops_daemon_request_received", {
            "request_id": request_id,
            "op": op,
            "request_path": request_path
        })
        
        # Process operation
        success = False
        error_msg = None
        
        if op == "write_config":
            relative_path = request.get("relative_path")
            content = request.get("content")
            
            if not relative_path or not isinstance(content, str):
                raise ValueError("write_config requires relative_path and content")
            
            success = _write_config_file(relative_path, content)
            if success:
                sdk_name = _CONFIG_FILE_TO_SDK.get(os.path.basename(relative_path))
                if sdk_name:
                    # Parse the config file content to determine approval status
                    approval_status = True  # Default to true if parsing fails
                    try:
                        config_data = json.loads(content)
                        if isinstance(config_data, dict):
                            # Check enabled flag (primary indicator)
                            if "enabled" in config_data:
                                approval_status = bool(config_data.get("enabled", False))
                            # Also check consent flag if present (both should be true for approval)
                            if "consent" in config_data and not config_data.get("consent", False):
                                approval_status = False
                    except Exception:
                        # If parsing fails, assume enabled (legacy behavior)
                        pass
                    _update_sdk_approval(sdk_name, approval_status)

        elif op == "write_measurement":
            tool = request.get("tool")
            data_b64 = request.get("data_b64")
            
            if not tool or not data_b64:
                raise ValueError("write_measurement requires tool and data_b64")
            
            import base64
            try:
                encrypted_bytes = base64.b64decode(data_b64)
            except Exception as e:
                raise ValueError(f"Invalid base64 data: {e}")
            
            success = _write_measurement_file(tool, encrypted_bytes)
            
        elif op == "add_firewall_rule":
            rule_name = request.get("rule_name")
            port = request.get("port", 0)
            protocol = request.get("protocol", "TCP").upper()
            direction = request.get("direction", "in").lower()
            program = request.get("program")

            # Handle bidirectional rules ("in,out") by creating two separate rules
            if direction == "in,out" or direction == "both":
                directions = ["in", "out"]
            else:
                directions = [direction]

            success = True
            for dir_single in directions:
                # Auto-generate rule name if not provided
                if request.get("rule_name"):
                    current_rule_name = f"{request.get('rule_name')}_{dir_single}"
                elif port:
                    current_rule_name = f"FRY_Port{port}_{protocol}_{dir_single}"
                else:
                    raise ValueError("add_firewall_rule requires rule_name or port")

                if not _add_firewall_rule(current_rule_name, port, protocol, dir_single, program):
                    success = False
            
        elif op == "remove_firewall_rule":
            rule_name = request.get("rule_name")
            
            if not rule_name:
                raise ValueError("remove_firewall_rule requires rule_name")
            
            success = _remove_firewall_rule(rule_name)
            
        elif op == "setup_mysterium_firewall":
            success = _setup_mysterium_firewall()
            
        elif op == "setup_presearch_firewall":
            success = _setup_presearch_firewall()
            
        elif op == "setup_diiisco_firewall":
            success = _setup_diiisco_firewall()
            
        elif op == "setup_spaceacres_firewall":
            success = _setup_spaceacres_firewall()
            
        elif op == "reload_config":
            # Reload service configuration from config files
            try:
                _reload_service_config()
                success = True
            except Exception as e:
                error_msg = str(e)
                success = False

        elif op == "start_service":
            service_name = request.get("service_name", "")

            if not service_name:
                raise ValueError("start_service requires service_name")

            success, msg = _start_windows_service(service_name)
            if not success:
                error_msg = msg

        elif op == "stop_service":
            service_name = request.get("service_name", "")

            if not service_name:
                raise ValueError("stop_service requires service_name")

            success, msg = _stop_windows_service(service_name)
            if not success:
                error_msg = msg

        elif op == "start_docker_container":
            container_name = request.get("container_name", "")
            if not container_name:
                raise ValueError("start_docker_container requires container_name")
            success, msg = _start_docker_container(container_name)
            if not success:
                error_msg = msg

        elif op == "stop_docker_container":
            container_name = request.get("container_name", "")
            if not container_name:
                raise ValueError("stop_docker_container requires container_name")
            success, msg = _stop_docker_container(container_name)
            if not success:
                error_msg = msg

        else:
            # Unknown operation: log and mark failure without raising to preserve result marker
            error_msg = f"Unknown operation: {op}"
            success = False
            log_step("ops_daemon_unknown_op", {"request_id": request_id, "op": op})
        
        # Write result marker
        if request_id:
            _write_ops_result(request_id, op, success, error_msg)
        
        # Move processed file
        processed_name = os.path.basename(request_path)
        if success:
            processed_path = os.path.join(data_dir(), "ops_processed", f"{processed_name}.processed")
        else:
            processed_path = os.path.join(data_dir(), "ops_processed", f"{processed_name}.error")
        
        try:
            os.replace(request_path, processed_path)
        except Exception:
            try:
                os.remove(request_path)
            except Exception:
                pass
        
        log_step("ops_daemon_processed", {
            "op": op,
            "success": success,
            "request_id": request_id
        })
        
    except Exception as e:
        log.error("Failed to process queue request %s: %s", os.path.basename(request_path), e)
        
        # Write result marker on failure (best-effort)
        if request_id:
            _write_ops_result(request_id, op, False, str(e))

        # Move to error
        try:
            error_name = os.path.basename(request_path)
            error_path = os.path.join(data_dir(), "ops_processed", f"{error_name}.error")
            os.replace(request_path, error_path)
        except Exception:
            try:
                os.remove(request_path)
            except Exception:
                pass
        
        log_step("ops_daemon_handle_failed", {
            "request_id": request_id,
            "op": op,
            "error": str(e)
        })

def _ops_queue_daemon_loop() -> None:
    """Background daemon that processes IPC queue requests.
    
    Polls ops_queue directory every 500ms and processes pending requests.
    """
    log_step("ops_daemon_start", {"supported_ops": [
        "write_config",
        "write_measurement",
        "add_firewall_rule",
        "remove_firewall_rule",
        "setup_mysterium_firewall",
        "setup_presearch_firewall",
        "setup_diiisco_firewall",
        "setup_spaceacres_firewall",
        "reload_config",
        "start_service",
        "stop_service",
        "start_docker_container",
        "stop_docker_container",
    ]})
    
    request_count = 0
    error_count = 0
    last_health_update = dt.datetime.now(UTC)
    
    while True:
        try:
            queue_dir = os.path.join(data_dir(), "ops_queue")
            
            # Ensure queue directory exists
            os.makedirs(queue_dir, exist_ok=True)
            
            # Get all .json files
            try:
                files = [f for f in os.listdir(queue_dir) if f.endswith(".json")]
            except Exception:
                files = []
            
            # Process each request
            for filename in files:
                request_path = os.path.join(queue_dir, filename)
                try:
                    _process_ops_queue_request(request_path)
                    request_count += 1
                except Exception as e:
                    error_count += 1
                    log.exception("Queue processing error for %s: %s", filename, e)
                    log_step("ops_daemon_loop_error", {"error": str(e), "file": filename})
            
            # Update health marker every 60 seconds
            now = dt.datetime.now(UTC)
            if (now - last_health_update).total_seconds() >= 60:
                try:
                    health_path = os.path.join(data_dir(), "ops_processed", "health.json")
                    health_data = {
                        "last_poll": now.isoformat(),
                        "daemon_status": "running",
                        "requests_processed": request_count,
                        "requests_failed": error_count
                    }
                    with open(health_path, "w", encoding="utf-8") as f:
                        json.dump(health_data, f)
                    last_health_update = now
                except Exception:
                    pass
            
            # Poll every 500ms
            time.sleep(0.5)
            
        except Exception as e:
            log.exception("Ops queue daemon loop error: %s", e)
            log_step("ops_daemon_loop_error", {"error": str(e)})
            time.sleep(1)  # Backoff on error


# ====================================================================
# MEASUREMENT COLLECTION DAEMON
# ====================================================================

_measurement_daemon_running = False

def _collect_and_write_measurements() -> None:
    """Autonomous measurement collection scheduler.
    
    Runs on configured intervals per sensor type.
    Collects and writes measurements to daily CSV files in measurements/ directory.
    
    Collection intervals:
    - Bandwidth (BM): 2-10 seconds (+ 10 min for real tests)
    - Satellite (ISM/OSM): 10 seconds
    - Radiation (IRM): 10 seconds
    - Decibel (IDM/ODM): 2 seconds
    - Tools (Mysterium, etc.): 60 seconds
    """
    try:
        from measurements.collector import write_latest_measurements
        
        # Collect and write measurements for this miner type
        write_latest_measurements(MINER_CODE)
        
        log.debug("Measurements collected and written for %s", MINER_CODE)
        
    except Exception as e:
        log.exception("Measurement collection error: %s", e)


def _collect_hardware_stats() -> Dict[str, Any]:
    """Collect hardware statistics (CPU, RAM, Disk, Network).
    
    Returns:
        Dictionary with hardware stats
    """
    stats = {}
    
    try:
        try:
            import psutil  # type: ignore[import-not-found]
        except Exception:
            psutil = None

        if psutil:
            try:
                # CPU
                stats["cpu_percent"] = psutil.cpu_percent(interval=1)
                stats["cpu_count"] = psutil.cpu_count()

                # Memory
                mem = psutil.virtual_memory()
                stats["memory_percent"] = mem.percent
                stats["memory_total_gb"] = round(mem.total / (1024**3), 2)
                stats["memory_used_gb"] = round(mem.used / (1024**3), 2)
            except Exception:
                pass
        
        # Disk and Network (guarded if psutil available)
        try:
            if psutil:
                disk = psutil.disk_usage('/')
                stats["disk_percent"] = disk.percent
                stats["disk_total_gb"] = round(disk.total / (1024**3), 2)
                stats["disk_used_gb"] = round(disk.used / (1024**3), 2)
                
                # Network (basic check)
                net_io = psutil.net_io_counters()
                stats["network_bytes_sent"] = net_io.bytes_sent
                stats["network_bytes_recv"] = net_io.bytes_recv
        except Exception:
            pass
        
    except Exception as e:
        log.warning("psutil not available or error collecting stats: %s", e)
        stats["error"] = str(e)
    
    return stats


def _collect_mysterium_stats(config: Dict[str, Any]) -> Dict[str, Any]:
    """Collect Mysterium node statistics."""
    stats = {"status": "unknown"}
    
    try:
        # Call Tequilapi to get node status
        response = requests.get(f"http://localhost:{MYST_TEQUILAPI_PORT}/healthcheck", timeout=5)
        if response.status_code == 200:
            stats["status"] = "online"
            stats["health"] = response.json()
        else:
            stats["status"] = "offline"
    except Exception as e:
        stats["status"] = "offline"
        stats["error"] = str(e)
    
    return stats


def _collect_presearch_stats(config: Dict[str, Any]) -> Dict[str, Any]:
    """Collect Presearch node statistics via Docker + cloud API."""
    stats: Dict[str, Any] = {"status": "unknown"}
    try:
        api_key = ""
        try:
            creds = _get_tool_credentials()
            api_key = creds.get("presearch_api_key", "")
        except Exception:
            pass

        from measurements.tools import poll_presearch
        result = poll_presearch(api_key=api_key)
        stats.update(result)
        stats["status"] = result.get("status", "unknown")
    except Exception as e:
        stats["error"] = str(e)
    return stats


def _collect_diiisco_stats(config: Dict[str, Any]) -> Dict[str, Any]:
    """Collect Diiisco node statistics."""
    stats = {"status": "unknown"}
    
    try:
        # TODO: Implement Diiisco stats collection
        stats["status"] = "not_implemented"
    except Exception as e:
        stats["error"] = str(e)
    
    return stats


def _collect_spaceacres_stats(config: Dict[str, Any]) -> Dict[str, Any]:
    """Collect Space Acres statistics."""
    stats = {"status": "unknown"}
    
    try:
        # TODO: Implement Space Acres stats collection
        # This would query the RPC endpoint
        stats["status"] = "not_implemented"
    except Exception as e:
        stats["error"] = str(e)
    
    return stats


def _collect_bright_stats(config: Dict[str, Any]) -> Dict[str, Any]:
    """Collect Bright Data statistics."""
    stats = {"status": "unknown"}
    
    try:
        # TODO: Implement Bright Data stats collection
        stats["status"] = "not_implemented"
    except Exception as e:
        stats["error"] = str(e)
    
    return stats


def _collect_honeygain_stats(config: Dict[str, Any]) -> Dict[str, Any]:
    """Collect Honeygain statistics."""
    stats = {"status": "unknown"}
    
    try:
        # TODO: Implement Honeygain stats collection
        stats["status"] = "not_implemented"
    except Exception as e:
        stats["error"] = str(e)
    
    return stats








def _measurement_collection_loop() -> None:
    """Background daemon loop for per-sensor measurement collection.
    
    Bandwidth mode:
    - Live polling: Every 10 seconds (reads system stats for UI display; CSV for GUI)
    - Real measurement: Every 600 seconds/10 min (actual 10MB DL + 5MB UL test; backend upload & PoD)    
    
    Other sensors:
    - Satellite: 10 sec (ISM/OSM miners)
    - Radiation: 10 sec (IRM miners)
    - Decibel: 2 sec (IDM/ODM miners)
    - Tools: 60 sec (Mysterium, Honeygain, etc.)
    """
    global _measurement_daemon_running
    _measurement_daemon_running = True
    
    log_step("measurement_daemon_started", {"timestamp": now_utc().isoformat()})
    
    # Track last collection time per sensor
    last_collection: dict[str, float] = {}
    
    intervals = _get_measurement_intervals()
    
    # Get API connection params for measurement upload
    # These will be passed to collection functions that upload to backend
    api_client = None
    hex_id = None
    miner_key = None
    install_id = None
    
    try:
        miner_key = read_miner_key()
        install_id = read_encrypted_install_config()
    except Exception:
        pass
    
    while _measurement_daemon_running:
        try:
            now = time.time()

            # Try to establish API connection if not connected
            # This allows measurements to upload to backend before saving to CSV
            if api_client is None and miner_key:
                try:
                    api_client = connect_mongo("", None, None)
                    if api_client and miner_key:
                        hex_id = registered_hexid_from_devices(api_client, miner_key)
                except Exception as e:
                    log.debug("API not available for measurement upload: %s", e)
                    api_client = None
                    hex_id = None
            
            # Retry hex_id resolution if api_client is connected but hex_id is still None
            # This handles cases where the proxy initially returns no hexId (syncing) but later has it
            if api_client is not None and hex_id is None and miner_key:
                try:
                    hex_id = registered_hexid_from_devices(api_client, miner_key)
                except Exception as e:
                    log.debug("Failed to resolve hex_id on retry: %s", e)

            # --- Build two sets of collectors: live writers (for UI) and uploads (backend) ---
            live_collectors: dict[str, tuple[Callable[[], bool], int]] = {}
            upload_collectors: dict[str, tuple[Callable[[], bool], int]] = {}

            presearch_api_key = _get_presearch_api_key()

            if MINER_CODE == "BM":
                # BM: live writes at configured 'bandwidth' interval; upload every 600s
                from measurements.collector import (
                    collect_and_write_bandwidth_live,
                    collect_and_upload_bandwidth,
                    collect_and_write_tool_stats,
                )
                live_collectors["bandwidth_live"] = (lambda: collect_and_write_bandwidth_live(MINER_CODE), intervals.get("bandwidth", 10))
                upload_collectors["bandwidth"] = (lambda: collect_and_upload_bandwidth(MINER_CODE, api_client, hex_id, install_id, miner_key), 600)
                live_collectors["tools"] = (lambda: collect_and_write_tool_stats(MINER_CODE, presearch_api_key=presearch_api_key), intervals["tools"])

            elif MINER_CODE in ("ISM", "OSM"):
                from measurements.collector import collect_and_write_satellite_live, collect_and_upload_satellite
                live_collectors["satellite_live"] = (lambda: collect_and_write_satellite_live(MINER_CODE), intervals["satellite"])
                upload_collectors["satellite"] = (lambda: collect_and_upload_satellite(MINER_CODE, api_client, hex_id, install_id, miner_key), 600)

            elif MINER_CODE == "IRM":
                from measurements.collector import collect_and_write_radiation_live, collect_and_upload_radiation
                live_collectors["radiation_live"] = (lambda: collect_and_write_radiation_live(MINER_CODE), intervals["radiation"])
                upload_collectors["radiation"] = (lambda: collect_and_upload_radiation(MINER_CODE, api_client, hex_id, install_id, miner_key), 600)

            elif MINER_CODE in ("IDM", "ODM"):
                from measurements.collector import collect_and_write_decibel_live, collect_and_upload_decibel
                live_collectors["decibel_live"] = (lambda: collect_and_write_decibel_live(MINER_CODE), intervals["decibel"])
                upload_collectors["decibel"] = (lambda: collect_and_upload_decibel(MINER_CODE, api_client, hex_id, install_id, miner_key), 600)

            elif MINER_CODE == "AEM":
                from measurements.collector import collect_and_upload_aem
                # AEM does not have separate fast live writer; upload interval remains 600s
                upload_collectors["aem"] = (lambda: collect_and_upload_aem(MINER_CODE, api_client, hex_id, install_id, miner_key), intervals["aem"])
            elif MINER_CODE == "RDN":
                from measurements.collector import collect_and_write_tool_stats
                live_collectors["tools"] = (lambda: collect_and_write_tool_stats(MINER_CODE, presearch_api_key=presearch_api_key), intervals["tools"])

            # Run due live collectors
            for sensor_type, (collector_fn, interval) in live_collectors.items():
                if sensor_type not in last_collection:
                    last_collection[sensor_type] = now
                if now - last_collection[sensor_type] >= interval:
                    try:
                        result = collector_fn()
                        last_collection[sensor_type] = now
                    except Exception as e:
                        log.warning("Live collection failed for %s: %s", sensor_type, e)

            # Run due upload collectors
            for sensor_type, (collector_fn, interval) in upload_collectors.items():
                if sensor_type not in last_collection:
                    last_collection[sensor_type] = now
                if now - last_collection[sensor_type] >= interval:
                    try:
                        result = collector_fn()
                        last_collection[sensor_type] = now
                        # If collector succeeded and this sensor is expected for PoD, mark slot pod_status True
                        try:
                            if result and miner_key:
                                expected = set(_normalize_measurement_group(g) for g in expected_measurement_groups())
                                norm = _normalize_measurement_group(sensor_type)
                                if norm and norm in expected:
                                    # Upload collectors use a 10-minute slot (600s)
                                    try:
                                        write_week_local(miner_key, now_utc(), "online", 600, pod_status=True, api_available=True)
                                    except Exception as e:
                                        log.debug("Failed to mark weekly pod_status: %s", e)
                        except Exception:
                            pass
                    except Exception as e:
                        log.warning("Upload collection failed for %s: %s", sensor_type, e)

            # Sleep briefly and check again
            time.sleep(1)
            
        except Exception as e:
            log.exception("Measurement daemon loop error: %s", e)
            log_step("measurement_daemon_loop_error", {"error": str(e)})
            time.sleep(5)  # Backoff on error


def _start_measurement_daemon() -> None:
    """Start the measurement collection daemon in a background thread."""
    try:
        # Load initial configuration
        _reload_service_config()
        
        # Start daemon thread
        daemon_thread = threading.Thread(
            target=_measurement_collection_loop,
            name="measurement-daemon",
            daemon=True
        )
        daemon_thread.start()
        
        intervals = _get_measurement_intervals()
        log.info("Measurement collection daemon started | intervals=%s", intervals)
        log_step("measurement_daemon_start", {
            "intervals": intervals,
            "miner_code": MINER_CODE
        })
    except Exception as e:
        log.error("Failed to start measurement daemon: %s", e)


def _start_ops_queue_daemon() -> None:
    """Start the privileged operations queue daemon in a background thread."""
    try:
        daemon_thread = threading.Thread(
            target=_ops_queue_daemon_loop,
            name="ops-queue-daemon",
            daemon=True
        )
        daemon_thread.start()
        log.info("IPC queue daemon started | queue_path=%s", os.path.join(data_dir(), "ops_queue"))
    except Exception as e:
        log.error("Failed to start ops queue daemon: %s", e)

def main() -> None:
    # Installer handles miner key validation + initial lease acquisition
    _init_service_file_logging()
    refresh_software_version(force=True)

    # Exit silently if not running as a Windows service (Session 0)
    if not _is_running_as_service():
        sys.exit(0)

    # Exit silently in virtualized environments (policy)
    try:
        vm_info = _detect_virtual_machine()
        if isinstance(vm_info, dict) and vm_info.get("vm") is True:
            sys.exit(0)
    except Exception:
        pass

    # Single instance lock
    try:
        acquire_service_lock()
    except Exception:
        pass
    try:
        import atexit
        atexit.register(_release_service_lock)
    except Exception:
        pass

    miner_key = read_miner_key()
    cfg = load_config()

    api_base = require_api_base(cfg)
    api_health_backoff = ApiHealthBackoff()

    # Validate embedded credentials
    if not cfg.get("api_token") and not cfg.get("api_key"):
        log.error("No API token found. This executable was not built with embedded credentials.")
        log.error("Build process required: python build_with_embedded_config.py <1password_config> <output_dir>")
        log.error("The config must use 1Password reference: op://VPS/Hardware_API/API_BEARER_TOKEN")
        sys.exit(5)

    tlsCAFile = cfg.get("tlsCAFile")

    interval = int(cfg.get("interval_seconds", 600))
    poi_poll_seconds = int(cfg.get("poi_interval_seconds", DEFAULT_POI_POLL_SECONDS))
    poi_poll_seconds = max(1, poi_poll_seconds)

    lease_seconds = int(cfg.get("lease_seconds", 900))

    try:
        cfg_path = os.path.join(app_dir(), "config.json")
        cfg_src = cfg_path if os.path.exists(cfg_path) else "(embedded/default)"
        log.info(
            "Starting monitoring service v%s | miner_key=%s | ProgramData=%s | config=%s | api_base=%s | interval=%s | poi_interval=%s | lease_seconds=%s",
            VERSION,
            miner_key,
            data_dir(),
            cfg_src,
            (api_base or "-"),
            interval,
            poi_poll_seconds,
            lease_seconds,
        )
    except Exception:
        pass

    # Read install_id from installer-created config (REQUIRED)
    install_id = read_encrypted_install_config()
    if not install_id:
        log.error("install_config.enc not found or invalid")
        log.error("The installer must create install_config.enc before starting the service")
        time.sleep(2)
        sys.exit(3)

    # Check Docker availability at startup
    docker_available = _is_docker_available()
    log.info("Docker availability at startup: %s", "available" if docker_available else "not available")

    client: Optional[MongoProxyClient] = None
    coll = None

    required_versions: Dict[str, str] = {}
    
    # Track last Docker check time for periodic polling
    last_docker_check_ts: float = time.time()

    # miner_mac will be detected in the main loop once we're fully initialized
    # At startup, leave it as None to avoid incorrect comparisons
    miner_mac_local: Optional[str] = None
    mac_registered: Optional[str] = None

    # Seed local state (best-effort)
    try:
        _update_poi_state(
            interval=interval,
            mac_registered=mac_registered,
            mac_mismatch=None,
            pod_status=None,
            status="offline",
            last_poi=None,
        )
    except Exception:
        pass

    # AEM: start fast PoI polling loop (UPDATED to weekly writer inside)
    if MINER_CODE == "AEM":
        try:
            _start_poi_local_loop(miner_key, interval, poi_poll_seconds)
        except Exception:
            pass

    # ----------------------------
    # Start IPC queue daemon for privileged operations
    # ----------------------------
    try:
        _start_ops_queue_daemon()
    except Exception as e:
        log.warning("Failed to start IPC queue daemon: %s", e)

    # ----------------------------
    # Start measurement collection daemon
    # ----------------------------
    try:
        _start_measurement_daemon()
    except Exception as e:
        log.warning("Failed to start measurement daemon: %s", e)

    # ----------------------------
    # RDN: One-time Presearch status poll to log remote_addr
    # ----------------------------
    if MINER_CODE == "RDN":
        try:
            enabled_tools = _get_enabled_tools()
            if "presearch" in enabled_tools:
                from measurements.tools import poll_presearch
                api_key = _get_presearch_api_key()
                if api_key:
                    poll_presearch(api_key=api_key)
                else:
                    log.info("Presearch enabled but no API key available for status poll")
        except Exception as e:
            log.debug("Presearch startup poll failed: %s", e)

    # ----------------------------
    # Connect + lease + bootstrap doc
    # ----------------------------
    while client is None:
        try:
            client = connect_mongo(api_base, tlsCAFile, cfg)
            coll = client["PoC"]["hardware"]

            # Ensure lease is valid (best-effort during API outages)
            if not verify_or_acquire_installation_lease(
                client, miner_key, install_id, lease_seconds=lease_seconds
            ):
                log.error(
                    "Installation lease verification/acquisition failed for %s (install_id: %s)",
                    miner_key,
                    install_id,
                )
                log.error("Continuing without lease - will retry on next slot (API may be down)")
                # Don't exit - allow service to run in degraded mode
                # Measurements will still be collected locally

            # Upsert installation record (per-machine)
            try:
                upsert_installation_record(client, miner_key, install_id)
            except Exception:
                pass

            # Fetch required versions (best-effort)
            try:
                required_versions = required_versions_from_db(client)
                software_required = required_versions.get("software_version")
                poc_required = required_versions.get("poc_version")
                multiplier_base = float(required_versions.get("multiplier_base", 1.0))
                multiplier_per_tool = float(required_versions.get("multiplier_per_tool", 0.1))
                try:
                    _update_sdk_rewards_params(multiplier_base, multiplier_per_tool)
                except Exception:
                    pass

                is_software_outdated = False
                is_poc_outdated = False

                if isinstance(software_required, str) and software_required:
                    try:
                        if cmp_ver(SOFTWARE_VERSION, software_required) < 0:
                            log.warning(
                                "Service version %s is below required %s; continuing (rewards may be paused)",
                                SOFTWARE_VERSION,
                                software_required,
                            )
                            is_software_outdated = True
                    except Exception:
                        pass

                if isinstance(poc_required, str) and poc_required:
                    try:
                        if cmp_ver(POC_VERSION, poc_required) < 0:
                            log.warning(
                                "PoC version %s is below required %s; continuing (rewards may be paused)",
                                POC_VERSION,
                                poc_required,
                            )
                            is_poc_outdated = True
                    except Exception:
                        pass

                is_outdated = bool(is_software_outdated or is_poc_outdated)

                try:
                    upsert_installation_record(
                        client,
                        miner_key,
                        install_id,
                        software_version_needed=software_required,
                        poc_version_needed=poc_required,
                        is_uptodate=(not is_outdated if (software_required or poc_required) else None),
                        is_outdated=is_outdated,
                    )
                except Exception:
                    pass
            except Exception:
                pass

            # Registered MAC (best-effort)
            try:
                mac_registered = registered_mac_from_devices(client, miner_key) or None
            except Exception:
                mac_registered = mac_registered or None

            if mac_registered:
                try:
                    norm_active_boot = re.sub(r"[^0-9a-f]", "", (miner_mac_local or "").lower())
                    norm_registered_boot = re.sub(r"[^0-9a-f]", "", mac_registered.lower())
                    boot_mismatch = bool(norm_active_boot and norm_registered_boot and norm_active_boot != norm_registered_boot)
                except Exception:
                    boot_mismatch = False
                try:
                    _update_poi_state(mac_registered=mac_registered, mac_mismatch=boot_mismatch)
                except Exception:
                    pass
                
                # AEM: Check Olostep status early for GUI
                poi_startup: Optional[bool] = None
                if MINER_CODE == "AEM":
                    try:
                        poi_startup = monitor_poi_for_aem()
                    except Exception:
                        poi_startup = None
                
                try:
                    write_week_local(
                        miner_key,
                        now_utc(),
                        "offline",
                        interval,
                        pod_status=None,
                        mac_registered=mac_registered,
                        mac_mismatch=boot_mismatch,
                        poi_data=poi_startup,
                        verified=verified_status,
                        gui_version=GUI_VERSION,
                        skip_slot=True,
                        api_available=None,
                    )
                except Exception:
                    pass

            # Bootstrap DB doc so UI sees everything immediately
            try:
                existing_doc_raw = _get_existing_hardware_doc(coll, miner_key)
                existing_doc: Dict[str, Any] = existing_doc_raw if isinstance(existing_doc_raw, dict) else {}

                miner_type_val = (
                    existing_doc.get("miner_type")
                    if isinstance(existing_doc.get("miner_type"), str)
                    else MINER_CODE
                )

                # software
                software_info = _extract_software_fields(existing_doc)
                software_required = required_versions.get("software_version")
                poc_required = required_versions.get("poc_version")

                if isinstance(software_required, str) and software_required:
                    software_info["software_version_needed"] = software_required
                    try:
                        software_info["software_uptodate"] = (cmp_ver(SOFTWARE_VERSION, software_required) >= 0)
                    except Exception:
                        software_info["software_uptodate"] = None
                else:
                    if not isinstance(software_info.get("software_uptodate"), bool):
                        software_info["software_uptodate"] = None

                if isinstance(poc_required, str) and poc_required:
                    software_info["poc_version_needed"] = poc_required
                    try:
                        software_info["poc_uptodate"] = (cmp_ver(POC_VERSION, poc_required) >= 0)
                    except Exception:
                        software_info["poc_uptodate"] = None
                else:
                    if not isinstance(software_info.get("poc_uptodate"), bool):
                        software_info["poc_uptodate"] = None

                has_req = bool(software_info.get("software_version_needed") or software_info.get("poc_version_needed"))
                if has_req:
                    sw_bad = (software_info.get("software_uptodate") is False)
                    poc_bad = (software_info.get("poc_uptodate") is False)
                    software_info["is_uptodate"] = not (sw_bad or poc_bad)
                else:
                    software_info["is_uptodate"] = None

                # MAC block init/update
                mac_info = _extract_mac_fields(existing_doc)
                if isinstance(miner_mac_local, str) and miner_mac_local:
                    mac_info["miner_mac"] = miner_mac_local
                if isinstance(mac_registered, str) and mac_registered:
                    mac_info["mac_registered"] = mac_registered

                def _norm_mac_boot(v: Optional[str]) -> str:
                    try:
                        return re.sub(r"[^0-9a-f]", "", (v or "").lower())
                    except Exception:
                        return ""

                norm_miner = _norm_mac_boot(mac_info.get("miner_mac"))
                norm_reg = _norm_mac_boot(mac_info.get("mac_registered"))
                mac_match = bool(norm_miner and norm_reg and norm_miner == norm_reg)

                boot_checked_at = now_utc().isoformat()
                existing_mac_raw = existing_doc.get("mac")
                existing_mac: Dict[str, Any] = existing_mac_raw if isinstance(existing_mac_raw, dict) else {}

                mac_status_old = existing_mac.get("status") if isinstance(existing_mac.get("status"), bool) else None
                last_changed_at = existing_mac.get("last_changed_at") if isinstance(existing_mac.get("last_changed_at"), str) else None
                if mac_status_old is None or mac_status_old != mac_match:
                    last_changed_at = boot_checked_at
                elif not last_changed_at:
                    last_changed_at = boot_checked_at

                mac_block = {
                    "status": mac_match,
                    "last_changed_at": last_changed_at,
                    "last_checked_at": boot_checked_at,
                    "evidence": {
                        "miner_mac": mac_info.get("miner_mac"),
                        "registered_mac": mac_info.get("mac_registered"),
                    },
                }

                # PoL placeholder (write_location_daily fills later)
                existing_pol_raw = existing_doc.get("pol")
                pol_block: Dict[str, Any] = existing_pol_raw if isinstance(existing_pol_raw, dict) else {
                    "status": False,
                    "last_changed_at": boot_checked_at,
                    "last_checked_at": boot_checked_at,
                    "evidence": {},
                }

                # AEM PoI bootstrap (optional)
                poi_boot: Optional[bool] = None
                if miner_type_val == "AEM":
                    try:
                        poi_boot = monitor_poi_for_aem()
                    except Exception:
                        poi_boot = None

                new_doc = _compose_hardware_doc(
                    miner_key,
                    miner_type=miner_type_val,
                    software=software_info,
                    last_updated=now_utc(),
                    poi=poi_boot,
                    poi_slots=None,
                )

                new_doc["mac"] = mac_block
                new_doc["pol"] = pol_block

                # Preserve rewards/history if any exist
                rewards_raw = existing_doc.get("rewards")
                new_doc["rewards"] = rewards_raw if isinstance(rewards_raw, dict) else {}

                hist_raw = existing_doc.get("rewards_multiplier_history")
                new_doc["rewards_multiplier_history"] = hist_raw if isinstance(hist_raw, list) else []

                rmd = existing_doc.get("rewards_multiplier_day")
                new_doc["rewards_multiplier_day"] = float(rmd) if isinstance(rmd, (int, float)) else 0.0
                rmcs = existing_doc.get("rewards_multiplier_day_counted_slots")
                new_doc["rewards_multiplier_day_counted_slots"] = int(rmcs) if isinstance(rmcs, int) else 0

                uptime_raw = existing_doc.get("uptime")
                if isinstance(uptime_raw, dict):
                    new_doc["uptime"] = uptime_raw

                boot_raw = existing_doc.get("boot_time")
                if isinstance(boot_raw, str) and boot_raw:
                    new_doc["boot_time"] = boot_raw

                new_doc["tz"] = "UTC"
                new_doc["day"] = day_iso(now_utc())

                coll.replace_one({"miner_key": miner_key}, new_doc, upsert=True)
            except Exception:
                pass

            try:
                api_health_backoff.reset()
            except Exception:
                pass

        except Exception as e:
            log.error("Initial API connect failed: %s", e)
            try:
                api_health_backoff.record_failure()
                api_health_backoff.wait_for_health(api_base)
            except Exception:
                pass
            time.sleep(1)

    last_pol_check: Optional[dt.datetime] = None
    pending_pol_update: Optional[Dict[str, Any]] = None
    pol_status: Optional[bool] = None  # Current PoL status for weekly cache
    try:
        initial_pol_ts = now_utc()
        pol_block_start = write_location_daily(coll, client, miner_key, initial_pol_ts)
        if pol_block_start:
            pending_pol_update = pol_block_start
            last_pol_check = initial_pol_ts
            # Extract pol_status for weekly cache
            pol_status = pol_block_start.get("status") if isinstance(pol_block_start.get("status"), bool) else None
    except Exception:
        pass

    # ----------------------------
    # Initial checks: MAC and PoL (persistent status)
    # ----------------------------
    try:
        init_miner_mac: Optional[str] = None
        init_mac_registered: Optional[str] = None
        init_mac_mismatch = False
        init_pol_status: Optional[bool] = None
        
        # Detect local MAC
        try:
            init_miner_mac = detect_local_mac()
        except Exception:
            init_miner_mac = None
        
        # Read registered MAC
        try:
            init_mac_registered = registered_mac_from_devices(client, miner_key)
        except Exception:
            init_mac_registered = None
        
        # Compare MACs
        if init_miner_mac and init_mac_registered:
            norm_active = re.sub(r"[^0-9a-f]", "", (init_miner_mac or "").lower())
            norm_registered = re.sub(r"[^0-9a-f]", "", init_mac_registered.lower())
            init_mac_mismatch = bool(norm_active and norm_registered and norm_active != norm_registered)
        
        # Check PoL (Proof of Location)
        try:
            pol_block = read_location_cache(client, miner_key)
            init_pol_status = pol_block.get("status") if isinstance(pol_block, dict) else None
        except Exception:
            init_pol_status = None
        
        # Update state with initial values
        try:
            update_kwargs = {
                "mac_registered": init_mac_registered,
                "mac_mismatch": init_mac_mismatch,
                "interval": interval,
            }
            _update_poi_state(**update_kwargs)
            log.info("Initial status: mac_mismatch=%s, pol=%s", init_mac_mismatch, init_pol_status)
        except Exception:
            pass
    except Exception as e:
        log.warning("Initial status checks failed: %s", e)

    # ----------------------------
    # Initial verified status check
    # ----------------------------
    verified_status: bool = False
    last_verified_check: Optional[dt.datetime] = None
    try:
        verified_status = client.get_verified_status(miner_key)
        last_verified_check = now_utc()
        log.info("Initial verified status: %s", verified_status)
    except Exception:
        verified_status = False

    # ----------------------------
    # Initial measurement collection (for first PoC update)
    # ----------------------------
    try:
        from measurements.collector import collect_all_by_miner_type
        hex_registered = None
        try:
            hex_registered = registered_hexid_from_devices(client, miner_key)
        except Exception:
            pass
        if hex_registered:
            log.info("Performing initial measurement collection before first PoC update")
            collect_all_by_miner_type(MINER_CODE, client, hex_registered, install_id, miner_key)
    except Exception as e:
        log.warning("Initial measurement collection failed: %s", e)

    # ----------------------------
    # Write initial Docker status to weekly file (for GUI visibility)
    # ----------------------------
    try:
        write_week_local(
            miner_key,
            now_utc(),
            "offline",
            interval,
            verified=verified_status,
            gui_version=GUI_VERSION,
            skip_slot=True,
            api_available=None,
        )
    except Exception as e:
        log.debug("Failed to write initial Docker status to weekly file: %s", e)

    # ----------------------------
    # Main monitoring loop
    # ----------------------------
    warned_version = False
    last_version_check_hour: Optional[dt.datetime] = None
    next_slot_time: Optional[dt.datetime] = None
    # Track last Autonomys daily upload target (YYYYMMDD) to avoid duplicate runs
    last_autonomys_target: Optional[str] = None
    last_presearch_ip_update: Optional[dt.datetime] = None
    # Docker check interval: 5 minutes (300 seconds)
    _DOCKER_PERIODIC_CHECK_INTERVAL: float = 300.0

    while True:
        refresh_software_version()
        now_loop = now_utc()
        
        # Periodic Docker availability check (every 5 minutes)
        now_ts = time.time()
        if (now_ts - last_docker_check_ts) >= _DOCKER_PERIODIC_CHECK_INTERVAL:
            try:
                docker_status = _is_docker_available()
                if docker_status != docker_available:
                    docker_available = docker_status
                    log.warning(
                        "Docker availability changed: %s",
                        "now available" if docker_available else "now unavailable",
                    )
            except Exception as e:
                log.debug("Docker periodic check failed: %s", e)
            last_docker_check_ts = now_ts
        
        if next_slot_time is None:
            next_slot_time = next_boundary(now_loop, interval)
        if now_loop + dt.timedelta(seconds=1) < next_slot_time:
            sleep_for = max(1.0, min(5.0, (next_slot_time - now_loop).total_seconds()))
            time.sleep(sleep_for)
            continue
        slot_ts = next_slot_time

        try:
            status_raw = "online" if is_internet_up(timeout=4) else "offline"

            poi_snapshot: Optional[bool] = None
            if MINER_CODE == "AEM":
                try:
                    snap = _get_poi_state_snapshot()
                    poi_snapshot = snap.get("last_poi")
                except Exception:
                    poi_snapshot = None
                if poi_snapshot is None:
                    try:
                        poi_snapshot = monitor_poi_for_aem()
                    except Exception:
                        poi_snapshot = None
                try:
                    _update_poi_state(last_poi=poi_snapshot)
                except Exception:
                    pass

            expected_groups = expected_measurement_groups()
            pod_slot_ok = False if expected_groups else True
            delivered_groups: List[str] = []

            if expected_groups:
                if status_raw == "online":
                    try:
                        pod_slot_ok, delivered_groups = upload_measurements_for_slot(
                            client,
                            miner_key,
                            install_id,
                            expected_groups=expected_groups,
                            slot_ts=slot_ts,
                            interval_seconds=interval,
                        )
                    except Exception as e:
                        log.error("Measurement upload error: %s", e)
                        pod_slot_ok = False
                else:
                    # Connection down; can't upload
                    pod_slot_ok = False

            if expected_groups and not pod_slot_ok:
                try:
                    log.debug(
                        "PoD incomplete for %s: expected %s delivered %s",
                        miner_key,
                        expected_groups,
                        delivered_groups,
                    )
                except Exception:
                    pass

            # Status for gates: online gate is just network connectivity
            status = status_raw

            # RDN tool states for this slot (declared outside try blocks so both writers can use them)
            rdn_presearch_active = False
            rdn_diiisco_active = False
            if MINER_CODE == "RDN":
                try:
                    rdn_presearch_active, rdn_diiisco_active = _rdn_tool_states_for_slot()
                except Exception:
                    pass

            # ----------------------------
            # Local weekly cache (ALWAYS)
            # ----------------------------
            try:
                norm_active = re.sub(r"[^0-9a-f]", "", (miner_mac_local or "").lower())
                norm_registered = re.sub(r"[^0-9a-f]", "", (mac_registered or "").lower())
                local_mismatch = bool(norm_active and norm_registered and norm_active != norm_registered)

                try:
                    update_kwargs = {
                        "status": status,
                        "pod_status": pod_slot_ok,
                        "mac_registered": mac_registered,
                        "mac_mismatch": local_mismatch,
                        "interval": interval,
                    }
                    if MINER_CODE == "AEM":
                        update_kwargs["last_poi"] = poi_snapshot
                    _update_poi_state(**update_kwargs)
                except Exception:
                    pass

                # NEW: weekly writer replaces write_status_local
                write_week_local(
                    miner_key,
                    slot_ts,
                    status,
                    interval,
                    pod_status=pod_slot_ok,
                    mac_registered=mac_registered,
                    mac_mismatch=local_mismatch,
                    poi_data=poi_snapshot,
                    pol_status=pol_status,
                    verified=verified_status,
                    gui_version=GUI_VERSION,
                    multiplier_base=multiplier_base,
                    multiplier_per_tool=multiplier_per_tool,
                    presearch_active=rdn_presearch_active,
                    diiisco_active=rdn_diiisco_active,
                    api_available=True,  # Will be updated in DB work block if API fails
                )
            except Exception:
                pass

            # ----------------------------
            # DB work (resilient to API outages)
            # ----------------------------
            api_available = True
            try:
                # RDN: push Presearch IP status every 10 minutes
                if MINER_CODE == "RDN":
                    try:
                        due_presearch = (
                            last_presearch_ip_update is None
                            or (slot_ts - last_presearch_ip_update).total_seconds() >= 600
                        )
                        if due_presearch:
                            enabled_tools = _get_enabled_tools()
                            if "presearch" in enabled_tools:
                                api_key = _get_presearch_api_key()
                                if api_key:
                                    from measurements.tools import fetch_presearch_nodes, get_public_ip
                                    nodes = fetch_presearch_nodes(api_key)
                                    if nodes:
                                        local_ip = get_public_ip()
                                        ip_value = local_ip
                                        nodes_for_ip = [n for n in nodes if n.get("remote_addr") == local_ip]
                                        if ip_value and nodes_for_ip:
                                            from external_api import get_global_api_client
                                            payload_nodes = []
                                            for node in nodes_for_ip:
                                                payload_nodes.append(
                                                    {
                                                        "miner_key": miner_key,
                                                        "node_key": node.get("node_key", ""),
                                                        "connected": bool(node.get("connected")),
                                                        "blocked": bool(node.get("blocked")),
                                                        "description": node.get("description", ""),
                                                    }
                                                )
                                            payload = {
                                                "ip": ip_value,
                                                "timestamp": now_utc().isoformat(),
                                                "nodes": payload_nodes,
                                            }
                                            client_api = get_global_api_client()
                                            if hasattr(client_api, "upsert_presearch_ip"):
                                                client_api.upsert_presearch_ip(ip_value, payload)
                                    last_presearch_ip_update = slot_ts
                    except Exception as e:
                        log.debug("Presearch IP status update failed: %s", e)

                # Refresh required versions once per UTC hour
                try:
                    hour_start = slot_ts.replace(minute=0, second=0, microsecond=0)
                    if (last_version_check_hour is None) or (hour_start > last_version_check_hour):
                        required_versions = required_versions_from_db(client)
                        last_version_check_hour = hour_start
                except Exception:
                    pass

                software_required = required_versions.get("software_version")
                poc_required = required_versions.get("poc_version")
                multiplier_base = float(required_versions.get("multiplier_base", 1.0))
                multiplier_per_tool = float(required_versions.get("multiplier_per_tool", 0.1))
                try:
                    _update_sdk_rewards_params(multiplier_base, multiplier_per_tool)
                except Exception:
                    pass

                outdated = False
                try:
                    is_sw_out = bool(isinstance(software_required, str) and software_required and (cmp_ver(SOFTWARE_VERSION, software_required) < 0))
                    is_poc_out = bool(isinstance(poc_required, str) and poc_required and (cmp_ver(POC_VERSION, poc_required) < 0))
                    outdated = bool(is_sw_out or is_poc_out)
                except Exception:
                    outdated = False

                # Warn once if outdated
                try:
                    if outdated and not warned_version:
                        if isinstance(software_required, str) and software_required and cmp_ver(SOFTWARE_VERSION, software_required) < 0:
                            log.warning("Software version %s is below required %s; please update.", SOFTWARE_VERSION, software_required)
                        if isinstance(poc_required, str) and poc_required and cmp_ver(POC_VERSION, poc_required) < 0:
                            log.warning("PoC version %s is below required %s; please update.", POC_VERSION, poc_required)
                        warned_version = True
                except Exception:
                    pass

                # Refresh miner_mac from disk (GUI selection) or fallback to detect
                try:
                    mm = read_selected_miner_mac()
                    if mm:
                        miner_mac_local = mm
                    else:
                        detected = detect_local_mac()
                        if detected:
                            miner_mac_local = detected
                except Exception:
                    pass

                # Refresh registered MAC opportunistically
                try:
                    mr = registered_mac_from_devices(client, miner_key)
                    if mr:
                        mac_registered = mr
                except Exception:
                    pass

                pol_hex_registered = None
                try:
                    pol_hex_registered = registered_hexid_from_devices(client, miner_key)
                except Exception:
                    pol_hex_registered = None

                # AEM: if snapshot missing, fetch PoI
                poi_data = poi_snapshot
                if MINER_CODE == "AEM" and poi_data is None:
                    try:
                        poi_data = monitor_poi_for_aem()
                    except Exception:
                        poi_data = None

                try:
                    due_pol = (
                        last_pol_check is None
                        or (slot_ts - last_pol_check).total_seconds() >= 600
                    )
                    if due_pol:
                        pol_block_new = write_location_daily(coll, client, miner_key, slot_ts)
                        if pol_block_new:
                            pending_pol_update = pol_block_new
                            last_pol_check = slot_ts
                            # Update pol_status for weekly cache
                            pol_status = pol_block_new.get("status") if isinstance(pol_block_new.get("status"), bool) else pol_status
                except Exception:
                    pass

                # Refresh verified status periodically (every 600s)
                try:
                    due_verified = (
                        last_verified_check is None
                        or (slot_ts - last_verified_check).total_seconds() >= 600
                    )
                    if due_verified:
                        v = client.get_verified_status(miner_key)
                        if isinstance(v, bool):
                            verified_status = v
                        last_verified_check = slot_ts
                except Exception:
                    pass

                # Write slot snapshot to DB
                write_status(
                    coll,
                    miner_key,
                    slot_ts,
                    status,
                    interval,
                    software_version_needed=software_required,
                    poc_version_needed=poc_required,
                    miner_mac=miner_mac_local,
                    mac_registered=mac_registered,
                    poi_data=poi_data,
                    hex_registered=pol_hex_registered,
                    pol_override=pending_pol_update,
                    pod_status=pod_slot_ok,
                    multiplier_base=multiplier_base,
                    multiplier_per_tool=multiplier_per_tool,
                    presearch_active=rdn_presearch_active,
                    diiisco_active=rdn_diiisco_active,
                )
                pending_pol_update = None

                # --- Daily Autonomys upload (run once/day shortly after midnight UTC) ---
                try:
                    enabled = os.environ.get('AUTONOMYS_UPLOAD_ENABLED') or os.environ.get('AUTONOMYS_UPLOADS_ENABLED')
                    # If not present in env, check embedded config (from build-time 1Password)
                    if not enabled:
                        try:
                            embedded_cfg = load_config()
                            b_enabled = embedded_cfg.get("autonomys_upload_enabled") or embedded_cfg.get("autonomys_uploads_enabled")
                            if isinstance(b_enabled, bool):
                                enabled = "true" if b_enabled else "false"
                            elif isinstance(b_enabled, (int, float)):
                                enabled = "true" if int(b_enabled) else "false"
                            elif isinstance(b_enabled, str) and b_enabled.strip():
                                enabled = b_enabled
                            # inject API key from embedded config into env if provided
                            tool_creds = embedded_cfg.get("tool_credentials", {})
                            api_key = tool_creds.get("autonomys_api_key")
                            if isinstance(api_key, str) and api_key.strip() and not os.environ.get("AUTONOMYS_API_KEY"):
                                os.environ["AUTONOMYS_API_KEY"] = api_key.strip()
                        except Exception:
                            pass

                    if isinstance(enabled, str) and enabled.strip().lower() in _TRUE_SET:
                        # target is yesterday
                        target_date = (slot_ts - dt.timedelta(days=1)).strftime("%Y%m%d")
                        # run once per target_date when we observe the first midnight slot
                        if last_autonomys_target != target_date and slot_ts.hour == 0 and slot_ts.minute == 0:
                            try:
                                # Import here to avoid heavy imports at module load
                                from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

                                # Determine hex id to use for upload
                                hex_for_upload = None
                                try:
                                    hex_for_upload = registered_hexid_from_devices(client, miner_key)
                                except Exception:
                                    hex_for_upload = pol_hex_registered or None

                                if not hex_for_upload:
                                    log.warning("Autonomys upload skipped: no registered hexId available")
                                else:
                                    log.info("Starting Autonomys daily upload for %s (date=%s)", hex_for_upload, target_date)
                                    results = process_yesterday_to_autonomys(MINER_CODE, hex_for_upload, install_id=install_id, upload_to_cloud=True)
                                    log.info("Autonomys daily upload results: %s", results)
                                    last_autonomys_target = target_date
                            except Exception as e:
                                log.error("Autonomys daily upload failed: %s", e)
                except Exception:
                    pass

                # Refresh installation heartbeat
                try:
                    has_requirements = bool(software_required or poc_required)
                    upsert_installation_record(
                        client,
                        miner_key,
                        install_id,
                        software_version_needed=software_required,
                        poc_version_needed=poc_required,
                        is_uptodate=(False if (has_requirements and outdated) else (True if has_requirements else None)),
                        is_outdated=outdated,
                    )
                except Exception:
                    pass

                # Renew lease; reacquire if needed (best-effort during API outages)
                try:
                    renewed = renew_installation_lease(client, miner_key, install_id, lease_seconds=lease_seconds)
                    if not renewed:
                        log.warning("Lease renewal failed, attempting to reacquire...")
                        if not verify_or_acquire_installation_lease(
                            client, miner_key, install_id, lease_seconds=lease_seconds, max_retries=5
                        ):
                            log.warning("Lost global lease for %s; will retry next slot (API may be down)", miner_key)
                            api_available = False
                        else:
                            log.info("Successfully reacquired lease for %s", miner_key)
                except Exception as e:
                    msg = str(e)
                    is_api_down = any(x in msg.lower() for x in ["502", "503", "504", "bad gateway", "timeout", "connection"])
                    if is_api_down:
                        log.warning("Hardware API unreachable during lease renewal: %s (continuing in degraded mode)", msg)
                        api_available = False
                    else:
                        log.error("Failed to renew lease for %s: %s (continuing with retry)", miner_key, e)
                        api_available = False

                if api_available:
                    try:
                        api_health_backoff.reset()
                        log.debug("Hardware API healthy")
                    except Exception:
                        pass
                else:
                    log.info("Hardware API unavailable - measurements continue locally, DB updates will resume when API recovers")
                    # Update weekly cache with API unavailable status for GUI warning
                    try:
                        write_week_local(
                            miner_key,
                            slot_ts,
                            status,
                            interval,
                            pod_status=pod_slot_ok,
                            mac_registered=mac_registered,
                            mac_mismatch=local_mismatch,
                            poi_data=poi_snapshot,
                            pol_status=pol_status,
                            verified=verified_status,
                            gui_version=GUI_VERSION,
                            multiplier_base=multiplier_base,
                            multiplier_per_tool=multiplier_per_tool,
                            presearch_active=rdn_presearch_active,
                            diiisco_active=rdn_diiisco_active,
                            api_available=False,
                        )
                    except Exception:
                        pass
            except Exception as db_err:
                log.warning("DB operations failed: %s (measurements continue locally)", db_err)
                api_available = False
                # Update weekly cache with API unavailable status for GUI warning
                try:
                    norm_active = re.sub(r"[^0-9a-f]", "", (miner_mac_local or "").lower())
                    norm_registered = re.sub(r"[^0-9a-f]", "", (mac_registered or "").lower())
                    local_mismatch = bool(norm_active and norm_registered and norm_active != norm_registered)
                    write_week_local(
                        miner_key,
                        slot_ts,
                        status,
                        interval,
                        pod_status=pod_slot_ok,
                        mac_registered=mac_registered,
                        mac_mismatch=local_mismatch,
                        poi_data=poi_snapshot,
                        pol_status=pol_status,
                        verified=verified_status,
                        gui_version=GUI_VERSION,
                        multiplier_base=multiplier_base,
                        multiplier_per_tool=multiplier_per_tool,
                        presearch_active=rdn_presearch_active,
                        diiisco_active=rdn_diiisco_active,
                        api_available=False,
                    )
                except Exception:
                    pass

        except ApiError as e:
            log.warning("Hardware API error: %s (continuing in degraded mode)", e)
            try:
                api_health_backoff.record_failure()
                api_health_backoff.wait_for_health(api_base)
            except Exception:
                pass
            # Try to reconnect but don't exit if it fails
            try:
                client = connect_mongo(api_base, tlsCAFile, cfg)
                coll = client["PoC"]["hardware"]
                log.info("Hardware API reconnected successfully")
                try:
                    api_health_backoff.reset()
                except Exception:
                    pass
            except Exception as e2:
                log.warning("Hardware API still unreachable: %s (will retry next slot)", e2)

        except Exception as e:
            log.error("Iteration failed: %s", e)
        finally:
            try:
                next_slot_time = next_boundary(slot_ts, interval)
            except Exception:
                next_slot_time = None

    sys.exit(8)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        try:
            log.exception("Fatal error: %s", e)
        except Exception:
            pass
        time.sleep(2)
        sys.exit(1)
