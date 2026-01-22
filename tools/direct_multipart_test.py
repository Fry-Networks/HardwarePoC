#!/usr/bin/env python3
import requests
from pathlib import Path

API_KEY='REDACTED_ROTATE_ME'
URL='https://mainnet.auto-drive.autonomys.xyz/api/uploads/file'
local=Path('test_upload.txt')
if not local.exists():
    print('test_upload.txt missing')
    raise SystemExit(2)

files={'file':('test_upload.txt', local.read_bytes(), 'text/plain')}
data={'filename':'test_upload.txt','mimeType':'text/plain'}
headers={'Authorization':f'Bearer {API_KEY}','X-Auth-Provider':'apikey'}

print('POSTing direct multipart with form fields filename & mimeType')
r=requests.post(URL,headers=headers,data=data,files=files,timeout=30)
print('Status',r.status_code)
print('Headers',r.headers)
print('Body:',r.text[:1000])
