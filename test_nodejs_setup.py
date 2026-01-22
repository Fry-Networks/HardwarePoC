#!/usr/bin/env python3
"""Test Node.js setup for Autonomys native uploader."""

import subprocess
import sys
import os

# Fix encoding for Windows console
if sys.platform == "win32":
    os.system('chcp 65001 > nul 2>&1')

print("=" * 70)
print("Node.js Setup Check for Autonomys Native Uploader")
print("=" * 70)
print()

# Check 1: Node.js
print("1. Checking Node.js...")
try:
    result = subprocess.run(
        ["node", "--version"],
        capture_output=True,
        text=True,
        timeout=5
    )
    if result.returncode == 0:
        version = result.stdout.strip()
        print(f"   [OK] Node.js found: {version}")
    else:
        print("   [FAIL] Node.js not working properly")
        sys.exit(1)
except FileNotFoundError:
    print("   [FAIL] Node.js not found")
    print("   Install from: https://nodejs.org/")
    print("   Recommended: Node.js v18 or higher")
    sys.exit(1)

# Check 2: npm
print("2. Checking npm...")
try:
    result = subprocess.run(
        ["npm", "--version"],
        capture_output=True,
        text=True,
        timeout=5
    )
    if result.returncode == 0:
        version = result.stdout.strip()
        print(f"   [OK] npm found: {version}")
    else:
        print("   [FAIL] npm not working properly")
        sys.exit(1)
except FileNotFoundError:
    print("   [FAIL] npm not found (should come with Node.js)")
    sys.exit(1)

# Check 3: Autonomys SDK
print("3. Checking @autonomys/auto-drive SDK...")
try:
    result = subprocess.run(
        ["npm", "list", "@autonomys/auto-drive", "--depth=0"],
        capture_output=True,
        text=True,
        timeout=10
    )
    if result.returncode == 0:
        print("   [OK] SDK already installed")
    else:
        print("   [INFO] SDK not installed yet")
        print("   Installing now...")

        # Install the SDK
        install_result = subprocess.run(
            ["npm", "install", "@autonomys/auto-drive", "@autonomys/auto-utils"],
            capture_output=True,
            text=True,
            timeout=120
        )

        if install_result.returncode == 0:
            print("   [OK] SDK installed successfully")
        else:
            print("   [FAIL] SDK installation failed")
            print("   Error:", install_result.stderr)
            sys.exit(1)
except Exception as e:
    print(f"   [FAIL] Error checking SDK: {e}")
    sys.exit(1)

# Check 4: Upload script
print("4. Checking upload script...")
from pathlib import Path
script_path = Path("autonomys_upload_folder.js")
if script_path.exists():
    print(f"   [OK] Upload script found: {script_path}")
else:
    print(f"   [FAIL] Upload script not found: {script_path}")
    print("   This should have been created by Claude")
    sys.exit(1)

print()
print("=" * 70)
print("[SUCCESS] ALL CHECKS PASSED")
print("=" * 70)
print()
print("Your system is ready to use the Autonomys native uploader!")
print()
print("Next steps:")
print("  1. Ensure your API key is in 1Password (AutoDrive item)")
print("  2. Run: python test_autonomys_upload_native.py")
print()
