"""Autonomys Auto Drive uploader using native SDK via Node.js.

This module provides a Python wrapper around the official Autonomys Auto Drive SDK,
which is implemented in TypeScript/JavaScript. It calls Node.js scripts to perform
uploads with proper encryption handling that the S3 API doesn't support.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

log = logging.getLogger("measurements.autonomys_uploader_native")


class AutonomysNativeUploader:
    """Native SDK uploader for Autonomys Auto Drive using Node.js."""

    def __init__(self, api_key: str, network: str = "mainnet"):
        """Initialize native uploader.

        Args:
            api_key: Auto Drive API key
            network: Network to use ('mainnet' or 'testnet')
        """
        self.api_key = api_key
        self.network = network

        # Check if Node.js is available
        if not self._check_nodejs():
            raise RuntimeError("Node.js is required for Autonomys native uploads. Install from: https://nodejs.org/")

        # Check if Auto Drive SDK is installed
        if not self._check_auto_drive_sdk():
            log.warning("@autonomys/auto-drive SDK not found. Installing...")
            if not self._install_auto_drive_sdk():
                raise RuntimeError("Failed to install @autonomys/auto-drive SDK")

        log.info("Autonomys native uploader initialized (network: %s)", network)

    def _check_nodejs(self) -> bool:
        """Check if Node.js is available."""
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                log.debug("Node.js found: %s", version)
                return True
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _check_auto_drive_sdk(self) -> bool:
        """Check if @autonomys/auto-drive SDK is installed."""
        try:
            result = subprocess.run(
                ["npm", "list", "@autonomys/auto-drive", "--depth=0"],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _install_auto_drive_sdk(self) -> bool:
        """Install @autonomys/auto-drive SDK via npm."""
        try:
            log.info("Installing @autonomys/auto-drive SDK...")
            result = subprocess.run(
                ["npm", "install", "@autonomys/auto-drive", "@autonomys/auto-utils"],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                log.info("SDK installed successfully")
                return True
            else:
                log.error("SDK installation failed: %s", result.stderr)
                return False
        except Exception as e:
            log.error("Failed to install SDK: %s", e)
            return False

    def upload_folder(
        self,
        local_folder: Path,
        encrypt: bool = False,
        password: Optional[str] = None
    ) -> Optional[str]:
        """Upload a folder to Autonomys Auto Drive.

        The folder structure will be preserved on Auto Drive.
        For example, if local_folder is "temp/851f9017fffffff", the upload will create:
          851f9017fffffff/
            └── bandwidth/
                 └── hourly/
                      └── 2026-01-21.parquet

        Args:
            local_folder: Path to local folder to upload
            encrypt: Whether to encrypt the folder
            password: Encryption password (required if encrypt=True)

        Returns:
            Folder CID if successful, None otherwise
        """
        if not local_folder.exists():
            log.error("Folder not found: %s", local_folder)
            return None

        if encrypt and not password:
            log.error("Password required when encryption is enabled")
            return None

        # Path to the Node.js upload script
        script_path = Path(__file__).parent.parent / "autonomys_upload_folder.js"
        if not script_path.exists():
            log.error("Upload script not found: %s", script_path)
            return None

        try:
            # Build command
            cmd = [
                "node",
                str(script_path),
                "--api-key", self.api_key,
                "--folder", str(local_folder),
                "--network", self.network
            ]

            if encrypt:
                cmd.extend(["--encrypt", "true", "--password", password])

            # Run upload
            log.info("Uploading folder via Node.js: %s", local_folder.name)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )

            # Parse output
            output_lines = result.stdout.split('\n')
            for line in output_lines:
                if line.startswith('RESULT_JSON:'):
                    json_str = line.replace('RESULT_JSON:', '').strip()
                    result_data = json.loads(json_str)

                    if result_data.get('success'):
                        cid = result_data.get('cid')
                        log.info("Folder uploaded successfully (CID: %s)", cid)
                        return cid
                    else:
                        error = result_data.get('error', 'Unknown error')
                        log.error("Upload failed: %s", error)
                        return None

            # If we didn't find RESULT_JSON, log the output
            log.error("Upload failed (no result): %s", result.stderr or result.stdout)
            return None

        except subprocess.TimeoutExpired:
            log.error("Upload timed out after 5 minutes")
            return None
        except Exception as e:
            log.error("Unexpected error during upload: %s", e)
            return None

    def upload_file_with_structure(
        self,
        local_file: Path,
        remote_path: str,
        encrypt: bool = False,
        password: Optional[str] = None
    ) -> Optional[str]:
        """Upload a single file by creating a temporary folder structure.

        This creates a temporary folder with the desired structure, copies the file,
        uploads the folder, then cleans up.

        Args:
            local_file: Path to local file
            remote_path: Desired remote path (e.g., "851f.../bandwidth/hourly/2026-01-21.parquet")
            encrypt: Whether to encrypt
            password: Encryption password

        Returns:
            Folder CID if successful, None otherwise
        """
        if not local_file.exists():
            log.error("File not found: %s", local_file)
            return None

        # Create temporary folder structure
        temp_dir = None
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="autonomys_upload_"))

            # Parse remote path to create structure
            # e.g., "851f9017fffffff/bandwidth/hourly/2026-01-21.parquet"
            parts = Path(remote_path).parts
            if len(parts) < 2:
                log.error("Invalid remote path (too short): %s", remote_path)
                return None

            # Create the directory structure in temp
            target_dir = temp_dir / Path(*parts[:-1])
            target_dir.mkdir(parents=True, exist_ok=True)

            # Copy file to target location
            target_file = target_dir / parts[-1]
            shutil.copy2(local_file, target_file)

            log.debug("Created temp structure: %s", temp_dir / parts[0])

            # Upload the root folder (e.g., temp_dir/851f9017fffffff)
            root_folder = temp_dir / parts[0]
            cid = self.upload_folder(root_folder, encrypt=encrypt, password=password)

            return cid

        finally:
            # Clean up temp directory
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                    log.debug("Cleaned up temp directory")
                except Exception as e:
                    log.warning("Failed to clean up temp directory: %s", e)


def get_native_uploader(
    api_key: Optional[str] = None,
    network: str = "mainnet"
) -> Optional[AutonomysNativeUploader]:
    """Get configured native Autonomys uploader.

    Args:
        api_key: Auto Drive API key (if None, will try to retrieve from secrets_manager)
        network: Network to use ('mainnet' or 'testnet')

    Returns:
        AutonomysNativeUploader instance or None if setup fails
    """
    # Get API key if not provided
    if not api_key:
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
        return AutonomysNativeUploader(api_key=api_key, network=network)
    except Exception as e:
        log.error("Failed to create native uploader: %s", e)
        return None


if __name__ == "__main__":
    """Test the native uploader."""
    import sys

    logging.basicConfig(level=logging.INFO)

    print("Testing Autonomys Native Uploader")
    print("=" * 60)

    # Check Node.js
    uploader = AutonomysNativeUploader(api_key="test")
    if uploader._check_nodejs():
        print("✓ Node.js is available")
    else:
        print("✗ Node.js not found")
        print("  Install from: https://nodejs.org/")
        sys.exit(1)

    # Check SDK
    if uploader._check_auto_drive_sdk():
        print("✓ @autonomys/auto-drive SDK is installed")
    else:
        print("✗ SDK not installed")
        print("  Run: npm install @autonomys/auto-drive @autonomys/auto-utils")

    print()
    print("Setup complete. Ready for uploads.")
