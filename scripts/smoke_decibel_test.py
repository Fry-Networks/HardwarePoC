import sys
import tempfile
import os
import shutil
import datetime as dt
# Ensure repo root is on sys.path for local imports
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
from measurements import collector as coll

TMP = tempfile.mkdtemp()
print('TMP:', TMP)
os.environ['PROGRAMDATA'] = TMP
miner = 'IDM'
# Ensure no pre-existing files
meas = os.path.join(TMP, 'FryNetworks', f'miner-{miner}', 'measurements')
shutil.rmtree(meas, ignore_errors=True)

# Call live writer
ok1 = coll.collect_and_write_decibel_live(miner)
print('collect_and_write_decibel_live returned', ok1)
# Call upload (real) writer without API
coll._UPLOAD_LAST_TS.clear()
ok2 = coll.collect_and_upload_decibel(miner, api_client=None, hex_id=None, install_id=None)
print('collect_and_upload_decibel returned', ok2)

# List files created
for root, dirs, files in os.walk(os.path.join(TMP, 'FryNetworks')):
    for f in files:
        print('file:', os.path.join(root, f))

shutil.rmtree(TMP)
