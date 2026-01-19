import sys, types
# Provide light-weight stubs for heavy optional deps used by measurement modules
sys.modules.setdefault('numpy', types.SimpleNamespace())
# Minimal psutil shim used by miner_online_simple during import
sys.modules.setdefault('psutil', types.SimpleNamespace(
    net_if_addrs=lambda: {},
    boot_time=lambda: 0,
))

import json
from types import SimpleNamespace

import pytest

from measurements.collector import collect_and_upload_bandwidth
import miner_online_simple as mos


def test_collect_and_upload_bandwidth_success_calls_api_and_writes_csv(monkeypatch):
    calls = {}

    def fake_append_row(key, miner_code, row):
        # record that append_row was invoked and return True to simulate success
        calls['append'] = (key, miner_code, row)
        return True

    monkeypatch.setattr('measurements.collector.append_row', fake_append_row)

    uploaded = {}

    class FakeAPI:
        def upload_measurement(self, **kwargs):
            uploaded['args'] = kwargs
            return None

    api_client = SimpleNamespace(_api=FakeAPI())

    res = collect_and_upload_bandwidth("BM", api_client, hex_id="fakehex", install_id="fakeinst", miner_key="MK")

    assert res is True
    assert 'append' in calls
    assert calls['append'][0] == 'bandwidth'
    assert 'args' in uploaded
    assert uploaded['args']['measurement_type'] == 'bandwidth'


def test_write_week_local_sets_pod_status_true(tmp_path, monkeypatch):
    # Use a temporary PROGRAMDATA so write_week_local writes to an isolated location
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path))
    # Ensure miner code is BM (bandwidth miner)
    monkeypatch.setenv('MINER_CODE', 'BM')

    miner_key = "MK_TEST"
    ts = mos.now_utc()
    interval = 600

    # Call the function under test
    mos.write_week_local(miner_key, ts, "online", interval, pod_status=True)

    week_start, _ = mos._week_bounds_for_rewards(ts)
    path = mos._week_file_path(week_start)

    with open(path, 'r', encoding='utf-8') as f:
        doc = json.load(f)

    day_doc = doc.get('days', {}).get(mos._date_iso(ts))
    assert day_doc is not None, "Expected day entry present in written week file"

    hour, slot = mos.hour_and_slot(ts, interval)
    hours = day_doc.get('hours', {})
    assert str(hour) in hours, "Expected hour present in day doc"

    hour_doc = hours[str(hour)]
    slots = hour_doc.get('slots', [])
    assert len(slots) > slot, "Expected slot index present in hour slots"

    slot_obj = slots[slot]
    assert isinstance(slot_obj, dict), "Expected slot object to be a dict"
    gates = slot_obj.get('gates', {})
    assert gates.get('data') is True, "Expected 'data' gate (pod_status) to be True"
