#!/usr/bin/env python3
"""
Test script to verify 1Password integration works.
"""

import subprocess
from typing import Optional

def get_1password_secret(reference: str) -> Optional[str]:
    """Retrieve a secret from 1Password using the CLI."""
    try:
        result = subprocess.run(
            ['op', 'read', reference],
            capture_output=True,
            text=True,
            timeout=10,
            check=True
        )
        value = result.stdout.strip()
        return value if value else None
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    except Exception:
        return None

def main():
    reference = "op://VPS/Hardware_API/API_BEARER_TOKEN"
    
    print(f"Testing 1Password integration...")
    print(f"Attempting to retrieve: {reference}")
    
    token = get_1password_secret(reference)
    
    if token:
        print(f"✅ SUCCESS: Retrieved token (length: {len(token)})")
        print(f"Token preview: {token[:10]}...")
        return True
    else:
        print(f"❌ FAILED: Could not retrieve token")
        print(f"Make sure:")
        print(f"1. 1Password CLI is installed: brew install 1password-cli")
        print(f"2. You are authenticated: op signin")
        print(f"3. The reference exists: op read \"{reference}\"")
        return False

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)