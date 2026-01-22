"""Example: Upload measurements to S3.

This demonstrates how to use the S3 uploader to backup original measurements.
"""

from pathlib import Path
from measurements.s3_uploader import S3Uploader, get_s3_uploader

def example_manual_upload():
    """Example 1: Manual upload with explicit credentials."""
    print("=" * 60)
    print("Example 1: Manual S3 Upload")
    print("=" * 60)

    # Create uploader with explicit configuration
    uploader = S3Uploader(
        bucket_name="frynetworks-measurements",
        miner_id="miner-001",
        aws_region="us-east-1",
        aws_access_key_id="AKIA...",  # Your access key
        aws_secret_access_key="...",  # Your secret key
        prefix="original-data"
    )

    # Upload a single file
    local_file = Path(r"C:\ProgramData\FryNetworks\measurements\decibel\hourly\2026-01-22.parquet")

    if local_file.exists():
        success = uploader.upload_file(
            local_path=local_file,
            measurement_type="decibel",
            date_str="2026-01-22",
            metadata={
                "miner_code": "IDM",
                "hex_id": "871f90151ffffff"
            }
        )

        if success:
            print("✓ File uploaded successfully")
        else:
            print("✗ Upload failed")
    else:
        print(f"File not found: {local_file}")


def example_config_based_upload():
    """Example 2: Upload using config file or environment variables."""
    print("\n" + "=" * 60)
    print("Example 2: Config-based Upload")
    print("=" * 60)

    # Get uploader from config (reads s3_config.json or env vars)
    uploader = get_s3_uploader(config_path=Path("s3_config.json"))

    if not uploader:
        print("S3 upload not configured. Check s3_config.json or environment variables.")
        return

    # Upload a directory
    measurements_dir = Path(r"C:\ProgramData\FryNetworks\measurements\decibel\hourly")

    if measurements_dir.exists():
        results = uploader.upload_directory(
            local_dir=measurements_dir,
            measurement_type="decibel"
        )

        print(f"\nUploaded {sum(results.values())}/{len(results)} files:")
        for filename, success in results.items():
            status = "✓" if success else "✗"
            print(f"  {status} {filename}")
    else:
        print(f"Directory not found: {measurements_dir}")


def example_check_uploads():
    """Example 3: Check what's already uploaded."""
    print("\n" + "=" * 60)
    print("Example 3: Check Uploaded Files")
    print("=" * 60)

    uploader = get_s3_uploader()

    if not uploader:
        print("S3 upload not configured")
        return

    # List uploaded files
    measurement_types = ["decibel", "bandwidth", "radiation"]

    for mtype in measurement_types:
        dates = uploader.list_uploaded_files(mtype)
        if dates:
            print(f"\n{mtype.upper()}:")
            print(f"  First: {dates[0]}")
            print(f"  Last:  {dates[-1]}")
            print(f"  Total: {len(dates)} days")
        else:
            print(f"\n{mtype.upper()}: No files uploaded yet")

    # Check specific file
    print("\n" + "-" * 60)
    exists = uploader.file_exists("decibel", "2026-01-22")
    print(f"decibel/2026-01-22.parquet exists: {exists}")


def example_update_metadata():
    """Example 4: Update miner metadata."""
    print("\n" + "=" * 60)
    print("Example 4: Update Miner Metadata")
    print("=" * 60)

    uploader = get_s3_uploader()

    if not uploader:
        print("S3 upload not configured")
        return

    metadata = {
        "miner_id": uploader.miner_id,
        "miner_code": "IDM",
        "hex_id": "871f90151ffffff",
        "location": {
            "lat": 40.712776,
            "lon": -74.005974,
            "resolution": 7
        },
        "measurement_types": ["decibel"],
        "notes": "Indoor decibel miner - Manhattan"
    }

    success = uploader.upload_miner_metadata(metadata)

    if success:
        print("✓ Metadata uploaded successfully")
        print(f"  Location: s3://{uploader.bucket_name}/{uploader.prefix}/{uploader.miner_id}/metadata.json")
    else:
        print("✗ Metadata upload failed")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("S3 Upload Examples")
    print("=" * 60)
    print("\nThese examples demonstrate how to upload original measurement")
    print("data to S3 for centralized API access.")
    print("\nNote: Configure S3 credentials before running (see s3_config.json)")
    print("=" * 60)

    # Run examples
    try:
        # Example 1: Manual upload (commented out - requires credentials)
        # example_manual_upload()

        # Example 2: Config-based upload
        example_config_based_upload()

        # Example 3: Check uploads
        example_check_uploads()

        # Example 4: Update metadata
        example_update_metadata()

    except Exception as e:
        print(f"\nError: {e}")
        print("\nMake sure:")
        print("  1. boto3 is installed: pip install boto3")
        print("  2. S3 credentials are configured (s3_config.json or env vars)")
        print("  3. S3 bucket exists and is accessible")
