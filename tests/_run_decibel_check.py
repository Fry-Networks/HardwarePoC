import sys
try:
    import numpy as np
except Exception as e:
    print('numpy missing:', e)
    sys.exit(0)
from measurements.collector import collect_and_upload_decibel
import measurements.decibel as decibel

decibel.collect_decibel_measurement = lambda: {"dbfs": np.float32(-42.5)}
class Fake:
    def __init__(self):
        self.last=None
    class API:
        def __init__(self,p): self._parent=p
        def upload_measurement(self, **kw): self._parent.last=kw
    @property
    def _api(self): return self.API(self)

f=Fake()
ok = collect_and_upload_decibel("IDM", api_client=f, hex_id="hh", install_id="ii", miner_key=None)
print('ok', ok)
print('last value:', f.last)
print('dbfs type:', type(f.last['value']['dbfs']))
import json
print('json ok:', json.dumps(f.last['value']))
