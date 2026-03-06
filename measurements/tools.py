"""Tool polling for BM miners.

Collects runtime stats from enabled tools:
- Mysterium: TequilAPI (/healthcheck, /traffic, /services)
- Presearch: Docker container + cloud API
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
DIIISCO_NODE_PORT = 8181
OLLAMA_DEFAULT_PORT = 11434
SPACE_ACRES_DEFAULT_RPC_PORT = 9944

# Cached poll results — written by the measurement daemon, read by the
# main PoC loop so it never blocks on Docker subprocess calls.
_last_presearch_result: Optional[Dict[str, Any]] = None
_last_diiisco_result: Optional[Dict[str, Any]] = None


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


def _post_json(url: str, payload: Dict[str, Any], timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """POST JSON to a URL and return the parsed response."""
    if not HAVE_REQUESTS or not requests:
        return None
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.debug("Failed to POST %s: %s", url, e)
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
# PRESEARCH
# ============================================================================

def get_cached_presearch() -> Optional[Dict[str, Any]]:
    """Return the last cached presearch poll result (non-blocking)."""
    return _last_presearch_result


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
    global _presearch_cloud_last_call, _presearch_cloud_cache, _last_presearch_result

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

    _last_presearch_result = result
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

def _docker_container_stats(container_name: str) -> Optional[Dict[str, Any]]:
    """Get CPU/memory stats for a Docker container via ``docker stats``.

    Returns dict with ``cpu_percent`` (float) and ``mem_usage_mb`` (float),
    or None if the container is not running or Docker is unavailable.
    """
    _cflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        completed = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.CPUPerc}}\t{{.MemUsage}}",
             container_name],
            capture_output=True, text=True, timeout=10,
            creationflags=_cflags,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        parts = completed.stdout.strip().split("\t")
        if len(parts) < 2:
            return None
        cpu_str = parts[0].replace("%", "").strip()
        mem_str = parts[1].split("/")[0].strip()  # e.g. "52.4MiB / 16GiB"
        cpu_percent = float(cpu_str)
        # Parse memory value (MiB, GiB, KiB)
        mem_mb = 0.0
        mem_upper = mem_str.upper()
        if "GIB" in mem_upper:
            mem_mb = float(mem_upper.replace("GIB", "").strip()) * 1024
        elif "MIB" in mem_upper:
            mem_mb = float(mem_upper.replace("MIB", "").strip())
        elif "KIB" in mem_upper:
            mem_mb = float(mem_upper.replace("KIB", "").strip()) / 1024
        elif "GB" in mem_upper:
            mem_mb = float(mem_upper.replace("GB", "").strip()) * 1000
        elif "MB" in mem_upper:
            mem_mb = float(mem_upper.replace("MB", "").strip())
        elif "KB" in mem_upper:
            mem_mb = float(mem_upper.replace("KB", "").strip()) / 1000
        return {"cpu_percent": round(cpu_percent, 2), "mem_usage_mb": round(mem_mb, 1)}
    except Exception as e:
        log.debug("docker stats failed for %s: %s", container_name, e)
        return None


def get_cached_diiisco() -> Optional[Dict[str, Any]]:
    """Return the last cached diiisco poll result (non-blocking)."""
    return _last_diiisco_result


def poll_diiisco() -> Dict[str, Any]:
    """Poll DIIISCO Docker Compose stack (Ollama + DIIISCO node).

    Monitors both containers:
    - Ollama health via :11434/api/tags
    - DIIISCO node liveness via port 8181 reachability
    - CPU/memory via ``docker stats`` for the diiisco-node container

    Returns dict with keys:
    - enabled: bool
    - running: bool
    - connected: bool
    - ollama_healthy: bool
    - model: str | None
    - cpu_percent: float | None
    - mem_usage_mb: float | None
    - status: str
    - error: str | None
    """
    global _last_diiisco_result
    result: Dict[str, Any] = {
        "enabled": False,
        "running": False,
        "connected": False,
        "ollama_healthy": False,
        "model": None,
        "cpu_percent": None,
        "mem_usage_mb": None,
        "status": "not_found",
        "error": None,
    }

    # Check Ollama health at :11434/api/tags
    if _port_reachable(OLLAMA_DEFAULT_PORT):
        ollama_data = _get_json(f"http://127.0.0.1:{OLLAMA_DEFAULT_PORT}/api/tags")
        if ollama_data is not None:
            result["ollama_healthy"] = True
            # Extract first model name from Ollama tag list
            models = ollama_data.get("models")
            if isinstance(models, list) and models:
                result["model"] = models[0].get("name", "unknown")

    # Check DIIISCO node at :8181
    if _port_reachable(DIIISCO_NODE_PORT):
        result["enabled"] = True
        result["running"] = True
        result["connected"] = True
        result["status"] = "running"

        # Get container resource usage via docker stats
        stats = _docker_container_stats("diiisco-node")
        if stats:
            result["cpu_percent"] = stats["cpu_percent"]
            result["mem_usage_mb"] = stats["mem_usage_mb"]

    _last_diiisco_result = result
    return result


# ============================================================================
# SPACE ACRES
# ============================================================================

def _query_substrate_sync_state(rpc_port: int = SPACE_ACRES_DEFAULT_RPC_PORT) -> Optional[Dict[str, Any]]:
    """Query the Substrate node's sync state via JSON-RPC.

    Makes three RPC calls:
    - system_health: {peers, isSyncing, shouldHavePeers}
    - system_syncState: {startingBlock, currentBlock, highestBlock}
    - chain_getFinalizedHead + chain_getHeader: finalized block number

    The node may report isSyncing=False (block imports caught up) while GRANDPA
    finalization is still thousands of blocks behind.  The farmer cannot start
    until finalization is reasonably close to chain head, so we expose
    ``finalizedBlock`` and ``finalizationGap`` for callers to decide.

    Returns combined dict or None if RPC unreachable.
    """
    if not _port_reachable(rpc_port):
        return None

    base_url = f"http://127.0.0.1:{rpc_port}"
    result: Dict[str, Any] = {}

    health = _post_json(base_url, {
        "jsonrpc": "2.0", "id": 1, "method": "system_health", "params": []
    })
    if health and isinstance(health.get("result"), dict):
        h = health["result"]
        result["peers"] = h.get("peers", 0)
        result["isSyncing"] = h.get("isSyncing", False)

    sync = _post_json(base_url, {
        "jsonrpc": "2.0", "id": 2, "method": "system_syncState", "params": []
    })
    if sync and isinstance(sync.get("result"), dict):
        s = sync["result"]
        result["currentBlock"] = s.get("currentBlock", 0)
        result["highestBlock"] = s.get("highestBlock", 0)

    current = result.get("currentBlock", 0)
    highest = result.get("highestBlock", 0)
    if highest > 0:
        result["syncPercent"] = round((current / highest) * 100, 2)
    else:
        result["syncPercent"] = 0.0

    # --- Finalization check ---
    # chain_getFinalizedHead → hash, then chain_getHeader(hash) → block number
    result["finalizedBlock"] = None
    result["finalizationGap"] = None
    finalized_resp = _post_json(base_url, {
        "jsonrpc": "2.0", "id": 3, "method": "chain_getFinalizedHead", "params": []
    })
    fin_hash = finalized_resp.get("result") if finalized_resp else None
    if fin_hash and isinstance(fin_hash, str):
        header_resp = _post_json(base_url, {
            "jsonrpc": "2.0", "id": 4, "method": "chain_getHeader", "params": [fin_hash]
        })
        if header_resp and isinstance(header_resp.get("result"), dict):
            hex_num = header_resp["result"].get("number", "0x0")
            try:
                finalized_block = int(hex_num, 16)
                result["finalizedBlock"] = finalized_block
                result["finalizationGap"] = current - finalized_block
            except (ValueError, TypeError):
                pass

    return result if result else None


def _read_autonomys_pid(pid_filename: str) -> Optional[int]:
    """Read a PID from ``{data_dir}/space-acres/run/<pid_filename>``.

    Resolves data_dir inline to avoid importing miner_online_simple
    (which runs as __main__ and would create a second module instance).
    """
    try:
        import sys as _sys
        if _sys.platform.startswith("win"):
            base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        else:
            base = "/var/lib"
        # Discover MINER_CODE from config_profile (already imported by main)
        try:
            import config_profile as _cfg
            miner_code = getattr(_cfg, "MINER_CODE", "")
        except ImportError:
            miner_code = ""
        if _sys.platform.startswith("win"):
            run_dir = os.path.join(base, "FryNetworks", f"miner-{miner_code}",
                                   "space-acres", "run")
        else:
            run_dir = os.path.join(base, "frynetworks", f"miner-{miner_code}",
                                   "space-acres", "run")
        p = os.path.join(run_dir, pid_filename)
        if not os.path.isfile(p):
            return None
        with open(p, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _is_pid_alive(pid: Optional[int]) -> bool:
    """Check whether a PID is alive (Windows-compatible)."""
    if pid is None:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        # Fallback: try os.kill signal 0 (works on Unix, may not on Windows)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def poll_space_acres() -> Dict[str, Any]:
    """Poll Space Acres native processes (node + farmer).

    Checks process liveness via ``_get_sa_process_state()`` and queries
    the Substrate RPC at localhost:9944 for blockchain sync state.

    Returns dict with keys:
    - enabled: bool     (at least one process is alive)
    - running: bool     (both node and farmer are alive)
    - node_healthy: bool (node RPC reachable)
    - farmer_running: bool
    - status: str       ("running", "syncing", "degraded", "stopped",
                          "not_created", "error")
    - isSyncing: bool | None   (from RPC, None if RPC unreachable)
    - currentBlock: int | None
    - highestBlock: int | None
    - syncPercent: float | None
    - peers: int | None
    - finalizedBlock: int | None
    - finalizationGap: int | None
    - error: str | None
    """
    result: Dict[str, Any] = {
        "enabled": False,
        "running": False,
        "node_healthy": False,
        "farmer_running": False,
        "status": "unknown",
        "error": None,
        "isSyncing": None,
        "currentBlock": None,
        "highestBlock": None,
        "syncPercent": None,
        "peers": None,
        "finalizedBlock": None,
        "finalizationGap": None,
    }

    # Step 1: Check process liveness via PID files (avoids double-import
    # issue when miner_online_simple runs as __main__ vs module).
    node_alive = _is_pid_alive(_read_autonomys_pid("node.pid"))
    farmer_alive = _is_pid_alive(_read_autonomys_pid("farmer.pid"))

    if not node_alive and not farmer_alive:
        result["status"] = "not_created"
        return result

    result["enabled"] = True
    result["farmer_running"] = farmer_alive

    # Step 2: Check node health via RPC reachability
    if node_alive:
        if _port_reachable(SPACE_ACRES_DEFAULT_RPC_PORT, timeout=2.0):
            result["node_healthy"] = True

    # Step 3: Query RPC for blockchain sync state
    if result["node_healthy"]:
        sync_state = _query_substrate_sync_state()
        if sync_state:
            result["isSyncing"] = sync_state.get("isSyncing")
            result["currentBlock"] = sync_state.get("currentBlock")
            result["highestBlock"] = sync_state.get("highestBlock")
            result["syncPercent"] = sync_state.get("syncPercent")
            result["peers"] = sync_state.get("peers")
            result["finalizedBlock"] = sync_state.get("finalizedBlock")
            result["finalizationGap"] = sync_state.get("finalizationGap")

    # Determine overall status
    # In --farmer mode, GRANDPA finalization lags behind chain head and
    # may never fully catch up.  The farmer can plot and submit solutions
    # against unfinalized blocks, so we treat the node as "running" once
    # block imports are at chain head (isSyncing=false), regardless of
    # finalizationGap.
    is_syncing = result.get("isSyncing")

    if node_alive and farmer_alive:
        if is_syncing:
            result["running"] = True
            result["status"] = "syncing"
        else:
            result["running"] = True
            result["status"] = "running"
    elif node_alive:
        if is_syncing:
            result["status"] = "syncing"
        else:
            result["status"] = "degraded"
    elif farmer_alive:
        result["status"] = "degraded"
    else:
        result["status"] = "stopped"

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
