import os
import requests
import json

def main():
    api_key = os.environ.get('AUTONOMYS_API_KEY')
    if not api_key:
        print('AUTONOMYS_API_KEY not set')
        return

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'X-Auth-Provider': 'apikey'
    }

    url = 'https://mainnet.auto-drive.autonomys.xyz/api/uploads/file'
    payload = {'filename': 'probe.txt', 'mimeType': 'text/plain'}

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    print(r.status_code)
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(r.text)

if __name__ == '__main__':
    main()
