"""Space Acres (Autonomy) farming integration for SDN miners.

This integrates with the Space Acres farming application from Autonomys.
Space Acres is a desktop GUI application for participating in the Autonomy Network.

References:
- Install: https://docs.autonomys.xyz/farming/space-acres/install/
- SDK: https://github.com/autonomys/auto-sdk
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config_profile import GROUP
from miner_GUI.config import app_dir
from miner_GUI.utils.data import data_dir_gui, log_step

try:
    import requests
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False


CONFIG_FILE_NAME = "space_acres_config.json"
DEFAULT_RPC_PORT = 9944  # Default Substrate RPC port


@dataclass
class SpaceAcresStatus:
    """Status surface for Space Acres UI."""

    configured: bool
    enabled: bool
    running: bool
    syncing: bool
    farming: bool
    plotted_space_gb: float
    reward_address: Optional[str]
    last_error: Optional[str]
    last_refresh: datetime
    node_connected: bool
    best_block: Optional[int] = None
    finalized_block: Optional[int] = None


class SpaceAcresConfig:
    """Configuration for Space Acres integration."""

    def __init__(self, path: Path, existed: bool, raw: Optional[Dict[str, Any]] = None):
        self.path = path
        self._raw = raw or {}
        self._existed = existed
        self._enabled = bool(self._raw.get("enabled", False))
        self._reward_address = str(self._raw.get("reward_address", "") or "")
        self._farm_path = str(self._raw.get("farm_path", "") or "")
        self._farm_size_gb = float(self._raw.get("farm_size_gb", 100) or 100)
        self._rpc_port = int(self._raw.get("rpc_port", DEFAULT_RPC_PORT) or DEFAULT_RPC_PORT)

    @property
    def existed(self) -> bool:
        return self._existed

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        self._raw["enabled"] = self._enabled

    @property
    def reward_address(self) -> str:
        return self._reward_address

    @reward_address.setter
    def reward_address(self, value: str) -> None:
        self._reward_address = str(value)
        self._raw["reward_address"] = self._reward_address

    @property
    def farm_path(self) -> str:
        return self._farm_path

    @farm_path.setter
    def farm_path(self, value: str) -> None:
        self._farm_path = str(value)
        self._raw["farm_path"] = self._farm_path

    @property
    def farm_size_gb(self) -> float:
        return self._farm_size_gb

    @farm_size_gb.setter
    def farm_size_gb(self, value: float) -> None:
        self._farm_size_gb = float(value)
        self._raw["farm_size_gb"] = self._farm_size_gb

    @property
    def rpc_port(self) -> int:
        return self._rpc_port

    def persist(self) -> None:
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
                    log_step("space_acres_config_save_failed", {"error": msg})
                    return
            except (ValueError, OSError):
                # If path is not in ProgramData (e.g., development), write directly
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(self._raw, handle, indent=2, sort_keys=True)
        except Exception as exc:
            log_step("space_acres_config_save_failed", {"error": str(exc)})


def _candidate_config_dirs() -> list[Path]:
    """Get candidate directories for configuration files."""
    candidates: list[Path] = []
    try:
        candidates.append(data_dir_gui() / "config")
    except Exception:
        pass
    candidates.append(app_dir() / "config")
    candidates.append(Path.cwd() / "config")
    return candidates


def locate_config_file() -> Tuple[Path, bool, Dict[str, Any]]:
    """Locate and load Space Acres configuration file."""
    candidate_dirs = _candidate_config_dirs()
    candidate_files = [p / CONFIG_FILE_NAME for p in candidate_dirs if p]

    for file_path in candidate_files:
        if file_path.exists():
            try:
                data = json.load(open(file_path, "r", encoding="utf-8"))
            except Exception:
                data = {}
            return file_path, True, data

    fallback = candidate_files[0] if candidate_files else (Path.cwd() / "config" / CONFIG_FILE_NAME)
    return fallback, False, {}


def load_config() -> SpaceAcresConfig:
    """Load Space Acres configuration."""
    path, existed, data = locate_config_file()
    return SpaceAcresConfig(path=path, existed=existed, raw=data)


class SpaceAcresController:
    """Controller to manage Space Acres farming lifecycle."""

    def __init__(self) -> None:
        self.config = load_config()
        self._space_acres_bin = self._find_space_acres_binary()
        self._last_error: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None

    def _find_space_acres_binary(self) -> Optional[Path]:
        """Locate Space Acres executable."""
        candidates = []

        # Check environment variable
        env_path = os.environ.get("SPACE_ACRES_BIN") or os.environ.get("SPACE_ACRES_PATH")
        if env_path:
            candidates.append(Path(env_path))

        # Platform-specific locations
        if os.name == "nt":
            # Windows locations
            program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            candidates.extend([
                Path(program_files) / "Space Acres" / "space-acres.exe",
                Path(local_app_data) / "Programs" / "Space Acres" / "space-acres.exe",
                Path.home() / "AppData" / "Local" / "Programs" / "Space Acres" / "space-acres.exe",
            ])
        else:
            # Linux/macOS locations
            candidates.extend([
                Path.home() / ".local" / "bin" / "space-acres",
                Path("/usr/local/bin/space-acres"),
                Path("/usr/bin/space-acres"),
                Path.home() / "Applications" / "space-acres",
            ])

        # SDK folder
        try:
            candidates.append(data_dir_gui() / "SDK" / "space-acres" / ("space-acres.exe" if os.name == "nt" else "space-acres"))
        except Exception:
            pass
        candidates.append(app_dir() / "SDK" / "space-acres" / ("space-acres.exe" if os.name == "nt" else "space-acres"))

        for cand in candidates:
            try:
                if cand and cand.exists():
                    return cand
            except Exception:
                continue
        return None

    def _port_reachable(self, port: int) -> bool:
        """Check if RPC port is reachable."""
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except Exception:
            return False

    def _rpc_call(self, method: str, params: list = None) -> Optional[Dict[str, Any]]:
        """Make an RPC call to the Space Acres node."""
        if not HAVE_REQUESTS:
            return None
        try:
            url = f"http://127.0.0.1:{self.config.rpc_port}"
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or []
            }
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result")
        except Exception as exc:
            log_step("space_acres_rpc_failed", {"method": method, "error": str(exc)})
        return None

    def is_running(self) -> bool:
        """Check if Space Acres is currently running."""
        # Check via RPC
        if self._port_reachable(self.config.rpc_port):
            return True

        # Check via process name
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                if 'space-acres' in proc.info['name'].lower():
                    return True
        except Exception:
            pass

        return False

    def start(self) -> bool:
        """Start Space Acres farming application."""
        if not self._space_acres_bin:
            self._last_error = "Space Acres binary not found"
            log_step("space_acres_start_failed", {"reason": "binary_not_found"})
            return False

        if self.is_running():
            log_step("space_acres_already_running")
            return True

        try:
            # Start Space Acres in background
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            self._process = subprocess.Popen(
                [str(self._space_acres_bin)],
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log_step("space_acres_started", {"pid": self._process.pid})

            # Wait a bit for startup
            time.sleep(2)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            log_step("space_acres_start_exception", {"error": str(exc)})
            return False

    def stop(self) -> bool:
        """Stop Space Acres farming application."""
        try:
            import psutil
            for proc in psutil.process_iter(['name', 'pid']):
                if 'space-acres' in proc.info['name'].lower():
                    proc.terminate()
                    log_step("space_acres_stopped", {"pid": proc.info['pid']})
        except Exception as exc:
            log_step("space_acres_stop_exception", {"error": str(exc)})
            return False
        return True

    def refresh_status(self) -> SpaceAcresStatus:
        """Poll Space Acres status."""
        running = self.is_running()
        node_connected = self._port_reachable(self.config.rpc_port)
        syncing = False
        farming = False
        best_block = None
        finalized_block = None
        plotted_space_gb = 0.0

        if node_connected:
            # Get sync status
            sync_state = self._rpc_call("system_syncState")
            if sync_state:
                best_block = sync_state.get("currentBlock")
                syncing = sync_state.get("highestBlock", 0) > sync_state.get("currentBlock", 0)

            # Get farming status (using Autonomy-specific RPC)
            farmer_state = self._rpc_call("farmer_info")
            if farmer_state:
                farming = farmer_state.get("is_farming", False)
                plotted_space_gb = farmer_state.get("total_space_pledged_gb", 0.0)

        return SpaceAcresStatus(
            configured=self.config.existed,
            enabled=self.config.enabled,
            running=running,
            syncing=syncing,
            farming=farming,
            plotted_space_gb=plotted_space_gb,
            reward_address=self.config.reward_address or None,
            last_error=self._last_error,
            last_refresh=datetime.utcnow(),
            node_connected=node_connected,
            best_block=best_block,
            finalized_block=finalized_block,
        )

    def enable(self) -> bool:
        """Enable Space Acres and start farming."""
        self.config.enabled = True
        self.config.persist()
        return self.start()

    def disable(self) -> bool:
        """Disable Space Acres and stop farming."""
        self.config.enabled = False
        self.config.persist()
        return self.stop()

    @property
    def binary_available(self) -> bool:
        """Check if Space Acres binary is available."""
        return self._space_acres_bin is not None

    def ensure_started(self) -> bool:
        """Ensure Space Acres is started if enabled."""
        if not self.config.enabled:
            return False
        if self.is_running():
            return True
        return self.start()

    def _update_status_json_on_offline(self) -> None:
        """Update today's status JSON to remove Space Acres from tools."""
        try:
            status_dir = data_dir_gui() / "status"
            now = datetime.utcnow()
            current_hour = now.hour
            json_path = status_dir / f"status-{now.strftime('%Y%m%d')}.json"

            if not json_path.exists():
                return

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            rewards_slots = data.get("rewards_multiplier_slots", {})
            if rewards_slots:
                for hour_str, slot_data in rewards_slots.items():
                    try:
                        hour = int(hour_str)
                        if hour >= current_hour and isinstance(slot_data, dict):
                            tools = slot_data.get("tools", [])
                            if isinstance(tools, list) and "SpaceAcres" in tools:
                                tools.remove("SpaceAcres")
                                slot_data["tools"] = tools
                    except (ValueError, TypeError):
                        continue

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            log_step("status_json_update_failed", {"error": str(exc)})

    def mark_offline(self) -> None:
        """Call this when Space Acres is detected as offline."""
        self._update_status_json_on_offline()
