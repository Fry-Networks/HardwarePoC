"""Daemon service that processes queued privileged operations.

Runs under NSSM as LocalSystem (admin). Watches the ProgramData ops_queue
for JSON requests and executes them using privileged_ops.

Processing model:
- Poll ops_queue every 500ms
- For each *.json file, parse and validate
- Execute operation
- Move to ops_processed/<id>.done.json with result metadata
- Log all errors via log_step

Installer must ensure Users: Modify on ops_queue so GUI can enqueue.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Any, Tuple

from miner_GUI.utils.data import data_dir_gui, log_step
from miner_GUI.services import privileged_ops

QUEUE_DIR_NAME = "ops_queue"
PROCESSED_DIR_NAME = "ops_processed"
SLEEP_MS = 500
ALLOWED_CONFIG_PREFIXES = ("config/", "status/")


def _is_allowed_relative(rel: str, prefixes: Tuple[str, ...]) -> bool:
    try:
        # Normalize separators
        rel_norm = rel.replace("\\", "/")
        # Must start with one of the allowed prefixes
        if not any(rel_norm.startswith(p) for p in prefixes):
            return False
        # Prevent traversal and absolute paths
        p = Path(rel_norm)
        if p.is_absolute():
            return False
        if any(part == ".." for part in p.parts):
            return False
        return True
    except Exception:
        return False


def _queue_dir() -> Path:
    return data_dir_gui() / QUEUE_DIR_NAME


def _processed_dir() -> Path:
    return data_dir_gui() / PROCESSED_DIR_NAME


def _ensure_dirs() -> None:
    _queue_dir().mkdir(parents=True, exist_ok=True)
    _processed_dir().mkdir(parents=True, exist_ok=True)


def _safe_move(src: Path, dst: Path) -> None:
    try:
        src.rename(dst)
    except Exception:
        # Fallback copy+remove
        try:
            shutil.copy2(src, dst)
            src.unlink(missing_ok=True)
        except Exception:
            pass


def _process_write_config(payload: Dict[str, Any]) -> Tuple[bool, str]:
    # Support both keys: "relative_path" and "path"
    rel = str(payload.get("relative_path") or payload.get("path") or "")
    content = str(payload.get("content") or "")
    if not rel or not content:
        return False, "relative_path and content required"
    # Only permit writes under config/ or status/
    if not _is_allowed_relative(rel, ALLOWED_CONFIG_PREFIXES):
        return False, "path not allowed"
    # Optionally require .json extension for config/status
    if not rel.lower().endswith(".json"):
        return False, "only .json files allowed for config/status"
    return privileged_ops.write_config_file(rel, content)


def _process_write_measurement(payload: Dict[str, Any]) -> Tuple[bool, str]:
    group = str(payload.get("group") or "")
    data_b64 = str(payload.get("data_b64") or "")
    if not group or not data_b64:
        return False, "group and data_b64 required"
    # Sanitize group: allow only letters, digits, dash, underscore
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]+", group or ""):
        return False, "invalid group"
    try:
        import base64
        encrypted = base64.b64decode(data_b64)
    except Exception as exc:
        return False, f"Invalid base64: {exc}"
    return privileged_ops.write_measurement_file(group, encrypted)


def _handle_file(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        op = str(payload.get("op") or "")
        req_id = str(payload.get("id") or path.stem)
        success = False
        msg = "unsupported op"

        if op == "write_config":
            success, msg = _process_write_config(payload)
        elif op == "write_measurement":
            success, msg = _process_write_measurement(payload)

        result = {
            "id": req_id,
            "op": op,
            "success": bool(success),
            "message": str(msg),
            "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_file": str(path.name),
        }
        done_path = _processed_dir() / f"{req_id}.done.json"
        done_path.write_text(json.dumps(result), encoding="utf-8")
        _safe_move(path, _processed_dir() / f"{path.name}.processed")
        log_step("ops_daemon_processed", {"op": op, "success": success})
    except Exception as exc:
        log_step("ops_daemon_handle_failed", {"file": str(path), "error": str(exc)})
        # Move to processed with error suffix
        err_path = _processed_dir() / f"{path.name}.error"
        _safe_move(path, err_path)


def run() -> None:
    log_step("ops_daemon_start", {})
    _ensure_dirs()
    while True:
        try:
            for path in sorted(_queue_dir().glob("*.json")):
                _handle_file(path)
        except Exception as exc:
            log_step("ops_daemon_loop_error", {"error": str(exc)})
        time.sleep(SLEEP_MS / 1000.0)


if __name__ == "__main__":
    run()
