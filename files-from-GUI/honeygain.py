"""Honeygain SDK integration helpers for FryNetworks miners."""

from __future__ import annotations

import base64
import time
import ctypes
import json
import os
import platform
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from miner_GUI.config import app_dir
from miner_GUI.utils.data import data_dir_gui, log_step

CONFIG_FILE_NAME = "honeygain.json"
SECRET_FILE_NAME = "honeygain.enc"
DEFAULT_SECRET_VERSION = "1.0"
DEFAULT_POLL_SECONDS = 60
SECRET_PASSWORD = "honeygain_config_encryption_key_v1"
SECRET_SALT = b"honeygain_config_salt_v1"
SECRET_ENV_PATH = "HONEYGAIN_SECRET_PATH"
SECRET_ENV_DIR = "HONEYGAIN_SECRET_DIR"

class HoneygainError(RuntimeError):
    """Base error for Honeygain helpers."""


class HoneygainConfigError(HoneygainError):
    """Raised when configuration cannot be loaded or saved."""


class HoneygainSDKError(HoneygainError):
    """Raised when the native SDK returns an error code."""

    def __init__(self, message: str, code: Optional[int] = None):
        if code is not None:
            super().__init__(f"{message} (code {code})")
        else:
            super().__init__(message)
        self.code = code


@dataclass
class HoneygainStatus:
    """Represents the most recent Honeygain runtime state."""

    configured: bool
    enabled: bool
    sdk_loaded: bool
    api_key_present: bool
    running: Optional[bool]
    opted_in: Optional[bool]
    device_id: Optional[str]
    session_id: Optional[str]
    traffic_total_bytes: Optional[int]
    traffic_24h_bytes: Optional[int]
    last_error: Optional[str]
    last_refresh: datetime
    identify_raw: Dict[str, Any] = field(default_factory=dict)
    library_path: Optional[Path] = None
    config_path: Optional[Path] = None
    secret_path: Optional[Path] = None


class HoneygainConfig:
    """Configuration wrapper backed by config/honeygain.json + encrypted honeygain.enc."""

    def __init__(
        self,
        path: Path,
        existed: bool,
        raw: Optional[Dict[str, Any]] = None,
        secret_path: Optional[Path] = None,
        secret_data: Optional[Dict[str, Any]] = None,
        secret_existed: bool = False,
    ):
        self.path = path
        self._raw = raw or {}
        self._existed = existed
        self.secret_path = secret_path
        self._secret_existed = secret_existed and bool(secret_data)
        self._api_key = ""
        if secret_data:
            self._api_key = str(secret_data.get("api_key") or "")
        # Strip legacy plaintext api_key if present to avoid re-writing it
        self._legacy_api_key = str(self._raw.pop("api_key", "") or "")
        if self._legacy_api_key:
            log_step(
                "honeygain_plain_api_key_detected",
                {"config_path": str(self.path)},
            )

    @property
    def existed(self) -> bool:
        return self._existed

    @property
    def secret_existed(self) -> bool:
        return self._secret_existed

    @property
    def enabled(self) -> bool:
        return bool(self._raw.get("enabled", False))

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._raw["enabled"] = bool(value)

    @property
    def api_key(self) -> str:
        if self._api_key:
            return self._api_key
        if self._legacy_api_key:
            return self._legacy_api_key
        return ""

    @property
    def sdk_root(self) -> Optional[Path]:
        value = self._raw.get("sdk_root") or self._raw.get("sdk_dir")
        return Path(str(value)) if value else None

    @property
    def library_path(self) -> Optional[Path]:
        value = self._raw.get("library_path") or self._raw.get("lib")
        return Path(str(value)) if value else None

    @property
    def log_dir(self) -> Optional[Path]:
        value = self._raw.get("log_dir")
        return Path(str(value)) if value else None

    @property
    def poll_seconds(self) -> int:
        raw = (
            self._raw.get("poll_seconds")
            or self._raw.get("poll_interval_seconds")
            or self._raw.get("poll_interval")
        )
        if raw is None:
            return DEFAULT_POLL_SECONDS
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_POLL_SECONDS
        return max(5, val)

    def persist(self) -> None:
        """Write configuration back to disk."""
        try:
            from miner_GUI.utils.data import data_dir_gui
            from miner_GUI.utils.ops_queue_client import enqueue_write_config
            
            content = json.dumps(self._raw, indent=2, sort_keys=True)
            
            # If file is in ProgramData, enqueue for service to write
            try:
                base_dir = data_dir_gui()
                rel_path = str(self.path.relative_to(base_dir))
                success, msg = enqueue_write_config(rel_path, content)
                if not success:
                    raise HoneygainConfigError(f"Failed to enqueue Honeygain config: {msg}")
            except (ValueError, OSError):
                # If path is not in ProgramData (e.g., development), write directly
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(self._raw, handle, indent=2, sort_keys=True)
        except OSError as exc:
            raise HoneygainConfigError(f"Failed to save Honeygain config: {exc}") from exc


def _unique_paths(items: Iterable[Path]) -> List[Path]:
    """Deduplicate while preserving order."""
    seen: set[str] = set()
    result: List[Path] = []
    for item in items:
        if not item:
            continue
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _candidate_config_dirs() -> List[Path]:
    """Return preferred directories for Honeygain config/secrets."""
    candidates: List[Path] = []

    env_dir = os.environ.get("HONEYGAIN_CONFIG_DIR")
    if env_dir:
        candidates.append(Path(env_dir))

    try:
        candidates.append(data_dir_gui() / "config")
    except Exception:
        pass

    candidates.append(app_dir() / "config")
    candidates.append(Path.cwd() / "config")
    return _unique_paths(candidates)


def locate_config_file() -> Tuple[Path, bool, Dict[str, Any], List[Path]]:
    """Locate the Honeygain config file (falling back to default path if missing)."""
    env_path = os.environ.get("HONEYGAIN_CONFIG_PATH")
    candidate_dirs = _candidate_config_dirs()
    if env_path:
        path = Path(env_path)
        data = _load_json(path)
        return path, path.exists(), data, candidate_dirs

    candidate_files = [p / CONFIG_FILE_NAME for p in candidate_dirs if p]

    for file_path in candidate_files:
        if file_path.exists():
            data = _load_json(file_path)
            return file_path, True, data, candidate_dirs

    # Default to the first candidate directory (or cwd/config if list is empty)
    if candidate_files:
        fallback = candidate_files[0]
    else:
        fallback_dir = Path.cwd() / "config"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback = fallback_dir / CONFIG_FILE_NAME
    return fallback, fallback.exists(), {}, candidate_dirs


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        raise HoneygainConfigError(f"Invalid Honeygain config at {path}: {exc}") from exc


def _load_secret_blob(path: Path) -> Dict[str, Any]:
    """Load and decrypt the Honeygain secret blob."""
    if not path.exists():
        return {}
    raw = _load_json(path)
    if not raw:
        return {}
    return _decrypt_secret_blob(raw, path)


def _derive_secret_key() -> bytes:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise HoneygainConfigError(
            "Cryptography library not available for Honeygain secret decryption"
        ) from exc
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SECRET_SALT,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(SECRET_PASSWORD.encode()))


def _decrypt_secret_blob(blob: Dict[str, Any], path: Path) -> Dict[str, Any]:
    """Decrypt the Honeygain secret blob containing sensitive fields."""
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise HoneygainConfigError(
            "Cryptography library not available for Honeygain secret decryption"
        ) from exc
    key = _derive_secret_key()
    payload = blob.get("data")
    if not isinstance(payload, str):
        raise HoneygainConfigError(f"Malformed Honeygain secret at {path}")
    fernet = Fernet(key)
    try:
        decrypted = fernet.decrypt(payload.encode())
        text = decrypted.decode("utf-8", errors="replace").strip()
        # Preferred format: JSON object with api_key
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            # Fallback: installer-encrypted raw API key string
            if text:
                return {"api_key": text, "config_version": DEFAULT_SECRET_VERSION}
        raise HoneygainConfigError("Decrypted secret is not valid JSON or string")
    except Exception as exc:
        raise HoneygainConfigError(f"Failed to decrypt Honeygain secret at {path}: {exc}") from exc


def locate_secret_file(candidate_dirs: Sequence[Path]) -> Tuple[Optional[Path], bool, Dict[str, Any]]:
    """Locate the encrypted Honeygain secret file containing the API key."""
    env_path = os.environ.get(SECRET_ENV_PATH)
    if env_path:
        path = Path(env_path)
        data = _load_secret_blob(path)
        return path, path.exists(), data

    candidates: List[Path] = []
    env_dir = os.environ.get(SECRET_ENV_DIR)
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(candidate_dirs or [])
    candidates = _unique_paths(candidates)

    candidate_files = [p / SECRET_FILE_NAME for p in candidates if p]
    for file_path in candidate_files:
        if file_path.exists():
            data = _load_secret_blob(file_path)
            return file_path, True, data

    fallback = candidate_files[0] if candidate_files else (Path.cwd() / "config" / SECRET_FILE_NAME)
    return fallback, fallback.exists(), {}


def load_config() -> HoneygainConfig:
    """Load Honeygain configuration."""
    path, existed, data, candidate_dirs = locate_config_file()
    secret_path, secret_existed, secret_data = locate_secret_file(candidate_dirs)
    return HoneygainConfig(
        path=path,
        existed=existed,
        raw=data,
        secret_path=secret_path,
        secret_data=secret_data,
        secret_existed=secret_existed,
    )


class HoneygainSDK:
    """Thin ctypes wrapper around libhgsdk/hgsdk.dll."""

    def __init__(self, config: HoneygainConfig):
        self._config = config
        lib_path = self._resolve_library_path()
        if lib_path is None:
            raise HoneygainSDKError("Honeygain SDK library not found")
        self._lib_path: Path = lib_path
        self._lib = self._load_library(self._lib_path)
        self._configure_prototypes()

    @property
    def library_path(self) -> Path:
        return self._lib_path

    def _resolve_library_path(self) -> Optional[Path]:
        env_path = os.environ.get("HONEYGAIN_SDK_LIB")
        if env_path:
            lib_path = Path(env_path)
            if lib_path.exists():
                return lib_path

        if self._config.library_path and self._config.library_path.exists():
            return self._config.library_path

        bases: List[Path] = []
        if self._config.sdk_root:
            bases.append(self._config.sdk_root)
        bases.append(app_dir() / "SDK")
        bases.append(Path.cwd() / "SDK")
        bases = _unique_paths(bases)

        candidates: List[Path] = []
        if sys.platform.startswith("win"):
            arch = platform.machine().lower()
            arch_order: Sequence[str]
            if arch in ("amd64", "x86_64"):
                arch_order = ("x64", "x86")
            elif arch in ("x86", "i386", "i686"):
                arch_order = ("x86",)
            else:
                arch_order = ("x64", "x86")
            for base in bases:
                root = base / "windows-honeygain-sdk"
                for part in arch_order:
                    candidates.append(root / part / "bin" / "hgsdk.dll")
                    candidates.append(root / part / "hgsdk.dll")
                candidates.append(base / "hgsdk.dll")
        else:
            arch = platform.machine().lower()
            variant_order: Sequence[str]
            if arch in ("x86_64", "amd64"):
                variant_order = ("x64-glibc", "x64-musl")
            elif arch in ("aarch64", "arm64"):
                variant_order = ("arm64-glibc", "arm64-musl")
            elif arch in ("armv7l", "armv7"):
                variant_order = ("armv7-glibc", "armv7-musl")
            elif arch in ("i386", "i686", "x86"):
                variant_order = ("x86-glibc", "x86-musl")
            else:
                variant_order = ("x64-glibc", "x64-musl")

            for base in bases:
                root = base / "linux-honeygain-sdk"
                for variant in variant_order:
                    candidates.append(root / variant / "lib" / "libhgsdk.so")
                    candidates.append(root / variant / "libhgsdk.so")
                candidates.append(base / "libhgsdk.so")

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _load_library(self, path: Path):
        try:
            if sys.platform.startswith("win"):
                return ctypes.WinDLL(str(path))
            return ctypes.CDLL(str(path))
        except OSError as exc:
            raise HoneygainSDKError(f"Could not load Honeygain SDK at {path}: {exc}") from exc

    def _configure_prototypes(self) -> None:
        c_int_ptr = ctypes.POINTER(ctypes.c_int32)
        c_size_ptr = ctypes.POINTER(ctypes.c_size_t)

        self._lib.hgsdk_start.argtypes = [ctypes.c_char_p, c_int_ptr]
        self._lib.hgsdk_start.restype = ctypes.c_int32
        self._lib.hgsdk_stop.argtypes = []
        self._lib.hgsdk_stop.restype = ctypes.c_int32
        self._lib.hgsdk_is_running.argtypes = [c_int_ptr]
        self._lib.hgsdk_is_running.restype = ctypes.c_int32
        self._lib.hgsdk_opt_in.argtypes = []
        self._lib.hgsdk_opt_in.restype = ctypes.c_int32
        self._lib.hgsdk_opt_out.argtypes = []
        self._lib.hgsdk_opt_out.restype = ctypes.c_int32
        self._lib.hgsdk_is_opted_in.argtypes = [c_int_ptr]
        self._lib.hgsdk_is_opted_in.restype = ctypes.c_int32
        if hasattr(self._lib, "hgsdk_identify"):
            self._lib.hgsdk_identify.argtypes = [ctypes.c_char_p, c_size_ptr]
            self._lib.hgsdk_identify.restype = ctypes.c_int32
        if hasattr(self._lib, "hgsdk_log"):
            self._lib.hgsdk_log.argtypes = [ctypes.c_char_p]
            self._lib.hgsdk_log.restype = ctypes.c_int32
        if hasattr(self._lib, "hgsdk_mute"):
            self._lib.hgsdk_mute.argtypes = []
            self._lib.hgsdk_mute.restype = ctypes.c_int32

    def start(self, api_key: str) -> bool:
        consent = ctypes.c_int32()
        rc = self._lib.hgsdk_start(api_key.encode("utf-8"), ctypes.byref(consent))
        if rc < 0:
            raise HoneygainSDKError("hgsdk_start failed", rc)
        return bool(consent.value)

    def stop(self) -> None:
        rc = self._lib.hgsdk_stop()
        if rc < 0:
            raise HoneygainSDKError("hgsdk_stop failed", rc)

    def is_running(self) -> bool:
        state = ctypes.c_int32()
        rc = self._lib.hgsdk_is_running(ctypes.byref(state))
        if rc < 0:
            raise HoneygainSDKError("hgsdk_is_running failed", rc)
        return bool(state.value)

    def opt_out(self) -> None:
        rc = self._lib.hgsdk_opt_out()
        if rc < 0:
            raise HoneygainSDKError("hgsdk_opt_out failed", rc)

    def opt_in(self) -> None:
        rc = self._lib.hgsdk_opt_in()
        if rc < 0:
            raise HoneygainSDKError("hgsdk_opt_in failed", rc)

    def is_opted_in(self) -> bool:
        state = ctypes.c_int32()
        rc = self._lib.hgsdk_is_opted_in(ctypes.byref(state))
        if rc < 0:
            raise HoneygainSDKError("hgsdk_is_opted_in failed", rc)
        return bool(state.value)

    def identify(self) -> Dict[str, Any]:
        if not hasattr(self._lib, "hgsdk_identify"):
            return {}
        size = ctypes.c_size_t(0)
        rc = self._lib.hgsdk_identify(None, ctypes.byref(size))
        if rc < 0:
            raise HoneygainSDKError("hgsdk_identify (size) failed", rc)
        if size.value == 0:
            return {}
        buffer = ctypes.create_string_buffer(size.value)
        rc = self._lib.hgsdk_identify(buffer, ctypes.byref(size))
        if rc < 0:
            raise HoneygainSDKError("hgsdk_identify failed", rc)
        raw = buffer.value.decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    def enable_logging(self, directory: Optional[Path]) -> None:
        if not directory or not hasattr(self._lib, "hgsdk_log"):
            return
        directory.mkdir(parents=True, exist_ok=True)
        rc = self._lib.hgsdk_log(str(directory).encode("utf-8"))
        if rc < 0:
            raise HoneygainSDKError("hgsdk_log failed", rc)

    def mute_logging(self) -> None:
        if hasattr(self._lib, "hgsdk_mute"):
            rc = self._lib.hgsdk_mute()
            if rc < 0:
                raise HoneygainSDKError("hgsdk_mute failed", rc)


class HoneygainController:
    """High-level controller used by the GUI."""

    def __init__(self) -> None:
        self.config = load_config()
        self._lock = threading.RLock()
        self._sdk: Optional[HoneygainSDK] = None
        self._sdk_error: Optional[str] = None
        # Throttle identify() calls to avoid spamming the SDK
        self._last_identify_at: float = 0.0
        self._last_running: Optional[bool] = None
        try:
            self._sdk = HoneygainSDK(self.config)
        except HoneygainSDKError as exc:
            self._sdk_error = str(exc)
            log_step("honeygain_sdk_load_failed", {"error": self._sdk_error})
        except HoneygainConfigError as exc:
            self._sdk_error = str(exc)
            log_step("honeygain_config_error", {"error": self._sdk_error})

        if self._sdk and self.config.log_dir:
            try:
                self._sdk.enable_logging(self.config.log_dir)
            except HoneygainSDKError as exc:
                self._sdk_error = str(exc)
                log_step("honeygain_enable_logging_failed", {"error": self._sdk_error})

    @property
    def poll_interval_ms(self) -> int:
        return self.config.poll_seconds * 1000

    @property
    def sdk_loaded(self) -> bool:
        return self._sdk is not None

    def ensure_started(self) -> bool:
        """Attempt to start the Honeygain SDK if enabled."""
        if not self._sdk or not self.config.enabled:
            return False
        api_key = self.config.api_key
        if not api_key:
            self._sdk_error = "Honeygain API key missing"
            return False
        with self._lock:
            try:
                running = self._sdk.is_running()
                if running:
                    return True
            except HoneygainSDKError as exc:
                self._sdk_error = str(exc)
                log_step("honeygain_running_check_failed", {"error": self._sdk_error})
            # Ensure consent is recorded before attempting start
            try:
                opted_in = self._sdk.is_opted_in()
            except HoneygainSDKError as exc:
                opted_in = False
                self._sdk_error = str(exc)
                log_step("honeygain_opt_state_failed", {"error": self._sdk_error})
            if not opted_in:
                try:
                    self._sdk.opt_in()
                    log_step("honeygain_opt_in_called", {})
                    # Give SDK time to persist consent (up to ~3s)
                    for _ in range(15):
                        try:
                            if self._sdk.is_opted_in():
                                break
                        except HoneygainSDKError:
                            pass
                        time.sleep(0.2)
                except HoneygainSDKError as exc:
                    self._sdk_error = str(exc)
                    log_step("honeygain_opt_in_failed", {"error": self._sdk_error})
            try:
                consent = self._sdk.start(api_key)
                log_step("honeygain_sdk_started", {"consent": consent})
                return consent
            except HoneygainSDKError as exc:
                self._sdk_error = str(exc)
                log_step("honeygain_start_failed", {"error": self._sdk_error})
        return False

    def refresh_status(self) -> HoneygainStatus:
        """Fetch the current Honeygain runtime status."""
        identify: Dict[str, Any] = {}
        running: Optional[bool] = None
        opted_in: Optional[bool] = None
        error = self._sdk_error
        api_key_present = bool(self.config.api_key)

        if self._sdk and self.config.enabled and api_key_present:
            with self._lock:
                try:
                    running = self._sdk.is_running()
                except HoneygainSDKError as exc:
                    running = None
                    error = str(exc)
                    log_step("honeygain_running_check_failed", {"error": error})
                try:
                    opted_in = self._sdk.is_opted_in()
                except HoneygainSDKError as exc:
                    opted_in = None
                    error = str(exc)
                    log_step("honeygain_opt_state_failed", {"error": error})
                # Only call identify() if at least 60s passed or running state changed
                do_identify = True
                now = time.monotonic()
                if self._last_identify_at > 0:
                    do_identify = (now - self._last_identify_at) >= 60.0 or (running != self._last_running)
                if do_identify:
                    try:
                        identify = self._sdk.identify()
                    except HoneygainSDKError as exc:
                        identify = {}
                        error = str(exc)
                        log_step("honeygain_identify_failed", {"error": error})
                    finally:
                        self._last_identify_at = now
                        self._last_running = running

        device_id = _extract_text(identify, ("device_id", "deviceId", "device"))
        if not device_id:
            raw_val = identify.get("raw")
            if isinstance(raw_val, str) and raw_val.strip():
                device_id = raw_val.strip()

        status = HoneygainStatus(
            configured=self.config.existed,
            enabled=self.config.enabled,
            sdk_loaded=self._sdk is not None,
            api_key_present=api_key_present,
            running=running,
            opted_in=opted_in,
            device_id=device_id,
            session_id=_extract_text(identify, ("session_id", "sessionId", "session")),
            traffic_total_bytes=_extract_int(
                identify,
                (
                    "traffic_total_bytes",
                    "trafficTotalBytes",
                    "traffic_total",
                    "trafficTotal",
                    "traffic_bytes",
                    "trafficBytes",
                ),
            ),
            traffic_24h_bytes=_extract_int(
                identify,
                (
                    "traffic_24h_bytes",
                    "traffic24hBytes",
                    "traffic_24h",
                    "traffic24h",
                ),
            ),
            last_error=error,
            last_refresh=datetime.utcnow(),
            identify_raw=identify,
            library_path=self._sdk.library_path if self._sdk else None,
            config_path=self.config.path if self.config.path else None,
            secret_path=self.config.secret_path,
        )
        return status

    def opt_out(self) -> bool:
        """Disable Honeygain and stop the SDK."""
        self.config.enabled = False
        try:
            self.config.persist()
        except HoneygainConfigError as exc:
            self._sdk_error = str(exc)
            log_step("honeygain_config_save_failed", {"error": self._sdk_error})
            raise

        if self._sdk:
            with self._lock:
                try:
                    self._sdk.opt_out()
                except HoneygainSDKError as exc:
                    self._sdk_error = str(exc)
                    log_step("honeygain_opt_out_failed", {"error": self._sdk_error})
                try:
                    self._sdk.stop()
                except HoneygainSDKError as exc:
                    self._sdk_error = str(exc)
                    log_step("honeygain_stop_failed", {"error": self._sdk_error})
        return True

    def enable(self) -> bool:
        """Re-enable Honeygain after the user previously opted out."""
        if not self._sdk:
            raise HoneygainSDKError("Honeygain SDK library not loaded")
        if not self.config.api_key:
            raise HoneygainSDKError("Honeygain API key missing from config")

        self.config.enabled = True
        try:
            self.config.persist()
        except HoneygainConfigError as exc:
            self._sdk_error = str(exc)
            log_step("honeygain_config_save_failed", {"error": self._sdk_error})
            raise

        with self._lock:
            try:
                # Request consent (idempotent on SDK side)
                self._sdk.opt_in()
                log_step("honeygain_opt_in_called", {})
                # Give SDK a short moment to persist the choice
                for _ in range(10):  # up to ~2s
                    try:
                        if self._sdk.is_opted_in():
                            break
                    except HoneygainSDKError:
                        pass
                    time.sleep(0.2)
            except HoneygainSDKError as exc:
                self._sdk_error = str(exc)
                log_step("honeygain_opt_in_failed", {"error": self._sdk_error})
                raise
        # Attempt to start regardless; start() returns True when consent is recorded
        return self.ensure_started()

    def _update_status_json_on_offline(self) -> None:
        """Update today's status JSON to remove Honeygain from tools starting at current hour."""
        try:
            status_dir = data_dir_gui() / "status"
            now = datetime.utcnow()
            current_hour = now.hour
            json_path = status_dir / f"status-{now.strftime('%Y%m%d')}.json"
            
            if not json_path.exists():
                return
            
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Remove Honeygain from tools only from current hour onwards
            rewards_slots = data.get("rewards_multiplier_slots", {})
            if rewards_slots:
                for hour_str, slot_data in rewards_slots.items():
                    try:
                        hour = int(hour_str)
                        # Only update from current hour onwards
                        if hour >= current_hour and isinstance(slot_data, dict):
                            tools = slot_data.get("tools", [])
                            if isinstance(tools, list) and "Honeygain" in tools:
                                tools.remove("Honeygain")
                                slot_data["tools"] = tools
                    except (ValueError, TypeError):
                        continue
            
            # Write back
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            log_step("status_json_update_failed", {"error": str(exc)})
    
    def mark_offline(self) -> None:
        """Call this when Honeygain is detected as offline to update JSON."""
        self._update_status_json_on_offline()


def _extract_text(payload: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_int(payload: Dict[str, Any], keys: Sequence[str]) -> Optional[int]:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            if isinstance(value, str):
                value = value.replace(",", "")
            return int(float(value))
        except (ValueError, TypeError):
            continue
    return None
