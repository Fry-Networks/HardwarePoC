"""Bandwidth measurement collection for BM (Bandwidth Miner).

Collects real download/upload speed measurements.
Transferred from GUI worker.py to autonomous service.
"""

import time
import logging
from typing import Dict, Any, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

log = logging.getLogger("measurements.bandwidth")

# Test file URLs for bandwidth testing
DL_TEST_URL = "http://speedtest.frynetworks.com/random100x100.jpg"  # 10MB test file
DL_TEST_SIZE_MB = 10.0

def collect_bandwidth_measurement() -> Optional[Dict[str, Any]]:
    """Collect real bandwidth measurement (download/upload speeds).
    
    Returns:
        Dict with dl (Mbps), ul (Mbps), iface (str) or None on failure
    """
    try:
        if not requests:
            log.warning("requests module not available for bandwidth test")
            return None
        
        # Download test
        dl_mbps = _test_download_speed()
        
        # Upload test (simplified - can be enhanced)
        ul_mbps = _test_upload_speed()
        
        # Determine active interface (simplified)
        iface = _get_active_interface()
        
        if dl_mbps is None and ul_mbps is None:
            return None
        
        return {
            "dl": round(dl_mbps, 2) if dl_mbps is not None else 0.0,
            "ul": round(ul_mbps, 2) if ul_mbps is not None else 0.0,
            "iface": iface or "Unknown"
        }
        
    except Exception as e:
        log.error("Bandwidth measurement failed: %s", e)
        return None


def _test_download_speed() -> Optional[float]:
    """Test download speed in Mbps using proven CDN sources."""
    if not requests:
        return None
    
    # Try multiple CDN sources for download test
    download_urls = [
        "https://proof.ovh.net/files/10Mb.dat",     # OVH CDN - 10MB
        "https://speed.hetzner.de/10MB.bin",        # Hetzner - 10MB
        "https://proof.ovh.net/files/1Mb.dat",      # OVH CDN - 1MB (fallback)
    ]
    
    for download_url in download_urls:
        try:
            start = time.time()
            resp = requests.get(download_url, stream=True, timeout=30)
            resp.raise_for_status()
            
            total_bytes = 0
            # Use larger chunk size (128KB) for better throughput measurement
            for chunk in resp.iter_content(chunk_size=131072):
                if chunk:
                    total_bytes += len(chunk)
            
            elapsed = time.time() - start
            
            if elapsed <= 0:
                continue
            
            # Convert to Mbps
            mbps = (total_bytes * 8) / 1_000_000 / elapsed
            return mbps
            
        except Exception:
            # Try next URL
            continue
    
    log.warning("All download URLs failed")
    return None


def _test_upload_speed() -> Optional[float]:
    """Test upload speed in Mbps using proven endpoints."""
    if not requests:
        return None
    
    # Try multiple endpoints for upload test
    upload_urls = [
        "https://httpbin.org/post",              # httpbin (reliable)
        "https://postman-echo.com/post",         # Postman Echo
    ]
    
    for upload_url in upload_urls:
        try:
            # Generate 5MB of data for accurate measurement
            data_size = 5 * 1024 * 1024  # 5MB
            upload_data = b'0' * data_size
            
            start = time.time()
            resp = requests.post(upload_url, data=upload_data, timeout=30)
            resp.raise_for_status()
            
            elapsed = time.time() - start
            
            if elapsed <= 0:
                continue
            
            # Convert to Mbps
            mbps = (data_size * 8) / 1_000_000 / elapsed
            return mbps
            
        except Exception:
            # Try next URL
            continue
    
    log.warning("All upload URLs failed")
    return None


def _get_active_interface() -> Optional[str]:
    """Get active network interface name."""
    try:
        import psutil  # type: ignore
        
        # Get network stats
        stats = psutil.net_io_counters(pernic=True)
        
        # Find interface with most bytes sent/received
        max_bytes = 0
        active_iface = None
        
        for iface, io in stats.items():
            total = io.bytes_sent + io.bytes_recv
            if total > max_bytes:
                max_bytes = total
                active_iface = iface
        
        return active_iface
        
    except Exception:
        return "Ethernet"  # Default fallback
