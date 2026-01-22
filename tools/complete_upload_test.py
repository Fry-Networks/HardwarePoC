#!/usr/bin/env python3
import sys
from pathlib import Path
import requests

# Paste the upload id returned earlier and the API key
UPLOAD_ID = "bd027ec7-1e72-4326-9eac-778d029b74cc"
API_KEY = "REDACTED_ROTATE_ME"
BASE = "https://mainnet.auto-drive.autonomys.xyz"

url = f"{BASE}/api/uploads/file/{UPLOAD_ID}/complete"
headers = {
    'Authorization': f'Bearer {API_KEY}',
    'X-Auth-Provider': 'apikey',
    'Accept': 'application/json'
}

print('POSTing complete to', url)

r = requests.post(url, headers=headers, timeout=30)
print('Status:', r.status_code)
print('Response headers:', r.headers)
try:
    print('Response JSON:', r.json())
except Exception:
    print('Response text:', r.text[:2000])
