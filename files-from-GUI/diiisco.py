"""Diiisco Node integration for SVN miners.

This integrates with Diiisco distributed discovery nodes.
Diiisco nodes participate in a decentralized discovery network.

Reference:
- GitHub: https://github.com/FrysCrypto/diiisco-node
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

from miner_GUI.config import app_dir
from miner_GUI.utils.data import data_dir_gui, log_step

try:
    import requests  # type: ignore
    HAVE_REQUESTS = True
except ImportError:
    requests = None  # type: ignore
    HAVE_REQUESTS = False


CONFIG_FILE_NAME = "diiisco_config.json"
DEFAULT_API_PORT = 8080  # Default Diiisco node API port


@dataclass
class DiiiscoStatus:
    """Status surface for Diiisco Node UI."""

    configured: bool
    enabled: bool
    running: bool
    connected: bool
    node_id: Optional[str]
    peer_count: int
    discoveries_count: int
    uptime_seconds: int
    last_error: Optional[str]
    last_refresh: datetime
    version: Optional[str] = None
    network: Optional[str] = None


class DiiiscoConfig:
    """Configuration for Diiisco Node integration."""

    def __init__(self, path: Path, existed: bool, raw: Optional[Dict[str, Any]] = None):
        self.path = path
        self._raw = raw or {}
        self._existed = existed
        self._enabled = bool(self._raw.get("enabled", False))
        self._node_key = str(self._raw.get("node_key", "") or "")
        self._api_port = int(self._raw.get("api_port", DEFAULT_API_PORT) or DEFAULT_API_PORT)
        self._docker_container_name = str(self._raw.get("docker_container_name", "diiisco-node") or "diiisco-node")
        self._network = str(self._raw.get("network", "mainnet") or "mainnet")

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
    def node_key(self) -> str:
        return self._node_key

    @node_key.setter
    def node_key(self, value: str) -> None:
        self._node_key = str(value)
        self._raw["node_key"] = self._node_key

    @property
    def api_port(self) -> int:
        return self._api_port

    @property
    def docker_container_name(self) -> str:
        return self._docker_container_name

    @property
    def network(self) -> str:
        return self._network

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
                    log_step("diiisco_config_save_failed", {"error": msg})
                    return
            except (ValueError, OSError):
                # If path is not in ProgramData (e.g., development), write directly
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(self._raw, handle, indent=2, sort_keys=True)
        except Exception as exc:
            log_step("diiisco_config_save_failed", {"error": str(exc)})


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
    """Locate and load Diiisco configuration file."""
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


def load_config() -> DiiiscoConfig:
    """Load Diiisco configuration."""
    path, existed, data = locate_config_file()
    return DiiiscoConfig(path=path, existed=existed, raw=data)


class DiiiscoController:
    """Controller to manage Diiisco Node lifecycle."""

    def __init__(self) -> None:
        self.config = load_config()
        self._last_error: Optional[str] = None
        self._diiisco_bin = self._find_diiisco_binary()
        self._docker_available = self._check_docker()
        self._process: Optional[subprocess.Popen] = None

    def _find_diiisco_binary(self) -> Optional[Path]:
        """Locate Diiisco node binary."""
        candidates = []

        # Check environment variable
        env_path = os.environ.get("DIIISCO_BIN") or os.environ.get("DIIISCO_PATH")
        if env_path:
            candidates.append(Path(env_path))

        # Platform-specific locations
        binary_name = "diiisco-node.exe" if os.name == "nt" else "diiisco-node"

        if os.name == "nt":
            # Windows locations
            program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            candidates.extend([
                Path(program_files) / "Diiisco" / binary_name,
                Path(local_app_data) / "Programs" / "Diiisco" / binary_name,
            ])
        else:
            # Linux/macOS locations
            candidates.extend([
                Path.home() / ".local" / "bin" / binary_name,
                Path("/usr/local/bin") / binary_name,
                Path("/usr/bin") / binary_name,
            ])

        # SDK folder
        try:
            candidates.append(data_dir_gui() / "SDK" / "diiisco-node" / binary_name)
        except Exception:
            pass
        candidates.append(app_dir() / "SDK" / "diiisco-node" / binary_name)
        candidates.append(Path.cwd() / "SDK" / "diiisco-node" / binary_name)

        for cand in candidates:
            try:
                if cand and cand.exists():
                    return cand
            except Exception:
                continue
        return None

    def _check_docker(self) -> bool:
        """Check if Docker is available."""
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                timeout=5,
                creationflags=creationflags,
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

    def _port_reachable(self, port: int) -> bool:
        """Check if API port is reachable."""
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except Exception:
            return False

    def _api_call(self, endpoint: str, method: str = "GET") -> Optional[Dict[str, Any]]:
        """Make an API call to the Diiisco node."""
        if not HAVE_REQUESTS:
            return None
        assert requests is not None
        try:
            url = f"http://127.0.0.1:{self.config.api_port}/api/v1{endpoint}"
            if method == "GET":
                resp = requests.get(url, timeout=5)
            else:
                resp = requests.post(url, timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            log_step("diiisco_api_failed", {"endpoint": endpoint, "error": str(exc)})
        return None

    def is_container_running(self) -> bool:
        """Check if Diiisco Docker container is running."""
        if not self._docker_available:
            return False
        code, out, _ = self._run_docker(
            ["ps", "--filter", f"name={self.config.docker_container_name}", "--format", "{{.Names}}"],
            timeout=10
        )
        return code == 0 and self.config.docker_container_name in out

    def is_container_exists(self) -> bool:
        """Check if Diiisco Docker container exists."""
        if not self._docker_available:
            return False
        code, out, _ = self._run_docker(
            ["ps", "-a", "--filter", f"name={self.config.docker_container_name}", "--format", "{{.Names}}"],
            timeout=10
        )
        return code == 0 and self.config.docker_container_name in out

    def is_running(self) -> bool:
        """Check if Diiisco node is running (binary or Docker)."""
        # Check Docker first
        if self.is_container_running():
            return True

        # Check via API
        if self._port_reachable(self.config.api_port):
            return True

        # Check via process name
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                if 'diiisco' in proc.info['name'].lower():
                    return True
        except Exception:
            pass

        return False

    def start(self) -> bool:
        """Start Diiisco node."""
        if self.is_running():
            log_step("diiisco_already_running")
            return True

        # Try Docker first
        if self._docker_available:
            return self._start_docker()

        # Try binary
        if self._diiisco_bin:
            return self._start_binary()

        self._last_error = "Neither Docker nor Diiisco binary available"
        log_step("diiisco_start_failed", {"reason": "no_runtime"})
        return False

    def _start_docker(self) -> bool:
        """Start Diiisco via Docker."""
        if self.is_container_running():
            return True

        # Start existing container
        if self.is_container_exists():
            code, _, err = self._run_docker(["start", self.config.docker_container_name], timeout=30)
            if code == 0:
                log_step("diiisco_container_started")
                return True
            self._last_error = err or "Failed to start container"
            return False

        # Create new container
        args = [
            "run", "-d",
            "--name", self.config.docker_container_name,
            "--restart", "unless-stopped",
            "-p", f"{self.config.api_port}:{self.config.api_port}",
        ]

        if self.config.node_key:
            args.extend(["-e", f"NODE_KEY={self.config.node_key}"])
        if self.config.network:
            args.extend(["-e", f"NETWORK={self.config.network}"])

        args.append("frycrypto/diiisco-node:latest")

        code, _, err = self._run_docker(args, timeout=120)
        if code == 0:
            log_step("diiisco_container_created")
            return True

        self._last_error = err or "Failed to create container"
        log_step("diiisco_docker_start_failed", {"error": err})
        return False

    def _start_binary(self) -> bool:
        """Start Diiisco via binary."""
        if not self._diiisco_bin:
            return False

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            args = [str(self._diiisco_bin)]

            if self.config.node_key:
                args.extend(["--node-key", self.config.node_key])
            if self.config.api_port:
                args.extend(["--api-port", str(self.config.api_port)])
            if self.config.network:
                args.extend(["--network", self.config.network])

            self._process = subprocess.Popen(
                args,
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log_step("diiisco_binary_started", {"pid": self._process.pid})
            time.sleep(2)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            log_step("diiisco_binary_start_failed", {"error": str(exc)})
            return False

    def stop(self) -> bool:
        """Stop Diiisco node."""
        success = True

        # Stop Docker container
        if self._docker_available and self.is_container_running():
            code, _, err = self._run_docker(["stop", self.config.docker_container_name], timeout=30)
            if code != 0:
                log_step("diiisco_container_stop_failed", {"error": err})
                success = False
            else:
                log_step("diiisco_container_stopped")

        # Stop binary process
        try:
            import psutil
            for proc in psutil.process_iter(['name', 'pid']):
                if 'diiisco' in proc.info['name'].lower():
                    proc.terminate()
                    log_step("diiisco_process_stopped", {"pid": proc.info['pid']})
        except Exception as exc:
            log_step("diiisco_process_stop_failed", {"error": str(exc)})

        return success

    def refresh_status(self) -> DiiiscoStatus:
        """Poll Diiisco node status."""
        running = self.is_running()
        connected = False
        node_id = None
        peer_count = 0
        discoveries_count = 0
        uptime_seconds = 0
        version = None
        network = self.config.network

        if running and self._port_reachable(self.config.api_port):
            # Get status from API
            status_data = self._api_call("/status")
            if status_data:
                connected = status_data.get("connected", False)
                node_id = status_data.get("node_id")
                peer_count = int(status_data.get("peer_count", 0) or 0)
                discoveries_count = int(status_data.get("discoveries_count", 0) or 0)
                uptime_seconds = int(status_data.get("uptime_seconds", 0) or 0)
                version = status_data.get("version")
                network = status_data.get("network") or network

        return DiiiscoStatus(
            configured=self.config.existed,
            enabled=self.config.enabled,
            running=running,
            connected=connected,
            node_id=node_id,
            peer_count=peer_count,
            discoveries_count=discoveries_count,
            uptime_seconds=uptime_seconds,
            last_error=self._last_error,
            last_refresh=datetime.utcnow(),
            version=version,
            network=network,
        )

    def enable(self) -> bool:
        """Enable Diiisco node."""
        self.config.enabled = True
        self.config.persist()
        return self.start()

    def disable(self) -> bool:
        """Disable Diiisco node."""
        self.config.enabled = False
        self.config.persist()
        return self.stop()

    @property
    def runtime_available(self) -> bool:
        """Check if Docker or binary is available."""
        return self._docker_available or self._diiisco_bin is not None

    def ensure_started(self) -> bool:
        """Ensure Diiisco is started if enabled."""
        if not self.config.enabled:
            return False
        if self.is_running():
            return True
        return self.start()

    def _update_status_json_on_offline(self) -> None:
        """Update today's status JSON to remove Diiisco from tools."""
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
                            if isinstance(tools, list) and "Diiisco" in tools:
                                tools.remove("Diiisco")
                                slot_data["tools"] = tools
                    except (ValueError, TypeError):
                        continue

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            log_step("status_json_update_failed", {"error": str(exc)})

    def mark_offline(self) -> None:
        """Call this when Diiisco is detected as offline."""
        self._update_status_json_on_offline()
