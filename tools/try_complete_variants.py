#!/usr/bin/env python3
import requests
API_KEY='REDACTED_ROTATE_ME'
UPLOAD_ID='bd027ec7-1e72-4326-9eac-778d029b74cc'
BASE='https://mainnet.auto-drive.autonomys.xyz'

candidates=[
    f"{BASE}/api/uploads/file/{UPLOAD_ID}/complete",
    f"{BASE}/api/uploads/{UPLOAD_ID}/complete",
    f"{BASE}/api/uploads/file/{UPLOAD_ID}/complete/",
    f"{BASE}/api/uploads/{UPLOAD_ID}/complete/",
    f"{BASE}/uploads/file/{UPLOAD_ID}/complete",
    f"{BASE}/uploads/{UPLOAD_ID}/complete",
]
headers={'Authorization':f'Bearer {API_KEY}','X-Auth-Provider':'apikey','Accept':'application/json'}

for url in candidates:
    try:
        r=requests.post(url,headers=headers,timeout=15)
        print('POST',url,'->',r.status_code)
        print(r.text[:400])
    except Exception as e:
        print('POST',url,'error',e)
    print('---')
