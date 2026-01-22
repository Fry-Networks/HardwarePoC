#!/usr/bin/env python3
import requests
API_KEY='REDACTED_ROTATE_ME'
UPLOAD_ID='bd027ec7-1e72-4326-9eac-778d029b74cc'
BASE='https://mainnet.auto-drive.autonomys.xyz'
url=f"{BASE}/api/uploads/file/{UPLOAD_ID}/complete"
headers={'Authorization':f'Bearer {API_KEY}','X-Auth-Provider':'apikey','Accept':'application/json'}
print('OPTIONS',url)
try:
    r=requests.options(url,headers=headers,timeout=15)
    print('OPTIONS status',r.status_code)
    print('OPTIONS headers',r.headers)
    print('OPTIONS text',r.text[:400])
except Exception as e:
    print('OPTIONS error',e)
print('\nGET',url)
try:
    r=requests.get(url,headers=headers,timeout=15)
    print('GET status',r.status_code)
    print('GET headers',r.headers)
    print('GET text',r.text[:400])
except Exception as e:
    print('GET error',e)
