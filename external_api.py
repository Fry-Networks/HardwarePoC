from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

import requests

# Use string forward-references for optional optimized client type to avoid
# import cycles at runtime. The optimized client is imported dynamically
# inside get_external_api_client when available.


class ApiError(RuntimeError):
    """Raised when the external backend rejects a request or returns invalid data."""


class ApiUnavailableError(ApiError):
    """Raised when the external API is temporarily unavailable (HTTP 5xx)."""


class ExternalApiClient:
    """HTTP client that mirrors the legacy MongoDB reads/writes via REST calls.

    Each public method documents the expected endpoint so the server can be
    implemented without reading the original Mongo-specific code.
    """

    def __init__(self, base_url: str, *, token: Optional[str] = None, timeout: float = 10.0):
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = max(1.0, float(timeout))

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                json=json_body,
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(f"{method} {url} failed: {exc}") from exc

        if response.status_code == 204 or not response.content:
            return None
        # Treat 5xx as temporarily unavailable
        if 500 <= response.status_code < 600:
            raise ApiUnavailableError(f"{method} {url} returned {response.status_code}: {response.text}")
        if response.status_code >= 400:
            raise ApiError(f"{method} {url} returned {response.status_code}: {response.text}")
        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise ApiError(f"{method} {url} expected JSON, got {content_type}")
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(f"{method} {url} returned invalid JSON: {exc}") from exc

    def get_required_version(self, miner_code: str) -> Optional[str]:
        """GET {base}/versions/{miner_code} -> {"required_version": "5.1.4"}

        (endpoint unchanged)
        """
        data = self._request("GET", f"/versions/{miner_code}")
        if isinstance(data, dict):
            value = data.get("required_version") or data.get("version")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def get_miner_profile(self, miner_key: str) -> Dict[str, Any]:
        """GET {base}/credentials/{miner_key} -> {"exists": bool, "registered_mac": str|None, "hex_id": str|None}

        NOTE: endpoint moved from /miners/... to /credentials/...
        """
        data = self._request("GET", f"/credentials/{miner_key}")
        if isinstance(data, dict):
            return data
        return {}

    def upsert_installation(self, miner_key: str, install_id: str, payload: Dict[str, Any]) -> None:
        """POST {base}/installations/{miner_key}/installations/{install_id} with heartbeat payload.

        NOTE: endpoint moved under /installations/...
        """
        body = dict(payload)
        body.setdefault("install_id", install_id)
        self._request("POST", f"/installations/{miner_key}/installations/{install_id}", json_body=body)

    def acquire_installation_lease(self, miner_key: str, install_id: str, lease_seconds: int) -> bool:
        """POST {base}/installations/{miner_key}/leases/{install_id} -> {"granted": bool, "expires_at": "..."}

        NOTE: endpoint moved under /installations/...
        """
        data = self._request(
            "POST",
            f"/installations/{miner_key}/leases/{install_id}",
            json_body={"mode": "acquire", "lease_seconds": int(lease_seconds)},
        )
        if isinstance(data, dict):
            return bool(data.get("granted"))
        return False

    def renew_installation_lease(self, miner_key: str, install_id: str, lease_seconds: int) -> bool:
        """PATCH {base}/installations/{miner_key}/leases/{install_id} -> {"granted": bool}

        NOTE: endpoint moved under /installations/...
        """
        data = self._request(
            "PATCH",
            f"/installations/{miner_key}/leases/{install_id}",
            json_body={"mode": "renew", "lease_seconds": int(lease_seconds)},
        )
        if isinstance(data, dict):
            return bool(data.get("granted", True))
        return True

    def lease_status(self, miner_key: str) -> Dict[str, Any]:
        """GET {base}/installations/{miner_key}/leases/current -> {"active": bool, "holder_install_id": str|None}

        NOTE: endpoint moved under /installations/...
        """
        data = self._request("GET", f"/installations/{miner_key}/leases/current")
        if isinstance(data, dict):
            return data
        return {}

    def lease_history(self, miner_key: str) -> List[Dict[str, Any]]:
        # NOTE: lease history endpoint was removed from the API. This method
        # is intentionally deprecated and no longer implemented.
        raise ApiError("lease_history endpoint removed from API")

    def get_hardware_doc(self, miner_key: str) -> Dict[str, Any]:
        """GET {base}/PoC/{miner_key}/hardware -> {"document": {...}}

        NOTE: hardware/PoC endpoints moved under /PoC/...
        """
        data = self._request("GET", f"/PoC/{miner_key}/hardware")
        if isinstance(data, dict):
            if isinstance(data.get("document"), dict):
                return data["document"]
            return data
        return {}

    def put_hardware_doc(self, miner_key: str, document: Dict[str, Any]) -> None:
        """PUT {base}/PoC/{miner_key}/hardware with {"document": {...}}

        NOTE: hardware/PoC endpoints moved under /PoC/...
        """
        payload = {"document": document}
        self._request("PUT", f"/PoC/{miner_key}/hardware", json_body=payload)

    def has_other_active_installation(self, miner_key: str, install_id: str) -> bool:
        status = self.lease_status(miner_key)
        if not isinstance(status, dict):
            return False
        if not status.get("active"):
            return False
        holder = status.get("holder_install_id")
        if not isinstance(holder, str) or not holder.strip():
            return False
        return holder.strip() != install_id

    def verify_lease_ownership(self, miner_key: str, install_id: str) -> bool:
        """Verify that this install_id currently holds the active lease for miner_key.
        
        This is the inverse of has_other_active_installation() and provides a clearer
        semantic meaning when checking ownership rather than conflicts.
        """
        status = self.lease_status(miner_key)
        if not isinstance(status, dict):
            return False
        if not status.get("active"):
            return False
        holder = status.get("holder_install_id")
        if not isinstance(holder, str) or not holder.strip():
            return False
        return holder.strip() == install_id


# ============================================================================
# Configuration Management & Factory Functions
# ============================================================================

def _get_1password_secret(reference: str) -> Optional[str]:
    """Retrieve a secret from 1Password using the CLI.
    
    Args:
        reference: 1Password reference like "op://VPS/Hardware_API/API_BEARER_TOKEN"
        
    Returns:
        The secret value, or None if retrieval fails
    """
    try:
        # Run 'op read' command to get the secret
        result = subprocess.run(
            ['op', 'read', reference],
            capture_output=True,
            text=True,
            timeout=10,
            check=True
        )
        
        # Strip whitespace and return the value
        value = result.stdout.strip()
        return value if value else None
        
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, FileNotFoundError):
        # 1Password CLI not available, not authenticated, or reference not found
        return None
    except Exception:
        # Any other error
        return None


def _load_build_config() -> Dict[str, Any]:
    """Load build configuration from embedded file or defaults."""
    import os
    debug = os.getenv('DEBUG_BUILD_CONFIG') == '1'
    
    try:
        # Try to find build config file (embedded during build)
        if getattr(sys, 'frozen', False):
            # Running from PyInstaller bundle
            bundle_dir = Path(getattr(sys, '_MEIPASS', '.'))
            config_file = bundle_dir / 'build_config.json'
            if debug:
                print(f"[DEBUG] Running from frozen bundle, looking for: {config_file}")
        else:
            # Running from source
            config_file = Path(__file__).parent / 'build_config.json'
            if debug:
                print(f"[DEBUG] Running from source, looking for: {config_file}")
        
        if config_file.exists():
            if debug:
                print(f"[DEBUG] Config file found, loading...")
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
                # IMPORTANT: For packaged builds, bearer_token MUST be embedded in build_config.json
                # We no longer support 1Password fallback at runtime for security and portability
                if 'external_api' in config and 'bearer_token' in config['external_api']:
                    if debug:
                        token_preview = config['external_api']['bearer_token'][:20] + '...' if len(config['external_api']['bearer_token']) > 20 else config['external_api']['bearer_token']
                        print(f"[DEBUG] Bearer token found: {token_preview}")
                    return config
                elif 'external_api' in config:
                    if debug:
                        print(f"[DEBUG] Config exists but no bearer token - build must embed 1Password credentials")
        else:
            if debug:
                print(f"[DEBUG] Config file NOT found at: {config_file}")
        
    except Exception as e:
        if debug:
            print(f"[DEBUG] Exception loading config: {e}")
        pass  # Silently fall through to fallbacks
    
    # No runtime 1Password fallback - credentials must be embedded at build time
    if debug:
        print(f"[DEBUG] No embedded credentials found - build with 1Password integration required")
    
    # Final fallback - return config without bearer token (will fail on API calls)
    # This is acceptable for packaged builds that should have embedded token
    return {
        'external_api': {
            'base_url': 'https://hardwareapi.frynetworks.com',
            'timeout': 10.0
        },
        'status': 'fallback',
        'source': 'defaults'
    }


# Load configuration once at module import
_BUILD_CONFIG = _load_build_config()


def get_external_api_client(base_url: Optional[str] = None, token: Optional[str] = None, 
                          timeout: float = 10.0, use_optimized: bool = True) -> Union[Any, ExternalApiClient]:
    """Get an API client instance (optimized or basic).
    
    Args:
        base_url: API base URL (uses build config if None)
        token: Optional auth token (uses build config bearer token if None)
        timeout: Request timeout in seconds
        use_optimized: If True, returns OptimizedExternalApiClient (requires miner_GUI)
        
    Returns:
        OptimizedExternalApiClient if use_optimized=True, else ExternalApiClient
    """
    # Use build configuration if not provided
    if base_url is None:
        base_url = _BUILD_CONFIG['external_api']['base_url']
    
    if token is None:
        token = _BUILD_CONFIG['external_api'].get('bearer_token')
    
    if timeout == 10.0:  # Default timeout
        timeout = _BUILD_CONFIG['external_api'].get('timeout', 10.0)
    
    # Ensure base_url is not None
    if not base_url:
        # Enforce canonical API base URL as ultimate fallback
        base_url = 'https://hardwareapi.frynetworks.com'
    
    if use_optimized:
        try:
            import importlib, importlib.util
            spec = importlib.util.find_spec('miner_GUI.utils.optimized_api')
            if spec is not None:
                mod = importlib.import_module('miner_GUI.utils.optimized_api')
                OptimizedExternalApiClient = getattr(mod, 'OptimizedExternalApiClient', None)
                if OptimizedExternalApiClient:
                    return OptimizedExternalApiClient(base_url, token=token, timeout=timeout, client_class=ExternalApiClient)
        except Exception:
            # Fall back to basic client if optimized version not available or import fails
            pass
    
    return ExternalApiClient(base_url, token=token, timeout=timeout)


# Global optimized client instance
_global_client: Optional[Any] = None


def get_global_api_client() -> Union[Any, ExternalApiClient]:
    """Get the global optimized API client instance (singleton pattern).
    
    Returns:
        OptimizedExternalApiClient singleton, or basic client as fallback
    """
    global _global_client
    
    if _global_client is None:
        base_url = _BUILD_CONFIG['external_api']['base_url']
        bearer_token = _BUILD_CONFIG['external_api'].get('bearer_token')
        timeout = _BUILD_CONFIG['external_api'].get('timeout', 10.0)
        
        try:
            import importlib, importlib.util
            spec = importlib.util.find_spec('miner_GUI.utils.optimized_api')
            if spec is not None:
                mod = importlib.import_module('miner_GUI.utils.optimized_api')
                OptimizedExternalApiClient = getattr(mod, 'OptimizedExternalApiClient', None)
                if OptimizedExternalApiClient:
                    _global_client = OptimizedExternalApiClient(base_url, token=bearer_token, timeout=timeout, client_class=ExternalApiClient)
                else:
                    return ExternalApiClient(base_url, token=bearer_token, timeout=timeout)
            else:
                return ExternalApiClient(base_url, token=bearer_token, timeout=timeout)
        except Exception:
            # Return basic client if optimized not available or import fails
            return ExternalApiClient(base_url, token=bearer_token, timeout=timeout)
    
    return _global_client


def reset_global_api_client():
    """Reset the global API client (useful for testing or configuration changes)."""
    global _global_client
    _global_client = None


def get_api_client():
    """Convenience function that matches the existing interface."""
    return get_global_api_client()


def get_build_config_info() -> Dict[str, Any]:
    """Get information about the current build configuration."""
    return {
        'source': _BUILD_CONFIG.get('source', 'unknown'),
        'status': _BUILD_CONFIG.get('status', 'unknown'),
        'api_url_configured': bool(_BUILD_CONFIG.get('external_api', {}).get('base_url')),
        'bearer_token_configured': bool(_BUILD_CONFIG.get('external_api', {}).get('bearer_token')),
        'timeout': _BUILD_CONFIG.get('external_api', {}).get('timeout', 10.0)
    }


# ============================================================================
# Safety & Validation Helpers
# ============================================================================

REQUIRED_API_METHODS = [
    'get_required_version',
    'get_miner_profile',
    'upsert_installation',
    'acquire_installation_lease',
    'renew_installation_lease',
    'lease_status',
    'get_hardware_doc',
    'put_hardware_doc',
]


def factory_has_all_endpoints(use_optimized: bool = True, base_url: Optional[str] = None, 
                             token: Optional[str] = None, timeout: float = 10.0) -> bool:
    """Return True if the client has all required API methods.

    This performs a shallow, non-network check: it simply verifies the methods exist and are callable.
    It does not make real HTTP calls.
    
    Args:
        use_optimized: Whether to check optimized client
        base_url: API base URL
        token: Optional auth token
        timeout: Request timeout
        
    Returns:
        True if all required methods are present and callable
    """
    try:
        client = get_external_api_client(base_url=base_url, token=token, timeout=timeout, use_optimized=use_optimized)
    except Exception:
        return False

    for name in REQUIRED_API_METHODS:
        if not hasattr(client, name) or not callable(getattr(client, name)):
            return False
    return True


def get_external_api_client_if_complete(*, base_url: Optional[str] = None, token: Optional[str] = None,
                                       timeout: float = 10.0, use_optimized: bool = True, 
                                       raise_on_missing: bool = True):
    """Return a client only if it exposes the complete set of required endpoints.

    If the client is missing methods, this returns None (or raises RuntimeError when raise_on_missing=True).
    
    Args:
        base_url: API base URL
        token: Optional auth token
        timeout: Request timeout
        use_optimized: Whether to use optimized client
        raise_on_missing: If True, raise error on missing methods; if False, return None
        
    Returns:
        API client instance if complete, None if incomplete (when raise_on_missing=False)
        
    Raises:
        RuntimeError: If client is missing methods and raise_on_missing=True
    """
    ok = factory_has_all_endpoints(use_optimized=use_optimized, base_url=base_url, token=token, timeout=timeout)
    if not ok:
        msg = "API client is missing required endpoints"
        if raise_on_missing:
            raise RuntimeError(msg)
        return None
    return get_external_api_client(base_url=base_url, token=token, timeout=timeout, use_optimized=use_optimized)
