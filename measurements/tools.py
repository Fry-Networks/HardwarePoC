"""Tool polling for BM miners.

Collects runtime stats from enabled tools:
- Mysterium: TequilAPI (/healthcheck, /traffic, /services)
- Bright: pythonnet SDK with BrightData.Api
- Honeygain: Native SDK (ctypes) with identify/traffic
- Presearch: Docker container + API
- Diiisco: Docker container + API  
- Space Acres: RPC endpoint polling

Tool stats are written to measurements/latest.json alongside hardware measurements.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import requests
    HAVE_REQUESTS = True
except ImportError:
    requests = None  # type: ignore
    HAVE_REQUESTS = False

log = logging.getLogger(__name__)

# Mysterium defaults
MYSTERIUM_DEFAULT_PORT = 4050
MYSTERIUM_WIREGUARD_PORT = 51820

# Tool API defaults
PRESEARCH_CLOUD_API_BASE = "https://nodes.presearch.com/api/nodes/status"
PRESEARCH_CLOUD_MIN_INTERVAL = 15.0  # seconds between cloud API calls (rate limit: 4/min)
_presearch_cloud_last_call: float = 0.0
_presearch_cloud_cache: Optional[Dict[str, Any]] = None
_presearch_last_remote_addrs: Optional[list[str]] = None
DIIISCO_MONITORING_PORT = 3001
OLLAMA_DEFAULT_PORT = 11434
SPACE_ACRES_DEFAULT_RPC_PORT = 9944


def _port_reachable(port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is reachable on localhost."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def _get_json(url: str, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Fetch JSON from a URL with timeout."""
    if not HAVE_REQUESTS or not requests:
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.debug("Failed to GET %s: %s", url, e)
        return None


# ============================================================================
# MYSTERIUM
# ============================================================================

def poll_mysterium() -> Dict[str, Any]:
    """Poll Mysterium TequilAPI for status and traffic stats.
    
    Returns CSV-compatible dict with keys:
    - online: bool (service running)
    - earnings_usd: float (estimated earnings)
    - sessions: int (active sessions)
    """
    port = int(os.environ.get("MYST_API_PORT", MYSTERIUM_DEFAULT_PORT))
    base_url = f"http://127.0.0.1:{port}"
    
    result = {
        "online": False,
        "earnings_usd": 0.0,
        "sessions": 0,
    }
    
    # Check port first
    if not _port_reachable(port):
        return result
    
    # Health check
    health = _get_json(f"{base_url}/healthcheck")
    if not health:
        return result
    
    result["online"] = True
    
    # Traffic stats (earnings proxy from sessions)
    traffic = _get_json(f"{base_url}/traffic")
    if traffic and isinstance(traffic, dict):
        sessions = traffic.get("sessions", 0)
        result["sessions"] = sessions
        # Rough estimate: $0.10 per session (adjust based on actual Mysterium rates)
        result["earnings_usd"] = round(sessions * 0.10, 2)
    
    return result


# ============================================================================
# BRIGHT
# ============================================================================

def poll_bright() -> Dict[str, Any]:
    """Poll Bright SDK status via pythonnet.
    
    Note: Requires pythonnet and BrightData.Api managed DLL.
    Returns dict with keys:
    - enabled: bool
    - running: bool
    - supported: bool | None
    - consent: bool | None
    - error: str | None
    """
    result = {
        "enabled": False,
        "running": False,
        "supported": None,
        "consent": None,
        "error": "Bright SDK polling not implemented in service",
    }
    
    # TODO: Import pythonnet and call BrightData.Api SDK
    # For now, return stub indicating GUI-only support
    
    return result


# ============================================================================
# HONEYGAIN
# ============================================================================

def poll_honeygain() -> Dict[str, Any]:
    """Poll Honeygain SDK status via ctypes.
    
    Note: Requires native SDK library (hgsdk.dll/so).
    Returns dict with keys:
    - enabled: bool
    - running: bool
    - device_id: str | None
    - traffic_total_bytes: int | None
    - traffic_24h_bytes: int | None
    - error: str | None
    """
    result = {
        "enabled": False,
        "running": False,
        "device_id": None,
        "traffic_total_bytes": None,
        "traffic_24h_bytes": None,
        "error": "Honeygain SDK polling not implemented in service",
    }
    
    # TODO: Load hgsdk via ctypes and call identify/traffic functions
    # For now, return stub indicating GUI-only support
    
    return result


# ============================================================================
# PRESEARCH
# ============================================================================

def poll_presearch(api_key: str = "") -> Dict[str, Any]:
    """Poll Presearch node status via Docker container state + cloud API.

    Presearch nodes do not expose a local HTTP endpoint, so monitoring uses:
    1. ``docker ps -a`` to determine container existence and running state
    2. Presearch cloud API at nodes.presearch.com for online/earnings data

    Args:
        api_key: Presearch API key from 1Password embedded credentials.

    Returns dict with keys:
    - enabled: bool  (container exists)
    - running: bool  (container is Up)
    - online: bool   (cloud API reports connected)
    - earnings_usd: float
    - status: str    ("running", "stopped", "not_created", "docker_missing", "error")
    - error: str | None
    """
    global _presearch_cloud_last_call, _presearch_cloud_cache

    result: Dict[str, Any] = {
        "enabled": False,
        "running": False,
        "online": False,
        "earnings_usd": 0.0,
        "status": "unknown",
        "error": None,
    }

    # Step 1: Check Docker container state
    _cflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        completed = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=presearch-node",
             "--format", "{{.Names}} {{.Status}}"],
            capture_output=True, text=True, timeout=5,
            creationflags=_cflags,
        )
        if completed.returncode == 0:
            output = completed.stdout.strip()
            if "presearch-node" in output:
                result["enabled"] = True
                if "Up" in output:
                    result["running"] = True
                    result["status"] = "running"
                else:
                    result["status"] = "stopped"
            else:
                result["status"] = "not_created"
        else:
            result["status"] = "docker_error"
    except FileNotFoundError:
        result["status"] = "docker_missing"
        return result
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        return result

    # Step 2: Cloud API (only if container is running and api_key provided)
    if not result["running"] or not api_key:
        result["online"] = result["running"]
        return result

    if not HAVE_REQUESTS or not requests:
        result["online"] = result["running"]
        return result

    # Rate-limited caching (15s between cloud API calls)
    now = time.time()
    if (now - _presearch_cloud_last_call) < PRESEARCH_CLOUD_MIN_INTERVAL and _presearch_cloud_cache:
        result["online"] = _presearch_cloud_cache.get("connected", False)
        result["earnings_usd"] = _presearch_cloud_cache.get("earnings_usd", 0.0)
        return result

    try:
        url = f"{PRESEARCH_CLOUD_API_BASE}/{api_key}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            _presearch_cloud_last_call = now
            # Cloud API returns per-node stats keyed by public key
            # Aggregate: consider online if any node is connected
            connected = False
            total_earnings = 0.0
            node_keys: list[str] = []
            remote_addrs: list[str] = []
            nodes_data = None
            if isinstance(data, dict):
                if isinstance(data.get("nodes"), dict):
                    nodes_data = data.get("nodes")
                else:
                    nodes_data = data
            if isinstance(nodes_data, dict):
                for _pk, node_data in nodes_data.items():
                    if not isinstance(node_data, dict):
                        continue
                    node_keys.append(str(_pk))
                    meta = node_data.get("meta") if isinstance(node_data.get("meta"), dict) else {}
                    remote_addr = ""
                    if isinstance(meta, dict):
                        remote_addr = str(meta.get("remote_addr", "") or "").strip()
                    if not remote_addr and isinstance(node_data.get("remote_addr"), str):
                        remote_addr = node_data.get("remote_addr", "").strip()
                    if remote_addr:
                        remote_addrs.append(remote_addr)
                    node_status = node_data.get("status", {})
                    if isinstance(node_status, dict) and node_status.get("connected"):
                        connected = True
                    period = node_data.get("period", {})
                    if isinstance(period, dict):
                        try:
                            total_earnings += float(period.get("total_reward_usd", 0.0))
                        except (ValueError, TypeError):
                            pass
            result["online"] = connected
            result["earnings_usd"] = total_earnings
            if node_keys:
                result["node_keys"] = node_keys
                if len(node_keys) == 1:
                    result["node_key"] = node_keys[0]
            if remote_addrs:
                result["remote_addrs"] = remote_addrs
                if len(remote_addrs) == 1:
                    result["remote_addr"] = remote_addrs[0]
                global _presearch_last_remote_addrs
                if _presearch_last_remote_addrs != remote_addrs:
                    log.info("Presearch remote_addr(s): %s", remote_addrs)
                    _presearch_last_remote_addrs = list(remote_addrs)
            _presearch_cloud_cache = {
                "connected": connected,
                "earnings_usd": total_earnings,
            }
        else:
            log.debug("Presearch cloud API returned %s", resp.status_code)
            result["online"] = result["running"]
    except Exception as e:
        log.debug("Presearch cloud API call failed: %s", e)
        result["online"] = result["running"]

    return result


def fetch_presearch_nodes(api_key: str) -> list[Dict[str, Any]]:
    """Fetch Presearch nodes and return normalized status entries.

    Each entry includes node_key, connected, blocked, description, remote_addr.
    """
    if not api_key:
        return []
    if not HAVE_REQUESTS or not requests:
        return []

    try:
        url = f"{PRESEARCH_CLOUD_API_BASE}/{api_key}"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    nodes_data: Optional[Dict[str, Any]] = None
    if isinstance(data, dict):
        if isinstance(data.get("nodes"), dict):
            nodes_data = data.get("nodes")
        else:
            nodes_data = data
    if not isinstance(nodes_data, dict):
        return []

    out: list[Dict[str, Any]] = []
    for node_key, node_data in nodes_data.items():
        if not isinstance(node_data, dict):
            continue
        meta = node_data.get("meta") if isinstance(node_data.get("meta"), dict) else {}
        status = node_data.get("status") if isinstance(node_data.get("status"), dict) else {}
        description = ""
        remote_addr = ""
        if isinstance(meta, dict):
            description = str(meta.get("description", "") or "").strip()
            remote_addr = str(meta.get("remote_addr", "") or "").strip()
        if not remote_addr and isinstance(node_data.get("remote_addr"), str):
            remote_addr = node_data.get("remote_addr", "").strip()
        out.append(
            {
                "node_key": str(node_key),
                "connected": bool(status.get("connected")) if isinstance(status, dict) else False,
                "blocked": bool(status.get("blocked")) if isinstance(status, dict) else False,
                "description": description,
                "remote_addr": remote_addr,
            }
        )
    return out


def get_public_ip() -> str:
    """Return this host's public IP (best-effort)."""
    if not HAVE_REQUESTS or not requests:
        return ""
    try:
        resp = requests.get("https://api.ipify.org?format=json", timeout=10)
        if resp.status_code != 200:
            return ""
        data = resp.json()
        if isinstance(data, dict):
            ip = data.get("ip")
            return ip.strip() if isinstance(ip, str) else ""
    except Exception:
        return ""
    return ""


# ============================================================================
# DIIISCO
# ============================================================================

def poll_diiisco() -> Dict[str, Any]:
    """Poll DIIISCO Docker Compose stack (Ollama + DIIISCO node).

    Monitors both containers:
    - Ollama health via :11434/api/tags
    - DIIISCO monitoring via :3001/stats

    Returns dict with keys:
    - enabled: bool
    - running: bool
    - connected: bool
    - ollama_healthy: bool
    - cpu_usage: dict | None
    - memory_usage: dict | None
    - uptime: float | None
    - model: str | None
    - error: str | None
    """
    result: Dict[str, Any] = {
        "enabled": False,
        "running": False,
        "connected": False,
        "ollama_healthy": False,
        "cpu_usage": None,
        "memory_usage": None,
        "uptime": None,
        "model": None,
        "error": None,
    }

    # Check Ollama health at :11434/api/tags
    if _port_reachable(OLLAMA_DEFAULT_PORT):
        ollama_data = _get_json(f"http://127.0.0.1:{OLLAMA_DEFAULT_PORT}/api/tags")
        if ollama_data is not None:
            result["ollama_healthy"] = True

    # Check DIIISCO monitoring at :3001/stats
    if _port_reachable(DIIISCO_MONITORING_PORT):
        result["enabled"] = True
        result["running"] = True
        stats = _get_json(f"http://127.0.0.1:{DIIISCO_MONITORING_PORT}/stats")
        if stats:
            result["connected"] = True
            result["cpu_usage"] = stats.get("cpu")
            result["memory_usage"] = stats.get("memory")
            result["uptime"] = stats.get("uptime")
            result["model"] = stats.get("model")

    return result


# ============================================================================
# SPACE ACRES
# ============================================================================

def poll_space_acres() -> Dict[str, Any]:
    """Poll Space Acres farming status via RPC endpoint.
    
    Returns dict with keys:
    - enabled: bool
    - running: bool
    - syncing: bool
    - farming: bool
    - plotted_space_gb: float
    - best_block: int | None
    - error: str | None
    """
    result = {
        "enabled": False,
        "running": False,
        "syncing": False,
        "farming": False,
        "plotted_space_gb": 0.0,
        "best_block": None,
        "error": None,
    }
    
    # Check RPC port
    port = int(os.environ.get("SPACE_ACRES_RPC_PORT", SPACE_ACRES_DEFAULT_RPC_PORT))
    if not _port_reachable(port):
        result["error"] = f"Space Acres RPC port {port} unreachable"
        return result
    
    result["enabled"] = True
    result["running"] = True
    
    # Poll RPC endpoint (Substrate JSON-RPC)
    # TODO: Implement proper Substrate RPC calls (system_health, system_syncState, etc.)
    # For now, return stub
    result["error"] = "Space Acres RPC polling not implemented"
    
    return result


# ============================================================================
# COLLECTOR
# ============================================================================

def collect_all_tool_stats(presearch_api_key: str = "", miner_code: str = "") -> Dict[str, Any]:
    """Collect stats from all enabled tools.

    Args:
        presearch_api_key: Presearch API key from 1Password embedded credentials.
        miner_code: Miner type code. Docker-based tools (presearch, diiisco) are
            only polled for RDN/SVN miners.

    Returns dict with tool names as keys and their stats as values.
    Only polls tools that are likely to be enabled based on environment or config.
    """
    stats = {}

    # Mysterium: Always poll if TequilAPI port is reachable
    mysterium_stats = poll_mysterium()
    if mysterium_stats.get("enabled"):
        stats["mysterium"] = mysterium_stats

    # Bright: Skip for now (requires pythonnet + managed DLL)
    # bright_stats = poll_bright()
    # if bright_stats.get("enabled"):
    #     stats["bright"] = bright_stats

    # Honeygain: Skip for now (requires native SDK)
    # honeygain_stats = poll_honeygain()
    # if honeygain_stats.get("enabled"):
    #     stats["honeygain"] = honeygain_stats

    # Docker-based tools: only poll for miner types that use them (RDN, SVN)
    if miner_code in ("RDN", "SVN", "SDN"):
        # Presearch: Poll via Docker + cloud API
        presearch_stats = poll_presearch(api_key=presearch_api_key)
        if presearch_stats.get("enabled"):
            stats["presearch"] = presearch_stats

        # Diiisco: Poll if Docker is available
        diiisco_stats = poll_diiisco()
        if diiisco_stats.get("enabled"):
            stats["diiisco"] = diiisco_stats

    # Space Acres: Poll if RPC port is reachable
    space_acres_stats = poll_space_acres()
    if space_acres_stats.get("enabled"):
        stats["space_acres"] = space_acres_stats

    return stats
