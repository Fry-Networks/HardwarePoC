"""Autonomys Auto Drive uploader using S3-compatible API.

Handles uploading of parquet files and metadata to Autonomys decentralized storage.
Uses 1Password for secure API key management in production.
"""

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("measurements.autonomys_uploader")

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    log.warning("boto3 not available. Install with: pip install boto3")

# Import secrets manager for 1Password integration
try:
    from .secrets_manager import get_autonomys_api_key
    SECRETS_MANAGER_AVAILABLE = True
except ImportError:
    SECRETS_MANAGER_AVAILABLE = False
    log.debug("Secrets manager not available")


class AutonomysUploader:
    """S3-compatible uploader for Autonomys Auto Drive."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint_url: str = "https://public.auto-drive.autonomys.xyz/api/s3",
        region: str = "us-east-1"
    ):
        """Initialize Autonomys uploader.

        Args:
            api_key: Auto Drive API key (optional, will use 1Password/env if not provided)
            endpoint_url: S3 endpoint URL (defaults to mainnet)
            region: AWS region (doesn't matter for Auto Drive but required by boto3)
        """
        if not BOTO3_AVAILABLE:
            raise RuntimeError("boto3 is required for Autonomys uploads")

        # Get API key using priority order:
        # 1. Provided parameter
        # 2. 1Password (via secrets_manager)
        # 3. Environment variable AUTONOMYS_API_KEY
        if api_key:
            self.api_key = api_key
            log.debug("Using provided API key")
        elif SECRETS_MANAGER_AVAILABLE:
            self.api_key = get_autonomys_api_key()
            if not self.api_key:
                raise ValueError(
                    "API key not found in 1Password or environment. "
                    "Set up 1Password item 'Autonomys API Key' or set AUTONOMYS_API_KEY environment variable. "
                    "Get your key at: https://ai3.storage"
                )
        else:
            # Fall back to environment variable only
            self.api_key = os.environ.get("AUTONOMYS_API_KEY")
            if not self.api_key:
                raise ValueError(
                    "API key required. Set AUTONOMYS_API_KEY environment variable "
                    "or pass api_key parameter. Get your key at: https://ai3.storage"
                )

        self.endpoint_url = endpoint_url
        self.region = region

        # Configure S3 client for Auto Drive
        config = Config(
            region_name=region,
            signature_version='s3v4',
            s3={'addressing_style': 'path'},  # Use path-style addressing for Auto Drive
            retries={'max_attempts': 3, 'mode': 'standard'}
        )

        self.s3_client = boto3.client(
            's3',
            endpoint_url=endpoint_url,
            aws_access_key_id=self.api_key,
            aws_secret_access_key="",  # Auto Drive doesn't use secret key
            config=config
        )

        log.info("Autonomys uploader initialized with endpoint: %s", endpoint_url)

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        metadata: Optional[dict] = None
    ) -> Optional[str]:
        """Upload a file to Autonomys Auto Drive.

        Args:
            local_path: Local file path
            remote_path: Remote path (e.g., "res-7/871f90151ffffff/bandwidth/hourly/2026-01-20.parquet")
            metadata: Optional metadata dict to attach to the object

        Returns:
            Remote path if successful, None otherwise
        """
        if not local_path.exists():
            log.error("File not found: %s", local_path)
            return None

        try:
            # Read file content
            with open(local_path, 'rb') as f:
                file_content = f.read()

            # Prepare kwargs for put_object
            put_kwargs = {
                'Bucket': 'uploads',  # Arbitrary bucket name for Auto Drive
                'Key': remote_path,
                'Body': file_content
            }

            # Prepare metadata
            # Per Auto Drive docs: omit encryption metadata for unencrypted uploads
            # Only include user-provided metadata if any
            if metadata:
                put_kwargs['Metadata'] = {str(k): str(v) for k, v in metadata.items()}

            # Upload using put_object for better error handling
            # Note: Auto Drive uses path-based routing, no actual bucket concept
            response = self.s3_client.put_object(**put_kwargs)

            log.info("Uploaded to Auto Drive: %s (ETag: %s)", remote_path, response.get('ETag', 'N/A'))
            return remote_path

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            log.error("Failed to upload %s (Code: %s): %s", local_path, error_code, error_msg)
            return None
        except Exception as e:
            log.error("Unexpected error uploading %s: %s", local_path, e)
            return None

    def upload_folder(
        self,
        local_folder: Path,
        remote_prefix: str
    ) -> int:
        """Upload all files in a folder to Autonomys Auto Drive.

        Args:
            local_folder: Local folder path
            remote_prefix: Remote path prefix (e.g., "res-7/871f90151ffffff")

        Returns:
            Number of files successfully uploaded
        """
        if not local_folder.is_dir():
            log.error("Folder not found: %s", local_folder)
            return 0

        uploaded = 0

        for file_path in local_folder.rglob('*'):
            if file_path.is_file():
                # Calculate relative path
                rel_path = file_path.relative_to(local_folder)
                remote_path = f"{remote_prefix}/{rel_path}".replace('\\', '/')

                if self.upload_file(file_path, remote_path):
                    uploaded += 1

        log.info("Uploaded %d files from %s", uploaded, local_folder)
        return uploaded

    def list_objects(self, prefix: str, max_keys: int = 1000) -> list:
        """List objects in Auto Drive with a given prefix.

        Args:
            prefix: Path prefix to filter by
            max_keys: Maximum number of keys to return

        Returns:
            List of object keys
        """
        try:
            response = self.s3_client.list_objects_v2(
                Bucket='frynetworks-measurements',
                Prefix=prefix,
                MaxKeys=max_keys
            )

            if 'Contents' in response:
                return [obj['Key'] for obj in response['Contents']]
            return []

        except ClientError as e:
            log.error("Failed to list objects with prefix %s: %s", prefix, e)
            return []

    def download_file(
        self,
        remote_path: str,
        local_path: Path
    ) -> bool:
        """Download a file from Autonomys Auto Drive.

        Args:
            remote_path: Remote path
            local_path: Local path to save file

        Returns:
            True if successful
        """
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)

            self.s3_client.download_file(
                'frynetworks-measurements',
                remote_path,
                str(local_path)
            )

            log.info("Downloaded from Auto Drive: %s", remote_path)
            return True

        except ClientError as e:
            log.error("Failed to download %s: %s", remote_path, e)
            return False


def get_uploader(
    api_key: Optional[str] = None,
    use_testnet: bool = False
) -> Optional[AutonomysUploader]:
    """Get configured Autonomys uploader instance.

    Automatically retrieves API key from 1Password if not provided.
    Falls back to environment variable for development/testing.

    Args:
        api_key: Auto Drive API key (optional, will use 1Password/env if not provided)
        use_testnet: Use testnet endpoint instead of mainnet

    Returns:
        AutonomysUploader instance or None if dependencies not available or API key not found
    """
    if not BOTO3_AVAILABLE:
        log.error("boto3 not available for Autonomys uploads")
        return None

    try:
        endpoint = "http://localhost:3000/s3" if use_testnet else \
                   "https://public.auto-drive.autonomys.xyz/api/s3"

        # AutonomysUploader will automatically retrieve from 1Password if api_key is None
        return AutonomysUploader(api_key=api_key, endpoint_url=endpoint)

    except Exception as e:
        log.error("Failed to create Autonomys uploader: %s", e)
        return None
