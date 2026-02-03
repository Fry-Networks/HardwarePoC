"""Test measurement aggregation."""

import sys
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent / 'measurements'))

from measurement_aggregator import get_aggregator


def test_aggregation():
    """Test that measurements are correctly aggregated."""
    
    print("Testing measurement aggregation...\n")
    
    aggregator = get_aggregator()
    
    # Simulate multiple measurements for the same hex
    hex_id = "871f90151ffffff"
    
    test_measurements = [
        {
            "timestamp": "2026-02-03T10:00:00Z",
            "measurement_type": "Bandwidth",
            "value": {"dl": 5.2, "ul": 3.1, "iface": "Ethernet"}
        },
        {
            "timestamp": "2026-02-03T10:15:00Z",
            "measurement_type": "Bandwidth",
            "value": {"dl": 8.7, "ul": 4.5, "iface": "Ethernet"}
        },
        {
            "timestamp": "2026-02-03T10:30:00Z",
            "measurement_type": "Bandwidth",
            "value": {"dl": 3.2, "ul": 2.1, "iface": "Ethernet"}
        },
        {
            "timestamp": "2026-02-02T15:00:00Z",
            "measurement_type": "Satellite",
            "value": {"fix": "GPS", "sats": 4, "lat": 45.92, "lon": 4.23, "hdop": 5.21}
        },
    ]
    
    print(f"Processing {len(test_measurements)} measurements...")
    
    # Build aggregated document
    doc = None
    for i, m in enumerate(test_measurements, 1):
        doc = aggregator.process_measurement_upload(
            hex_id=hex_id,
            timestamp=m["timestamp"],
            measurement_type=m["measurement_type"],
            value=m["value"],
            existing_doc=doc
        )
        print(f"  {i}. {m['measurement_type']} at {m['timestamp']}")
    
    # Check aggregated data
    print(f"\n✓ Aggregated document:")
    print(f"  hex_id: {doc.get('hex_id')}")
    print(f"  country: {doc.get('country', 'Not set')}")
    
    # Check Bandwidth aggregation
    if 'Bandwidth' in doc:
        print(f"\n✓ Bandwidth aggregates:")
        for date, stats in doc['Bandwidth'].items():
            print(f"    {date}:")
            print(f"      count: {stats.get('count')}")
            if 'dl' in stats:
                print(f"      dl: min={stats['dl']['min']}, max={stats['dl']['max']}, avg={stats['dl']['avg']}")
            if 'ul' in stats:
                print(f"      ul: min={stats['ul']['min']}, max={stats['ul']['max']}, avg={stats['ul']['avg']}")
            
        # Verify aggregation is correct
        feb3_stats = doc['Bandwidth'].get('2026-02-03', {})
        if feb3_stats.get('count') == 3:
            print(f"\n  ✅ Count correct: 3 measurements aggregated")
        else:
            print(f"\n  ❌ Count incorrect: expected 3, got {feb3_stats.get('count')}")
            return False
        
        dl_stats = feb3_stats.get('dl', {})
        expected_avg = round((5.2 + 8.7 + 3.2) / 3, 2)
        if dl_stats.get('min') == 3.2 and dl_stats.get('max') == 8.7 and dl_stats.get('avg') == expected_avg:
            print(f"  ✅ Min/Max/Avg correct: min=3.2, max=8.7, avg={expected_avg}")
        else:
            print(f"  ❌ Stats incorrect: got {dl_stats}, expected avg={expected_avg}")
            return False
    
    # Check Satellite aggregation  
    if 'Satellite' in doc:
        print(f"\n✓ Satellite aggregates:")
        for date, stats in doc['Satellite'].items():
            print(f"    {date}: count={stats.get('count')}")
    
    # Calculate size reduction
    import json
    raw_size = sum(len(json.dumps({
        "hex_id": hex_id,
        "timestamp": m["timestamp"],
        "miner_code": "BM",
        "install_id": "test-1",
        "measurement_type": m["measurement_type"],
        "value": m["value"]
    })) for m in test_measurements)
    
    agg_size = len(json.dumps(doc))
    reduction = ((raw_size - agg_size) / raw_size) * 100
    
    print(f"\n📊 Storage comparison:")
    print(f"  Raw: {raw_size} bytes ({len(test_measurements)} measurements)")
    print(f"  Aggregated: {agg_size} bytes")
    print(f"  Reduction: {reduction:.1f}%")
    
    print(f"\n✅ All tests passed!")
    return True


if __name__ == '__main__':
    success = test_aggregation()
    sys.exit(0 if success else 1)
