import types
import time
import builtins

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


def test_bandwidth_upload_rate_limited(monkeypatch):
    # Arrange: fake bandwidth measurement and API
    monkeypatch.setattr(collector, 'collect_bandwidth_measurement', lambda: {'dl': 100, 'ul': 10, 'iface': 'eth0'})

    api = FakeAPI()

    # Control time.time() so repeated calls are within the rate limit
    base = [1000.0]

    def fake_time():
        return base[0]

    monkeypatch.setattr(time, 'time', fake_time)

    # Ensure CSV writes succeed in test environment
    monkeypatch.setattr(collector, 'append_row', lambda *a, **k: True)

    # First upload should succeed
    ok1 = collector.collect_and_upload_bandwidth('BM', api, hex_id='hex', install_id='inst', miner_key='mk')
    assert ok1 is True
    assert len(api.calls) == 1

    # Move time forward but still within _BANDWIDTH_UPLOAD_MIN_INTERVAL (10s)
    base[0] += 10.0

    ok2 = collector.collect_and_upload_bandwidth('BM', api, hex_id='hex', install_id='inst', miner_key='mk')
    # Second call should be skipped by rate limiter
    assert ok2 is False
    assert len(api.calls) == 1

    # Move time past the interval and try again
    base[0] += (collector._UPLOAD_MIN_INTERVAL + 1)
    ok3 = collector.collect_and_upload_bandwidth('BM', api, hex_id='hex', install_id='inst', miner_key='mk')
    assert ok3 is True
    assert len(api.calls) == 2
