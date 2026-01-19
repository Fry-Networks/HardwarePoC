import json
import pytest

from measurements.collector import collect_and_upload_decibel


class FakeAPI:
    def __init__(self):
        self.last = None

    class _Inner:
        def __init__(self, parent):
            self._parent = parent

        def upload_measurement(self, **kwargs):
            self._parent.last = kwargs

    @property
    def _api(self):
        return self._Inner(self)


@pytest.mark.skipif(not pytest.importorskip("numpy", reason="numpy missing"), reason="requires numpy")
def test_decibel_upload_coerces_numpy_scalar(monkeypatch):
    import numpy as np

    # Monkeypatch the decibel measurement collector to return a numpy.float32
    monkeypatch.setattr("measurements.decibel.collect_decibel_measurement", lambda: {"dbfs": np.float32(-42.5)})

    fake = FakeAPI()

    # Call the uploader
    ok = collect_and_upload_decibel("IDM", api_client=fake, hex_id="hh", install_id="ii", miner_key=None)

    assert ok is True
    assert fake.last is not None
    val = fake.last.get("value")
    assert isinstance(val, dict)

    # dbfs must be a native Python float and JSON-serializable
    assert isinstance(val.get("dbfs"), float)
    json.dumps(val)  # should not raise
