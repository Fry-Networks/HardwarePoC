"""Secrets manager for secure credential retrieval.

Integrates with 1Password CLI to retrieve API keys and other secrets.
Falls back to environment variables for development/testing.
"""

import logging
import os
import subprocess
import json
from typing import Optional

log = logging.getLogger("measurements.secrets_manager")


def get_from_1password(item_name: str, field_name: str = "credential") -> Optional[str]:
    """Retrieve a secret from 1Password using the CLI.

    Requires 1Password CLI (op) to be installed and authenticated.
    Install: https://developer.1password.com/docs/cli/get-started/

    Args:
        item_name: Name of the 1Password item (e.g., "Autonomys API Key")
        field_name: Field name within the item (default: "credential")

    Returns:
        Secret value or None if not found/error
    """
    try:
        # Try to get secret using 1Password CLI
        # Format: op item get "Item Name" --field "field_name" --reveal
        # The --reveal flag is needed to get the actual secret value
        result = subprocess.run(
            ["op", "item", "get", item_name, "--field", field_name, "--reveal"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False
        )

        if result.returncode == 0:
            secret = result.stdout.strip()
            if secret:
                log.debug("Retrieved secret '%s' from 1Password", item_name)
                return secret
            else:
                log.warning("1Password returned empty value for '%s'", item_name)
                return None
        else:
            error_msg = result.stderr.strip()
            if "not currently signed in" in error_msg.lower():
                log.warning("1Password CLI not signed in. Run: op signin")
            elif "not found" in error_msg.lower():
                log.warning("1Password item '%s' not found", item_name)
            else:
                log.warning("1Password CLI error: %s", error_msg)
            return None

    except FileNotFoundError:
        log.debug("1Password CLI (op) not found. Install from: https://developer.1password.com/docs/cli/get-started/")
        return None
    except subprocess.TimeoutExpired:
        log.error("1Password CLI timeout")
        return None
    except Exception as e:
        log.error("Failed to retrieve from 1Password: %s", e)
        return None


def get_autonomys_api_key() -> Optional[str]:
    """Get Autonomys API key from embedded config, 1Password, or environment variable.

    Lookup order:
    1. Embedded build-time credentials (from tool_credentials in config)
    2. 1Password CLI direct read (op://Hardware/Autonomys/api_key)
    3. 1Password item "Autonomys API Key" (field: "credential")
    4. Environment variable AUTONOMYS_API_KEY

    Returns:
        API key or None if not found
    """
    # Try embedded credentials first (from build_PoC_windows.ps1)
    try:
        # Import from parent directory to access miner_online_simple
        import sys
        from pathlib import Path
        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        # Try to import and use _get_tool_credentials from miner_online_simple
        from miner_online_simple import _get_tool_credentials
        tool_creds = _get_tool_credentials()
        api_key = tool_creds.get('autonomys_api_key')
        if api_key:
            log.info("Using Autonomys API key from embedded build credentials")
            return api_key
    except ImportError:
        log.debug("miner_online_simple not available (likely dev environment)")
    except Exception as e:
        log.debug("Could not retrieve embedded Autonomys key: %s", e)

    # Try 1Password item "AutoDrive" (production setup)
    api_key = get_from_1password("AutoDrive", "AUTONOMYS_API_KEY")
    if api_key:
        log.info("Using Autonomys API key from 1Password (AutoDrive)")
        return api_key

    # Try 1Password CLI direct read (alternative path)
    api_key = get_from_1password("Hardware/Autonomys", "api_key")
    if api_key:
        log.info("Using Autonomys API key from 1Password CLI (op://Hardware/Autonomys/api_key)")
        return api_key

    # Try 1Password item "Autonomys API Key" (manual setup)
    api_key = get_from_1password("Autonomys API Key", "credential")
    if api_key:
        log.info("Using Autonomys API key from 1Password item")
        return api_key

    # Fall back to environment variable (development/testing)
    api_key = os.environ.get("AUTONOMYS_API_KEY")
    if api_key:
        log.info("Using Autonomys API key from environment variable")
        return api_key

    log.error("Autonomys API key not found in embedded config, 1Password, or environment")
    log.error("For production builds: Store in 1Password at op://Hardware/Autonomys/api_key")
    log.error("For dev/test: set AUTONOMYS_API_KEY=<your-key>")
    return None


def get_mongodb_credentials() -> Optional[dict]:
    """Get MongoDB credentials from 1Password or environment variables.

    Returns:
        Dict with connection details or None if not found
    """
    # Try 1Password first
    try:
        username = get_from_1password("MongoDB Credentials", "username")
        password = get_from_1password("MongoDB Credentials", "password")
        host = get_from_1password("MongoDB Credentials", "server")

        if username and password:
            log.info("Using MongoDB credentials from 1Password")
            return {
                "username": username,
                "password": password,
                "host": host or "localhost"
            }
    except Exception as e:
        log.debug("Could not retrieve MongoDB credentials from 1Password: %s", e)

    # Fall back to environment variables
    username = os.environ.get("MONGODB_USERNAME")
    password = os.environ.get("MONGODB_PASSWORD")
    host = os.environ.get("MONGODB_HOST", "localhost")

    if username and password:
        log.info("Using MongoDB credentials from environment variables")
        return {
            "username": username,
            "password": password,
            "host": host
        }

    return None


def test_1password_connection() -> bool:
    """Test if 1Password CLI is available and authenticated.

    Returns:
        True if 1Password CLI is working
    """
    try:
        result = subprocess.run(
            ["op", "account", "list"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False
        )

        if result.returncode == 0:
            log.info("1Password CLI is available and authenticated")
            return True
        else:
            log.warning("1Password CLI is not authenticated. Run: op signin")
            return False

    except FileNotFoundError:
        log.warning("1Password CLI not found. Install from: https://developer.1password.com/docs/cli/get-started/")
        return False
    except Exception as e:
        log.error("Failed to test 1Password connection: %s", e)
        return False


def create_autonomys_api_key_item(api_key: str) -> bool:
    """Create a new 1Password item for Autonomys API key.

    Args:
        api_key: The API key to store

    Returns:
        True if successful
    """
    try:
        result = subprocess.run(
            [
                "op", "item", "create",
                "--category=password",
                "--title=Autonomys API Key",
                f"credential={api_key}",
                "--tags=frynetworks,autonomys,api"
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False
        )

        if result.returncode == 0:
            log.info("Created Autonomys API Key item in 1Password")
            return True
        else:
            log.error("Failed to create 1Password item: %s", result.stderr)
            return False

    except Exception as e:
        log.error("Failed to create 1Password item: %s", e)
        return False


if __name__ == "__main__":
    """Test the secrets manager."""
    logging.basicConfig(level=logging.INFO)

    print("Testing 1Password integration...")
    print()

    # Test 1Password connection
    if test_1password_connection():
        print("✓ 1Password CLI is working")
    else:
        print("✗ 1Password CLI not available")
    print()

    # Test Autonomys API key retrieval
    api_key = get_autonomys_api_key()
    if api_key:
        print(f"✓ Autonomys API key found: {api_key[:8]}...{api_key[-4:]}")
    else:
        print("✗ Autonomys API key not found")
        print()
        print("To set up:")
        print('  1. Get your API key from https://ai3.storage')
        print('  2. Run: op item create --category=password --title="Autonomys API Key" credential=<your-key>')
        print('  Or set environment: set AUTONOMYS_API_KEY=<your-key>')
