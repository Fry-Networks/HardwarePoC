#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import json, os, sys, time, tempfile, logging, re, hashlib, argparse, uuid, platform, pathlib
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

from config_profile import MINER_CODE, VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("miner-online")

DEFAULT_H3_RES = 4
MAX_POC_DAYS = 14

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
    info: dict[str, Any] = {
        "software_version": VERSION,
        "software_needed": None,
        "software_uptodate": None,
    }
    if not isinstance(doc, dict):
        return info
    version_val = doc.get("software_version")
    if isinstance(version_val, str) and version_val:
        info["software_version"] = version_val
    needed_val = doc.get("software_needed")
    if isinstance(needed_val, str) and needed_val:
        info["software_needed"] = needed_val
    uptodate_val = doc.get("software_uptodate")
    if isinstance(uptodate_val, bool):
        info["software_uptodate"] = uptodate_val
    elif isinstance(doc.get("software_outdated"), bool):
        info["software_uptodate"] = not doc.get("software_outdated")
    sw_obj = doc.get("software")
    if isinstance(sw_obj, dict):
        if isinstance(sw_obj.get("software_version"), str) and sw_obj.get("software_version"):
            info["software_version"] = sw_obj["software_version"]
        if isinstance(sw_obj.get("software_needed"), str) and sw_obj.get("software_needed"):
            info["software_needed"] = sw_obj["software_needed"]
        if isinstance(sw_obj.get("software_uptodate"), bool):
            info["software_uptodate"] = sw_obj["software_uptodate"]
        elif isinstance(sw_obj.get("software_outdated"), bool):
            info["software_uptodate"] = not sw_obj["software_outdated"]
    return info

def _extract_poc_map(doc: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    if not isinstance(doc, dict):
        return result
    poc_obj = doc.get("PoC")
    if isinstance(poc_obj, dict):
        for key, value in poc_obj.items():
            date_key = key
            percent_val = value
            if isinstance(value, dict):
                if isinstance(value.get("date"), str) and value.get("date"):
                    date_key = value["date"]
                percent_val = value.get("OnlinePercentDay")
            try:
                if isinstance(date_key, str) and date_key:
                    if isinstance(percent_val, (int, float)):
                        result[date_key] = float(percent_val)
                    elif isinstance(percent_val, str):
                        result[date_key] = float(percent_val)
            except (TypeError, ValueError):
                continue
    return result


def _extract_pol_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(doc, dict):
        return result
    pol_obj = doc.get("PoL")
    if isinstance(pol_obj, dict):
        for key, value in pol_obj.items():
            if isinstance(value, dict):
                entry: dict[str, Any] = {
                    "ip": value.get("ip"),
                    "hexID_registered": value.get("hexID_registered") or value.get("hexId") or value.get("hexId_registered"),
                    "ipCountry": value.get("ipCountry"),
                    "hexCountry": value.get("hexCountry"),
                    "country_match": value.get("country_match") if isinstance(value.get("country_match"), bool) else None,
                }
                if entry["country_match"] is None and isinstance(value.get("sameCountry"), bool):
                    entry["country_match"] = value.get("sameCountry")
                result[str(key)] = entry
    return result

def _compose_hardware_doc(
    miner_key: str,
    *,
    miner_type: Optional[str],
    mac: dict[str, Any],
    software: dict[str, Any],
    poc: dict[str, float],
    pol: dict[str, dict[str, Any]],
    last_updated: dt.datetime,
    poi: Optional[dict] = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    doc["miner_key"] = miner_key
    doc["miner_type"] = (miner_type or MINER_CODE)
    def _format_mac_display(value: Optional[str]) -> str:
        try:
            if not isinstance(value, str) or not value:
                return ""
            # strip non-hex chars
            norm = re.sub(r"[^0-9a-fA-F]", "", value)
            if len(norm) != 12:
                # fallback: return original string unchanged
                return value
            norm = norm.upper()
            return ":".join(norm[i : i + 2] for i in range(0, 12, 2))
        except Exception:
            return value or ""

    doc["mac"] = {
        "miner_mac": _format_mac_display(mac.get("miner_mac") or ""),
        "mac_registered": _format_mac_display(mac.get("mac_registered") or ""),
        "mac_match": bool(mac.get("mac_match")),
    }
    doc["software"] = {
        "software_version": software.get("software_version") or VERSION,
        "software_needed": software.get("software_needed") if isinstance(software.get("software_needed"), str) and software.get("software_needed") else None,
        "software_uptodate": software.get("software_uptodate") if isinstance(software.get("software_uptodate"), bool) else None,
    }
    doc["PoC"] = {str(k): float(v) for k, v in poc.items()}
    pol_compact: dict[str, dict[str, Any]] = {}
    for key in sorted(pol.keys()):
        value = pol[key] or {}
        pol_compact[str(key)] = {
            "ip": value.get("ip"),
            "hexID_registered": value.get("hexID_registered") or value.get("hexId") or value.get("hexId_registered"),
            "ipCountry": value.get("ipCountry"),
            "hexCountry": value.get("hexCountry"),
            "country_match": value.get("country_match") if isinstance(value.get("country_match"), bool) else None,
        }
    doc["PoL"] = pol_compact
    doc["lastUpdated"] = last_updated.isoformat()
    # Add PoI only for AEM miners
    if (doc.get("miner_type") == "AEM" or (miner_type == "AEM")) and poi:
        doc["PoI"] = poi
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
        
        # Get the PoC version from config_profile (same as VERSION for hardware PoC service)
        from config_profile import VERSION as POC_VERSION
        
        payload_set = {
            "miner_key": miner_key,
            "install_id": install_id,
            "minerCode": MINER_CODE,
            "software_version_installed": VERSION,
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
        coll.update_one(
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
        doc = coll.find_one(q, {"_id": 1})
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
                "version_installed": VERSION,
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
        res = coll.update_one(filt, update, upsert=True)
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
        res = coll.update_one(
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



def write_status(coll, miner_key: str, ts: dt.datetime, status: str, interval_seconds: int,
                 software_needed: Optional[str] = None,
                 miner_mac: Optional[str] = None,
                 mac_registered: Optional[str] = None,
                 poi_data: Optional[dict] = None) -> None:
    day = day_iso(ts)
    hour, slot = hour_and_slot(ts, interval_seconds)

    slots_per_hour = max(1, 3600 // max(1, interval_seconds))
    day_total_full = 24 * slots_per_hour

    existing_doc = _get_existing_hardware_doc(coll, miner_key)
    existing_poc = _extract_poc_map(existing_doc)
    existing_pol = _extract_pol_map(existing_doc)
    mac_info = _extract_mac_fields(existing_doc)
    software_info = _extract_software_fields(existing_doc)

    local_doc: dict[str, Any] = {}
    try:
        cache_doc = read_local_cache(cache_path_for(ts))
        if isinstance(cache_doc, dict) and cache_doc.get("date") == day:
            local_doc = cache_doc
    except Exception:
        local_doc = {}

    slots_elapsed = hour * slots_per_hour + (slot + 1)
    day_online_so_far = 0
    local_online = local_doc.get("onlineCountDay") if isinstance(local_doc, dict) else None
    local_total = local_doc.get("totalSlotsDay") if isinstance(local_doc, dict) else None
    if isinstance(local_online, int) and isinstance(local_total, int) and local_total > 0:
        slots_elapsed = max(1, min(day_total_full, local_total))
        day_online_so_far = max(0, min(local_online, slots_elapsed))
    elif isinstance(local_online, int):
        day_online_so_far = max(0, min(local_online, slots_elapsed))

    local_percent = local_doc.get("onlinePercentDay") if isinstance(local_doc, dict) else None
    if isinstance(local_percent, (int, float)):
        day_percent = round(float(local_percent), 1)
    elif slots_elapsed > 0:
        day_percent = round(100.0 * day_online_so_far / max(1, slots_elapsed), 1)
    else:
        day_percent = 0.0

    existing_poc[day] = float(day_percent)
    poc_keys = sorted(existing_poc.keys())[-MAX_POC_DAYS:]
    poc_compact = {key: float(existing_poc[key]) for key in poc_keys}

    if isinstance(miner_mac, str) and miner_mac:
        mac_info["miner_mac"] = miner_mac
    if isinstance(mac_registered, str) and mac_registered:
        mac_info["mac_registered"] = mac_registered

    def _norm_mac(value: Optional[str]) -> str:
        try:
            return re.sub(r"[^0-9a-f]", "", (value or "").lower())
        except Exception:
            return ""

    norm_miner = _norm_mac(mac_info.get("miner_mac"))
    norm_registered = _norm_mac(mac_info.get("mac_registered"))
    mac_info["mac_match"] = bool(norm_miner and norm_registered and norm_miner == norm_registered)

    needed_value = software_needed.strip() if isinstance(software_needed, str) and software_needed.strip() else software_info.get("software_needed")
    software_info["software_version"] = VERSION
    software_info["software_needed"] = needed_value
    if isinstance(needed_value, str) and needed_value:
        try:
            software_info["software_uptodate"] = (cmp_ver(VERSION, needed_value) >= 0)
        except Exception:
            software_info["software_uptodate"] = None
    else:
        if not isinstance(software_info.get("software_uptodate"), bool):
            software_info["software_uptodate"] = None

    # For AEM miners, include PoI data if provided
    miner_type_val = existing_doc.get("miner_type") if isinstance(existing_doc.get("miner_type"), str) else MINER_CODE
    
    new_doc = _compose_hardware_doc(
        miner_key,
        miner_type=miner_type_val,
        mac=mac_info,
        software=software_info,
        poc=poc_compact,
        pol=existing_pol,
        last_updated=now_utc(),
        poi=poi_data if (miner_type_val == "AEM" and poi_data) else None,
    )

    try:
        coll.replace_one({"miner_key": miner_key}, new_doc, upsert=True)
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

def _lc_write_slot(doc: dict, ts: dt.datetime, status: str, interval_seconds: int):
    hour, slot = hour_and_slot(ts, interval_seconds)
    h = doc["hours"].get(str(hour))
    slots_per_hour = max(1, 3600 // max(1, interval_seconds))
    if not isinstance(h, dict) or not isinstance(h.get("slots"), list) or len(h["slots"]) < slots_per_hour:
        h = {"slots": ["offline"] * slots_per_hour, "onlineCount": 0, "totalSlots": slots_per_hour}
    h["slots"][slot] = status
    h["onlineCount"] = sum(1 for s in h["slots"] if s == "online")
    h["totalSlots"] = slots_per_hour
    doc["hours"][str(hour)] = h

def write_status_local(miner_key: str, ts: dt.datetime, status: str, interval_seconds: int, mac_registered: Optional[str] = None, mac_mismatch: Optional[bool] = None):
    ensure_dirs()
    cur_day_path = cache_path_for(ts)
    cur_lock_path = cache_lock_path_for(ts)
    day_iso = ts.strftime("%Y-%m-%d")
    with _CacheLock(cur_lock_path):
        doc = read_local_cache(cur_day_path)
    if not doc or doc.get("date") != day_iso:
        doc = {
            "miner_key": miner_key,
            "minerCode": MINER_CODE,
            "agentVersion": VERSION,
            "date": day_iso,
            "hours": {},
            "lastSlotWritten": None
        }

    def floor_slot(x: dt.datetime) -> dt.datetime:
        # Floor to interval boundary
        secs = x.hour*3600 + x.minute*60 + x.second
        floored = (secs // max(1, interval_seconds)) * max(1, interval_seconds)
        return x.replace(hour=0, minute=0, second=0) + dt.timedelta(seconds=floored)

    cur_slot = floor_slot(ts)
    last_str = doc.get("lastSlotWritten")
    last_ts = None
    if last_str:
        try:
            last_ts = dt.datetime.strptime(last_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except Exception: last_ts = None

    if last_ts and last_ts.date() != ts.date():
        last_day_path = cache_path_for(last_ts)
        last_lock_path = cache_lock_path_for(last_ts)
        last_day_iso = last_ts.strftime("%Y-%m-%d")
        with _CacheLock(last_lock_path):
            last_doc = read_local_cache(last_day_path) or {
                "miner_key": miner_key,
                "minerCode": MINER_CODE,
                "agentVersion": VERSION,
                "date": last_day_iso,
                "hours": {},
                "lastSlotWritten": None
            }
            t = floor_slot(last_ts) + dt.timedelta(seconds=max(1, interval_seconds))
            # last slot start of that day
            start_of_day = dt.datetime(last_ts.year, last_ts.month, last_ts.day, tzinfo=UTC)
            end = start_of_day + dt.timedelta(days=1) - dt.timedelta(seconds=max(1, interval_seconds))
            while t <= end:
                _lc_write_slot(last_doc, t, "offline", interval_seconds)
                t += dt.timedelta(seconds=max(1, interval_seconds))
            last_doc["lastUpdated"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
            last_doc["lastSlotWritten"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
            # Attach signature and write
            try:
                sig = compute_cache_signature(last_doc)
                if isinstance(sig, str):
                    last_doc["sig"] = sig
            except Exception:
                pass
            atomic_write_json(last_day_path, last_doc)
        last_ts = None

    if last_ts:
        t = floor_slot(last_ts) + dt.timedelta(seconds=max(1, interval_seconds))
        while t < cur_slot and t.date() == ts.date():
            _lc_write_slot(doc, t, "offline", interval_seconds)
            t += dt.timedelta(seconds=max(1, interval_seconds))

    _lc_write_slot(doc, cur_slot, status, interval_seconds)
    # Ensure all 24 hours exist and compute day totals
    slots_per_hour = max(1, 3600 // max(1, interval_seconds))
    OFFLINE_N = ["offline"] * slots_per_hour
    day_total_full = 24 * slots_per_hour
    day_online = 0
    hourly_online_counts: list[int] = []
    for h in range(24):
        key = str(h)
        hdoc = doc["hours"].get(key)
        if not isinstance(hdoc, dict):
            hdoc = {"slots": OFFLINE_N.copy(), "onlineCount": 0, "totalSlots": slots_per_hour}
        slots = hdoc.get("slots") or []
        if len(slots) < slots_per_hour:
            slots = (slots + OFFLINE_N)[:slots_per_hour]
        online_count = sum(1 for s in slots if s == "online")
        hdoc["slots"] = slots
        hdoc["onlineCount"] = online_count
        hdoc["totalSlots"] = slots_per_hour
        doc["hours"][key] = hdoc
        day_online += online_count
        hourly_online_counts.append(online_count)

    hour, slot = hour_and_slot(ts, interval_seconds)
    slots_elapsed = max(1, min(day_total_full, hour * slots_per_hour + (slot + 1)))
    completed_online = sum(hourly_online_counts[:hour])
    current_hour_online = hourly_online_counts[hour] if hour < len(hourly_online_counts) else 0
    day_online_so_far = completed_online + max(0, min(current_hour_online, slot + 1))
    doc["onlineCountDay"] = day_online_so_far
    doc["totalSlotsDay"] = slots_elapsed
    doc["onlinePercentDay"] = round(100.0 * day_online_so_far / max(1, slots_elapsed), 1)

    if isinstance(mac_registered, str) and mac_registered.strip():
        doc["mac_registered"] = mac_registered.strip()
    else:
        doc["mac_registered"] = doc.get("mac_registered", "")
    if mac_mismatch is not None:
        doc["mac_mismatch"] = bool(mac_mismatch)
    else:
        doc["mac_mismatch"] = bool(doc.get("mac_mismatch", False))

    doc["lastUpdated"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["lastSlotWritten"] = cur_slot.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Sign the payload if a signing key is configured
    try:
        sig = compute_cache_signature(doc)
        if isinstance(sig, str):
            doc["sig"] = sig
    except Exception:
        pass
    with _CacheLock(cur_lock_path):
        atomic_write_json(cur_day_path, doc)



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
        doc = client["main"]["devices"].find_one({"miner_key": miner_key}, {"hexId": 1, "_id": 0})
        if isinstance(doc, dict):
            v = doc.get("hexId")
            if isinstance(v, str) and v:
                return v
    except Exception:
        pass
    return None





def write_location_daily(coll, client: MongoProxyClient, miner_key: str, ts: dt.datetime):
    """Record a daily country consistency proof keyed by day."""
    area_threshold = COUNTRY_AREA_THRESHOLD
    hex7 = registered_hexid_from_devices(client, miner_key)
    try:
        country_check = check_country_once(hex7, area_threshold=area_threshold)
    except Exception:
        country_check = {}
    if not isinstance(country_check, dict):
        country_check = {}

    checked_at = now_utc()
    day = day_iso(ts)

    ip_value = country_check.get("ip") if isinstance(country_check.get("ip"), str) else None
    ip_country = country_check.get("ip_country") if isinstance(country_check.get("ip_country"), str) else None
    hex_country = country_check.get("h3_country") if isinstance(country_check.get("h3_country"), str) else None
    same_country_val = country_check.get("same_country") if isinstance(country_check.get("same_country"), bool) else None

    pol_entry: dict[str, Any] = {
        "ip": ip_value,
        "hexID_registered": hex7 if isinstance(hex7, str) and hex7 else None,
        "ipCountry": ip_country,
        "hexCountry": hex_country,
        "country_match": same_country_val,
    }

    existing_doc = _get_existing_hardware_doc(coll, miner_key)
    mac_info = _extract_mac_fields(existing_doc)
    software_info = _extract_software_fields(existing_doc)
    poc_map = _extract_poc_map(existing_doc)
    pol_map = _extract_pol_map(existing_doc)

    pol_map[day] = pol_entry
    pol_keys = sorted(pol_map.keys())[-MAX_POC_DAYS:]
    pol_compact = {key: pol_map[key] for key in pol_keys}

    new_doc = _compose_hardware_doc(
        miner_key,
        miner_type=existing_doc.get("miner_type") if isinstance(existing_doc.get("miner_type"), str) else MINER_CODE,
        mac=mac_info,
        software=software_info,
        poc=poc_map,
        pol=pol_compact,
        last_updated=checked_at,
    )

    try:
        coll.replace_one({"miner_key": miner_key}, new_doc, upsert=True)
    except Exception:
        pass

    try:
        coll.delete_many({"miner_key": miner_key, "date": {"$exists": True}})
    except Exception:
        pass

def registered_mac_from_devices(client: MongoProxyClient, miner_key: str) -> Optional[str]:
    """Return the registered MAC for a miner_key from creds.hardware.miner_mac.
    Returns None if not present or on error."""
    try:
        dev = client["creds"]["hardware"].find_one(
            {"miner_key": miner_key}, {"miner_mac": 1, "_id": 0}
        )
        if isinstance(dev, dict):
            v = dev.get("miner_mac")
            if isinstance(v, str):
                v = v.strip()
                return v or None
    except Exception:
        pass
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

def main():
    # Remove check-only modes since installer handles miner key validation and initial lease acquisition

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
    
    # Validate that we have embedded credentials from 1Password build process
    if not cfg.get("api_token") and not cfg.get("api_key"):
        log.error("No API token found. This executable was not built with embedded credentials.")
        log.error("Build process required: python build_with_embedded_config.py <1password_config> <output_dir>")
        log.error("The config must use 1Password reference: op://VPS/Hardware_API/API_BEARER_TOKEN")
        sys.exit(5)

    tlsCAFile = cfg.get("tlsCAFile")

    interval = int(cfg.get("interval_seconds", 600))

    lease_seconds = int(cfg.get("lease_seconds", 900))

    try:
        cfg_path = os.path.join(app_dir(), "config.json")
        cfg_src = cfg_path if os.path.exists(cfg_path) else "(embedded/default)"
        log.info(
            "Starting monitoring service v%s | miner_key=%s | ProgramData=%s | config=%s | api_base=%s | interval=%s | lease_seconds=%s",
            VERSION,
            miner_key,
            data_dir(),
            cfg_src,
            (api_base or "-"),
            interval,
            lease_seconds,
        )
    except Exception:
        pass

    client: Optional[MongoProxyClient] = None
    coll = None
    
    # Read install_id from installer-created config (REQUIRED)
    install_id = read_encrypted_install_config()
    if not install_id:
        log.error("install_config.enc not found or invalid")
        log.error("The installer must create install_config.enc before starting the service")
        time.sleep(2)
        sys.exit(3)  # Exit code 3: not found
    
    required_versions: Dict[str, str] = {}
    # miner_mac selected in GUI (persisted to shared data dir)
    miner_mac_local: Optional[str] = read_selected_miner_mac() or None
    mac_registered: Optional[str] = None
    while client is None:
        try:
            client = connect_mongo(api_base, tlsCAFile, cfg)
            coll = client["PoC"]["hardware"]
            
            # Verify that we hold the lease for this miner_key (or acquire if API was down during install)
            if not verify_or_acquire_installation_lease(client, miner_key, install_id, lease_seconds=lease_seconds):
                log.error("Installation lease verification/acquisition failed for %s (install_id: %s)", miner_key, install_id)
                log.error("Cannot start service without valid lease. Check that:")
                log.error("  1. The External API is reachable")
                log.error("  2. No other instance is running for this miner_key")
                time.sleep(2)
                sys.exit(9)  # Exit code 9: lease verification failed
            # Update this installation record (per-machine)
            upsert_installation_record(client, miner_key, install_id)
            # Check required versions and warn (do not exit; backend can enforce rewards policy)
            try:
                required_versions = required_versions_from_db(client)
                software_required = required_versions.get("software_version")
                poc_required = required_versions.get("poc_version")
                
                # Check if software version is outdated
                is_software_outdated = False
                if isinstance(software_required, str) and cmp_ver(VERSION, software_required) < 0:
                    log.warning("Service version %s is below required %s; continuing (rewards may be paused)", VERSION, software_required)
                    is_software_outdated = True
                
                # Check if PoC version is outdated (using same VERSION since hardware PoC service version = PoC version)
                is_poc_outdated = False
                if isinstance(poc_required, str) and cmp_ver(VERSION, poc_required) < 0:
                    log.warning("PoC version %s is below required %s; continuing (rewards may be paused)", VERSION, poc_required)
                    is_poc_outdated = True
                
                is_outdated = is_software_outdated or is_poc_outdated
                
                # Update installations with version requirements and status
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
                # Also capture the registered MAC (best-effort)
                try:
                    mac_registered = registered_mac_from_devices(client, miner_key)
                except Exception:
                    mac_registered = mac_registered or None
                # Populate hardware document immediately so UI shows registered values
                try:
                    hex_registered = None
                    try:
                        hex_registered = registered_hexid_from_devices(client, miner_key)
                    except Exception:
                        hex_registered = None
                    try:
                        existing_doc = _get_existing_hardware_doc(coll, miner_key)
                        mac_info = _extract_mac_fields(existing_doc)
                        software_info = _extract_software_fields(existing_doc)
                        poc_map = _extract_poc_map(existing_doc)
                        pol_map = _extract_pol_map(existing_doc)
                        # update registered values if present
                        # prefer the local selected miner MAC when available so the UI can show a match immediately
                        if isinstance(miner_mac_local, str) and miner_mac_local:
                            mac_info["miner_mac"] = miner_mac_local
                        if isinstance(mac_registered, str) and mac_registered:
                            mac_info["mac_registered"] = mac_registered
                        # compute mac_match using the same normalization rules as write_status
                        try:
                            def _norm_mac_start(v: Optional[str]) -> str:
                                try:
                                    return re.sub(r"[^0-9a-f]", "", (v or "").lower())
                                except Exception:
                                    return ""
                            norm_miner_start = _norm_mac_start(mac_info.get("miner_mac"))
                            norm_registered_start = _norm_mac_start(mac_info.get("mac_registered"))
                            mac_info["mac_match"] = bool(norm_miner_start and norm_registered_start and norm_miner_start == norm_registered_start)
                        except Exception:
                            pass
                        day = day_iso(now_utc())
                        pol_entry = pol_map.get(day) or {}
                        if isinstance(hex_registered, str) and hex_registered:
                            pol_entry["hexID_registered"] = hex_registered
                        pol_map[day] = pol_entry
                        # Ensure software requirement fields are present immediately so the UI shows them
                        try:
                            software_required = required_versions.get("software_version")
                            if isinstance(software_required, str) and software_required:
                                software_info["software_needed"] = software_required
                                try:
                                    software_info["software_uptodate"] = (cmp_ver(VERSION, software_required) >= 0)
                                except Exception:
                                    software_info["software_uptodate"] = None
                            else:
                                if not isinstance(software_info.get("software_uptodate"), bool):
                                    software_info["software_uptodate"] = None
                        except Exception:
                            pass

                        # For AEM miners, add PoI (Proof of Installed)
                        miner_type_val = existing_doc.get("miner_type") if isinstance(existing_doc.get("miner_type"), str) else MINER_CODE
                        poi_data = None
                        if miner_type_val == "AEM":
                            poi_data = monitor_poi_for_aem()
                        new_doc = _compose_hardware_doc(
                            miner_key,
                            miner_type=miner_type_val,
                            mac=mac_info,
                            software=software_info,
                            poc=poc_map,
                            pol=pol_map,
                            last_updated=now_utc(),
                            poi=poi_data,
                        )
                        try:
                            coll.replace_one({"miner_key": miner_key}, new_doc, upsert=True)
                        except Exception:
                            pass
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                # If we cannot read requirement, proceed; next loop will try again
                pass
        except Exception as e:
            log.error("Initial API connect failed: %s", e)
            time.sleep(5)

    first_wake = next_boundary(now_utc(), interval)
    time.sleep(max(1, int((first_wake - now_utc()).total_seconds())))

    warned_version = False
    last_location_day: Optional[str] = None
    last_version_check_hour: Optional[dt.datetime] = None
    while True:
        slot_ts = now_utc()
        # Determine online status first
        status = "online" if is_internet_up(timeout=4) else "offline"
        # Always write the local rolling 24h cache, regardless of DB connectivity
        try:
            norm_active = re.sub(r'[^0-9a-f]', '', (miner_mac_local or '').lower())
            norm_registered = re.sub(r'[^0-9a-f]', '', (mac_registered or '').lower())
            local_mismatch = bool(norm_active and norm_registered and norm_active != norm_registered)
            write_status_local(miner_key, slot_ts, status, interval, mac_registered=mac_registered, mac_mismatch=local_mismatch)
        except Exception:
            pass
        try:
            # Refresh required versions at most once per UTC hour
            try:
                hour_start_for_check = slot_ts.replace(minute=0, second=0, microsecond=0)
                if (last_version_check_hour is None) or (hour_start_for_check > last_version_check_hour):
                    required_versions = required_versions_from_db(client)
                    last_version_check_hour = hour_start_for_check
            except Exception:
                pass
            
            # Check if either software or PoC version is outdated
            software_required = required_versions.get("software_version")
            poc_required = required_versions.get("poc_version")
            outdated: bool = False
            
            try:
                is_software_outdated = isinstance(software_required, str) and software_required and (cmp_ver(VERSION, software_required) < 0)
                is_poc_outdated = isinstance(poc_required, str) and poc_required and (cmp_ver(VERSION, poc_required) < 0)
                outdated = bool(is_software_outdated or is_poc_outdated)
            except Exception:
                outdated = False
            
            # Warn the user once if this install is behind the required versions
            try:
                if outdated and not warned_version:
                    if isinstance(software_required, str) and software_required and cmp_ver(VERSION, software_required) < 0:
                        log.warning("Software version %s is below required %s; please update.", VERSION, software_required)
                    if isinstance(poc_required, str) and poc_required and cmp_ver(VERSION, poc_required) < 0:
                        log.warning("PoC version %s is below required %s; please update.", VERSION, poc_required)
                    warned_version = True
            except Exception:
                pass
            # Refresh miner_mac from disk (if GUI selection changed)
            try:
                mm = read_selected_miner_mac()
                if mm:
                    miner_mac_local = mm
                else:
                    # fallback to auto-detection when user hasn't selected a MAC
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
            
            # For AEM miners, get PoI data
            poi_data = None
            if MINER_CODE == "AEM":
                poi_data = monitor_poi_for_aem()
            
            # Write to DB (day/hour aggregates)
            write_status(coll, miner_key, slot_ts, status, interval,
                         software_needed=software_required,
                         miner_mac=miner_mac_local,
                         mac_registered=mac_registered,
                         poi_data=poi_data)
            # Refresh installation heartbeat each cycle (and mark version state)
            try:
                has_requirements = software_required or poc_required
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
            
            # Upload measurements to External API (if registered hexId exists)
            try:
                # Get registered hexId for this miner
                hex_registered = registered_hexid_from_devices(client, miner_key)
                
                if hex_registered:
                    # Read and decrypt measurement files
                    measurement_data_list = read_measurement_files()
                    
                    # Upload each measurement
                    for measurement_data in measurement_data_list:
                        try:
                            timestamp = measurement_data.get("timestamp")
                            measurements = measurement_data.get("measurement", {})
                            
                            if timestamp and measurements:
                                # Upload to External API indexed by hexId
                                client._api.upload_measurements(
                                    hex_id=hex_registered,
                                    miner_code=MINER_CODE,
                                    install_id=install_id,
                                    timestamp=timestamp,
                                    measurements=measurements
                                )
                        except Exception as e:
                            # Log but don't fail - continue with other measurements
                            log.error("Failed to upload measurement: %s", e)
            except Exception as e:
                # Don't fail the main loop if measurement upload fails
                log.error("Measurement upload error: %s", e)
            
            # Renew our lease; if renewal fails, try to reacquire (API may have been down)
            try:
                renewed = renew_installation_lease(client, miner_key, install_id, lease_seconds=lease_seconds)
                if not renewed:
                    log.warning("Lease renewal failed, attempting to reacquire...")
                    # Try to reacquire the lease (handles case where API was down and lease expired)
                    if not verify_or_acquire_installation_lease(client, miner_key, install_id, lease_seconds=lease_seconds, max_retries=5):
                        log.error("Lost global lease for %s and failed to reacquire; exiting.", miner_key)
                        time.sleep(2)
                        sys.exit(8)
                    else:
                        log.info("Successfully reacquired lease for %s", miner_key)
            except Exception as e:
                # API error during renewal - try to reacquire
                error_msg = str(e)
                is_api_down = any(x in error_msg.lower() for x in ["502", "503", "504", "bad gateway", "timeout", "connection"])
                
                if is_api_down:
                    log.warning("External API unreachable during lease renewal: %s", error_msg)
                    log.info("Will retry lease acquisition...")
                    if not verify_or_acquire_installation_lease(client, miner_key, install_id, lease_seconds=lease_seconds, max_retries=5):
                        log.error("Failed to reacquire lease for %s; exiting.", miner_key)
                        time.sleep(2)
                        sys.exit(8)
                    else:
                        log.info("Successfully reacquired lease for %s after API recovery", miner_key)
                else:
                    log.error("Failed to renew lease for %s: %s; exiting.", miner_key, e)
                    time.sleep(2)
                    sys.exit(8)

            # Record daily location once per day (res4 via IP) with hexId(res7) containment check
            try:
                day = day_iso(slot_ts)
                if last_location_day != day:
                    # finalize yesterday's day TS for this miner
                    write_location_daily(coll, client, miner_key, slot_ts)
                    last_location_day = day
            except Exception:
                pass

        except ApiError as e:
            log.error("API error: %s", e)
            try:
                client = connect_mongo(api_base, tlsCAFile, cfg)
                coll = client["PoC"]["hardware"]
            except Exception as e2:
                log.error("Reconnect failed: %s", e2)
        except Exception as e:
            log.error("Iteration failed: %s", e)

        next_wake = next_boundary(slot_ts, interval)
        while next_wake <= now_utc():
            next_wake += dt.timedelta(seconds=interval)
        time.sleep(int((next_wake - now_utc()).total_seconds()))
    # exit after loop (conflict)
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
