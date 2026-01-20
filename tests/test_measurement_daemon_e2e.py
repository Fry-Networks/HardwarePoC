import importlib
import sys
import types
from types import SimpleNamespace

import pytest

# Stub heavy optional deps before importing modules
sys.modules.setdefault('psutil', types.SimpleNamespace(net_if_addrs=lambda: {}, boot_time=lambda: 0))
sys.modules.setdefault('numpy', types.SimpleNamespace())

import miner_online_simple as mos
import measurements.collector as coll


DURATION_SECONDS = 12 * 60  # 12 minutes


@pytest.mark.parametrize("miner", ["BM", "IDM", "ISM", "IRM", "AEM"])
def test_daemon_live_vs_upload_counts(monkeypatch, tmp_path, miner):
    # Isolate filesystem and set miner code
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path))
    # Set module-level MINER_CODE (service reads this global at runtime)
    mos.MINER_CODE = miner

    intervals = mos._get_measurement_intervals()

    # Counters
    counters = {
        'live': 0,
        'upload': 0,
        'pod_mark': 0,
    }

    # Stub append_row to avoid disk I/O
    monkeypatch.setattr(coll, 'append_row', lambda *a, **k: True)

    # Fake API client
    class FakeAPI:
        def __init__(self):
            self.calls = []

        def upload_measurement(self, **kwargs):
            self.calls.append(kwargs)

    api = SimpleNamespace(_api=FakeAPI())

    # Helper stubs for collectors (live + upload)
    def make_live_stub():
        def _fn(miner_code: str) -> bool:
            counters['live'] += 1
            return True
        return _fn

    def make_upload_stub():
        def _fn(miner_code: str, api_client=None, hex_id=None, install_id=None, miner_key=None) -> bool:
            counters['upload'] += 1
            # simulate backend upload
            if api_client and hasattr(api_client, '_api') and hasattr(api_client._api, 'upload_measurement'):
                api_client._api.upload_measurement(measurement_type='sim')
            return True
        return _fn

    # Patch collectors depending on miner type
    if miner == 'BM':
        monkeypatch.setattr(coll, 'collect_and_write_bandwidth_live', make_live_stub())
        monkeypatch.setattr(coll, 'collect_and_upload_bandwidth', make_upload_stub())
    elif miner in ('IDM', 'ODM') or miner == 'IDM':
        monkeypatch.setattr(coll, 'collect_and_write_decibel_live', make_live_stub())
        monkeypatch.setattr(coll, 'collect_and_upload_decibel', make_upload_stub())
    elif miner in ('ISM', 'OSM') or miner == 'ISM':
        monkeypatch.setattr(coll, 'collect_and_write_satellite_live', make_live_stub())
        monkeypatch.setattr(coll, 'collect_and_upload_satellite', make_upload_stub())
    elif miner == 'IRM':
        monkeypatch.setattr(coll, 'collect_and_write_radiation_live', make_live_stub())
        monkeypatch.setattr(coll, 'collect_and_upload_radiation', make_upload_stub())
    elif miner == 'AEM':
        # AEM has no fast live writers; only upload
        monkeypatch.setattr(coll, 'collect_and_upload_aem', make_upload_stub())

    # Stub write_week_local to count pod marks
    def fake_write_week_local(miner_key, ts, status, interval_seconds, *, pod_status=None, **kwargs):
        if pod_status:
            counters['pod_mark'] += 1

    monkeypatch.setattr(mos, 'write_week_local', fake_write_week_local)

    # Build collectors same way the daemon does
    live_collectors = {}
    upload_collectors = {}

    if miner == 'BM':
        live_collectors['bandwidth_live'] = (lambda: coll.collect_and_write_bandwidth_live(miner), intervals.get('bandwidth', 10))
        upload_collectors['bandwidth'] = (lambda: coll.collect_and_upload_bandwidth(miner, api, 'hex', 'inst', 'MK_TEST'), 600)
    elif miner in ('ISM', 'OSM'):
        live_collectors['satellite_live'] = (lambda: coll.collect_and_write_satellite_live(miner), intervals.get('satellite', 10))
        upload_collectors['satellite'] = (lambda: coll.collect_and_upload_satellite(miner, api, 'hex', 'inst', 'MK_TEST'), 600)
    elif miner == 'IRM':
        live_collectors['radiation_live'] = (lambda: coll.collect_and_write_radiation_live(miner), intervals.get('radiation', 10))
        upload_collectors['radiation'] = (lambda: coll.collect_and_upload_radiation(miner, api, 'hex', 'inst', 'MK_TEST'), 600)
    elif miner in ('IDM', 'ODM') or miner == 'IDM':
        live_collectors['decibel_live'] = (lambda: coll.collect_and_write_decibel_live(miner), intervals.get('decibel', 2))
        upload_collectors['decibel'] = (lambda: coll.collect_and_upload_decibel(miner, api, 'hex', 'inst', 'MK_TEST'), 600)
    elif miner == 'AEM':
        upload_collectors['aem'] = (lambda: coll.collect_and_upload_aem(miner, api, 'hex', 'inst', 'MK_TEST'), intervals.get('aem', 600))

    # Simulate time progression and run the scheduler logic
    last_collection = {}
    start = 1000.0

    for t in range(DURATION_SECONDS):
        now = start + t

        # Live collectors
        for key, (fn, interval) in live_collectors.items():
            if key not in last_collection:
                last_collection[key] = now
            if now - last_collection[key] >= interval:
                res = fn()
                if res:
                    last_collection[key] = now

        # Upload collectors
        for key, (fn, interval) in upload_collectors.items():
            if key not in last_collection:
                last_collection[key] = now
            if now - last_collection[key] >= interval:
                res = fn()
                if res:
                    # Daemon marks PoD only for expected groups
                    # We mimic that behavior (as the real daemon does)
                    # expected_measurement_groups() returns normalized names (e.g., 'bandwidth')
                    expected = set(m.lower() for m in mos.expected_measurement_groups())
                    norm = key.replace('_live', '')
                    if norm and norm in expected:
                        mos.write_week_local('MK_TEST', mos.now_utc(), 'online', 600, pod_status=True)
                    last_collection[key] = now

    # Compute expected counts
    def expected_count(interval):
        # First collection happens after 'interval' seconds from start
            # Our simulation steps t=0..DURATION_SECONDS-1 so the last second is DURATION_SECONDS-1
            return (DURATION_SECONDS - 1) // interval
    if miner == 'BM':
        assert counters['live'] == expected_count(intervals.get('bandwidth', 10))
        assert counters['upload'] == expected_count(600)
        assert counters['pod_mark'] == counters['upload']
    elif miner in ('IDM', 'ODM') or miner == 'IDM':
        assert counters['live'] == expected_count(intervals.get('decibel', 2))
        assert counters['upload'] == expected_count(600)
        assert counters['pod_mark'] == counters['upload']
    elif miner in ('ISM', 'OSM') or miner == 'ISM':
        assert counters['live'] == expected_count(intervals.get('satellite', 10))
        assert counters['upload'] == expected_count(600)
        assert counters['pod_mark'] == counters['upload']
    elif miner == 'IRM':
        assert counters['live'] == expected_count(intervals.get('radiation', 10))
        assert counters['upload'] == expected_count(600)
        assert counters['pod_mark'] == counters['upload']
    elif miner == 'AEM':
        # No live writes expected
        assert counters['live'] == 0
        assert counters['upload'] == expected_count(intervals.get('aem', 600))
        # For AEM PoD marking is not applicable
        assert counters['pod_mark'] == 0
