"""Autonomys uploader using your existing working Node.js SDK.

This module wraps your proven Autonomys/upload_with_sdk.js script for
reliable uploads with flat file naming.
"""

import logging
import os
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from datetime import datetime

log = logging.getLogger("measurements.autonomys_uploader_nodejs")


class AutonomysNodeJSUploader:
    """Node.JS SDK uploader wrapper for Autonomys Auto Drive."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Node.JS uploader.

        Args:
            api_key: Auto Drive API key (optional, can use 1Password via script)
        """
        self.api_key = api_key

        # Find the upload script
        self.script_path = self._find_upload_script()
        if not self.script_path:
            raise FileNotFoundError("Autonomys upload script not found")

        # Check if Node.js is available
        if not self._check_nodejs():
            raise RuntimeError("Node.js is required. Install from: https://nodejs.org/")

        log.info("Autonomys Node.JS uploader initialized")

    def _find_upload_script(self) -> Optional[Path]:
        """Find the upload_with_sdk.js script."""
        # Try multiple possible locations
        possible_paths = [
            Path(__file__).parent.parent / "Autonomys" / "upload_with_sdk.js",
            Path("Autonomys/upload_with_sdk.js"),
            Path("../Autonomys/upload_with_sdk.js")
        ]

        for path in possible_paths:
            if path.exists():
                log.debug("Found upload script at: %s", path)
                return path.resolve()

        return None

    def _check_nodejs(self) -> bool:
        """Check if Node.js is available."""
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def upload_file(
        self,
        local_path: Path,
        remote_filename: str
    ) -> Optional[str]:
        """Upload a file using the Node.JS SDK.

        Args:
            local_path: Path to local file
            remote_filename: Remote filename (flat naming)
                           Example: "851f9017fffffff_bandwidth_2026-01-21.parquet"

        Returns:
            CID if successful, None otherwise
        """
        if not local_path.exists():
            log.error("File not found: %s", local_path)
            return None

        # Create temp directory for renamed file
        temp_dir = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="autonomys_upload_"))

            # Copy file with new name to temp dir
            temp_file = temp_dir / remote_filename
            shutil.copy2(local_path, temp_file)

            # Build command
            cmd = [
                "node",
                str(self.script_path),
                "--use-1pw",  # Use 1Password for API key
                str(temp_file)  # File to upload
            ]

            # If API key provided directly, pass it via environment
            env = os.environ.copy()
            if self.api_key:
                env['AUTONOMYS_API_KEY'] = self.api_key

            # Run upload
            log.info("Uploading %s as %s", local_path.name, remote_filename)
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes
            )

            # Parse output for CID
            if result.returncode == 0:
                # Look for CID in output
                for line in result.stdout.split('\n'):
                    if 'CID' in line or 'cid' in line:
                        # Extract CID from line
                        parts = line.split(':')
                        if len(parts) >= 2:
                            cid = parts[-1].strip()
                            log.info("Upload successful: %s (CID: %s)", remote_filename, cid)
                            return cid

                # If no CID found but success, return success indicator
                log.info("Upload successful: %s", remote_filename)
                return "success"
            else:
                log.error("Upload failed (exit code %d): %s", result.returncode, result.stderr)
                return None

        except subprocess.TimeoutExpired:
            log.error("Upload timeout for %s", local_path)
            return None
        except Exception as e:
            log.error("Upload error for %s: %s", local_path, e)
            return None
        finally:
            # Clean up temp directory
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    log.warning("Failed to clean up temp dir: %s", e)

    def upload_daily_file(
        self,
        local_path: Path,
        hex_id: str,
        measurement_type: str,
        date: datetime
    ) -> Optional[str]:
        """Upload a daily measurement file with flat naming.

        Args:
            local_path: Path to local file
            hex_id: Redacted H3 hex ID
            measurement_type: Type of measurement
            date: Date of measurement

        Returns:
            CID if successful, None otherwise
        """
        # Format: {hexId}_{measurement_type}_YYYY-MM-DD.ext
        date_str = date.strftime("%Y-%m-%d")
        ext = local_path.suffix

        remote_filename = f"{hex_id}_{measurement_type}_{date_str}{ext}"

        return self.upload_file(local_path, remote_filename)


def get_nodejs_uploader(api_key: Optional[str] = None) -> Optional[AutonomysNodeJSUploader]:
    """Get configured Node.JS uploader.

    Args:
        api_key: Auto Drive API key (optional, will use 1Password if not provided)

    Returns:
        AutonomysNodeJSUploader instance or None if setup fails
    """
    # If no API key provided, the Node.JS script will use 1Password
    # So we don't need to retrieve it here

    try:
        return AutonomysNodeJSUploader(api_key=api_key)
    except Exception as e:
        log.error("Failed to create Node.JS uploader: %s", e)
        return None


if __name__ == "__main__":
    """Test the Node.JS uploader."""
    import sys
    logging.basicConfig(level=logging.INFO)

    print("=" * 70)
    print("Autonomys Node.JS Uploader Test")
    print("=" * 70)
    print()

    # Get uploader
    uploader = get_nodejs_uploader()
    if not uploader:
        print("Failed to initialize uploader")
        sys.exit(1)

    print("[OK] Node.JS uploader initialized")
    print(f"  Script: {uploader.script_path}")
    print()

    # Test with actual file if available
    test_file = Path("C:/ProgramData/FryNetworks/miner-BM/measurements/hourly/2026-01-21.parquet")
    if test_file.exists():
        print(f"Testing upload with: {test_file}")

        hex_id = "851f9017fffffff"
        date = datetime(2026, 1, 21)

        cid = uploader.upload_daily_file(
            local_path=test_file,
            hex_id=hex_id,
            measurement_type="bandwidth",
            date=date
        )

        if cid:
            print(f"[OK] Upload successful! CID: {cid}")
        else:
            print("[FAIL] Upload failed")
    else:
        print(f"Test file not found: {test_file}")
