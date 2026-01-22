#!/usr/bin/env python3
"""Test Autonomys API authentication."""

import sys
import os

# Fix Windows console encoding
if sys.platform == "win32":
    os.system('chcp 65001 > nul 2>&1')

import requests

# Get API key from environment variable
api_key = os.environ.get("AUTONOMYS_API_KEY")
if not api_key:
    print("AUTONOMYS_API_KEY environment variable not set")
    print("Set it with: set AUTONOMYS_API_KEY=your-api-key")
    sys.exit(1)

print(f"API key retrieved (length: {len(api_key)})")

url = "https://mainnet.auto-drive.autonomys.xyz/api/accounts/@me"

# X-Auth-Provider should be literal "apikey" per original curl example
headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {api_key}",
    "X-Auth-Provider": "apikey"
}

print(f"\nTesting: GET {url}")
print(f"Headers: Authorization=Bearer ***, X-Auth-Provider=apikey")

response = requests.get(url, headers=headers)

print(f"\nStatus: {response.status_code}")
print(f"Response: {response.text[:500]}")
