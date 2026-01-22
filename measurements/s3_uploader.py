"""S3 uploader for original measurement data.

Uploads original (unredacted) measurement data from local storage to S3
for centralized API server access. This is separate from Autonomys uploads.

Storage structure in S3:
  s3://bucket-name/original-data/
    ├── {miner_id}/
    │   ├── decibel/hourly/2026-01-22.parquet
    │   ├── bandwidth/hourly/2026-01-22.parquet
    │   └── metadata.json
"""

import logging
import os
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timezone
import json

log = logging.getLogger("measurements.s3_uploader")

# Try to import boto3 (optional dependency)
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    S3_AVAILABLE = True
except ImportError:
    S3_AVAILABLE = False
    log.warning("boto3 not available - S3 upload disabled. Install with: pip install boto3")


class S3Uploader:
    """Handles uploading original measurement data to S3."""

    def __init__(
        self,
        bucket_name: str,
        miner_id: str,
        aws_region: str = "us-east-1",
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        prefix: str = "original-data"
    ):
        """Initialize S3 uploader.

        Args:
            bucket_name: S3 bucket name (e.g., "frynetworks-measurements")
            miner_id: Unique miner identifier (e.g., "miner-001", install_id, or custom ID)
            aws_region: AWS region (default: us-east-1)
            aws_access_key_id: AWS access key (optional, uses IAM role if not provided)
            aws_secret_access_key: AWS secret key (optional, uses IAM role if not provided)
            prefix: S3 key prefix (default: "original-data")
        """
        if not S3_AVAILABLE:
            raise RuntimeError("boto3 not installed. Install with: pip install boto3")

        self.bucket_name = bucket_name
        self.miner_id = miner_id
        self.prefix = prefix
        self.aws_region = aws_region

        # Initialize S3 client
        session_kwargs = {"region_name": aws_region}
        if aws_access_key_id and aws_secret_access_key:
            session_kwargs["aws_access_key_id"] = aws_access_key_id
            session_kwargs["aws_secret_access_key"] = aws_secret_access_key

        self.s3_client = boto3.client("s3", **session_kwargs)

        # Test connection
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
            log.info(f"S3 uploader initialized: bucket={bucket_name}, miner={miner_id}")
        except ClientError as e:
            log.error(f"Cannot access S3 bucket {bucket_name}: {e}")
            raise

    def upload_file(
        self,
        local_path: Path,
        measurement_type: str,
        date_str: str,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Upload a single measurement file to S3.

        Args:
            local_path: Local file path to upload
            measurement_type: Type (bandwidth, decibel, radiation, satellite, aem)
            date_str: Date string (YYYY-MM-DD)
            metadata: Optional metadata dict to attach as S3 object metadata

        Returns:
            True if successful, False otherwise

        Example S3 path:
            s3://bucket/original-data/miner-001/decibel/hourly/2026-01-22.parquet
        """
        if not local_path.exists():
            log.error(f"Local file not found: {local_path}")
            return False

        # Construct S3 key
        s3_key = f"{self.prefix}/{self.miner_id}/{measurement_type}/hourly/{date_str}.parquet"

        try:
            # Prepare extra args (metadata, content type, etc.)
            extra_args = {
                "ContentType": "application/octet-stream",
                "ServerSideEncryption": "AES256"  # Encrypt at rest
            }

            # Add custom metadata if provided
            if metadata:
                # S3 metadata keys must be lowercase and alphanumeric
                s3_metadata = {
                    "miner-id": self.miner_id,
                    "measurement-type": measurement_type,
                    "date": date_str,
                    "uploaded-at": datetime.now(timezone.utc).isoformat()
                }
                if metadata:
                    for k, v in metadata.items():
                        key = k.lower().replace("_", "-")
                        s3_metadata[key] = str(v)
                extra_args["Metadata"] = s3_metadata

            # Upload file
            log.info(f"Uploading {local_path.name} to s3://{self.bucket_name}/{s3_key}")
            self.s3_client.upload_file(
                str(local_path),
                self.bucket_name,
                s3_key,
                ExtraArgs=extra_args
            )

            log.info(f"Successfully uploaded {s3_key}")
            return True

        except ClientError as e:
            log.error(f"Failed to upload {local_path} to S3: {e}")
            return False
        except Exception as e:
            log.error(f"Unexpected error uploading {local_path}: {e}")
            return False

    def upload_directory(
        self,
        local_dir: Path,
        measurement_type: str
    ) -> Dict[str, bool]:
        """Upload all parquet files from a directory.

        Args:
            local_dir: Local directory containing .parquet files
            measurement_type: Type of measurement

        Returns:
            Dict mapping filename to upload success status
        """
        if not local_dir.exists() or not local_dir.is_dir():
            log.error(f"Directory not found: {local_dir}")
            return {}

        results = {}

        for parquet_file in local_dir.glob("*.parquet"):
            # Extract date from filename (assumes YYYY-MM-DD.parquet)
            date_str = parquet_file.stem  # e.g., "2026-01-22"

            success = self.upload_file(
                parquet_file,
                measurement_type,
                date_str
            )
            results[parquet_file.name] = success

        log.info(f"Uploaded {sum(results.values())}/{len(results)} files from {local_dir}")
        return results

    def upload_miner_metadata(self, metadata: Dict) -> bool:
        """Upload miner metadata file.

        Args:
            metadata: Dict with miner information (location, types, etc.)

        Returns:
            True if successful

        Example metadata:
            {
                "miner_id": "miner-001",
                "hex_id": "871f90151ffffff",
                "location": {"lat": 40.712776, "lon": -74.005974},
                "measurement_types": ["bandwidth", "decibel"],
                "first_upload": "2026-01-20T00:00:00Z",
                "last_upload": "2026-01-22T23:59:59Z"
            }
        """
        s3_key = f"{self.prefix}/{self.miner_id}/metadata.json"

        try:
            # Convert to JSON
            metadata_json = json.dumps(metadata, indent=2)

            # Upload
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=metadata_json.encode('utf-8'),
                ContentType="application/json",
                ServerSideEncryption="AES256"
            )

            log.info(f"Uploaded miner metadata to {s3_key}")
            return True

        except Exception as e:
            log.error(f"Failed to upload miner metadata: {e}")
            return False

    def list_uploaded_files(self, measurement_type: str) -> List[str]:
        """List files already uploaded for a measurement type.

        Args:
            measurement_type: Type to check

        Returns:
            List of date strings (YYYY-MM-DD) already uploaded
        """
        prefix = f"{self.prefix}/{self.miner_id}/{measurement_type}/hourly/"

        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix
            )

            dates = []
            if 'Contents' in response:
                for obj in response['Contents']:
                    # Extract date from key: .../2026-01-22.parquet
                    key = obj['Key']
                    filename = key.split('/')[-1]
                    date_str = filename.replace('.parquet', '')
                    dates.append(date_str)

            return sorted(dates)

        except Exception as e:
            log.error(f"Failed to list uploaded files: {e}")
            return []

    def file_exists(self, measurement_type: str, date_str: str) -> bool:
        """Check if a file already exists in S3.

        Args:
            measurement_type: Type of measurement
            date_str: Date string (YYYY-MM-DD)

        Returns:
            True if file exists in S3
        """
        s3_key = f"{self.prefix}/{self.miner_id}/{measurement_type}/hourly/{date_str}.parquet"

        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError:
            return False


def get_s3_uploader(
    config_path: Optional[Path] = None
) -> Optional[S3Uploader]:
    """Get S3 uploader instance from config.

    Args:
        config_path: Optional path to config file

    Returns:
        S3Uploader instance or None if not configured

    Config file format (JSON):
        {
            "s3_upload": {
                "enabled": true,
                "bucket_name": "frynetworks-measurements",
                "miner_id": "miner-001",
                "aws_region": "us-east-1",
                "aws_access_key_id": "AKIA...",
                "aws_secret_access_key": "...",
                "prefix": "original-data"
            }
        }

    Or use environment variables:
        S3_UPLOAD_ENABLED=true
        S3_BUCKET_NAME=frynetworks-measurements
        S3_MINER_ID=miner-001
        S3_AWS_REGION=us-east-1
        AWS_ACCESS_KEY_ID=AKIA...
        AWS_SECRET_ACCESS_KEY=...
        S3_PREFIX=original-data
    """
    if not S3_AVAILABLE:
        log.debug("boto3 not available, S3 upload disabled")
        return None

    # Try environment variables first
    enabled = os.getenv("S3_UPLOAD_ENABLED", "").lower() == "true"
    bucket_name = os.getenv("S3_BUCKET_NAME")
    miner_id = os.getenv("S3_MINER_ID")
    aws_region = os.getenv("S3_AWS_REGION", "us-east-1")
    aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    prefix = os.getenv("S3_PREFIX", "original-data")

    # Try config file if provided
    if config_path and config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                s3_config = config.get("s3_upload", {})
                enabled = s3_config.get("enabled", enabled)
                bucket_name = s3_config.get("bucket_name", bucket_name)
                miner_id = s3_config.get("miner_id", miner_id)
                aws_region = s3_config.get("aws_region", aws_region)
                aws_access_key_id = s3_config.get("aws_access_key_id", aws_access_key_id)
                aws_secret_access_key = s3_config.get("aws_secret_access_key", aws_secret_access_key)
                prefix = s3_config.get("prefix", prefix)
        except Exception as e:
            log.warning(f"Failed to load S3 config from {config_path}: {e}")

    # Check if S3 upload is enabled and configured
    if not enabled:
        log.debug("S3 upload not enabled")
        return None

    if not bucket_name or not miner_id:
        log.warning("S3 upload enabled but bucket_name or miner_id not configured")
        return None

    try:
        uploader = S3Uploader(
            bucket_name=bucket_name,
            miner_id=miner_id,
            aws_region=aws_region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            prefix=prefix
        )
        return uploader
    except Exception as e:
        log.error(f"Failed to initialize S3 uploader: {e}")
        return None
