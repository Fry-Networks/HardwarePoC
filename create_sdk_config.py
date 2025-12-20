#!/usr/bin/env python3
"""
Create encrypted SDK configuration file.
This tool is used by the installer to create a secure, encrypted config file
containing SDK (Bright, Honeygain, Mysterium) approval settings for BM miners.

SDK approval determines which bandwidth-sharing tools are allowed to run,
affecting the rewards multiplier calculation.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

# Add tools directory to path
tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
if os.path.exists(tools_dir):
    sys.path.insert(0, tools_dir)

# Import encryption functions
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

# Valid SDK names for BM miners
VALID_SDK_NAMES = {'bright', 'honeygain', 'mysterium'}


def encrypt_sdk_config(config_data: dict) -> dict:
    """Encrypt SDK configuration data using Fernet."""
    # Use a fixed salt for SDK config (same approach as miner_config)
    salt = b'sdk_config_salt_v1'
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )

    # Derive key from a known string (obfuscation, not cryptographic security)
    password = "sdk_config_encryption_key_v1".encode()
    key = base64.urlsafe_b64encode(kdf.derive(password))

    # Encrypt
    f = Fernet(key)
    config_json = json.dumps(config_data)
    encrypted_data = f.encrypt(config_json.encode())

    return {
        "data": encrypted_data.decode(),
        "version": "1.0"
    }


def decrypt_sdk_config(encrypted_data: dict) -> dict:
    """Decrypt SDK configuration data."""
    # Use the same parameters as encryption
    salt = b'sdk_config_salt_v1'
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )

    password = "sdk_config_encryption_key_v1".encode()
    key = base64.urlsafe_b64encode(kdf.derive(password))

    # Decrypt
    f = Fernet(key)
    decrypted_data = f.decrypt(encrypted_data['data'].encode())
    config_data = json.loads(decrypted_data)

    return config_data


def validate_sdk_name(name: str) -> bool:
    """Validate SDK name."""
    return name.lower().strip() in VALID_SDK_NAMES


def create_sdk_config(
    approvals: Dict[str, bool],
    output_path: Optional[str] = None,
    installer_version: Optional[str] = None,
) -> str:
    """Create encrypted SDK configuration file.

    Args:
        approvals: Dict mapping SDK name to approval status (True/False)
        output_path: Output file path (default: sdk_config.enc)
        installer_version: Version of the installer that created this (optional)

    Returns:
        Path to created config file
    """

    # Validate and normalize approvals
    normalized_approvals = {}
    for name, approved in approvals.items():
        name_lower = name.lower().strip()
        if not validate_sdk_name(name_lower):
            raise ValueError(
                f"Invalid SDK name: {name}. Valid names are: {', '.join(sorted(VALID_SDK_NAMES))}"
            )
        normalized_approvals[name_lower] = bool(approved)

    # Create config data
    config_data = {
        "approvals": normalized_approvals,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_version": "1.0"
    }

    if installer_version:
        config_data["installer_version"] = installer_version

    # Determine output path
    if output_path is None:
        output_path = "sdk_config.enc"

    # Ensure output directory exists
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Encrypt the config
    encrypted_data = encrypt_sdk_config(config_data)

    # Save encrypted config
    with open(output_path, 'w') as f:
        json.dump(encrypted_data, f)

    return output_path


def read_sdk_config(config_path: str) -> dict:
    """Read and decrypt SDK configuration file."""
    try:
        with open(config_path, 'r') as f:
            encrypted_data = json.load(f)

        # Decrypt the config
        config_data = decrypt_sdk_config(encrypted_data)

        return config_data

    except FileNotFoundError:
        raise RuntimeError(f"SDK config file not found: {config_path}")
    except Exception as e:
        raise RuntimeError(f"Failed to read SDK config: {e}")


def update_sdk_approval(
    config_path: str,
    sdk_name: str,
    approved: bool,
    output_path: Optional[str] = None,
) -> str:
    """Update a single SDK approval in an existing config.

    Args:
        config_path: Path to existing encrypted config
        sdk_name: SDK name to update
        approved: New approval status
        output_path: Output path (overwrites input if not specified)

    Returns:
        Path to updated config file
    """
    # Read existing config
    config = read_sdk_config(config_path)

    # Get or create approvals dict
    approvals = config.get('approvals', {})
    if not isinstance(approvals, dict):
        approvals = {}

    # Validate SDK name
    name_lower = sdk_name.lower().strip()
    if not validate_sdk_name(name_lower):
        raise ValueError(
            f"Invalid SDK name: {sdk_name}. Valid names are: {', '.join(sorted(VALID_SDK_NAMES))}"
        )

    # Update approval
    approvals[name_lower] = bool(approved)

    # Write back
    return create_sdk_config(
        approvals=approvals,
        output_path=output_path or config_path,
        installer_version=config.get('installer_version'),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Manage encrypted SDK configuration for BM (Bandwidth Miner) deployments"
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Create command
    create_parser = subparsers.add_parser('create', help='Create encrypted SDK config')
    create_parser.add_argument(
        '--bright', type=str, choices=['true', 'false', 'yes', 'no', '1', '0'],
        help='Bright SDK approval (true/false)'
    )
    create_parser.add_argument(
        '--honeygain', type=str, choices=['true', 'false', 'yes', 'no', '1', '0'],
        help='Honeygain SDK approval (true/false)'
    )
    create_parser.add_argument(
        '--mysterium', type=str, choices=['true', 'false', 'yes', 'no', '1', '0'],
        help='Mysterium SDK approval (true/false)'
    )
    create_parser.add_argument('--output', '-o', help='Output file path (default: sdk_config.enc)')
    create_parser.add_argument('--installer-version', help='Installer version string')

    # Read command
    read_parser = subparsers.add_parser('read', help='Read encrypted SDK config')
    read_parser.add_argument('config_file', help='Path to encrypted config file')

    # Update command
    update_parser = subparsers.add_parser('update', help='Update SDK approval in existing config')
    update_parser.add_argument('config_file', help='Path to encrypted config file')
    update_parser.add_argument('sdk_name', choices=['bright', 'honeygain', 'mysterium'],
                               help='SDK name to update')
    update_parser.add_argument('approved', choices=['true', 'false', 'yes', 'no', '1', '0'],
                               help='New approval status')
    update_parser.add_argument('--output', '-o', help='Output file path (overwrites input if not specified)')

    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate SDK name')
    validate_parser.add_argument('sdk_name', help='SDK name to validate')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    def _parse_bool(value: Optional[str]) -> Optional[bool]:
        if value is None:
            return None
        v = value.lower().strip()
        if v in ('true', 'yes', '1'):
            return True
        if v in ('false', 'no', '0'):
            return False
        return None

    try:
        if args.command == 'create':
            approvals = {}
            if args.bright is not None:
                approvals['bright'] = _parse_bool(args.bright)
            if args.honeygain is not None:
                approvals['honeygain'] = _parse_bool(args.honeygain)
            if args.mysterium is not None:
                approvals['mysterium'] = _parse_bool(args.mysterium)

            if not approvals:
                print("❌ Error: At least one SDK approval must be specified")
                print("Use --bright, --honeygain, or --mysterium with true/false")
                sys.exit(1)

            output_path = create_sdk_config(
                approvals=approvals,
                output_path=args.output,
                installer_version=args.installer_version,
            )
            config = read_sdk_config(output_path)
            print(f"✅ Created encrypted SDK config: {output_path}")
            print(f"📋 SDK Approvals:")
            for sdk, approved in config.get('approvals', {}).items():
                status = "✓ Approved" if approved else "✗ Denied"
                print(f"   {sdk.capitalize()}: {status}")
            print(f"⏰ Created: {config['created_at']}")
            print(f"🔒 Config is encrypted and ready for deployment")

        elif args.command == 'read':
            config = read_sdk_config(args.config_file)
            print(f"📋 SDK Approvals:")
            approvals = config.get('approvals', {})
            for sdk in sorted(VALID_SDK_NAMES):
                approved = approvals.get(sdk)
                if approved is True:
                    status = "✓ Approved"
                elif approved is False:
                    status = "✗ Denied"
                else:
                    status = "? Not set"
                print(f"   {sdk.capitalize()}: {status}")
            print(f"⏰ Created: {config.get('created_at', 'Unknown')}")
            if config.get('installer_version'):
                print(f"📦 Installer Version: {config['installer_version']}")

        elif args.command == 'update':
            approved = _parse_bool(args.approved)
            if approved is None:
                print(f"❌ Invalid approval value: {args.approved}")
                sys.exit(1)

            output_path = update_sdk_approval(
                config_path=args.config_file,
                sdk_name=args.sdk_name,
                approved=approved,
                output_path=args.output,
            )
            print(f"✅ Updated {args.sdk_name} approval to: {'Approved' if approved else 'Denied'}")
            print(f"📄 Config file: {output_path}")

        elif args.command == 'validate':
            if validate_sdk_name(args.sdk_name):
                print(f"✅ Valid SDK name: {args.sdk_name}")
            else:
                print(f"❌ Invalid SDK name: {args.sdk_name}")
                print(f"Valid SDK names: {', '.join(sorted(VALID_SDK_NAMES))}")
                sys.exit(1)

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
