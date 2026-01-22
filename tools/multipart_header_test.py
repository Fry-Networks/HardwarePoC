#!/usr/bin/env python3
import logging
import sys
from pathlib import Path
import requests

# Ensure project root is on sys.path so local packages can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from measurements.autonomys_uploader_rest import get_rest_uploader, AutonomysRestUploader

logging.basicConfig(level=logging.DEBUG)

# If you want to paste your API key directly into this script, set API_KEY_LOCAL to the key string below.
# Example: API_KEY_LOCAL = "REDACTED_ROTATE_ME"
API_KEY_LOCAL = "REDACTED_ROTATE_ME"

if API_KEY_LOCAL:
    print('Using local API key from API_KEY_LOCAL')
    uploader = AutonomysRestUploader(api_key=API_KEY_LOCAL)
else:
    uploader = get_rest_uploader()
    if not uploader:
        print('No uploader (API key not found)')
        raise SystemExit(2)

url = uploader.upload_url
api_key = uploader.api_key

local = Path('test_upload.txt')
if not local.exists():
    print('test_upload.txt not found')
    raise SystemExit(3)

file_bytes = local.read_bytes()

def try_post(add_accept=False):
    headers = {
        'Authorization': f'Bearer {api_key}',
        'X-Auth-Provider': 'apikey'
    }
    if add_accept:
        headers['Accept'] = 'application/json'
    # Try two approaches: (A) simple filename+mimetype parts, (B) metadata JSON part.
    # First attempt uses metadata JSON part which some endpoints expect when sending multipart.
    import json as _json
    metadata_json = _json.dumps({'filename': 'test_upload.txt', 'mimeType': 'text/plain'})
    files = {
        'metadata': (None, metadata_json, 'application/json'),
        'file': ('test_upload.txt', file_bytes, 'text/plain')
    }
    r = requests.post(url, headers=headers, files=files, timeout=30)
    print('\n--- add_accept=%s ---' % add_accept)
    print('Status:', r.status_code)
    print('Headers:', r.headers)
    # print small snippet of body
    text = r.text
    print('Body (first 800 chars):')
    print(text[:800])


try_post(add_accept=False)
try_post(add_accept=True)
