"""Autonomys Auto Drive uploader using REST API (pure Python).

This module uploads files to Autonomys Auto Drive using the REST API,
no Node.js required. Files are uploaded with flat naming since folders
are not supported.

Naming pattern: {hexId}_{measurement_type}_YYYY-MM-DD_HH.parquet
Example: 851f9017fffffff_bandwidth_2026-01-21_17.parquet
"""

import logging
import requests
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

log = logging.getLogger("measurements.autonomys_uploader_rest")


class AutonomysRestUploader:
    """REST API uploader for Autonomys Auto Drive."""

    def __init__(self, api_key: str, endpoint_url: str = "https://mainnet.auto-drive.autonomys.xyz"):
        """Initialize REST uploader.

        Args:
            api_key: Auto Drive API key
            endpoint_url: API endpoint URL (default: mainnet)
        """
        self.api_key = api_key
        self.endpoint_url = endpoint_url.rstrip('/')
        self.upload_url = f"{self.endpoint_url}/api/uploads/file"

        log.debug("Autonomys REST uploader initialized (endpoint: %s)", endpoint_url)

    def upload_file(
        self,
        local_path: Path,
        remote_filename: str,
        metadata: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        """Upload a file to Autonomys Auto Drive using REST API.

        Args:
            local_path: Path to local file
            remote_filename: Remote filename (flat, no folders)
                            Example: "851f9017fffffff_bandwidth_2026-01-21_17.parquet"
            metadata: Optional metadata dict (not currently used by API)

        Returns:
            CID (Content Identifier) if successful, None otherwise
        """
        # Always use chunked upload flow for reliability
        if not local_path.exists():
            log.error("File not found: %s", local_path)
            return None

        log.debug("Using chunked upload for %s as %s (%d bytes)", local_path.name, remote_filename, local_path.stat().st_size)
        try:
            return self.upload_file_chunked(local_path=local_path, remote_filename=remote_filename)
        except Exception as e:
            log.error("Chunked upload failed for %s: %s", local_path, e)
            return None

    # --- Chunked upload helpers ---
    def create_upload_session(self, remote_filename: str, mime_type: str, upload_options: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Create an upload session via POST /api/uploads/file (JSON). Returns the JSON response containing `id`.

        This mirrors the API's create-session behavior used for large file uploads.
        """
        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'X-Auth-Provider': 'apikey',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            payload = {
                'filename': remote_filename,
                'mimeType': mime_type
            }
            if upload_options:
                payload['uploadOptions'] = upload_options

            r = requests.post(self.upload_url, headers=headers, json=payload, timeout=30)
            if r.status_code in (200, 201):
                return r.json()
            else:
                log.error("Failed to create upload session (HTTP %d): %s", r.status_code, r.text[:500])
                return None
        except Exception as e:
            log.error("Error creating upload session: %s", e)
            return None

    def upload_chunk(self, upload_id: str, index: int, chunk_bytes: bytes, remote_filename: str, mime_type: str) -> bool:
        """Upload a single chunk to POST /api/uploads/file/{uploadId}/chunk as multipart with fields `file` and `index`.

        Returns True on success.
        """
        try:
            url = f"{self.endpoint_url}/api/uploads/file/{upload_id}/chunk"
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'X-Auth-Provider': 'apikey',
                'Accept': 'application/json'
            }
            files = {
                'file': (remote_filename, chunk_bytes, mime_type)
            }
            data = {'index': int(index)}
            r = requests.post(url, headers=headers, files=files, data=data, timeout=120)
            if r.status_code == 200:
                return True
            else:
                log.error("Chunk upload failed (index=%d HTTP %d): %s", index, r.status_code, r.text[:500])
                return False
        except Exception as e:
            log.error("Chunk upload error (index=%d): %s", index, e)
            return False

    def complete_upload(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Complete the upload by calling POST /api/uploads/{uploadId}/complete (returns JSON with `cid`)."""
        try:
            url = f"{self.endpoint_url}/api/uploads/{upload_id}/complete"
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'X-Auth-Provider': 'apikey',
                'Accept': 'application/json'
            }
            r = requests.post(url, headers=headers, timeout=60)
            if r.status_code in (200, 201):
                try:
                    return r.json()
                except Exception:
                    return {'result': 'ok'}
            else:
                log.error("Complete upload failed (HTTP %d): %s", r.status_code, r.text[:500])
                return None
        except Exception as e:
            log.error("Error completing upload: %s", e)
            return None

    def upload_file_chunked(self, local_path: Path, remote_filename: str, chunk_size: Optional[int] = None) -> Optional[str]:
        """High-level chunked upload flow: create session, upload chunks, complete, return CID."""
        if not local_path.exists():
            log.error("File not found for chunked upload: %s", local_path)
            return None

        # Determine mime type
        ext = local_path.suffix.lower()
        mime_type = 'application/octet-stream' if ext == '.parquet' else ('application/json' if ext == '.json' else 'application/octet-stream')

        # Decide desired chunk size (use provided or default 4MiB)
        desired_chunk = chunk_size if chunk_size and chunk_size > 0 else (4 * 1024 * 1024)

        # Prepare sensible uploadOptions to satisfy API (compression + encryption + chunkSize)
        upload_options = {
            'compression': {'algorithm': 'ZLIB', 'level': 8},
            'encryption': {'algorithm': 'AES_256_GCM', 'chunkSize': int(desired_chunk)}
        }

        # Create session
        sess = self.create_upload_session(remote_filename=remote_filename, mime_type=mime_type, upload_options=upload_options)
        if not sess:
            log.error("Failed to create upload session for %s", remote_filename)
            return None

        upload_id = sess.get('id') or sess.get('uploadId')
        if not upload_id:
            log.error("Upload session response missing id: %s", sess)
            return None

        # Decide chunk size: use provided or server-suggested
        server_chunk = None
        try:
            server_chunk = int(sess.get('uploadOptions', {}).get('encryption', {}).get('chunkSize') or 0)
        except Exception:
            server_chunk = None
        if not chunk_size:
            chunk_size = server_chunk if server_chunk and server_chunk > 0 else (4 * 1024 * 1024)

        log.debug("Uploading %s in chunks (uploadId=%s, chunk_size=%d)", local_path.name, upload_id, chunk_size)

        # Stream file and upload chunks
        idx = 0
        try:
            with open(local_path, 'rb') as fh:
                while True:
                    chunk = fh.read(chunk_size)
                    if not chunk:
                        break
                    ok = self.upload_chunk(upload_id=upload_id, index=idx, chunk_bytes=chunk, remote_filename=remote_filename, mime_type=mime_type)
                    if not ok:
                        log.error("Failed uploading chunk %d", idx)
                        return None
                    idx += 1

            # Complete
            comp = self.complete_upload(upload_id)
            if comp and isinstance(comp, dict):
                # common response contains 'cid'
                cid = comp.get('cid') or comp.get('CID') or comp.get('cidString') or None
                if cid:
                    log.debug("Chunked upload complete: %s", cid)
                    return cid
                # else maybe entire response is the object metadata
                return comp.get('cid') if isinstance(comp, dict) else None
            return None
        except Exception as e:
            log.error("Chunked upload failed: %s", e)
            return None

    def upload_hourly_file(
        self,
        local_path: Path,
        hex_id: str,
        measurement_type: str,
        date: datetime,
        hour: int
    ) -> Optional[str]:
        """Upload an hourly measurement file with proper naming.

        Args:
            local_path: Path to local parquet file
            hex_id: Redacted H3 hex ID
            measurement_type: Type of measurement (bandwidth, satellite, etc.)
            date: Date of measurement
            hour: Hour of day (0-23)

        Returns:
            CID if successful, None otherwise
        """
        # Format: {hexId}_{measurement_type}_YYYY-MM-DD_HH.parquet
        date_str = date.strftime("%Y-%m-%d")
        hour_str = f"{hour:02d}"

        # Determine extension
        ext = local_path.suffix  # .parquet or .json

        remote_filename = f"{hex_id}_{measurement_type}_{date_str}_{hour_str}{ext}"

        return self.upload_file(local_path, remote_filename)

    def upload_daily_file(
        self,
        local_path: Path,
        hex_id: str,
        measurement_type: str,
        date: datetime
    ) -> Optional[str]:
        """Upload a daily measurement file with proper naming.

        Args:
            local_path: Path to local parquet file
            hex_id: Redacted H3 hex ID
            measurement_type: Type of measurement
            date: Date of measurement

        Returns:
            CID if successful, None otherwise
        """
        # Format: {hexId}_{measurement_type}_YYYY-MM-DD.parquet
        date_str = date.strftime("%Y-%m-%d")
        ext = local_path.suffix

        remote_filename = f"{hex_id}_{measurement_type}_{date_str}{ext}"

        return self.upload_file(local_path, remote_filename)


def get_rest_uploader(
    api_key: Optional[str] = None,
    use_testnet: bool = False
) -> Optional[AutonomysRestUploader]:
    """Get configured REST uploader.

    Args:
        api_key: Auto Drive API key (if None, retrieves from secrets_manager)
        use_testnet: Use testnet endpoint instead of mainnet

    Returns:
        AutonomysRestUploader instance or None if API key not found
    """
    # Get API key if not provided - check env var first, then secrets_manager
    if not api_key:
        import os
        api_key = os.environ.get("AUTONOMYS_API_KEY")
        if api_key:
            log.debug("Using Autonomys API key from environment variable")
        else:
            try:
                from .secrets_manager import get_autonomys_api_key
                api_key = get_autonomys_api_key()
                if not api_key:
                    log.error("Autonomys API key not found")
                    return None
            except ImportError:
                log.error("Cannot import secrets_manager")
                return None

    try:
        endpoint = "https://testnet.auto-drive.autonomys.xyz" if use_testnet else \
                   "https://mainnet.auto-drive.autonomys.xyz"

        return AutonomysRestUploader(api_key=api_key, endpoint_url=endpoint)

    except Exception as e:
        log.error("Failed to create REST uploader: %s", e)
        return None


if __name__ == "__main__":
    """Test the REST uploader."""
    import sys
    logging.basicConfig(level=logging.INFO)

    print("=" * 70)
    print("Autonomys REST Uploader Test")
    print("=" * 70)
    print()

    # Get uploader
    uploader = get_rest_uploader()
    if not uploader:
        print("Failed to initialize uploader (API key not found)")
        sys.exit(1)

    print("✓ REST uploader initialized")
    print(f"  Endpoint: {uploader.endpoint_url}")
    print()

    # Test with a small file
    test_file = Path("test_upload.txt")
    if test_file.exists():
        print(f"Testing upload with: {test_file}")

        # Test flat naming
        hex_id = "851f9017fffffff"
        date = datetime.now()

        cid = uploader.upload_hourly_file(
            local_path=test_file,
            hex_id=hex_id,
            measurement_type="bandwidth",
            date=date,
            hour=17
        )

        if cid:
            print(f"✓ Upload successful! CID: {cid}")
        else:
            print("✗ Upload failed")
    else:
        print(f"Test file not found: {test_file}")
        print("Create it to test upload:")
        print(f'  echo "test data" > {test_file}')
