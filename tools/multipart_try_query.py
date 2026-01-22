#!/usr/bin/env python3
import requests
from pathlib import Path
API_KEY='REDACTED_ROTATE_ME'
BASE='https://mainnet.auto-drive.autonomys.xyz'
url=f"{BASE}/api/uploads/file?filename=test_upload.txt&mimeType=text/plain"
local=Path('test_upload.txt')
files={'file':('test_upload.txt', local.read_bytes(), 'text/plain')}
headers={'Authorization':f'Bearer {API_KEY}','X-Auth-Provider':'apikey','Accept':'application/json'}
print('POST',url)
r=requests.post(url,headers=headers,files=files,timeout=30)
print(r.status_code)
print(r.text)
