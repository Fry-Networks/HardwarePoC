#!/usr/bin/env python3
import os
from pathlib import Path
import sys
import logging

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from measurements.autonomys_uploader_rest import get_rest_uploader

logging.basicConfig(level=logging.INFO)

BASE = Path(r"C:/ProgramData/FryNetworks/miner-BM/measurements/hourly")

def find_latest(ext):
    files = list(BASE.glob(f"*{ext}"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)

def main():
    api_key = os.environ.get('AUTONOMYS_API_KEY')
    if not api_key:
        print('AUTONOMYS_API_KEY not set')
        return 2

    uploader = get_rest_uploader(api_key=api_key)
    if not uploader:
        print('Failed to create uploader')
        return 3

    parquet = find_latest('.parquet')
    meta = find_latest('.meta.json')

    if not parquet and not meta:
        print('No files found to upload in', BASE)
        return 4

    results = {}

    if parquet:
        print('Uploading parquet:', parquet)
        cid = uploader.upload_file(parquet, parquet.name)
        print('Parquet CID:', cid)
        results['parquet'] = cid

    if meta:
        print('Uploading meta:', meta)
        cid = uploader.upload_file(meta, meta.name)
        print('Meta CID:', cid)
        results['meta'] = cid

    # Print a simple JSON summary
    try:
        import json
        print('\nRESULT_JSON: ' + json.dumps(results))
    except Exception:
        pass

    return 0

if __name__ == '__main__':
    raise SystemExit(main())
