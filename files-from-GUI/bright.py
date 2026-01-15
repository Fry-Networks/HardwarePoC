"""Bright SDK integration using managed BrightData.Api (pythonnet)."""

from __future__ import annotations

import json
import sys
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from miner_GUI.config import app_dir
from miner_GUI.utils.data import data_dir_gui, log_step

clr = None  # type: ignore
try:
    import clr  # type: ignore[attr-defined]
    HAVE_PYTHONNET = True
except Exception:
    HAVE_PYTHONNET = False

CONFIG_FILE_NAME = "brd_config.json"
DEFAULT_POLL_SECONDS = 60


class BrightError(RuntimeError):
    """Base error for Bright integration."""


class BrightConfigError(BrightError):
    """Raised when Bright configuration cannot be loaded or saved."""

class BrightSDKError(BrightError):
    """Raised when Bright managed SDK calls fail."""


@dataclass
class BrightStatus:
    configured: bool
    enabled: bool
    sdk_loaded: bool
    supported: Optional[bool]
    running: Optional[bool]
    consent_choice: Optional[bool]
    last_error: Optional[str]
    last_refresh: datetime
    config_path: Optional[Path] = None


class BrightConfig:
    """Configuration backed by brd_config.json."""

    def __init__(self, path: Path, existed: bool, raw: Optional[Dict[str, Any]] = None):
        self.path = path
        self._raw = raw or {}
        self._existed = existed
        self._app_id = str(self._raw.get("app_id", "") or "")
        self._app_name = str(self._raw.get("app_name", "") or "")
        self._logo_link = str(self._raw.get("logo_link", "") or self._raw.get("app_logo", "") or "")
        self._language = str(self._raw.get("lang", "") or self._raw.get("language", "") or "")
        self._consent = bool(self._raw.get("consent", False))
        self._enabled = bool(self._raw.get("enabled", False))

    @property
    def existed(self) -> bool:
        return self._existed

    @property
    def app_id(self) -> str:
        return self._app_id

    @property
    def app_name(self) -> Optional[str]:
        return self._app_name or None

    @property
    def logo_link(self) -> Optional[str]:
        return self._logo_link or None

    @property
    def language(self) -> Optional[str]:
        return self._language or None

    @property
    def consent(self) -> bool:
        return self._consent

    @consent.setter
    def consent(self, value: bool) -> None:
        self._consent = bool(value)
        self._raw["consent"] = self._consent

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        self._raw["enabled"] = self._enabled

    def persist(self) -> None:
        try:
            import json
            from miner_GUI.utils.data import data_dir_gui
            from miner_GUI.utils.ops_queue_client import enqueue_write_config
            
            content = json.dumps(self._raw, indent=2, sort_keys=True)
            
            # Write to wherever the config was loaded from (or standard location)
            try:
                base_dir = data_dir_gui()
                # Use the actual path's relative location, don't force it to config/
                rel_path = str(self.path.relative_to(base_dir))
                success, msg = enqueue_write_config(rel_path, content)
                if not success:
                    raise BrightConfigError(f"Failed to enqueue Bright config: {msg}")
            except (ValueError, OSError):
                # If path is not in ProgramData (e.g., development), write directly
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(self._raw, handle, indent=2, sort_keys=True)
        except Exception as exc:
            raise BrightConfigError(f"Failed to save Bright config: {exc}") from exc


def _unique_paths(items: Sequence[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for item in items:
        if not item:
            continue
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _candidate_config_dirs() -> list[Path]:
    candidates: list[Path] = []
    try:
        candidates.append(data_dir_gui() / "SDK" / "windows-bright-sdk")
        candidates.append(data_dir_gui() / "config")
    except Exception:
        pass
    candidates.append(app_dir() / "SDK" / "windows-bright-sdk")
    candidates.append(app_dir() / "config")
    candidates.append(Path.cwd() / "SDK" / "windows-bright-sdk")
    candidates.append(Path.cwd() / "config")
    return _unique_paths(candidates)


def _find_managed_dll(candidate_dirs: Sequence[Path]) -> Optional[Path]:
    names = ["lum_sdk_managed_x64.dll", "lum_sdk_managed.dll"]
    for base in candidate_dirs:
        for name in names:
            cand = base / name
            if cand.exists():
                return cand
    return None


def locate_config_file() -> tuple[Path, bool, Dict[str, Any], list[Path]]:
    import json

    candidate_dirs = _candidate_config_dirs()
    candidate_files = [p / CONFIG_FILE_NAME for p in candidate_dirs if p]
    for file_path in candidate_files:
        if file_path.exists():
            try:
                data = json.load(open(file_path, "r", encoding="utf-8"))
            except Exception:
                data = {}
            return file_path, True, data, candidate_dirs

    fallback = candidate_files[0] if candidate_files else (Path.cwd() / "config" / CONFIG_FILE_NAME)
    return fallback, fallback.exists(), {}, candidate_dirs


def load_config() -> BrightConfig:
    path, existed, data, _ = locate_config_file()
    return BrightConfig(path=path, existed=existed, raw=data)


class BrightManaged:
    """Managed BrightData.Api access via pythonnet."""

    def __init__(self, config: BrightConfig):
        self.config = config
        self.available = False
        self._api_cls = None
        self._api_obj = None
        self._settings_cls = None
        self._initialized = False
        if not HAVE_PYTHONNET:
            log_step("bright_managed_pythonnet_missing")
            return
        dll = _find_managed_dll(_candidate_config_dirs())
        if not dll:
            log_step("bright_managed_dll_missing")
            return
        try:
            if clr is not None:
                clr.AddReference(str(dll))  # type: ignore[attr-defined]
            import BrightData  # type: ignore

            self._api_cls = BrightData.Api  # type: ignore
            try:
                self._api_obj = BrightData.Api()  # type: ignore
            except Exception:
                self._api_obj = None
            self._settings_cls = BrightData.Api.Settings  # type: ignore
            self.available = True
            log_step("bright_managed_loaded", {"dll": str(dll)})
        except Exception as exc:
            log_step("bright_managed_load_failed", {"error": str(exc), "dll": str(dll)})
            self.available = False

    def _build_settings(self, skip_consent: bool) -> Optional[Any]:
        if not self._settings_cls:
            return None
        try:
            settings = self._settings_cls()
            settings.AppId = self.config.app_id
            if self.config.app_name:
                settings.AppName = self.config.app_name
            if self.config.logo_link:
                settings.AppLogo = self.config.logo_link
            if self.config.language:
                settings.Language = self.config.language
            settings.SkipConsent = bool(skip_consent)
            return settings
        except Exception as exc:
            log_step("bright_managed_settings_failed", {"error": str(exc)})
            return None

    def init(self, skip_consent: bool) -> bool:
        if not self.available or not self._api_cls:
            log_step("bright_managed_init_skipped", {"reason": "unavailable_or_missing_api_cls"})
            return False
        if self._initialized:
            return True
        try:
            settings = self._build_settings(skip_consent)
            if settings is None:
                log_step("bright_managed_settings_missing")
                return False
            if self._api_obj is None:
                try:
                    self._api_obj = self._api_cls()  # type: ignore[call-arg]
                except Exception:
                    self._api_obj = None
            api = self._api_obj or self._api_cls
            log_step("bright_managed_init_start", {"app_id": self.config.app_id or "(missing)", "skip_consent": bool(skip_consent)})
            api.Init(settings)  # type: ignore[attr-defined]
            log_step("bright_managed_init_ok")
            self._initialized = True
            return True
        except Exception as exc:
            log_step("bright_managed_init_failed", {"error": str(exc)})
            return False

    def notify_show_consent(self) -> None:
        if not self.available or not self._api_cls:
            return
        try:
            api = self._api_obj or self._api_cls
            if hasattr(api, "NotifyShowConsent"):
                api.NotifyShowConsent()  # type: ignore[attr-defined]
        except Exception:
            pass

    def external_opt_in(self) -> bool:
        if not self.available or not self._api_cls:
            return False
        try:
            api = self._api_obj or self._api_cls
            if hasattr(api, "ExternalOptIn"):
                api.ExternalOptIn()  # type: ignore[attr-defined]
                return True
        except Exception:
            pass
        return False

    def show_consent(self) -> Optional[bool]:
        if not self.available or not self._api_cls:
            return None
        try:
            # Ensure SDK is initialized with consent allowed
            self.init(skip_consent=False)
            api = self._api_obj or self._api_cls
            try:
                api.ShowConsent()  # type: ignore[attr-defined]
            except Exception:
                pass
            # After dialog closes, re-read choice and log it
            choice = None
            for _ in range(40):
                choice = self.consent_choice()
                if choice is not None:
                    break
                try:
                    from PySide6 import QtCore as _QtCore  # type: ignore
                    _QtCore.QThread.msleep(100)
                except Exception:
                    pass
            log_step("bright_managed_consent_choice", {"choice": choice})
            return choice if isinstance(choice, bool) else None
        except Exception as exc:
            log_step("bright_managed_show_consent_failed", {"error": str(exc)})
            return None

    def opt_out(self) -> None:
        if not self.available or not self._api_cls:
            return
        try:
            api = self._api_obj or self._api_cls
            api.OptOut()  # type: ignore[attr-defined]
        except Exception as exc:
            log_step("bright_managed_opt_out_failed", {"error": str(exc)})

    def close(self) -> None:
        if not self.available or not self._api_cls:
            return
        try:
            api = self._api_obj or self._api_cls
            api.Close()  # type: ignore[attr-defined]
        except Exception:
            pass

    def consent_choice(self) -> Optional[bool]:
        if not self.available or not self._api_cls:
            return None
        try:
            api = self._api_obj or self._api_cls
            choice = getattr(api, "ConsentChoice", None)
            # Handle Nullable<bool>: check HasValue/Value
            if choice is None:
                return None
            if hasattr(choice, "HasValue"):
                try:
                    if getattr(choice, "HasValue"):
                        return bool(getattr(choice, "Value"))
                    return None
                except Exception:
                    return None
            if isinstance(choice, bool):
                return choice
            return None
        except Exception:
            return None

    def is_supported(self) -> Optional[bool]:
        if not self.available or not self._api_cls:
            return None
        try:
            api = self._api_obj or self._api_cls
            status = api.GetServiceStatus()  # type: ignore[attr-defined]
            return status is not None
        except Exception:
            return None


class BrightController:
    """Controller that manages Bright SDK lifecycle via managed BrightData.Api."""

    def __init__(self) -> None:
        self.config = load_config()
        self._managed = BrightManaged(self.config)
        self._sdk_error: Optional[str] = None
        if not sys.platform.startswith("win"):
            log_step("bright_skip_non_windows")
            return
        if not self._managed.available:
            self._sdk_error = "Managed Bright SDK not available"
            log_step("bright_managed_unavailable")

    def _has_network(self) -> bool:
        """Best-effort check for internet connectivity (fast, non-blocking)."""
        try:
            import socket
            # Try common DNS endpoints with a very short timeout
            for host in ("1.1.1.1", "8.8.8.8"):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(1.0)
                        if s.connect_ex((host, 53)) == 0:
                            return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    @property
    def poll_interval_ms(self) -> int:
        return DEFAULT_POLL_SECONDS * 1000

    @property
    def sdk_loaded(self) -> bool:
        return self._managed.available

    def ensure_started(self) -> bool:
        if not self._managed.available or not self.config.enabled:
            return False
        if not self.config.app_id:
            self._sdk_error = "Bright app_id missing (brd_config.json)"
            return False
        # Always skip the built-in consent; we handle our own UX
        ok = self._managed.init(skip_consent=True)
        if ok:
            # Skip service start if offline to avoid long timeouts
            if not self._has_network():
                log_step("bright_net_start_service_skipped_offline")
            else:
                log_step("bright_net_start_service_triggered")
                self._start_service()
        return ok

    def refresh_status(self) -> BrightStatus:
        consent_choice = None
        supported = None
        running = None
        error = self._sdk_error
        if self._managed.available:
            consent_choice = self._managed.consent_choice()
            supported = self._managed.is_supported()
            # Treat running as enabled AND online; avoids misleading "running" when offline
            running = bool(self.config.enabled and self._has_network())

        status = BrightStatus(
            configured=self.config.existed,
            enabled=self.config.enabled,
            sdk_loaded=self._managed.available,
            supported=supported,
            running=running,
            consent_choice=consent_choice,
            last_error=error,
            last_refresh=datetime.utcnow(),
            config_path=self.config.path if self.config.path else None,
        )
        return status

    # ----------- net_updater helpers (fallback start/stop) -----------
    def _net_updater_path(self) -> Optional[Path]:
        # Prefer x64 binary; fall back to 32-bit if not found.
        for base in _candidate_config_dirs():
            if not base:
                continue
            for name in ("net_updater64.exe", "net_updater32.exe", "net_updater.exe"):
                cand = base / name
                if cand.exists():
                    return cand
        return None

    def _run_net_updater(self, args: list[str], step: str) -> bool:
        exe = self._net_updater_path()
        if not exe:
            log_step(f"{step}_missing_exe")
            return False
        try:
            startupinfo = None
            creationflags = 0
            if sys.platform.startswith("win"):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creationflags = subprocess.CREATE_NO_WINDOW
            completed = subprocess.run(
                [str(exe), *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            log_step(
                step,
                {
                    "exe": str(exe),
                    "args": args,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                },
            )
            return completed.returncode == 0
        except Exception as exc:
            log_step(f"{step}_failed", {"error": str(exc)})
            return False

    def _start_service(self) -> None:
        if not self.config.app_id:
            log_step("bright_net_start_service_missing_app_id")
            return
        ok = self._run_net_updater(["--start-service", self.config.app_id], "bright_net_start_service")
        if not ok:
            log_step("bright_net_start_service_not_ok")

    def _stop_service(self) -> None:
        if not self.config.app_id:
            log_step("bright_net_stop_service_missing_app_id")
            return
        self._run_net_updater(["--stop-service", self.config.app_id], "bright_net_stop_service")

    def opt_out(self) -> None:
        log_step("bright_opt_out_begin")
        self.config.enabled = False
        self.config.consent = False
        self.config.persist()
        if self._managed.available:
            self._managed.opt_out()
            log_step("bright_opt_out_managed_called")
        else:
            log_step("bright_opt_out_skipped_managed_missing")
        # Ensure service is stopped from tray as well
        self._stop_service()

    def enable(self) -> bool:
        if not self.config.app_id:
            raise BrightSDKError("Bright app_id missing from brd_config.json")
        if not self._managed.available:
            raise BrightSDKError("Managed Bright SDK not available (pythonnet missing?)")
        if not self.config.consent:
            # Consent is handled by the caller; refuse to start without it.
            return False
        self.config.enabled = True
        self.config.persist()
        ok = self.ensure_started()
        return ok

    def prepare_custom_consent(self) -> bool:
        if not self._managed.available:
            self._sdk_error = "Managed Bright SDK not available"
            return False
        return self._managed.init(skip_consent=True)

    def notify_show_consent(self) -> None:
        if not self._managed.available:
            return
        self._managed.notify_show_consent()

    def record_consent(self, accepted: bool) -> bool:
        if not self._managed.available:
            self._sdk_error = "Managed Bright SDK not available"
            return False
        if accepted:
            self._managed.notify_show_consent()
            self._managed.external_opt_in()
            self.config.consent = True
            self.config.enabled = True
            self.config.persist()
            ok = self.ensure_started()
            return ok
        self.config.consent = False
        self.config.enabled = False
        self.config.persist()
        self._managed.opt_out()
        return False

    def ensure_consent_from_flag(self) -> None:
        if not self.config.consent:
            return
        if not self._managed.available:
            return
        choice = self._managed.consent_choice()
        if choice is True:
            return
        self.show_consent()

    # Backwards-compatible helper: show managed consent dialog (default SDK UI)
    def show_consent(self) -> bool:
        if not self._managed.available:
            self._sdk_error = "Managed Bright SDK not available"
            return False
        self._managed.init(skip_consent=False)
        choice = self._managed.show_consent()
        log_step("bright_show_consent_choice", {"choice": choice})
        ok = choice is True
        if ok:
            self.config.consent = True
            self.config.enabled = True
            self.config.persist()
            return True
        if choice is False:
            self.config.consent = False
            self.config.enabled = False
            self.config.persist()
        return False

    def shutdown(self) -> None:
        if self._managed.available:
            try:
                self._managed.close()
            except Exception:
                pass

    def _update_status_json_on_offline(self) -> None:
        """Update today's status JSON to remove Bright from tools starting at current hour."""
        try:
            status_dir = data_dir_gui() / "status"
            now = datetime.utcnow()
            current_hour = now.hour
            json_path = status_dir / f"status-{now.strftime('%Y%m%d')}.json"
            
            if not json_path.exists():
                return
            
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Remove Bright from tools only from current hour onwards
            rewards_slots = data.get("rewards_multiplier_slots", {})
            if rewards_slots:
                for hour_str, slot_data in rewards_slots.items():
                    try:
                        hour = int(hour_str)
                        # Only update from current hour onwards
                        if hour >= current_hour and isinstance(slot_data, dict):
                            tools = slot_data.get("tools", [])
                            if isinstance(tools, list) and "Bright" in tools:
                                tools.remove("Bright")
                                slot_data["tools"] = tools
                    except (ValueError, TypeError):
                        continue
            
            # Write back
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            log_step("status_json_update_failed", {"error": str(exc)})
    
    def mark_offline(self) -> None:
        """Call this when Bright is detected as offline to update JSON."""
        self._update_status_json_on_offline()
