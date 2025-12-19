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
    """Attach a file handler under the ProgramData logs folder for service diagnostics."""
    try:
        log_dir = os.path.join(data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        file_path = os.path.join(log_dir, "service.err.log")
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        root = logging.getLogger()
        root.addHandler(fh)
    except Exception:
        # Do not block startup if logging cannot initialize
        pass

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
_TRUE_SET = {"1", "true", "yes", "y", "on", "approved", "allow", "allowed", "enabled"}
_FALSE_SET = {"0", "false", "no", "n", "off", "deny", "denied", "blocked"}

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

def read_encrypted_miner_config() -> Optional[str]:
    """Read miner key from encrypted config file if present."""
    try:
        config_path = os.path.join(app_dir(), "config", "miner_config.enc")
        if not os.path.exists(config_path):
            return None
        with open(config_path, 'r') as f:
            encrypted_data = json.load(f)
        # Decrypt using the same method as the API config
        # We'll use a simple key derivation for the miner config
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64
        # Use a fixed salt for miner config (this is acceptable since it's just obfuscation)
        salt = b'miner_config_salt_v1'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        # Derive key from a known string (this is obfuscation, not true security)
        password = "miner_config_encryption_key_v1".encode()
        key = base64.urlsafe_b64encode(kdf.derive(password))
        # Decrypt
        f = Fernet(key)
        decrypted_data = f.decrypt(encrypted_data['data'].encode())
        config_data = json.loads(decrypted_data)
        return config_data.get('miner_key')
    except Exception:
        return None

def read_encrypted_install_config() -> Optional[str]:
    """Read install_id from encrypted install config file created by installer."""
    try:
        config_path = os.path.join(app_dir(), "config", "install_config.enc")
        if not os.path.exists(config_path):
            return None
        with open(config_path, 'r') as f:
            encrypted_data = json.load(f)
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
) -> Tuple[bool, List[str]]:
    """Upload local measurement files and return (pod_met, delivered_groups)."""
    delivered_groups: List[str] = []
    expected_set = {_normalize_measurement_group(g) for g in (expected_groups or []) if g}
    pod_required = bool(expected_set)
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
    try:
        measurement_data_list = read_measurement_files()
    except Exception:
        measurement_data_list = []
    if not measurement_data_list:
        return (False if pod_required else True), delivered_groups
    for measurement_data in measurement_data_list:
        try:
            timestamp = measurement_data.get("timestamp")
            measurement_type_raw = measurement_data.get("group") or measurement_data.get("measurement_type")
            value = measurement_data.get("measurement", {})
            measurement_type = measurement_type_raw.strip() if isinstance(measurement_type_raw, str) else None
            if timestamp and measurement_type and isinstance(value, dict) and value:
                client._api.upload_measurement(
                    hex_id=hex_registered,
                    miner_code=MINER_CODE,
                    install_id=install_id,
                    timestamp=str(timestamp),
                    measurement_type=measurement_type,
                    value=value,
                )
                normalized = _normalize_measurement_group(measurement_type)
                if normalized and normalized in expected_set and normalized not in delivered_groups:
                    delivered_groups.append(normalized)
            else:
                log.debug("Skipping measurement upload; missing required fields: %s", measurement_data)
        except Exception as e:
            _record_critical_failure(f"pod-upload-error-{miner_key}", f"Failed to upload measurement for {miner_key}: {e}", miner_key=miner_key)
        else:
            _reset_critical_failure(f"pod-upload-error-{miner_key}")
    if not pod_required:
        return True, delivered_groups
    delivered_set = set(delivered_groups)
    return (delivered_set.issuperset(expected_set), delivered_groups)

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

def load_config() -> Dict[str, Any]:
    # Use embedded encrypted config (credentials from 1Password at build time)
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
                return embedded_cfg
    except Exception:
        pass
    
    # No fallback - production builds must have embedded config from 1Password
    return {}

def required_versions_from_db(client) -> Dict[str, str]:
    """Return software_version_needed and poc_version_needed for this miner code from PoC.versions.
    Returns dict with 'software_version' and 'poc_version' keys (may be empty if unavailable)."""
    try:
        doc = client["PoC"]["versions"].find_one(
            {"miner_code": MINER_CODE}, 
            {"software_version_needed": 1, "poc_version_needed": 1, "_id": 0}
        )
        result: Dict[str, str] = {}
        if isinstance(doc, dict):
            software = doc.get("software_version_needed")
            poc = doc.get("poc_version_needed")
            if isinstance(software, str) and software:
                result["software_version"] = software
            if isinstance(poc, str) and poc:
                result["poc_version"] = poc
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
    poi_ok: Optional[bool],
    bright_active: bool,
    honeygain_active: bool,
    mysterium_active: bool,
) -> float:
    """Compute per-slot rewards multiplier based on gating and active tools."""
    if not (mac_match and poc_ok and pod_ok):
        return 0.0
    if miner_type == "AEM":
        return 1.0 if poi_ok else 0.0
    if miner_type == "BM":
        tool_count = (
            int(bool(bright_active))
            + int(bool(honeygain_active))
            + int(bool(mysterium_active))
        )
        base = 0.25
        return min(1.0, base + 0.25 * tool_count)
    # Non-BM/AEM: gate passed -> full multiplier
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

    # --- AEM PoI slots (keep existing format) ---
    poi_slots: Optional[dict[str, Any]] = None
    poi_slot_ok: Optional[bool] = None
    if miner_type_val == "AEM":
        poi_slot_ok = bool(poi_data)

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
    poc_slot_ok = False
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
        poc_slot_ok = (status == "online")

    # --- slot-level PoD from local cache ---
    pod_slot_ok = False
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

    # --- compute multiplier ---
    rewards_multiplier_value = _compute_rewards_multiplier(
        miner_type_val if isinstance(miner_type_val, str) and miner_type_val else MINER_CODE,
        mac_match,
        bool(poc_slot_ok),
        bool(pod_slot_ok),
        poi_slot_ok,
        bright_active,
        honeygain_active,
        mysterium_active,
    )

    # --- unified slot snapshot for the graph ---
    slot_obj: dict[str, Any] = {
        "gates": {
            "data": True,
            "online": (status == "online"),
            "mac_match": mac_match,
            "pol": bool(pol_status),
            "poc": bool(poc_slot_ok),
            "pod": bool(pod_slot_ok),
            "poi": (bool(poi_slot_ok) if miner_type_val == "AEM" else None),
        },
        "tools_active": (selected_tools if miner_type_val == "BM" else []),
        "tools_count": (len(selected_tools) if miner_type_val == "BM" else 0),
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

def read_encrypted_sdk_config() -> Dict[str, Any]:
    """Read SDK approval config from encrypted file (installer-managed)."""
    try:
        config_path = os.path.join(app_dir(), "config", "sdk_config.enc")
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
        encrypted_payload = read_encrypted_sdk_config()
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
        import psutil
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
    gui_version: Optional[str] = None,
    skip_slot: bool = False,
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

    # Always populate GUI_version (prefer explicit arg, fallback to global)
    gv = gui_version.strip() if isinstance(gui_version, str) and gui_version.strip() else ""
    if not gv:
        gv = GUI_VERSION.strip() if isinstance(GUI_VERSION, str) and GUI_VERSION.strip() else ""
    doc["GUI_version"] = gv

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
    data_ok = bool(pod_status) if pod_status is not None else None  # BM: PoD delivered (data)
    poi_ok = bool(poi_data) if (MINER_CODE == "AEM" and poi_data is not None) else None

    bright_active, honeygain_active, mysterium_active = _tool_states_for_slot()
    tools_active: list[str] = []
    if MINER_CODE == "BM":
        if bright_active:
            tools_active.append("bright")
        if honeygain_active:
            tools_active.append("honeygain")
        if mysterium_active:
            tools_active.append("mysterium")

    # Tools gate policy (BM):
    # - If BM and zero tools selected => fail tools gate
    tools_ok: Optional[bool]
    if MINER_CODE == "BM":
        tools_ok = (len(tools_active) > 0)
    else:
        tools_ok = None

    # "data" gate: BM uses pod_status; if unknown, treat as False for multiplier gating
    data_gate_for_multiplier = bool(data_ok) if isinstance(data_ok, bool) else False

    multiplier: Optional[float] = None
    try:
        if MINER_CODE == "AEM":
            # AEM multiplier: typically depends on PoC + PoI (+ mac)
            multiplier = _compute_rewards_multiplier(
                MINER_CODE,
                bool(mac_ok),
                bool(online_ok),     # PoC in your system is basically online gate
                True,               # pod_ok irrelevant for AEM
                poi_ok,
                bright_active,
                honeygain_active,
                mysterium_active,
            )
        else:
            multiplier = _compute_rewards_multiplier(
                MINER_CODE,
                bool(mac_ok),
                bool(online_ok),             # PoC
                bool(data_gate_for_multiplier),  # PoD/data
                None,
                bright_active,
                honeygain_active,
                mysterium_active,
            )
    except Exception:
        multiplier = None

    slot_obj: Dict[str, Any] = {
        "gates": {
            "online": bool(online_ok),
            "mac": bool(mac_ok),
            "data": bool(data_gate_for_multiplier),
            "tools": (bool(tools_ok) if tools_ok is not None else True),
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
    """Launch a background loop that refreshes PoI in the WEEKLY local cache more frequently (AEM only)."""
    if poll_seconds <= 0:
        return

    def _loop() -> None:
        _POI_STATE_READY.wait(timeout=5)
        while True:
            try:
                installed = monitor_poi_for_aem()
            except Exception:
                installed = None

            try:
                snap = _get_poi_state_snapshot()

                # For weekly cache we only need enough to write the current slot
                status = snap.get("status", "offline")
                interval = int(snap.get("interval", interval_seconds))
                mac_registered = snap.get("mac_registered")
                mac_mismatch = snap.get("mac_mismatch")

                # AEM: "data" gate isn’t relevant; pass pod_status=None
                write_week_local(
                    miner_key,
                    now_utc(),
                    status,
                    interval,
                    pod_status=None,
                    mac_registered=mac_registered,
                    mac_mismatch=mac_mismatch,
                    poi_data=installed,
                    gui_version=GUI_VERSION,
                )

                _update_poi_state(last_poi=installed)
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
) -> None:
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

    try:
        coll.update_one(
            {"miner_key": miner_key},
            {
                "$set": {"pol": pol_block},
                "$setOnInsert": base_on_insert,
            },
            upsert=True,
        )
    except Exception:
        pass

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

def main() -> None:
    # Installer handles miner key validation + initial lease acquisition
    _init_service_file_logging()

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
            SOFTWARE_VERSION,
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

    client: Optional[MongoProxyClient] = None
    coll = None

    required_versions: Dict[str, str] = {}

    # miner_mac selected in GUI (persisted to shared data dir)
    miner_mac_local: Optional[str] = read_selected_miner_mac() or None
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
    # Connect + lease + bootstrap doc
    # ----------------------------
    while client is None:
        try:
            client = connect_mongo(api_base, tlsCAFile, cfg)
            coll = client["PoC"]["hardware"]

            # Ensure lease is valid
            if not verify_or_acquire_installation_lease(
                client, miner_key, install_id, lease_seconds=lease_seconds
            ):
                log.error(
                    "Installation lease verification/acquisition failed for %s (install_id: %s)",
                    miner_key,
                    install_id,
                )
                log.error("Cannot start service without valid lease.")
                time.sleep(2)
                sys.exit(9)

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
                try:
                    write_week_local(
                        miner_key,
                        now_utc(),
                        "offline",
                        interval,
                        pod_status=None,
                        mac_registered=mac_registered,
                        mac_mismatch=boot_mismatch,
                        gui_version=GUI_VERSION,
                        skip_slot=True,
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

    # ----------------------------
    # Main monitoring loop
    # ----------------------------
    warned_version = False
    last_location_day: Optional[str] = None
    last_version_check_hour: Optional[dt.datetime] = None
    next_slot_time: Optional[dt.datetime] = None

    while True:
        now_loop = now_utc()
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
                        )
                    except Exception as e:
                        log.error("Measurement upload error: %s", e)
                        pod_slot_ok = False
                else:
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

            status = status_raw
            if MINER_CODE == "AEM":
                status = "online" if (status_raw == "online" and bool(poi_snapshot)) else "offline"

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
                    gui_version=GUI_VERSION,
                )
            except Exception:
                pass

            # ----------------------------
            # DB work
            # ----------------------------
            try:
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

                # AEM: if snapshot missing, fetch PoI
                poi_data = poi_snapshot
                if MINER_CODE == "AEM" and poi_data is None:
                    try:
                        poi_data = monitor_poi_for_aem()
                    except Exception:
                        poi_data = None

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
                )

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

                # Renew lease; reacquire if needed
                try:
                    renewed = renew_installation_lease(client, miner_key, install_id, lease_seconds=lease_seconds)
                    if not renewed:
                        log.warning("Lease renewal failed, attempting to reacquire...")
                        if not verify_or_acquire_installation_lease(
                            client, miner_key, install_id, lease_seconds=lease_seconds, max_retries=5
                        ):
                            log.error("Lost global lease for %s and failed to reacquire; exiting.", miner_key)
                            time.sleep(2)
                            sys.exit(8)
                        log.info("Successfully reacquired lease for %s", miner_key)
                except Exception as e:
                    msg = str(e)
                    is_api_down = any(x in msg.lower() for x in ["502", "503", "504", "bad gateway", "timeout", "connection"])
                    if is_api_down:
                        log.warning("External API unreachable during lease renewal: %s", msg)
                        if not verify_or_acquire_installation_lease(
                            client, miner_key, install_id, lease_seconds=lease_seconds, max_retries=5
                        ):
                            log.error("Failed to reacquire lease for %s; exiting.", miner_key)
                            time.sleep(2)
                            sys.exit(8)
                        log.info("Successfully reacquired lease for %s after API recovery", miner_key)
                    else:
                        log.error("Failed to renew lease for %s: %s; exiting.", miner_key, e)
                        time.sleep(2)
                        sys.exit(8)

                # Once-per-day location check (does NOT touch lastUpdated)
                try:
                    day = day_iso(slot_ts)
                    if last_location_day != day:
                        write_location_daily(coll, client, miner_key, slot_ts)
                        last_location_day = day
                except Exception:
                    pass

                try:
                    api_health_backoff.reset()
                except Exception:
                    pass
            except Exception:
                pass

        except ApiError as e:
            log.error("API error: %s", e)
            try:
                api_health_backoff.record_failure()
                api_health_backoff.wait_for_health(api_base)
            except Exception:
                pass
            try:
                client = connect_mongo(api_base, tlsCAFile, cfg)
                coll = client["PoC"]["hardware"]
                try:
                    api_health_backoff.reset()
                except Exception:
                    pass
            except Exception as e2:
                log.error("Reconnect failed: %s", e2)

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
