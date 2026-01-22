#!/usr/bin/env python3
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path so local packages can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from measurements.autonomys_uploader_rest import get_rest_uploader

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

uploader = get_rest_uploader()
if not uploader:
    print('No uploader (API key missing)')
    raise SystemExit(2)

print('Endpoint:', uploader.upload_url)

local = Path('test_upload.txt')
if not local.exists():
    print('test_upload.txt not found')
    raise SystemExit(3)

cid = uploader.upload_file(local_path=local, remote_filename='test_upload.txt')
print('Result CID:', cid)
