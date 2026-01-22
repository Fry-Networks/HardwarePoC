#!/usr/bin/env python3
import requests
API_KEY='REDACTED_ROTATE_ME'
CID='bafkr6iarkhdsnkz4i4orzgstgln5ep4ab5wxs5jmybufybynvq7wfykly4'
url=f'https://mainnet.auto-drive.autonomys.xyz/api/objects/{CID}'
headers={'Authorization':f'Bearer {API_KEY}','X-Auth-Provider':'apikey','Accept':'application/json'}
r=requests.get(url,headers=headers,timeout=15)
print('GET',url,'->',r.status_code)
try:
    print(r.json())
except Exception:
    print(r.text[:1000])
