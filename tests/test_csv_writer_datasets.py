import os
import datetime as dt
from measurements import csv_writer as cw
from measurements import collector as coll


def test_append_row_live_and_real(tmp_path):
    # Use tmp dir as ProgramData to avoid writing to real system dirs
    os.environ['PROGRAMDATA'] = str(tmp_path)
    miner = 'BM'
    now = dt.datetime.now()
    row = {'timestamp': now.isoformat(), 'dl': 1.23, 'ul': 0.45, 'iface': 'eth0'}

    # Live
    ok_live = cw.append_row('bandwidth', miner, row, dataset='live')
    assert ok_live
    today = now.strftime('%Y%m%d')
    live_path = tmp_path / 'FryNetworks' / f'miner-{miner}' / 'measurements' / f'bandwidth_live_{today}.csv'
    assert live_path.exists()

    # Real
    ok_real = cw.append_row('bandwidth', miner, row, dataset='real')
    assert ok_real
    real_path = tmp_path / 'FryNetworks' / f'miner-{miner}' / 'measurements' / f'bandwidth_real_{today}.csv'
    assert real_path.exists()

    # Both
    os.remove(live_path)
    os.remove(real_path)
    ok_both = cw.append_row('bandwidth', miner, row, dataset='both')
    assert ok_both
    assert (tmp_path / 'FryNetworks' / f'miner-{miner}' / 'measurements' / f'bandwidth_live_{today}.csv').exists()
    assert (tmp_path / 'FryNetworks' / f'miner-{miner}' / 'measurements' / f'bandwidth_real_{today}.csv').exists()


def test_collect_and_upload_bandwidth_writes_real(monkeypatch):
    # Ensure no rate-limit blocking
    coll._UPLOAD_LAST_TS.clear()

    calls = []

    def fake_append_row(sensor_type, miner_code, row, *, dataset='live'):
        calls.append((sensor_type, miner_code, row, dataset))
        return True

    monkeypatch.setattr(coll, 'append_row', fake_append_row)

    # Monkeypatch measurement to return deterministic payload
    monkeypatch.setattr(coll, 'collect_bandwidth_measurement', lambda: {'dl': 10.0, 'ul': 5.0, 'iface': 'eth0'})

    ok = coll.collect_and_upload_bandwidth('BM', api_client=None, hex_id=None, install_id=None)
    assert ok is True
    # We expect one append_row call and dataset should be 'real'
    assert len(calls) == 1
    assert calls[0][0] == 'bandwidth'
    assert calls[0][3] == 'real'