"""Presearch Node integration for SVN miners.

This integrates with Presearch decentralized search nodes.
Presearch nodes help power a decentralized search engine and earn PRE tokens.

References:
- Setup: https://docs.presearch.io/nodes/setup
- API: https://docs.presearch.io/nodes/api
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


CONFIG_FILE_NAME = "presearch_config.json"
DEFAULT_API_PORT = 80  # Default Presearch node port
PRESEARCH_API_BASE = "https://nodes.presearch.com/api"


@dataclass
class PresearchStatus:
    """Status surface for Presearch Node UI."""

    configured: bool
    enabled: bool
    running: bool
    connected: bool
    registration_code: Optional[str]
    node_id: Optional[str]
    node_description: Optional[str]
    total_uptime_hours: float
    reliability_score: float
    total_pre_earned: float
    last_error: Optional[str]
    last_refresh: datetime
    disconnections_today: int = 0
    queries_today: int = 0


class PresearchConfig:
    """Configuration for Presearch Node integration."""

    def __init__(self, path: Path, existed: bool, raw: Optional[Dict[str, Any]] = None):
        self.path = path
        self._raw = raw or {}
        self._existed = existed
        self._enabled = bool(self._raw.get("enabled", False))
        self._registration_code = str(self._raw.get("registration_code", "") or "")
        self._docker_container_name = str(self._raw.get("docker_container_name", "presearch-node") or "presearch-node")
        self._api_key = str(self._raw.get("api_key", "") or "")

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
    def registration_code(self) -> str:
        return self._registration_code

    @registration_code.setter
    def registration_code(self, value: str) -> None:
        self._registration_code = str(value)
        self._raw["registration_code"] = self._registration_code

    @property
    def docker_container_name(self) -> str:
        return self._docker_container_name

    @property
    def api_key(self) -> str:
        return self._api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._api_key = str(value)
        self._raw["api_key"] = self._api_key

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
                    log_step("presearch_config_save_failed", {"error": msg})
                    return
            except (ValueError, OSError):
                # If path is not in ProgramData (e.g., development), write directly
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(self._raw, handle, indent=2, sort_keys=True)
        except Exception as exc:
            log_step("presearch_config_save_failed", {"error": str(exc)})


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
    """Locate and load Presearch configuration file."""
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


def load_config() -> PresearchConfig:
    """Load Presearch configuration."""
    path, existed, data = locate_config_file()
    return PresearchConfig(path=path, existed=existed, raw=data)


class PresearchController:
    """Controller to manage Presearch Node lifecycle."""

    def __init__(self) -> None:
        self.config = load_config()
        self._last_error: Optional[str] = None
        self._docker_available = self._check_docker()

    def _check_docker(self) -> bool:
        """Check if Docker is available."""
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_docker(self, args: list[str], timeout: int = 30) -> Tuple[int, str, str]:
        """Run a Docker command."""
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            result = subprocess.run(
                ["docker"] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creationflags,
            )
            return result.returncode, result.stdout, result.stderr
        except Exception as exc:
            return 1, "", str(exc)

    def is_container_running(self) -> bool:
        """Check if Presearch container is running."""
        code, out, _ = self._run_docker(
            ["ps", "--filter", f"name={self.config.docker_container_name}", "--format", "{{.Names}}"],
            timeout=10
        )
        return code == 0 and self.config.docker_container_name in out

    def is_container_exists(self) -> bool:
        """Check if Presearch container exists (running or stopped)."""
        code, out, _ = self._run_docker(
            ["ps", "-a", "--filter", f"name={self.config.docker_container_name}", "--format", "{{.Names}}"],
            timeout=10
        )
        return code == 0 and self.config.docker_container_name in out

    def start(self) -> bool:
        """Start Presearch node container."""
        if not self._docker_available:
            self._last_error = "Docker not available"
            log_step("presearch_start_failed", {"reason": "docker_not_available"})
            return False

        if not self.config.registration_code:
            self._last_error = "Registration code not configured"
            log_step("presearch_start_failed", {"reason": "no_registration_code"})
            return False

        if self.is_container_running():
            log_step("presearch_already_running")
            return True

        # If container exists but stopped, start it
        if self.is_container_exists():
            code, _, err = self._run_docker(["start", self.config.docker_container_name], timeout=30)
            if code == 0:
                log_step("presearch_container_started")
                return True
            self._last_error = err or "Failed to start container"
            return False

        # Create and start new container
        args = [
            "run", "-d",
            "--name", self.config.docker_container_name,
            "--restart", "unless-stopped",
            "-e", f"REGISTRATION_CODE={self.config.registration_code}",
            "presearch/node"
        ]

        code, _, err = self._run_docker(args, timeout=120)
        if code == 0:
            log_step("presearch_container_created")
            return True

        self._last_error = err or "Failed to create container"
        log_step("presearch_start_failed", {"error": err})
        return False

    def stop(self) -> bool:
        """Stop Presearch node container."""
        if not self._docker_available:
            return False

        code, _, err = self._run_docker(["stop", self.config.docker_container_name], timeout=30)
        if code == 0:
            log_step("presearch_container_stopped")
            return True

        log_step("presearch_stop_failed", {"error": err})
        return False

    def _get_node_stats(self) -> Optional[Dict[str, Any]]:
        """Get node stats from Presearch API."""
        if not HAVE_REQUESTS or not self.config.api_key:
            return None

        try:
            headers = {"Authorization": f"Bearer {self.config.api_key}"}
            resp = requests.get(
                f"{PRESEARCH_API_BASE}/nodes/status",
                headers=headers,
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            log_step("presearch_api_failed", {"error": str(exc)})
        return None

    def _get_node_info_from_container(self) -> Optional[Dict[str, Any]]:
        """Get node info from container logs."""
        if not self._docker_available:
            return None

        code, out, _ = self._run_docker(
            ["logs", "--tail", "50", self.config.docker_container_name],
            timeout=10
        )
        if code != 0:
            return None

        # Parse log output for node info
        info = {}
        for line in out.split("\n"):
            if "Node ID:" in line:
                parts = line.split("Node ID:")
                if len(parts) > 1:
                    info["node_id"] = parts[1].strip()
            if "connected" in line.lower():
                info["connected"] = True
            if "disconnected" in line.lower():
                info["connected"] = False

        return info if info else None

    def refresh_status(self) -> PresearchStatus:
        """Poll Presearch node status."""
        running = self.is_container_running()
        connected = False
        node_id = None
        node_description = None
        total_uptime_hours = 0.0
        reliability_score = 0.0
        total_pre_earned = 0.0
        disconnections_today = 0
        queries_today = 0

        # Try to get stats from API
        if running:
            stats = self._get_node_stats()
            if stats:
                node_info = stats.get("node", {})
                node_id = node_info.get("id")
                node_description = node_info.get("description")
                connected = node_info.get("connected", False)
                total_uptime_hours = float(node_info.get("uptime_hours", 0) or 0)
                reliability_score = float(node_info.get("reliability_score", 0) or 0)
                total_pre_earned = float(node_info.get("total_pre_earned", 0) or 0)
                disconnections_today = int(node_info.get("disconnections_today", 0) or 0)
                queries_today = int(node_info.get("queries_today", 0) or 0)
            else:
                # Fallback to container logs
                container_info = self._get_node_info_from_container()
                if container_info:
                    node_id = container_info.get("node_id")
                    connected = container_info.get("connected", False)

        return PresearchStatus(
            configured=self.config.existed and bool(self.config.registration_code),
            enabled=self.config.enabled,
            running=running,
            connected=connected,
            registration_code=self.config.registration_code or None,
            node_id=node_id,
            node_description=node_description,
            total_uptime_hours=total_uptime_hours,
            reliability_score=reliability_score,
            total_pre_earned=total_pre_earned,
            last_error=self._last_error,
            last_refresh=datetime.utcnow(),
            disconnections_today=disconnections_today,
            queries_today=queries_today,
        )

    def enable(self) -> bool:
        """Enable Presearch node."""
        self.config.enabled = True
        self.config.persist()
        return self.start()

    def disable(self) -> bool:
        """Disable Presearch node."""
        self.config.enabled = False
        self.config.persist()
        return self.stop()

    @property
    def docker_available(self) -> bool:
        """Check if Docker is available."""
        return self._docker_available

    def ensure_started(self) -> bool:
        """Ensure Presearch is started if enabled."""
        if not self.config.enabled:
            return False
        if self.is_container_running():
            return True
        return self.start()

    def _update_status_json_on_offline(self) -> None:
        """Update today's status JSON to remove Presearch from tools."""
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
                            if isinstance(tools, list) and "Presearch" in tools:
                                tools.remove("Presearch")
                                slot_data["tools"] = tools
                    except (ValueError, TypeError):
                        continue

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            log_step("status_json_update_failed", {"error": str(exc)})

    def mark_offline(self) -> None:
        """Call this when Presearch is detected as offline."""
        self._update_status_json_on_offline()
