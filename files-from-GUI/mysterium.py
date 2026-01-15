"""Mysterium node controller for FryNetworks BM miners.

This wraps the `myst` CLI (provided by the installer) to:
- start/stop the local service
- poll TequilAPI health and traffic stats
- write encrypted measurements for backend ingestion (no user-visible earnings)

Assumptions:
- Installer places `myst.exe` somewhere we can find (env MYST_BIN or SDK folders)
- Installer pre-seeds keystore + payout address; we will only use them after user consent
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import base64
import requests
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import request

from config_profile import MINER_CODE
try:
    # Build embeds this module with constants from 1Password; required at runtime.
    from miner_GUI.embedded_secrets import (
        MYST_PAYOUT_ADDR as EMBEDDED_PAYOUT_ADDR,
        MYST_REG_TOKEN as EMBEDDED_REG_TOKEN,
        MYST_API_KEY as EMBEDDED_API_KEY,
    )
except Exception:
    EMBEDDED_PAYOUT_ADDR = None
    EMBEDDED_REG_TOKEN = None
    EMBEDDED_API_KEY = None
from miner_GUI.config import app_dir
from miner_GUI.utils.data import data_dir_gui, log_step, read_miner_key
from miner_GUI.services import privileged_ops

try:
    import miner_GUI.utils.encryption  # type: ignore
    HAVE_ENCRYPT = True
except Exception:
    HAVE_ENCRYPT = False

try:
    import psutil  # type: ignore

    HAVE_PSUTIL = True
except Exception:
    psutil = None  # type: ignore
    HAVE_PSUTIL = False

DEFAULT_PORT = 4050  # TequilAPI default
WIREGUARD_PORT = 51820
WIREGUARD_PORT_RANGE = f"{WIREGUARD_PORT}:{WIREGUARD_PORT}"


@dataclass
class MysteriumStatus:
    """Lightweight status surface for UI."""

    enabled: bool
    running: bool
    warning: Optional[str]
    last_error: Optional[str]
    port_ok: bool
    api_ok: bool
    wireguard_port_ok: bool = True
    provider_status: Optional[str] = None
    quality: Optional[float] = None
    consent_given: bool = False


class MysteriumController:
    """Controller to manage myst service and poll metrics."""

    def __init__(self) -> None:
        self._miner_code = MINER_CODE
        self._myst_bin = self._find_myst_binary()
        self._data_dir = self._resolve_data_dir()
        self._port = self._resolve_port()
        self._last_totals: Dict[str, float] = {}
        # No fallback allowed: require embedded payout address at build time.
        self._payout_addr = EMBEDDED_PAYOUT_ADDR
        # Registration token and MMN API key are embedded; environment not relied on.
        self._reg_token = EMBEDDED_REG_TOKEN
        self._myst_api_key = EMBEDDED_API_KEY
        self._reg_marker = self._resolve_data_dir() / "myst_registered.flag"

    # ------------- discovery helpers -------------
    def _resolve_data_dir(self) -> Path:
        try:
            return data_dir_gui() / "mysterium"
        except Exception:
            return Path.cwd() / "mysterium"

    def _resolve_port(self) -> int:
        try:
            raw = os.environ.get("MYST_API_PORT")
            if raw:
                return int(raw)
        except Exception:
            pass
        return DEFAULT_PORT

    def _hardware_api_info(self) -> tuple[Optional[str], Optional[str]]:
        base = os.environ.get("HARDWARE_API_BASE") or "https://api.frynetworks.com/hardware"
        token = os.environ.get("HARDWARE_API_TOKEN")
        return base, token

    def _registration_params(self) -> tuple[Optional[str], Optional[str]]:
        return self._reg_token, self._payout_addr

    def _find_myst_binary(self) -> Optional[Path]:
        """Locate myst.exe via env or common SDK locations."""
        candidates = []
        env_path = os.environ.get("MYST_BIN") or os.environ.get("MYST_PATH")
        if env_path:
            candidates.append(Path(env_path))
        try:
            candidates.append(data_dir_gui() / "SDK" / "windows-myst-sdk" / "myst.exe")
        except Exception:
            pass
        candidates.append(app_dir() / "SDK" / "windows-myst-sdk" / "myst.exe")
        candidates.append(Path.cwd() / "SDK" / "windows-myst-sdk" / "myst.exe")
        candidates.append(Path.cwd() / "myst.exe")
        for cand in candidates:
            try:
                if cand and cand.exists():
                    return cand
            except Exception:
                continue
        return None

    def _has_keystore(self) -> bool:
        try:
            ks_dir = self._data_dir / "keystore"
            if not ks_dir.exists():
                return False
            return any(ks_dir.iterdir())
        except Exception:
            return False

    def _ensure_identity(self) -> None:
        """Create a new identity if none exists."""
        if self._has_keystore():
            return
        # Try to pull from backend first
        miner_key = ""
        try:
            miner_key = read_miner_key()
        except Exception:
            miner_key = ""
        if miner_key and self._pull_remote_keystore(miner_key):
            log_step("mysterium_keystore_restored", {"miner_key_present": True})
            return
        # Identity creation will be handled after service is running
        # via TequilAPI POST /identities in ensure_installed
        log_step("mysterium_identity_deferred", {"reason": "will_create_after_service_start"})

    def _ensure_firewall_port_windows(self) -> None:
        """Request service to open TequilAPI port in Windows firewall.
        
        Since this requires admin privileges, the service should handle it.
        Log outcome for diagnostics but don't block on failure.
        """
        if not os.name == "nt":
            return
        try:
            # Request the NSSM service to add the firewall rule
            success, msg = privileged_ops.add_firewall_rule(
                port=self._port,
                protocol="tcp",
                direction="in",
            )
            if not success:
                log_step("mysterium_firewall_rule_skipped", {
                    "port": self._port,
                    "reason": "service_call_failed",
                    "detail": msg,
                })
        except Exception as exc:
            log_step("mysterium_firewall_rule_exception", {"error": str(exc)})

    def _ensure_wireguard_firewall_port_windows(self) -> None:
        """Request service to open WireGuard UDP port 51820 in Windows firewall.
        
        Since this requires admin privileges, the service should handle it.
        Log outcome for diagnostics but don't block on failure.
        """
        if not os.name == "nt":
            return
        try:
            # Request the NSSM service to add both inbound and outbound rules
            success, msg = privileged_ops.add_firewall_rule(
                port=WIREGUARD_PORT,
                protocol="udp",
                direction="in,out",
            )
            if not success:
                log_step("mysterium_wireguard_firewall_rule_skipped", {
                    "port": WIREGUARD_PORT,
                    "reason": "service_call_failed",
                    "detail": msg,
                })
        except Exception as exc:
            log_step("mysterium_wireguard_firewall_rule_exception", {"error": str(exc)})


    def _read_keystore_bytes(self) -> Optional[bytes]:
        try:
            ks_dir = self._data_dir / "keystore"
            if not ks_dir.exists():
                return None
            for file in ks_dir.iterdir():
                if file.is_file():
                    return file.read_bytes()
        except Exception:
            return None
        return None

    def _write_keystore_bytes(self, payload: bytes) -> bool:
        try:
            ks_dir = self._data_dir / "keystore"
            ks_dir.mkdir(parents=True, exist_ok=True)
            target = ks_dir / "keystore.json"
            target.write_bytes(payload)
            return True
        except Exception as exc:
            log_step("mysterium_write_keystore_failed", {"error": str(exc)})
            return False

    def _pull_remote_keystore(self, miner_key: str) -> bool:
        base, token = self._hardware_api_info()
        if not base or not token or not miner_key:
            return False
        url = f"{base.rstrip('/')}/mysterium/keystore"
        try:
            resp = requests.get(url, params={"miner_key": miner_key}, timeout=8, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                return False
            data = resp.json()
            b64 = data.get("keystore_b64")
            if not b64:
                return False
            try:
                blob = base64.b64decode(b64)
            except Exception:
                return False
            if self._write_keystore_bytes(blob):
                return True
        except Exception as exc:
            log_step("mysterium_keystore_pull_failed", {"error": str(exc)})
        return False

    def _register_node(self) -> None:
        """One-time registration to MyMystNodes with referral token, beneficiary (payout), and MMN API key."""
        ident = self._current_identity()
        if not ident:
            log_step("mysterium_register_no_identity")
            return
        
        # Ensure identity is unlocked before any operation that requires signing
        try:
            self._unlock_identity()
        except Exception:
            pass
        
        # Set MMN API key early (for node claiming). Beneficiary will be set after registration to ensure persistence.
        if self._myst_api_key:
            self._set_mmn_api_key(self._myst_api_key)
        
        # Early return if already registered (marker exists)
        if self._reg_marker.exists():
            return
        
        # Check if identity is already registered on Mysterium network
        if self._is_identity_registered(ident):
            self._reg_marker.write_text("registered", encoding="utf-8")
            log_step("mysterium_already_registered", {"id": ident})
            return
        
        # 1. Register identity with referral code and beneficiary (payout address)
        if not self._reg_token:
            return
        try:
            payload = {"referralCode": self._reg_token}
            if self._payout_addr:
                payload["beneficiary"] = self._payout_addr
                payload["is_channel_address"] = "false"

            url = f"http://127.0.0.1:{self._port}/identities/{ident}/register"
            resp = requests.post(
                url,
                json=payload,
                timeout=10,
            )
            if resp.status_code in (200, 202, 204):
                self._reg_marker.write_text("registered", encoding="utf-8")
                log_step("mysterium_registered", {
                    "id": ident,
                    "has_beneficiary": bool(self._payout_addr),
                    "has_mmn_key": bool(self._myst_api_key)
                })
                # 2. Set beneficiary again to ensure it persists post-registration
                if self._payout_addr:
                    self._set_beneficiary(ident, self._payout_addr)
            else:
                log_step("mysterium_register_failed", {"status": resp.status_code, "text": resp.text[:200], "id": ident})
        except Exception as exc:
            log_step("mysterium_register_exception", {"error": str(exc)})

    def _is_identity_registered(self, identity: str) -> bool:
        """Check if identity is already registered on Mysterium network via TequilAPI."""
        try:
            url = f"http://127.0.0.1:{self._port}/identities/{identity}/registration"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return bool(data.get("registered", False))
        except Exception:
            pass
        return False

    def _set_mmn_api_key(self, api_key: str) -> bool:
        """Set MMN API key for node claiming on mystnodes.com."""
        try:
            url = f"http://127.0.0.1:{self._port}/mmn/api-key"
            resp = requests.post(url, json={"api_key": api_key}, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _set_beneficiary(self, identity: str, beneficiary_addr: str) -> bool:
        """Set beneficiary (payout) address for an identity.
        TequilAPI expects both 'beneficiary' and 'is_channel_address'.
        """
        try:
            url = f"http://127.0.0.1:{self._port}/identities/{identity}/beneficiary"
            payload = {"beneficiary": beneficiary_addr, "is_channel_address": False}
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code in (200, 202, 204):
                log_step("mysterium_beneficiary_set", {"id": identity, "beneficiary": beneficiary_addr})
                return True
            else:
                # Include response body for troubleshooting
                text = resp.text[:500] if hasattr(resp, "text") else ""
                log_step("mysterium_beneficiary_failed", {"status": resp.status_code, "text": text})
                return False
        except Exception as exc:
            log_step("mysterium_beneficiary_exception", {"error": str(exc)})
            return False

    def _unlock_identity(self) -> bool:
        """Unlock the current identity with empty passphrase (for service restart recovery)."""
        try:
            identity = self._current_identity()
            if not identity:
                return False
            
            if not self._port_reachable():
                return False
            
            url = f"http://127.0.0.1:{self._port}/identities/{identity}/unlock"
            resp = requests.put(url, json={"passphrase": ""}, timeout=10)
            
            if resp.status_code in (200, 202):
                log_step("mysterium_identity_unlocked", {"id": identity})
                return True
            else:
                log_step("mysterium_identity_unlock_failed", {"status": resp.status_code, "text": resp.text[:200]})
                return False
        except Exception as exc:
            log_step("mysterium_identity_unlock_exception", {"error": str(exc)})
            return False

    def _start_wireguard_service(self) -> bool:
        """Start the WireGuard service via TequilAPI POST /services."""
        try:
            identity = self._current_identity()
            if not identity:
                log_step("mysterium_service_start_failed_no_identity", {})
                return False
            
            if not self._port_reachable():
                return False
            
            # Ensure identity is unlocked before starting services
            try:
                self._unlock_identity()
            except Exception:
                pass
            
            # Check if service already running
            resp = requests.get(f"http://127.0.0.1:{self._port}/services", timeout=5)
            if resp.status_code == 200:
                services = resp.json()
                # Handle multiple formats:
                # - list of services
                # - dict with "value": list, "Count": int
                # - single service dict with status
                if isinstance(services, list):
                    if len(services) > 0:
                        log_step("mysterium_service_already_running", {"count": len(services)})
                        return True
                elif isinstance(services, dict):
                    val = services.get("value")
                    if isinstance(val, list) and len(val) > 0:
                        log_step("mysterium_service_already_running", {"count": len(val)})
                        return True
                    if services.get("status") == "Running":
                        log_step("mysterium_service_already_running", {"count": 1})
                        return True
            
            # Start WireGuard service
            url = f"http://127.0.0.1:{self._port}/services"
            payload = {
                "provider_id": identity,
                "type": "wireguard"
            }
            resp = requests.post(url, json=payload, timeout=10)
            
            if resp.status_code in (201, 200):
                log_step("mysterium_service_started", {"type": "wireguard", "provider_id": identity})
                return True
            else:
                # Retry once after attempting unlock if authentication error
                if resp.status_code in (401, 403, 500):
                    try:
                        self._unlock_identity()
                    except Exception:
                        pass
                    resp2 = requests.post(url, json=payload, timeout=10)
                    if resp2.status_code in (201, 200):
                        log_step("mysterium_service_started", {"type": "wireguard", "provider_id": identity, "retry": True})
                        return True
                    log_step("mysterium_service_start_failed", {"status": resp2.status_code, "text": resp2.text[:200], "retry": True})
                else:
                    log_step("mysterium_service_start_failed", {"status": resp.status_code, "text": resp.text[:200]})
                return False
        except Exception as exc:
            log_step("mysterium_service_start_exception", {"error": str(exc)})
            return False

    def _start_service(self, service_type: str) -> bool:
        """Start a specific service via TequilAPI POST /services.
        Args:
            service_type: one of "dvpn", "data_transfer", "scraping", "monitoring", etc.
        Returns:
            True if the service is running or was started successfully.
        """
        try:
            identity = self._current_identity()
            if not identity or not self._port_reachable():
                return False

            # Ensure identity is unlocked before starting services
            try:
                self._unlock_identity()
            except Exception:
                pass

            # Skip if already running
            try:
                resp = requests.get(f"http://127.0.0.1:{self._port}/services", timeout=5)
                if resp.status_code == 200:
                    body = resp.json()
                    items = []
                    if isinstance(body, list):
                        items = body
                    elif isinstance(body, dict):
                        if body.get("status") == "Running" and body.get("type") == service_type:
                            return True
                        val = body.get("value")
                        if isinstance(val, list):
                            items = val
                    for s in items:
                        if isinstance(s, dict) and s.get("type") == service_type and s.get("status") == "Running":
                            return True
            except Exception:
                pass

            # Start service
            url = f"http://127.0.0.1:{self._port}/services"
            payload = {"provider_id": identity, "type": service_type}
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code in (200, 201, 202):
                log_step("mysterium_service_started", {"type": service_type, "provider_id": identity})
                return True
            # Simple retry on potential unlock/lag issues
            if resp.status_code in (401, 403, 500):
                try:
                    self._unlock_identity()
                except Exception:
                    pass
                resp2 = requests.post(url, json=payload, timeout=10)
                if resp2.status_code in (200, 201, 202):
                    log_step("mysterium_service_started", {"type": service_type, "provider_id": identity, "retry": True})
                    return True
                log_step("mysterium_service_start_failed", {"type": service_type, "status": resp2.status_code, "text": (resp2.text or "")[:200], "retry": True})
                return False
            log_step("mysterium_service_start_failed", {"type": service_type, "status": resp.status_code, "text": (resp.text or "")[:200]})
            return False
        except Exception as exc:
            log_step("mysterium_service_start_exception", {"type": service_type, "error": str(exc)})
            return False

    def start_additional_services(self, types: Optional[List[str]] = None) -> Dict[str, bool]:
        """Start a set of provider services after WireGuard.

        If types is None, starts dvpn, data_transfer, scraping, and monitoring.
        Returns a map of service_type -> started(bool).
        """
        results: Dict[str, bool] = {}
        if types is None:
            types = ["dvpn", "data_transfer", "scraping", "monitoring"]
        for t in types:
            try:
                results[t] = self._start_service(t)
            except Exception:
                results[t] = False
        return results

    def _create_identity_via_api(self) -> None:
        """Create a new identity via TequilAPI (requires service to be running)."""
        try:
            # Wait a moment for service to be ready
            for _ in range(5):
                if self._port_reachable():
                    break
                time.sleep(1)
            
            if not self._port_reachable():
                log_step("mysterium_api_unreachable", {"port": self._port, "reason": "port_not_reachable"})
                raise RuntimeError(
                    f"Mysterium API (port {self._port}) is not reachable. "
                    f"Check if the service is running and port {self._port} is not blocked by firewall."
                )
            
            # Check via API if identity already exists before creating
            existing = self._current_identity()
            if existing:
                log_step("mysterium_identity_already_exists_api", {"id": existing})
                return
            
            # Create identity via POST /identities
            url = f"http://127.0.0.1:{self._port}/identities"
            resp = requests.post(url, json={"passphrase": ""}, timeout=10)
            
            if resp.status_code in (200, 201):
                data = resp.json()
                identity_id = data.get("id") or data.get("address")
                log_step("mysterium_identity_created_api", {"id": identity_id})
                
                # Push to backend
                miner_key = ""
                try:
                    miner_key = read_miner_key()
                except Exception:
                    pass
                if miner_key:
                    self._push_remote_keystore(miner_key)
            else:
                log_step("mysterium_identity_create_failed", {"status": resp.status_code, "text": resp.text[:200]})
        except requests.exceptions.ConnectionError as exc:
            log_step("mysterium_api_connection_error", {"port": self._port, "error": str(exc)})
            raise RuntimeError(
                f"Cannot connect to Mysterium API on port {self._port}. "
                f"Ensure the Mysterium service is running and port {self._port} is accessible (not blocked by firewall)."
            ) from exc
        except requests.exceptions.Timeout as exc:
            log_step("mysterium_api_timeout", {"port": self._port})
            raise RuntimeError(
                f"Mysterium API (port {self._port}) timed out. The service may be overloaded or the port may be blocked."
            ) from exc
        except Exception as exc:
            log_step("mysterium_identity_create_exception", {"error": str(exc)})
            raise

    def _current_identity(self) -> Optional[str]:
        """Return current identity address via TequilAPI /identities."""
        try:
            resp = requests.get(f"http://127.0.0.1:{self._port}/identities", timeout=5)
            if resp.status_code != 200:
                return None
            data = resp.json()
            identities = data.get("identities") or data.get("items") or []
            if identities and isinstance(identities, list):
                first = identities[0]
                if isinstance(first, dict):
                    return first.get("id") or first.get("address")
        except Exception:
            return None
        return None

    def _push_remote_keystore(self, miner_key: str) -> bool:
        base, token = self._hardware_api_info()
        if not base or not token or not miner_key:
            return False
        blob = self._read_keystore_bytes()
        if not blob:
            return False
        url = f"{base.rstrip('/')}/mysterium/keystore"
        payload = {
            "miner_key": miner_key,
            "keystore_b64": base64.b64encode(blob).decode("utf-8"),
        }
        try:
            resp = requests.put(
                url,
                json=payload,
                timeout=8,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code in (200, 201, 204):
                return True
            log_step("mysterium_keystore_push_failed", {"status": resp.status_code, "text": resp.text[:200]})
        except Exception as exc:
            log_step("mysterium_keystore_push_exception", {"error": str(exc)})
        return False

    def _get_terms_agreement(self) -> bool:
        """Check if user has agreed to Mysterium terms via TequilAPI /terms."""
        try:
            resp = requests.get(f"http://127.0.0.1:{self._port}/terms", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                # agreed_provider=true means user accepted provider terms
                return bool(data.get("agreed_provider", False))
        except Exception:
            pass
        return False

    def _check_inbound_reachability(self) -> bool:
        """Pre-flight check: test if UDP 51820 is reachable from outside.
        
        This checks if the port can be bound locally (basic test).
        For a full external reachability test, users should ensure port forwarding is configured.
        
        Returns:
            True if port can be bound locally, False otherwise.
        """
        try:
            # Quick test: try to bind to 51820 locally to confirm firewall allows it
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Try to bind; if it fails, firewall or other process is blocking
            test_socket.bind(("127.0.0.1", WIREGUARD_PORT))
            test_socket.close()
            log_step("mysterium_port_51820_reachable", {"status": "ok"})
            return True
        except OSError as exc:
            log_step("mysterium_port_51820_unreachable", {"error": str(exc)})
            return False
        except Exception as exc:
            log_step("mysterium_port_check_exception", {"error": str(exc)})
            return False

    def _wireguard_port_listening(self) -> bool:
        """Check if WireGuard UDP port still has a listener (best effort)."""
        if not HAVE_PSUTIL:
            # If psutil isn't available, assume OK to avoid false warnings.
            return True
        try:
            for conn in psutil.net_connections(kind="udp"):  # type: ignore[arg-type]
                laddr = getattr(conn, "laddr", None)
                if not laddr:
                    continue
                with suppress(Exception):
                    if isinstance(laddr, tuple):
                        port = laddr[1]
                    else:
                        port = getattr(laddr, "port", None)
                    if port == WIREGUARD_PORT:
                        return True
        except Exception as exc:
            log_step("mysterium_wireguard_probe_failed", {"error": str(exc)})
            return True
        return False

    def accept_terms(self, version: str = "0.0.53") -> bool:
        """Update terms agreement via TequilAPI POST /terms.
        
        Args:
            version: Terms version to accept (default 0.0.53).
            
        Returns:
            True if terms were successfully accepted.
        """
        try:
            payload = {
                "agreed_provider": True,
                "agreed_consumer": False,
                "agreed_version": version,
            }
            resp = requests.post(
                f"http://127.0.0.1:{self._port}/terms",
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                log_step("mysterium_terms_accepted", {"version": version})
                return True
            log_step("mysterium_terms_accept_failed", {"status": resp.status_code, "text": resp.text[:200]})
        except Exception as exc:
            log_step("mysterium_terms_accept_exception", {"error": str(exc)})
        return False

    # ------------- command helpers -------------
    def _run(self, args: list[str], timeout: int = 20) -> Tuple[int, str, str]:
        cmd = [str(self._myst_bin)] + args if self._myst_bin else args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
                encoding="utf-8",
                errors="ignore",
            )
            return proc.returncode, proc.stdout, proc.stderr
        except Exception as exc:
            return 1, "", str(exc)

    # ------------- lifecycle -------------
    def _install_windows_service_via_nssm(self) -> bool:
        """Install Mysterium daemon as Windows service using NSSM."""
        try:
            nssm_path = Path("nssm.exe").resolve()
            if not nssm_path.exists():
                # Try ProgramData location
                nssm_path = Path("C:/ProgramData/FryNetworks") / f"miner-{self._miner_code}" / "nssm.exe"
            if not nssm_path.exists():
                log_step("mysterium_nssm_not_found", {"expected": str(nssm_path)})
                return False
            
            service_name = "MysteriumNode"
            
            # Check if service already exists and verify its parameters
            check_status = subprocess.run(
                [str(nssm_path), "status", service_name],
                capture_output=True,
                timeout=5,
                encoding="utf-8",
                errors="ignore",
            )
            
            if check_status.returncode == 0:
                # Service exists - check if AppParameters are correct
                get_params = subprocess.run(
                    [str(nssm_path), "get", service_name, "AppParameters"],
                    capture_output=True,
                    timeout=5,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                current_params = (get_params.stdout or "").replace("\x00", "").strip()
                log_step("mysterium_service_current_params", {"params": current_params})
                # If existing params include unsupported flags or miss our fixed port settings, update in place
                desired_app_params = [
                    f"--data-dir={self._data_dir}",
                    f"--tequilapi.address=127.0.0.1",
                    f"--tequilapi.port={self._port}",
                    f"--udp.ports={WIREGUARD_PORT_RANGE}",
                    "daemon",
                ]
                desired_params_str = " ".join(desired_app_params)
                missing_fixed_udp = f"--udp.ports={WIREGUARD_PORT_RANGE}" not in current_params
                wrong_port = f"--tequilapi.port={self._port}" not in current_params
                wrong_datadir = f"--data-dir={self._data_dir}" not in current_params
                missing_daemon = "daemon" not in current_params
                if (
                    missing_fixed_udp
                    or wrong_port
                    or wrong_datadir
                    or missing_daemon
                ):
                    # Update AppParameters to supported, deterministic value
                    set_params = subprocess.run(
                        [str(nssm_path), "set", service_name, "AppParameters", desired_params_str],
                        capture_output=True,
                        timeout=10,
                        encoding="utf-8",
                        errors="ignore",
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    log_step(
                        "mysterium_service_params_updated",
                        {
                            "code": set_params.returncode,
                            "old": current_params,
                            "new": desired_params_str,
                            "err": (set_params.stderr or "")[:200],
                        },
                    )
                    # Try to start or continue the service later in the lifecycle
                    log_step("mysterium_service_already_installed_nssm", {"service": service_name})
                    return True
                
                # If params contain wrong flag order (daemon before --data-dir), remove and reinstall
                # Also reinstall if it has the old unsupported agreement flags
                if "daemon" in current_params and "--data-dir" in current_params:
                    daemon_pos = current_params.find("daemon")
                    datadir_pos = current_params.find("--data-dir")
                    has_wrong_order = daemon_pos < datadir_pos
                    has_old_flags = "--agreed-terms-and-conditions" in current_params
                    
                    if has_wrong_order or has_old_flags:
                        reason = "wrong_order" if has_wrong_order else "unsupported_flags"
                        log_step("mysterium_service_wrong_param_order", {"params": current_params, "reason": reason})
                        # Stop and delete the service
                        try:
                            subprocess.run(
                                ["sc", "stop", service_name],
                                capture_output=True,
                                timeout=10,
                                encoding="utf-8",
                                errors="ignore",
                                creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                            time.sleep(1)
                        except Exception:
                            pass
                        try:
                            subprocess.run(
                                ["sc", "delete", service_name],
                                capture_output=True,
                                timeout=10,
                                encoding="utf-8",
                                errors="ignore",
                                creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                            log_step("mysterium_service_deleted_for_reinstall", {})
                            time.sleep(1)
                        except Exception:
                            pass
                    else:
                        log_step("mysterium_service_already_installed_nssm", {"service": service_name})
                        return True
                else:
                    log_step("mysterium_service_already_installed_nssm", {"service": service_name})
                    return True
            
            # Install service with NSSM, passing explicit data dir and tequilapi port
            log_dir = self._data_dir / "logs"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            # Flags must come BEFORE 'daemon' command in myst CLI
            # Terms acceptance must be done via TequilAPI after daemon starts, not via CLI flags
            # Use fixed UDP port 51820 for WireGuard to enable port-forwarding and inbound reachability
            app_params = [
                f"--data-dir={self._data_dir}",
                f"--tequilapi.address=127.0.0.1",
                f"--tequilapi.port={self._port}",
                f"--udp.ports={WIREGUARD_PORT_RANGE}",
                "daemon",
            ]

            install_args = [
                str(nssm_path),
                "install",
                service_name,
                str(self._myst_bin),
                *app_params,
            ]
            
            result = subprocess.run(
                install_args,
                capture_output=True,
                timeout=20,
                encoding="utf-8",
                errors="ignore",
            )
            
            if result.returncode != 0:
                log_step("mysterium_nssm_install_failed", {
                    "code": result.returncode,
                    "out": result.stdout[:200],
                    "err": result.stderr[:200]
                })
                return False
            
            # Set service parameters
            params = [
                ("AppDirectory", str(self._data_dir)),
                ("DisplayName", "Mysterium Node"),
                ("Description", "Mysterium Network VPN Node"),
                ("Start", "SERVICE_AUTO_START"),
                ("AppStdout", str(log_dir / "mysterium_service.out.log")),
                ("AppStderr", str(log_dir / "mysterium_service.err.log")),
            ]
            
            for param, value in params:
                subprocess.run(
                    [str(nssm_path), "set", service_name, param, value],
                    capture_output=True,
                    timeout=5,
                )
            
            log_step("mysterium_service_installed_nssm", {"service": service_name, "app": str(self._myst_bin), "params": app_params})
            return True
            
        except Exception as exc:
            log_step("mysterium_nssm_install_exception", {"error": str(exc)})
            return False

    def ensure_installed(self, payout_addr: Optional[str] = None) -> None:
        """Best-effort install; no-op if already installed.
        
        Pre-flight check: UDP 51820 must be reachable (firewall rule required).
        """
        if not self._myst_bin:
            raise RuntimeError("myst binary not found")
        
        # Pre-flight check: ensure UDP 51820 is not blocked by Windows firewall
        if not self._check_inbound_reachability():
            raise RuntimeError(
                f"UDP port {WIREGUARD_PORT} is blocked by Windows firewall or already in use. "
                f"Please add a firewall rule: netsh advfirewall firewall add rule name=\"Mysterium WireGuard UDP {WIREGUARD_PORT}\" dir=in action=allow protocol=UDP localport={WIREGUARD_PORT}. "
                f"Also ensure your router has port {WIREGUARD_PORT} (UDP) forwarded to this device."
            )
        
        # Update payout address if provided (overrides environment variable)
        if payout_addr:
            self._payout_addr = payout_addr
        
        log_step("mysterium_ensure_installed", {"myst_bin": str(self._myst_bin)})
        
        # Check if MysteriumNode service exists with a wrong target (Application not myst.exe)
        # and remove it so we can reinstall correctly
        if sys.platform == "win32":
            try:
                nssm_path = Path("nssm.exe").resolve()
                if not nssm_path.exists():
                    nssm_path = Path("C:/ProgramData/FryNetworks") / f"miner-{self._miner_code}" / "nssm.exe"
                if nssm_path.exists():
                    nssm_app = subprocess.run(
                        [str(nssm_path), "get", "MysteriumNode", "Application"],
                        capture_output=True,
                        timeout=8,
                        encoding="utf-8",
                        errors="ignore",
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    # Strip null bytes from UTF-16 encoded output
                    app_path = (nssm_app.stdout or "").replace("\x00", "").strip()
                    if nssm_app.returncode == 0 and app_path:
                        log_step("mysterium_service_existing_application", {"application": app_path})
                        if "myst.exe" not in app_path.lower():
                            log_step("mysterium_service_corrupted_detected", {"application": app_path})
                            try:
                                subprocess.run(
                                    ["sc", "stop", "MysteriumNode"],
                                    capture_output=True,
                                    timeout=10,
                                    encoding="utf-8",
                                    errors="ignore",
                                    creationflags=subprocess.CREATE_NO_WINDOW,
                                )
                                time.sleep(1)
                            except Exception:
                                pass
                            try:
                                del_result = subprocess.run(
                                    ["sc", "delete", "MysteriumNode"],
                                    capture_output=True,
                                    timeout=10,
                                    encoding="utf-8",
                                    errors="ignore",
                                    creationflags=subprocess.CREATE_NO_WINDOW,
                                )
                                log_step("mysterium_service_deleted", {"code": del_result.returncode})
                            except Exception as exc:
                                log_step("mysterium_service_delete_failed", {"error": str(exc)})
                    else:
                        log_step("mysterium_service_existing_application", {"application": app_path or "", "code": nssm_app.returncode})
            except Exception:
                pass
        
        # Try to restore identity from backend first (before service install)
        self._ensure_identity()
        
        # On Windows, prefer NSSM service wrapper (avoids myst service install terms issues)
        if sys.platform == "win32":
            if self._install_windows_service_via_nssm():
                self._ensure_firewall_port_windows()
                self._ensure_wireguard_firewall_port_windows()
                try:
                    self.start()
                except Exception:
                    pass
                # If port still not reachable, capture Windows service status for diagnostics
                if not self._port_reachable():
                    try:
                        sc_q = subprocess.run(
                            ["sc", "query", "MysteriumNode"],
                            capture_output=True,
                            timeout=10,
                            encoding="utf-8",
                            errors="ignore",
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        log_step(
                            "mysterium_service_status",
                            {
                                "code": sc_q.returncode,
                                "out": (sc_q.stdout or "")[:400],
                                "err": (sc_q.stderr or "")[:200],
                            },
                        )
                        # If PAUSED, try to resume it
                        if "PAUSED" in (sc_q.stdout or ""):
                            log_step("mysterium_service_paused_detected", {})
                            try:
                                sc_continue = subprocess.run(
                                    ["sc", "continue", "MysteriumNode"],
                                    capture_output=True,
                                    timeout=10,
                                    encoding="utf-8",
                                    errors="ignore",
                                    creationflags=subprocess.CREATE_NO_WINDOW,
                                )
                                log_step("mysterium_service_continue_attempt", {
                                    "code": sc_continue.returncode,
                                    "out": (sc_continue.stdout or "")[:200],
                                })
                                # Wait for port after resume
                                for _ in range(10):
                                    if self._port_reachable():
                                        break
                                    try:
                                        time.sleep(0.5)
                                    except Exception:
                                        pass
                            except Exception as exc:
                                log_step("mysterium_service_continue_exception", {"error": str(exc)})
                        # If STOPPED, get service config to see binary path
                        if "STOPPED" in (sc_q.stdout or ""):
                            sc_qc = subprocess.run(
                                ["sc", "qc", "MysteriumNode"],
                                capture_output=True,
                                timeout=10,
                                encoding="utf-8",
                                errors="ignore",
                                creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                            log_step(
                                "mysterium_service_config",
                                {
                                    "code": sc_qc.returncode,
                                    "out": (sc_qc.stdout or "")[:600],
                                },
                            )
                        # Try to read recent myst daemon logs from NSSM stderr/stdout
                        try:
                            log_dir = self._data_dir / "logs"
                            err_log = log_dir / "mysterium_service.err.log"
                            out_log = log_dir / "mysterium_service.out.log"
                            if err_log.exists():
                                recent_err = err_log.read_text(encoding="utf-8", errors="ignore")[-2000:]
                                if recent_err.strip():
                                    log_step("mysterium_daemon_stderr", {"tail": recent_err})
                            if out_log.exists():
                                recent_out = out_log.read_text(encoding="utf-8", errors="ignore")[-2000:]
                                if recent_out.strip():
                                    log_step("mysterium_daemon_stdout", {"tail": recent_out})
                        except Exception:
                            pass
                    except Exception as exc:
                        log_step("mysterium_service_status_exception", {"error": str(exc)})
                # Attempt identity creation only if keystore missing, but don't fail install on API issues
                if not self._has_keystore():
                    try:
                        self._create_identity_via_api()
                    except Exception as exc:
                        log_step("mysterium_identity_create_skipped", {"error": str(exc)})
                self._register_node()
                return
        
        # Fallback: check myst service status
        code, out, err = self._run(["service", "status"], timeout=10)
        if code == 0 and "installed" in (out + err).lower():
            # Service already installed; try to ensure identity via API, but don't fail if unreachable
            if not self._has_keystore():
                try:
                    self._create_identity_via_api()
                except Exception as exc:
                    log_step("mysterium_identity_create_skipped", {"error": str(exc)})
            return

        args = [
            "service",
            "install",
            "--data-dir",
            str(self._data_dir),
            "--agreed-terms-and-conditions",
            "yes",
            "--agreed-privacy-policy",
            "yes",
            "--agreed-provider-terms",
            "yes",
        ]
        payout = payout_addr or self._payout_addr
        if payout:
            args.extend(["--tequilapi-addr", f"127.0.0.1:{self._port}"])
            args.extend(["--payout-address", payout])
        code, out, err = self._run(args, timeout=30)
        if code != 0:
            # Check if output contains actual errors vs just debug/info logs
            error_output = (err or out or "").upper()
            has_error = "ERR" in error_output or "ERROR" in error_output or "FAIL" in error_output
            # If no actual error keywords found, treat as success (myst outputs logs to stderr)
            if has_error:
                raise RuntimeError(f"myst install failed: {err or out or code}")
        self._ensure_firewall_port_windows()
        self._ensure_wireguard_firewall_port_windows()
        
        # Ensure service is running before attempting identity creation via API
        try:
            self.start()
        except Exception:
            pass
        
        # Create identity after service is installed/started (if not already present)
        if not self._has_keystore():
            try:
                self._create_identity_via_api()
            except Exception as exc:
                log_step("mysterium_identity_create_skipped", {"error": str(exc)})
        
        self._register_node()

    def start(self) -> None:
        if not self._myst_bin:
            raise RuntimeError("myst binary not found")
        
        # Check if already running - skip start attempts to avoid noise in logs
        if self._port_reachable():
            return
        
        # Try NSSM service first on Windows
        if sys.platform == "win32":
            try:
                nssm_path = Path("nssm.exe").resolve()
                if not nssm_path.exists():
                    nssm_path = Path("C:/ProgramData/FryNetworks") / f"miner-{self._miner_code}" / "nssm.exe"
                if nssm_path.exists():
                    result = subprocess.run(
                        [str(nssm_path), "start", "MysteriumNode"],
                        capture_output=True,
                        timeout=15,
                        encoding="utf-8",
                        errors="ignore",
                    )
                    if result.returncode == 0:
                        # Wait briefly for port to become reachable
                        for _ in range(10):
                            if self._port_reachable():
                                break
                            try:
                                time.sleep(0.5)
                            except Exception:
                                pass
                        # Unlock identity if it was locked on daemon restart
                        self._unlock_identity()
                        log_step("mysterium_service_started_nssm")
                        return
                    else:
                        log_step(
                            "mysterium_service_start_attempt",
                            {
                                "method": "nssm",
                                "code": result.returncode,
                                "out": (result.stdout or "").replace("\x00", "")[:200],
                                "err": (result.stderr or "").replace("\x00", "")[:200],
                            },
                        )
            except Exception:
                pass
            # Fallback: try using Windows Service Control Manager (sc)
            try:
                sc_start = subprocess.run(
                    ["sc", "start", "MysteriumNode"],
                    capture_output=True,
                    timeout=15,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if sc_start.returncode == 0:
                    for _ in range(20):
                        if self._port_reachable():
                            break
                        try:
                            time.sleep(0.5)
                        except Exception:
                            pass
                    if self._port_reachable():
                        log_step("mysterium_service_started_sc")
                        return
                else:
                    log_step(
                        "mysterium_service_start_attempt",
                        {
                            "method": "sc",
                            "code": sc_start.returncode,
                            "out": (sc_start.stdout or "").replace("\x00", "")[:200],
                            "err": (sc_start.stderr or "").replace("\x00", "")[:200],
                        },
                    )
            except Exception as exc:
                log_step("mysterium_service_start_exception", {"method": "sc", "error": str(exc)})
        
        # Fallback to myst service start
        code, out, err = self._run(["service", "start"], timeout=15)
        if code != 0:
            # Check if output contains actual errors vs just debug/info logs
            error_output = (err or out or "").upper()
            has_error = "ERR" in error_output or "ERROR" in error_output or "FAIL" in error_output
            # If no actual error keywords found, treat as success (myst outputs logs to stderr)
            if has_error:
                raise RuntimeError(f"myst start failed: {err or out or code}")
        # Wait briefly for port to become reachable
        for _ in range(10):
            if self._port_reachable():
                break
            try:
                time.sleep(0.5)
            except Exception:
                pass

    def stop(self) -> None:
        if not self._myst_bin:
            return
        
        # Try NSSM service first on Windows
        if sys.platform == "win32":
            try:
                nssm_path = Path("nssm.exe").resolve()
                if not nssm_path.exists():
                    nssm_path = Path("C:/ProgramData/FryNetworks") / f"miner-{self._miner_code}" / "nssm.exe"
                if nssm_path.exists():
                    subprocess.run(
                        [str(nssm_path), "stop", "MysteriumNode"],
                        capture_output=True,
                        timeout=10,
                        encoding="utf-8",
                        errors="ignore",
                    )
                    # Wait briefly for service to stop
                    time.sleep(1)
                    log_step("mysterium_service_stopped_nssm")
                    return
            except Exception:
                pass
        
        # Fallback to myst service stop
        self._run(["service", "stop"], timeout=10)
        time.sleep(1)

    def uninstall(self) -> None:
        """Uninstall Mysterium service and clean up resources."""
        # 1. Stop the service first
        try:
            self.stop()
            log_step("mysterium_service_stopped_for_uninstall")
        except Exception as exc:
            log_step("mysterium_stop_failed_during_uninstall", {"error": str(exc)})
        
        # 2. Clear MMN API key from config (allows node to be unclaimed)
        try:
            url = f"http://127.0.0.1:{self._port}/mmn/api-key"
            req = request.Request(url, method="DELETE")  # nosec B310
            with request.urlopen(req, timeout=5) as _resp:  # nosec B310
                log_step("mysterium_mmn_api_key_cleared")
        except Exception as exc:
            log_step("mysterium_mmn_api_key_clear_failed", {"error": str(exc)})
        
        # 3. Delete the Windows service
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["sc", "stop", "MysteriumNode"],
                    capture_output=True,
                    timeout=10,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                time.sleep(1)
            except Exception:
                pass
            
            try:
                del_result = subprocess.run(
                    ["sc", "delete", "MysteriumNode"],
                    capture_output=True,
                    timeout=10,
                    encoding="utf-8",
                    errors="ignore",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if del_result.returncode == 0:
                    log_step("mysterium_service_deleted")
                else:
                    log_step("mysterium_service_delete_failed", {"code": del_result.returncode})
            except Exception as exc:
                log_step("mysterium_service_delete_exception", {"error": str(exc)})
        
        # 4. Remove local registration marker
        try:
            if self._reg_marker.exists():
                self._reg_marker.unlink()
                log_step("mysterium_registration_marker_removed")
        except Exception as exc:
            log_step("mysterium_registration_marker_remove_failed", {"error": str(exc)})
        
        # 5. Mark as offline in status tracking
        try:
            self.mark_offline()
        except Exception:
            pass

    # ------------- status / metrics -------------
    def _port_reachable(self) -> bool:
        # Small, quick retries to reduce transient false negatives
        for _ in range(2):
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=2):
                    return True
            except Exception:
                try:
                    time.sleep(0.2)
                except Exception:
                    pass
        return False

    def _get_json(self, path: str) -> Optional[Dict[str, Any]]:
        url = f"http://127.0.0.1:{self._port}{path}"
        try:
            with request.urlopen(url, timeout=5) as resp:  # nosec B310
                body = resp.read().decode("utf-8", errors="ignore")
                return json.loads(body)
        except Exception:
            return None

    def _compute_delta(self, totals: Dict[str, Any]) -> Dict[str, float]:
        delta: Dict[str, float] = {}
        for key in ("bytes_up", "bytes_down", "sessions", "earn_myst"):
            try:
                cur = float(totals.get(key, 0) or 0)
                prev = float(self._last_totals.get(key, 0) or 0)
                delta[key] = max(0.0, cur - prev)
            except Exception:
                delta[key] = 0.0
        self._last_totals = {
            "bytes_up": float(totals.get("bytes_up", 0) or 0),
            "bytes_down": float(totals.get("bytes_down", 0) or 0),
            "sessions": float(totals.get("sessions", 0) or 0),
            "earn_myst": float(totals.get("earn_myst", 0) or 0),
        }
        return delta

    def refresh_status(self) -> MysteriumStatus:
        """Poll TequilAPI, emit measurements, and return status."""
        port_ok = self._port_reachable()
        api_ok = False
        running = False
        warning: Optional[str] = None
        last_error = None
        provider_status = None
        quality = None
        consent_given = False
        wireguard_port_ok = True

        stats: Optional[Dict[str, Any]] = None

        if not port_ok:
            last_error = f"API port {self._port} not reachable - check firewall or service status"
        
        if port_ok:
            # Health endpoint is lightweight; try that first
            try:
                health = self._get_json("/healthcheck")
                if health:
                    api_ok = True
                    # If we can fetch health successfully, daemon is running
                    running = True
                    provider_status = str(health.get("state", {}).get("service", "")) if isinstance(health, dict) else None
            except requests.exceptions.ConnectionError:
                last_error = f"Cannot connect to API on port {self._port} - service may not be running or port blocked"
            except requests.exceptions.Timeout:
                last_error = f"API timeout on port {self._port} - service may be overloaded or port blocked"
            except Exception as exc:
                last_error = f"API error: {str(exc)[:100]}"

            # Check terms agreement if API is up
            if api_ok:
                consent_given = self._get_terms_agreement()

            # Check if service is actually running via /services endpoint
            if api_ok and not running:
                try:
                    services_data = self._get_json("/services")
                    if services_data:
                        # API can return single service object or array
                        if isinstance(services_data, dict) and services_data.get("status") == "Running":
                            running = True
                            provider_status = "running"
                        elif isinstance(services_data, list) and len(services_data) > 0:
                            running = any(s.get("status") == "Running" for s in services_data if isinstance(s, dict))
                            if running:
                                provider_status = "running"
                except Exception:
                    pass

            # Traffic/earnings; tolerate missing fields
            if api_ok:
                stats = self._get_json("/traffic") or self._get_json("/status")
            if not running and stats:
                try:
                    running = bool(stats.get("running") or stats.get("online"))
                except Exception:
                    pass
        else:
            warning = "Mysterium API port unreachable."

        if running:
            wireguard_port_ok = self._wireguard_port_listening()
            if not wireguard_port_ok:
                msg = (
                    f"WireGuard UDP port {WIREGUARD_PORT} is not listening locally. "
                    "Re-open the port or restart Mysterium to rebind WireGuard."
                )
                warning = f"{warning}\n{msg}" if warning else msg
                log_step("mysterium_wireguard_port_not_listening", {"port": WIREGUARD_PORT})

        # Don't fall back to CLI if API is working - myst doesn't have a standalone 'status' command
        # The diagnostics can use _run for testing, but refresh_status should rely on API only

        # Note: Measurement writing is now handled autonomously by the service.
        # Service reads status from mysterium API and writes to measurements/latest.json

        return MysteriumStatus(
            enabled=running,
            running=running,
            warning=warning,
            last_error=last_error,
            port_ok=port_ok,
            api_ok=api_ok,
            wireguard_port_ok=wireguard_port_ok,
            provider_status=provider_status,
            quality=quality,
            consent_given=consent_given,
        )

    # ------------- diagnostics -------------
    def diagnose(self) -> Dict[str, Any]:
        """Run a series of connectivity checks to help diagnose issues.

        Returns a dictionary with keys:
        - port_check: { ok: bool, error: str|None }
        - http_health: { ok: bool, status: int|None, body: str|None, error: str|None }
        - cli_status: { ok: bool, code: int, out: str, err: str }
        - service_status: { ok: bool, code: int, out: str, err: str }
        - env: { port: int, myst_bin: str|None }
        """
        results: Dict[str, Any] = {
            "env": {"port": int(self._port), "myst_bin": str(self._myst_bin) if self._myst_bin else None}
        }

        # Port check
        port_ok = False
        port_err: Optional[str] = None
        try:
            with socket.create_connection(("127.0.0.1", self._port), timeout=3):
                port_ok = True
        except Exception as exc:
            port_err = str(exc)
        results["port_check"] = {"ok": port_ok, "error": port_err}

        # HTTP health
        http_res = {"ok": False, "status": None, "body": None, "error": None}
        try:
            if port_ok:
                with request.urlopen(f"http://127.0.0.1:{self._port}/healthcheck", timeout=5) as resp:  # nosec B310
                    http_res["status"] = getattr(resp, "status", None)
                    body = resp.read().decode("utf-8", errors="ignore")
                    http_res["body"] = body[:500]
                    http_res["ok"] = True
        except Exception as exc:
            http_res["error"] = str(exc)
        results["http_health"] = http_res

        # CLI: myst status
        if self._myst_bin:
            code, out, err = self._run(["status"], timeout=10)
            results["cli_status"] = {"ok": code == 0, "code": code, "out": (out or "")[:1000], "err": (err or "")[:1000]}
            code2, out2, err2 = self._run(["service", "status"], timeout=10)
            results["service_status"] = {"ok": code2 == 0, "code": code2, "out": (out2 or "")[:1000], "err": (err2 or "")[:1000]}
        else:
            results["cli_status"] = {"ok": False, "code": 1, "out": "", "err": "myst binary not found"}
            results["service_status"] = {"ok": False, "code": 1, "out": "", "err": "myst binary not found"}

        return results

    def _update_status_json_on_offline(self) -> None:
        """Update today's status JSON to remove Mysterium from tools starting at current hour."""
        try:
            from datetime import datetime
            status_dir = data_dir_gui() / "status"
            now = datetime.now()
            current_hour = now.hour
            json_path = status_dir / f"status-{now.strftime('%Y%m%d')}.json"
            
            if not json_path.exists():
                return
            
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Remove Mysterium from tools only from current hour onwards
            rewards_slots = data.get("rewards_multiplier_slots", {})
            if rewards_slots:
                for hour_str, slot_data in rewards_slots.items():
                    try:
                        hour = int(hour_str)
                        # Only update from current hour onwards
                        if hour >= current_hour and isinstance(slot_data, dict):
                            tools = slot_data.get("tools", [])
                            if isinstance(tools, list) and "Mysterium" in tools:
                                tools.remove("Mysterium")
                                slot_data["tools"] = tools
                    except (ValueError, TypeError):
                        continue
            
            # Write status file directly (no privilege elevation needed for status files)
            from miner_GUI.utils.ops_queue_client import write_status_file
            
            try:
                base_dir = data_dir_gui()
                rel_path = str(json_path.relative_to(base_dir))
                content = json.dumps(data, indent=2)
                success, msg = write_status_file(rel_path, content)
                if not success:
                    log_step("status_json_update_failed", {"error": msg})
            except (ValueError, OSError):
                # If not in ProgramData, write directly
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
        except Exception as exc:
            log_step("status_json_update_failed", {"error": str(exc)})
    
    def list_identities(self) -> List[str]:
        """
        Fetch list of all identities on this Mysterium node.
        
        Returns:
            List of identity addresses (hex strings)
        """
        try:
            response = requests.get(
                f"http://127.0.0.1:{self._port}/identities",
                timeout=5
            )
            response.raise_for_status()
            data = response.json()
            # API returns {"identities": [{"id": "0x..."}, ...]}
            identities = data.get("identities", [])
            return [id_obj.get("id", "") for id_obj in identities if id_obj.get("id")]
        except Exception as exc:
            log_step("list_identities_error", {"error": str(exc)})
            return []
    
    def unclaim_from_mmn(self) -> bool:
        """
        Unclaim the node from Mysterium Network Manager (mystnodes.com).
        This clears the MMN API key, which removes the node from the account.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            response = requests.delete(
                f"http://127.0.0.1:{self._port}/mmn/api-key",
                timeout=5
            )
            if response.status_code in (200, 204):
                log_step("mmn_unclaim_success", {})
                return True
            else:
                log_step("mmn_unclaim_failed", {"status": response.status_code, "text": response.text})
                return False
        except Exception as exc:
            log_step("mmn_unclaim_error", {"error": str(exc)})
            return False

    def mark_offline(self) -> None:
        """Call this when Mysterium is detected as offline to update JSON."""
        self._update_status_json_on_offline()
