#!/usr/bin/env python3
import sys
from pathlib import Path
import requests

# Paste the upload id you received from the create-session response
UPLOAD_ID = "bd027ec7-1e72-4326-9eac-778d029b74cc"
API_KEY = "REDACTED_ROTATE_ME"
BASE = "https://mainnet.auto-drive.autonomys.xyz"

local = Path('test_upload.txt')
if not local.exists():
    print('test_upload.txt not found')
    raise SystemExit(2)

b = local.read_bytes()
size = len(b)

url = f"{BASE}/api/uploads/file/{UPLOAD_ID}/chunk"
headers = {
    'Authorization': f'Bearer {API_KEY}',
    'X-Auth-Provider': 'apikey',
    'Accept': 'application/json',
    # Keep Content-Range for server to know chunk offsets
    'Content-Range': f'bytes 0-{size-1}/{size}'
}

print('POSTing chunk (multipart) to', url)

# Send chunk as multipart/form-data field named 'file'
files = {'file': ('test_upload.txt', b, 'application/octet-stream')}
data = {'index': 0}

r = requests.post(url, headers=headers, files=files, data=data, timeout=30)
print('Status:', r.status_code)
print('Response headers:', r.headers)
try:
    print('Response JSON:', r.json())
except Exception:
    print('Response text:', r.text[:1000])
