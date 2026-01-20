import tempfile
import shutil
import os
import datetime as dt
from measurements import csv_writer as cw
from measurements import collector as coll

TMP = tempfile.mkdtemp()
try:
    os.environ['PROGRAMDATA'] = TMP
    miner='BM'
    now = dt.datetime.datetime.now()
    row={'timestamp': now.isoformat(), 'dl':1.23, 'ul':0.45, 'iface':'eth0'}
    assert cw.append_row('bandwidth', miner, row, dataset='live')
    today = now.strftime('%Y%m%d')
    live_path = os.path.join(TMP, 'FryNetworks', f'miner-{miner}', 'measurements', f'bandwidth_live_{today}.csv')
    assert os.path.exists(live_path)
    assert cw.append_row('bandwidth', miner, row, dataset='real')
    real_path = os.path.join(TMP, 'FryNetworks', f'miner-{miner}', 'measurements', f'bandwidth_real_{today}.csv')
    assert os.path.exists(real_path)
    # collector behavior
    coll._UPLOAD_LAST_TS.clear()
    called=[]
    def fake_append_row(sensor_type, miner_code, row, *, dataset='live'):
        called.append((sensor_type, miner_code, row, dataset))
        return True
    coll.append_row = fake_append_row
    coll.collect_bandwidth_measurement = lambda: {'dl':10.0,'ul':5.0,'iface':'eth0'}
    ok = coll.collect_and_upload_bandwidth('BM', api_client=None, hex_id=None, install_id=None)
    assert ok is True
    assert len(called)==1 and called[0][3]=='real'
    print('SMOKE OK')
finally:
    shutil.rmtree(TMP)
