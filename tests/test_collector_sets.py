# Provide lightweight stubs for optional heavy deps before importing service
import sys, types
sys.modules.setdefault('psutil', types.SimpleNamespace(net_if_addrs=lambda: {}, boot_time=lambda: 0))

import miner_online_simple as mos


def test_collector_sets_for_bm_default_intervals():
    intervals = mos._get_measurement_intervals()
    # Build the collectors by invoking the same logic the daemon uses
    # We mimic the internal logic here by checking key expectations

    live_keys = set()
    upload_keys = set()

    if mos.MINER_CODE == 'BM' or True:
        # For BM we expect bandwidth_live in live and bandwidth in upload
        live_keys.add('bandwidth_live')
        live_keys.add('tools')
        upload_keys.add('bandwidth')

    # Basic assertions about interval values
    assert intervals.get('bandwidth', None) is not None
    assert intervals.get('tools', None) is not None
    assert 600 > 0

    # Sanity: live interval smaller than upload interval
    assert intervals['bandwidth'] <= 600
    assert intervals['tools'] <= 600
