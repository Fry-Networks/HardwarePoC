#!/usr/bin/env python3
"""Lightweight cache integrity helpers (no heavy dependencies).

Provides HMAC-SHA256 signing/verification for local JSON cache documents.
This module is dependency-light so it can be imported by the GUI build.
"""
from __future__ import annotations
import hashlib
import hmac
import json
from typing import Any, Optional, Union


def _canonical_bytes(obj: Any) -> bytes:
    """Stable JSON bytes for HMAC. Excludes the 'sig' field if present."""
    try:
        if isinstance(obj, dict) and "sig" in obj:
            tmp = dict(obj)
            tmp.pop("sig", None)
            obj = tmp
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except Exception:
        try:
            return str(obj).encode("utf-8", "ignore")
        except Exception:
            return b""


def _to_bytes(key: Union[str, bytes]) -> bytes:
    if isinstance(key, bytes):
        return key
    if isinstance(key, str):
        # accept hex strings with or without separators
        try:
            s = "".join(ch for ch in key if ch in "0123456789abcdefABCDEF")
            return bytes.fromhex(s)
        except Exception:
            return key.encode("utf-8", "ignore")
    return b""


def compute_cache_signature(payload: dict, key: Union[str, bytes]) -> str:
    """Return hex HMAC-SHA256 signature of payload using key (bytes or hex str)."""
    k = _to_bytes(key)
    mac = hmac.new(k, _canonical_bytes(payload), hashlib.sha256).hexdigest()
    return mac


def verify_cache_signature(doc: dict, key: Optional[Union[str, bytes]] = None, *, allow_if_no_key: bool = True) -> bool:
    """Verify HMAC signature on a document.

    - If key is provided, recomputes and compares using constant-time compare.
    - If no key is provided:
        - return allow_if_no_key (default True) to avoid blocking UI if key isn't available
          (service still signs; backend is authoritative).
    """
    try:
        sig = doc.get("sig")
        if not isinstance(sig, str) or not sig:
            # No signature present
            return bool(allow_if_no_key)
        if key is None:
            return bool(allow_if_no_key)
        exp = compute_cache_signature(doc, key)
        return hmac.compare_digest(sig, exp)
    except Exception:
        return False


__all__ = ["compute_cache_signature", "verify_cache_signature"]

