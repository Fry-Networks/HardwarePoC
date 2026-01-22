#!/usr/bin/env python3
import json
import sys
from pathlib import Path
import requests

# Ensure project root imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from measurements.autonomys_uploader_rest import get_rest_uploader, AutonomysRestUploader

# Paste API key here to bypass 1Password, or leave None to use existing secrets behavior
API_KEY_LOCAL = "REDACTED_ROTATE_ME"

if API_KEY_LOCAL:
    uploader = AutonomysRestUploader(api_key=API_KEY_LOCAL)
else:
    uploader = get_rest_uploader()
    if not uploader:
        print('No uploader (API key not found)')
        raise SystemExit(2)

url = uploader.upload_url
api_key = uploader.api_key

payload = {
    "filename": "test_upload.txt",
    "mimeType": "text/plain",
    "uploadOptions": {
        "compression": {"algorithm": "ZLIB", "level": 8},
        "encryption": {"algorithm": "AES_256_GCM", "chunkSize": 1024}
    }
}

headers = {
    'Authorization': f'Bearer {api_key}',
    'X-Auth-Provider': 'apikey',
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

print('POSTing JSON create-session to', url)
print('Request payload:', json.dumps(payload))

r = requests.post(url, headers=headers, json=payload, timeout=30)
print('\nStatus:', r.status_code)
print('Response headers:', r.headers)
try:
    print('Response JSON:', json.dumps(r.json(), indent=2))
except Exception:
    print('Response text:', r.text[:1000])
