#!/usr/bin/env python3
import sys
from pathlib import Path
import requests

API_KEY='REDACTED_ROTATE_ME'
URL='https://mainnet.auto-drive.autonomys.xyz/api/uploads/file'
local=Path('test_upload.txt')
if not local.exists():
    print('test_upload.txt missing')
    raise SystemExit(2)

file_bytes = local.read_bytes()

# Variants to inspect
variants = []
# variant 1: data fields + files
variants.append(('data+files', {'filename':'test_upload.txt','mimeType':'text/plain'}, {'file':('test_upload.txt', file_bytes, 'text/plain')}))
# variant 2: files param for filename (None -> form field)
variants.append(('files-filename', None, {'filename':(None,'test_upload.txt'), 'mimeType':(None,'text/plain'), 'file':('test_upload.txt', file_bytes, 'text/plain')}))
# variant 3: metadata json part
import json as _json
variants.append(('metadata-json', None, {'metadata':(None,_json.dumps({'filename':'test_upload.txt','mimeType':'text/plain'}),'application/json'), 'file':('test_upload.txt', file_bytes, 'text/plain')}))

s = requests.Session()
for name, data, files in variants:
    req = requests.Request('POST', URL, headers={'Authorization':f'Bearer {API_KEY}','X-Auth-Provider':'apikey'}, data=data, files=files)
    prepped = s.prepare_request(req)
    print('\n=== VARIANT:', name, '===')
    # print headers
    for k,v in prepped.headers.items():
        if k.lower()=='authorization':
            print(k+':','Bearer ***')
        else:
            print(k+':',v)
    print('\n--- body (first 2000 chars) ---')
    body = prepped.body
    try:
        if isinstance(body, bytes):
            print(body.decode('utf-8', errors='replace')[:2000])
        else:
            print(str(body)[:2000])
    except Exception as e:
        print('error reading body:', e)

print('\nDone')
