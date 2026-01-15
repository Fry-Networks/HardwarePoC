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
PRESEARCH_DEFAULT_PORT = 80
DIIISCO_DEFAULT_PORT = 8080
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
    
    Returns dict with keys:
    - enabled: bool (API reachable)
    - running: bool (service running)
    - provider_status: str | None
    - traffic: dict with bytes_sent, bytes_received, etc.
    - error: str | None
    """
    port = int(os.environ.get("MYST_API_PORT", MYSTERIUM_DEFAULT_PORT))
    base_url = f"http://127.0.0.1:{port}"
    
    result = {
        "enabled": False,
        "running": False,
        "provider_status": None,
        "traffic": {},
        "error": None,
    }
    
    # Check port first
    if not _port_reachable(port):
        result["error"] = f"Mysterium API port {port} unreachable"
        return result
    
    result["enabled"] = True
    
    # Health check
    health = _get_json(f"{base_url}/healthcheck")
    if health:
        result["running"] = True
        if isinstance(health, dict):
            state = health.get("state", {})
            if isinstance(state, dict):
                result["provider_status"] = state.get("service")
    
    # Traffic stats
    traffic = _get_json(f"{base_url}/traffic")
    if traffic and isinstance(traffic, dict):
        result["traffic"] = {
            "bytes_sent": traffic.get("bytes_sent", 0),
            "bytes_received": traffic.get("bytes_received", 0),
            "sessions": traffic.get("sessions", 0),
        }
    
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

def poll_presearch() -> Dict[str, Any]:
    """Poll Presearch node status via Docker container.
    
    Returns dict with keys:
    - enabled: bool
    - running: bool
    - connected: bool
    - node_id: str | None
    - uptime_hours: float
    - error: str | None
    """
    result = {
        "enabled": False,
        "running": False,
        "connected": False,
        "node_id": None,
        "uptime_hours": 0.0,
        "error": None,
    }
    
    # Check if presearch-node container is running
    try:
        completed = subprocess.run(
            ["docker", "ps", "--filter", "name=presearch-node", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode == 0 and "presearch-node" in completed.stdout:
            result["enabled"] = True
            result["running"] = True
    except Exception as e:
        result["error"] = f"Docker check failed: {e}"
        return result
    
    # Poll Presearch API (if running)
    if result["running"]:
        port = int(os.environ.get("PRESEARCH_API_PORT", PRESEARCH_DEFAULT_PORT))
        if _port_reachable(port):
            api_data = _get_json(f"http://127.0.0.1:{port}/api/node/status")
            if api_data:
                result["connected"] = api_data.get("connected", False)
                result["node_id"] = api_data.get("node_id")
                result["uptime_hours"] = api_data.get("uptime_hours", 0.0)
    
    return result


# ============================================================================
# DIIISCO
# ============================================================================

def poll_diiisco() -> Dict[str, Any]:
    """Poll Diiisco node status via Docker container.
    
    Returns dict with keys:
    - enabled: bool
    - running: bool
    - connected: bool
    - node_id: str | None
    - peer_count: int
    - discoveries_count: int
    - error: str | None
    """
    result = {
        "enabled": False,
        "running": False,
        "connected": False,
        "node_id": None,
        "peer_count": 0,
        "discoveries_count": 0,
        "error": None,
    }
    
    # Check if diiisco-node container is running
    try:
        completed = subprocess.run(
            ["docker", "ps", "--filter", "name=diiisco-node", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if completed.returncode == 0 and "diiisco-node" in completed.stdout:
            result["enabled"] = True
            result["running"] = True
    except Exception as e:
        result["error"] = f"Docker check failed: {e}"
        return result
    
    # Poll Diiisco API (if running)
    if result["running"]:
        port = int(os.environ.get("DIIISCO_API_PORT", DIIISCO_DEFAULT_PORT))
        if _port_reachable(port):
            api_data = _get_json(f"http://127.0.0.1:{port}/api/status")
            if api_data:
                result["connected"] = api_data.get("connected", False)
                result["node_id"] = api_data.get("node_id")
                result["peer_count"] = api_data.get("peer_count", 0)
                result["discoveries_count"] = api_data.get("discoveries_count", 0)
    
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

def collect_all_tool_stats() -> Dict[str, Any]:
    """Collect stats from all enabled tools.
    
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
    
    # Presearch: Poll if Docker is available
    presearch_stats = poll_presearch()
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
