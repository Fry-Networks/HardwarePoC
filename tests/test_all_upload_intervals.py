import time

import pytest

from measurements import collector


class FakeAPI:
    def __init__(self):
        self.calls = []

    class _Inner:
        def __init__(self, outer):
            self.outer = outer

        def upload_measurement(self, **kwargs):
            self.outer.calls.append(kwargs)

    @property
    def _api(self):
        return self._Inner(self)


@pytest.mark.parametrize(
    "func_name,collect_patch,return_value,measurement_type",
    [
        ("collect_and_upload_satellite", "collect_satellite_measurement", {"sats": 5}, "satellite"),
        ("collect_and_upload_radiation", "collect_radiation_measurement", {"cpm": 2, "usv": 0.1, "mr": 0.0}, "radiation"),
        ("collect_and_upload_decibel", "collect_decibel_measurement", {"dbfs": -20.0}, "decibel"),
        ("collect_and_upload_aem", "collect_aem_measurement", {"poi": 1, "tasks_completed": 3, "last_activity_age_s": 120.5, "mem_rss_mb": 36.2, "proc_count": 6}, "aem"),
    ],
)
def test_upload_rate_limited_for_all(monkeypatch, func_name, collect_patch, return_value, measurement_type):
    # Arrange: fake measurement and API
    fake_api = FakeAPI()

    # patch the underlying collect_* function to return value
    monkeypatch.setattr(collector, collect_patch, lambda: return_value)

    # Ensure CSV writes succeed
    monkeypatch.setattr(collector, 'append_row', lambda *a, **k: True)

    # Control time
    base = [2000.0]

    def fake_time():
        return base[0]

    monkeypatch.setattr(time, 'time', fake_time)

    fn = getattr(collector, func_name)

    ok1 = fn('BM', fake_api, hex_id='hex', install_id='inst', miner_key='mk')
    assert ok1 is True
    assert len(fake_api.calls) == 1

    # within interval -> skipped
    base[0] += 10.0
    ok2 = fn('BM', fake_api, hex_id='hex', install_id='inst', miner_key='mk')
    assert ok2 is False
    assert len(fake_api.calls) == 1

    # After interval -> allowed
    base[0] += (collector._UPLOAD_MIN_INTERVAL + 1)
    ok3 = fn('BM', fake_api, hex_id='hex', install_id='inst', miner_key='mk')
    assert ok3 is True
    assert len(fake_api.calls) == 2
