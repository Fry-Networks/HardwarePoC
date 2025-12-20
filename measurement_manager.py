#!/usr/bin/env python3
"""
Measurement Manager - Enhanced measurement handling with queuing, integrity, and batch uploads.

This module implements the "Future Enhancements" from ENCRYPTED_MEASUREMENTS.md:
- Measurement queuing (retain last N measurements for resilience)
- Integrity hash (SHA-256) alongside ciphertext
- Batch uploads for efficiency
- Configurable upload intervals per group
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Use timezone-aware UTC
UTC = getattr(__import__('datetime'), 'UTC', timezone.utc)

# Optional imports with graceful fallback
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    import base64
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

# Default configuration
DEFAULT_QUEUE_SIZE = 10  # Retain last N measurements per group
DEFAULT_UPLOAD_INTERVAL = 600  # 10 minutes
MAX_BATCH_SIZE = 50  # Maximum measurements per batch upload


def _now_utc() -> datetime:
    """Current UTC time as timezone-aware datetime."""
    return datetime.now(UTC).replace(microsecond=0)


def _compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hash of data and return hex string."""
    return hashlib.sha256(data).hexdigest()


def derive_measurement_key(miner_key: str) -> Optional[bytes]:
    """Derive Fernet key from miner_key for measurement encryption.

    Uses PBKDF2-HMAC-SHA256 with fixed salt (same as GUI worker).
    This allows GUI and service to encrypt/decrypt measurements using the same key.
    """
    if not HAVE_CRYPTO:
        return None
    try:
        salt = b'measurements_key_v1'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        derived = kdf.derive(miner_key.encode('utf-8'))
        return base64.urlsafe_b64encode(derived)
    except Exception:
        return None


class MeasurementQueue:
    """Thread-safe measurement queue with configurable retention.

    Implements FIFO queue with maximum size limit.
    Old measurements are automatically dropped when queue exceeds max_size.
    """

    def __init__(self, group: str, max_size: int = DEFAULT_QUEUE_SIZE):
        self.group = group
        self.max_size = max(1, max_size)
        self._queue: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def push(self, measurement: Dict[str, Any]) -> None:
        """Add measurement to queue, dropping oldest if at capacity."""
        with self._lock:
            self._queue.append(measurement)
            while len(self._queue) > self.max_size:
                self._queue.pop(0)

    def peek_all(self) -> List[Dict[str, Any]]:
        """Get all measurements without removing them."""
        with self._lock:
            return list(self._queue)

    def pop_batch(self, count: int) -> List[Dict[str, Any]]:
        """Remove and return up to count measurements from front of queue."""
        with self._lock:
            count = min(count, len(self._queue))
            batch = self._queue[:count]
            self._queue = self._queue[count:]
            return batch

    def clear_delivered(self, timestamps: List[str]) -> None:
        """Remove measurements with matching timestamps (after successful upload)."""
        with self._lock:
            ts_set = set(timestamps)
            self._queue = [m for m in self._queue if m.get('timestamp') not in ts_set]

    def size(self) -> int:
        """Return current queue size."""
        with self._lock:
            return len(self._queue)

    def is_empty(self) -> bool:
        """Return True if queue is empty."""
        with self._lock:
            return len(self._queue) == 0


class MeasurementManager:
    """Central manager for measurement queuing, integrity, and batch uploads.

    Features:
    - Per-group measurement queues with configurable retention
    - SHA-256 integrity hashes on encrypted data
    - Batch upload support for efficiency
    - Configurable upload intervals per group
    - Thread-safe operations
    """

    def __init__(
        self,
        miner_key: str,
        miner_code: str,
        data_dir: str,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        upload_intervals: Optional[Dict[str, int]] = None,
    ):
        """Initialize measurement manager.

        Args:
            miner_key: The miner key for encryption key derivation
            miner_code: Miner type code (BM, ISM, etc.)
            data_dir: Base data directory for measurement files
            queue_size: Maximum measurements to retain per group
            upload_intervals: Per-group upload intervals in seconds
        """
        self.miner_key = miner_key
        self.miner_code = miner_code
        self.data_dir = data_dir
        self.queue_size = max(1, queue_size)

        # Per-group upload intervals (default 600s)
        self.upload_intervals: Dict[str, int] = upload_intervals or {}

        # Per-group queues
        self._queues: Dict[str, MeasurementQueue] = {}
        self._queue_lock = threading.Lock()

        # Last upload timestamps per group
        self._last_upload: Dict[str, float] = {}

        # Derive encryption key
        self._fernet_key = derive_measurement_key(miner_key)
        self._fernet: Optional[Any] = None
        if self._fernet_key and HAVE_CRYPTO:
            try:
                self._fernet = Fernet(self._fernet_key)
            except Exception:
                pass

        # Ensure measurements directory exists
        self._measurements_dir = os.path.join(data_dir, 'measurements')
        os.makedirs(self._measurements_dir, exist_ok=True)

        # Queue persistence directory
        self._queue_dir = os.path.join(data_dir, 'measurement_queue')
        os.makedirs(self._queue_dir, exist_ok=True)

        # Load persisted queues
        self._load_persisted_queues()

    def _get_queue(self, group: str) -> MeasurementQueue:
        """Get or create queue for a measurement group."""
        with self._queue_lock:
            if group not in self._queues:
                self._queues[group] = MeasurementQueue(group, self.queue_size)
            return self._queues[group]

    def _queue_file_path(self, group: str) -> str:
        """Get path to persisted queue file for a group."""
        safe_group = group.replace('/', '_').replace('\\', '_')
        return os.path.join(self._queue_dir, f'queue-{safe_group}.json')

    def _load_persisted_queues(self) -> None:
        """Load queues from disk on startup."""
        try:
            for filename in os.listdir(self._queue_dir):
                if not filename.startswith('queue-') or not filename.endswith('.json'):
                    continue
                filepath = os.path.join(self._queue_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    group = data.get('group')
                    measurements = data.get('measurements', [])
                    if isinstance(group, str) and isinstance(measurements, list):
                        queue = self._get_queue(group)
                        for m in measurements:
                            if isinstance(m, dict):
                                queue.push(m)
                except Exception:
                    pass
        except Exception:
            pass

    def _persist_queue(self, group: str) -> None:
        """Persist queue to disk for resilience."""
        try:
            queue = self._get_queue(group)
            data = {
                'group': group,
                'measurements': queue.peek_all(),
                'updated_at': _now_utc().isoformat(),
            }
            filepath = self._queue_file_path(group)
            tmp_path = filepath + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, filepath)
        except Exception:
            pass

    def get_upload_interval(self, group: str) -> int:
        """Get upload interval for a measurement group."""
        return self.upload_intervals.get(group, DEFAULT_UPLOAD_INTERVAL)

    def set_upload_interval(self, group: str, interval_seconds: int) -> None:
        """Set upload interval for a measurement group."""
        self.upload_intervals[group] = max(60, interval_seconds)

    def is_upload_due(self, group: str) -> bool:
        """Check if upload is due for a group based on configured interval."""
        interval = self.get_upload_interval(group)
        last = self._last_upload.get(group, 0)
        return (time.time() - last) >= interval

    def record_upload(self, group: str) -> None:
        """Record that an upload was performed for a group."""
        self._last_upload[group] = time.time()

    def add_measurement(
        self,
        group: str,
        measurement: Dict[str, Any],
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a measurement to the queue.

        Args:
            group: Measurement group (Bandwidth, Radiation, etc.)
            measurement: Measurement data dict
            timestamp: ISO timestamp (generated if not provided)

        Returns:
            The queued measurement record with metadata
        """
        if not timestamp:
            timestamp = _now_utc().isoformat()

        record = {
            'timestamp': timestamp,
            'miner_key': self.miner_key,
            'miner_code': self.miner_code,
            'group': group,
            'measurement': measurement,
            'queued_at': _now_utc().isoformat(),
        }

        queue = self._get_queue(group)
        queue.push(record)
        self._persist_queue(group)

        return record

    def encrypt_measurement(self, data: Dict[str, Any]) -> Optional[Tuple[bytes, str]]:
        """Encrypt measurement data and compute integrity hash.

        Args:
            data: Measurement data to encrypt

        Returns:
            Tuple of (encrypted_bytes, sha256_hash) or None if encryption fails
        """
        if not self._fernet:
            return None

        try:
            json_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
            encrypted = self._fernet.encrypt(json_bytes)
            integrity_hash = _compute_sha256(encrypted)
            return encrypted, integrity_hash
        except Exception:
            return None

    def decrypt_measurement(self, encrypted: bytes, expected_hash: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Decrypt measurement data with optional integrity verification.

        Args:
            encrypted: Encrypted bytes to decrypt
            expected_hash: Expected SHA-256 hash for integrity check

        Returns:
            Decrypted measurement dict or None if decryption/verification fails
        """
        if not self._fernet:
            return None

        try:
            # Verify integrity if hash provided
            if expected_hash:
                actual_hash = _compute_sha256(encrypted)
                if actual_hash != expected_hash:
                    return None

            decrypted = self._fernet.decrypt(encrypted)
            return json.loads(decrypted)
        except Exception:
            return None

    def write_encrypted_file(
        self,
        group: str,
        data: Dict[str, Any],
        include_hash: bool = True,
    ) -> Optional[str]:
        """Write encrypted measurement file with optional integrity hash.

        Args:
            group: Measurement group
            data: Measurement data
            include_hash: Whether to write hash file alongside

        Returns:
            Path to encrypted file or None if failed
        """
        result = self.encrypt_measurement(data)
        if not result:
            return None

        encrypted, hash_value = result

        safe_group = group.replace('/', '_').replace('\\', '_')
        base_path = os.path.join(self._measurements_dir, f'measurements-{safe_group}-latest')
        enc_path = base_path + '.json.enc'
        hash_path = base_path + '.sha256'

        try:
            # Atomic write of encrypted file
            tmp_enc = enc_path + '.tmp'
            with open(tmp_enc, 'wb') as f:
                f.write(encrypted)
            os.replace(tmp_enc, enc_path)

            # Write hash file if requested
            if include_hash:
                tmp_hash = hash_path + '.tmp'
                with open(tmp_hash, 'w', encoding='utf-8') as f:
                    f.write(hash_value)
                os.replace(tmp_hash, hash_path)

            return enc_path
        except Exception:
            return None

    def read_encrypted_files(self, verify_integrity: bool = True) -> List[Dict[str, Any]]:
        """Read and decrypt all measurement files with optional integrity verification.

        Args:
            verify_integrity: Whether to verify SHA-256 hash if present

        Returns:
            List of decrypted measurement dicts
        """
        measurements = []

        if not self._fernet:
            return measurements

        try:
            for filename in os.listdir(self._measurements_dir):
                if not filename.endswith('.json.enc'):
                    continue

                enc_path = os.path.join(self._measurements_dir, filename)
                hash_path = enc_path.replace('.json.enc', '.sha256')

                try:
                    with open(enc_path, 'rb') as f:
                        encrypted = f.read()

                    expected_hash = None
                    if verify_integrity and os.path.exists(hash_path):
                        with open(hash_path, 'r', encoding='utf-8') as f:
                            expected_hash = f.read().strip()

                    data = self.decrypt_measurement(encrypted, expected_hash)
                    if data:
                        measurements.append(data)
                except Exception:
                    pass
        except Exception:
            pass

        return measurements

    def get_pending_measurements(self, group: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all pending (queued) measurements.

        Args:
            group: Optional group filter

        Returns:
            List of pending measurement records
        """
        pending = []

        with self._queue_lock:
            queues = [self._queues[group]] if group and group in self._queues else list(self._queues.values())

        for queue in queues:
            pending.extend(queue.peek_all())

        return pending

    def prepare_batch_upload(
        self,
        group: Optional[str] = None,
        max_count: int = MAX_BATCH_SIZE,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
        """Prepare a batch of measurements for upload.

        Args:
            group: Optional group filter (all groups if None)
            max_count: Maximum measurements in batch

        Returns:
            Tuple of (batch_list, group_timestamps_map)
            group_timestamps_map is used to clear delivered measurements after upload
        """
        batch = []
        timestamps_by_group: Dict[str, List[str]] = {}
        remaining = max_count

        with self._queue_lock:
            queues = [(group, self._queues[group])] if group and group in self._queues else list(self._queues.items())

        for grp, queue in queues:
            if remaining <= 0:
                break

            items = queue.pop_batch(remaining)
            if items:
                batch.extend(items)
                timestamps_by_group[grp] = [m.get('timestamp', '') for m in items]
                remaining -= len(items)

        return batch, timestamps_by_group

    def mark_uploaded(self, group_timestamps: Dict[str, List[str]]) -> None:
        """Mark measurements as successfully uploaded.

        Args:
            group_timestamps: Map of group -> list of timestamps that were uploaded
        """
        for group, timestamps in group_timestamps.items():
            queue = self._get_queue(group)
            queue.clear_delivered(timestamps)
            self._persist_queue(group)

    def get_queue_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics about all measurement queues.

        Returns:
            Dict mapping group -> stats dict
        """
        stats = {}

        with self._queue_lock:
            for group, queue in self._queues.items():
                stats[group] = {
                    'size': queue.size(),
                    'max_size': queue.max_size,
                    'upload_interval': self.get_upload_interval(group),
                    'last_upload': self._last_upload.get(group),
                    'upload_due': self.is_upload_due(group),
                }

        return stats


def batch_upload_measurements(
    api_client,
    hex_id: str,
    measurements: List[Dict[str, Any]],
    miner_code: str,
    install_id: str,
) -> Tuple[int, int, List[str]]:
    """Upload a batch of measurements to the API.

    Args:
        api_client: API client with upload_measurement method
        hex_id: Registered H3 hex cell ID
        measurements: List of measurement records
        miner_code: Miner type code
        install_id: Installation UUID

    Returns:
        Tuple of (success_count, failure_count, delivered_timestamps)
    """
    success = 0
    failure = 0
    delivered = []

    for m in measurements:
        try:
            timestamp = m.get('timestamp')
            measurement_type = m.get('group') or m.get('measurement_type')
            value = m.get('measurement', {})

            if timestamp and measurement_type and isinstance(value, dict) and value:
                api_client.upload_measurement(
                    hex_id=hex_id,
                    miner_code=miner_code,
                    install_id=install_id,
                    timestamp=str(timestamp),
                    measurement_type=measurement_type,
                    value=value,
                )
                success += 1
                if timestamp:
                    delivered.append(timestamp)
            else:
                failure += 1
        except Exception:
            failure += 1

    return success, failure, delivered


__all__ = [
    'MeasurementQueue',
    'MeasurementManager',
    'batch_upload_measurements',
    'derive_measurement_key',
    'DEFAULT_QUEUE_SIZE',
    'DEFAULT_UPLOAD_INTERVAL',
    'MAX_BATCH_SIZE',
]
