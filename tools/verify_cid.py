import os
import sys
import requests
import json

def main():
    if len(sys.argv) < 2:
        print('Usage: verify_cid.py <cid>')
        return
    cid = sys.argv[1]
    api_key = os.environ.get('AUTONOMYS_API_KEY')
    if not api_key:
        print('AUTONOMYS_API_KEY not set')
        return

    headers = {
        'Authorization': f'Bearer {api_key}',
        'X-Auth-Provider': 'apikey',
        'Accept': 'application/json'
    }

    url = f'https://mainnet.auto-drive.autonomys.xyz/api/objects/{cid}'
    r = requests.get(url, headers=headers, timeout=30)
    print(r.status_code)
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(r.text)

if __name__ == '__main__':
    main()
