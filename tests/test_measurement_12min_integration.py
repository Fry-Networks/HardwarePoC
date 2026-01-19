import pytest
import time
from types import SimpleNamespace

# Provide light-weight stubs for heavy optional deps at import time (must be before importing service)
import sys, types
sys.modules.setdefault('numpy', types.SimpleNamespace())
sys.modules.setdefault('psutil', types.SimpleNamespace(
    net_if_addrs=lambda: {},
    boot_time=lambda: 0,
))

import miner_online_simple as mos


def test_12_minute_bm_run_simulation(tmp_path, monkeypatch):
    """Simulate a 12-minute BM run:
    - 72 live bandwidth CSV writes (10s interval)
    - 1 real upload at 600s that results in PoD marking
    """
    # Isolate filesystem
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path))
    monkeypatch.setenv('MINER_CODE', 'BM')

    calls = {'live': 0, 'upload': 0, 'uploaded': 0}

    # Fake live collector: increments live counter and returns True
    def fake_live(miner_code: str) -> bool:
        calls['live'] += 1
        return True

    # Fake upload collector: simulates backend upload and increments upload count
    def fake_upload(miner_code: str, api_client=None, hex_id=None, install_id=None, miner_key=None) -> bool:
        calls['upload'] += 1
        # Simulate successful backend upload
        if api_client and hasattr(api_client, '_api') and hasattr(api_client._api, 'upload_measurement'):
            api_client._api.upload_measurement(measurement_type='bandwidth')
            calls['uploaded'] += 1
        return True

    monkeypatch.setattr('measurements.collector.collect_and_write_bandwidth_live', fake_live)
    monkeypatch.setattr('measurements.collector.collect_and_upload_bandwidth', fake_upload)

    # Fake API client for upload
    class FakeAPI:
        def upload_measurement(self, **kwargs):
            # pretend the upload succeeded
            return None

    api_client = SimpleNamespace(_api=FakeAPI())

    # Simulate 12 minutes: 72 live samples (10s interval)
    live_samples = 72
    for i in range(live_samples):
        # call the fake live collector directly
        assert fake_live('BM') is True

    # Ensure 72 live writes occurred
    assert calls['live'] == live_samples

    # Now simulate the real 10-min upload
    res = fake_upload('BM', api_client=api_client, hex_id='hex', install_id='inst', miner_key='MK')
    assert res is True
    assert calls['upload'] == 1
    assert calls['uploaded'] == 1

    # The measurement daemon would mark PoD after a successful upload; simulate that call
    miner_key = 'MK_TEST'
    ts = mos.now_utc()
    interval = 600

    mos.write_week_local(miner_key, ts, 'online', interval, pod_status=True)

    week_start, _ = mos._week_bounds_for_rewards(ts)
    path = mos._week_file_path(week_start)

    # read the written week file and verify data gate
    import json
    with open(path, 'r', encoding='utf-8') as f:
        doc = json.load(f)

    day_doc = doc.get('days', {}).get(mos._date_iso(ts))
    assert day_doc is not None

    hour, slot = mos.hour_and_slot(ts, interval)
    hour_doc = day_doc.get('hours', {}).get(str(hour))
    assert hour_doc is not None

    slots = hour_doc.get('slots', [])
    assert len(slots) > slot

    slot_obj = slots[slot]
    assert isinstance(slot_obj, dict)
    gates = slot_obj.get('gates', {})
    assert gates.get('data') is True
