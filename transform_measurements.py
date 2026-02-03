"""
Transform measurements from verbose individual records to optimized daily aggregates.

Original structure:
- 1.14M lines, 27.4 MB
- Individual measurements with redundant timestamps, miner_code, install_id

New structure:
- One document per hex_id (grouped by country)
- Daily aggregates with min/max/average
- Removes all redundancy

Expected reduction: 95%+ (27MB → <1MB)
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import statistics

try:
    import h3
except ImportError:
    print("Installing h3 library...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'h3'])
    import h3

try:
    from geopy.geocoders import Nominatim
except ImportError:
    print("Installing geopy library...")
    import subprocess
    subprocess.check_call(['pip', 'install', 'geopy'])
    from geopy.geocoders import Nominatim


def get_country_from_hex(hex_id):
    """Get country name from H3 hex ID"""
    try:
        # Convert hex string to H3 index and get coordinates
        lat, lon = h3.cell_to_latlng(hex_id)
        
        # Use geopy for reverse geocoding
        geolocator = Nominatim(user_agent="hardware_poc_measurements")
        location = geolocator.reverse(f"{lat}, {lon}", language='en', timeout=10)
        
        if location and location.raw.get('address'):
            country = location.raw['address'].get('country', 'Unknown')
            print(f"  {hex_id} → {country} ({lat:.4f}, {lon:.4f})")
            return country
        
        return 'Unknown'
    except Exception as e:
        print(f"  Warning: Could not geocode {hex_id}: {e}")
        return 'Unknown'


def parse_timestamp(ts_str):
    """Parse various timestamp formats"""
    if not ts_str:
        return None
    try:
        # Handle ISO format with timezone
        if '+' in ts_str or ts_str.endswith('Z'):
            return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return datetime.fromisoformat(ts_str)
    except:
        return None


def aggregate_daily_values(measurements):
    """Aggregate measurements by date with min/max/avg"""
    daily_data = defaultdict(list)
    
    for m in measurements:
        ts = parse_timestamp(m.get('timestamp'))
        if not ts:
            continue
            
        date_key = ts.strftime('%Y-%m-%d')
        daily_data[date_key].append(m.get('value', {}))
    
    # Calculate min/max/avg for each day
    result = {}
    for date, values in daily_data.items():
        if not values:
            continue
            
        # Extract numeric fields from the first value to know what to aggregate
        sample = values[0]
        aggregated = {'count': len(values)}
        
        # Handle different measurement types
        for key in sample:
            numeric_values = []
            all_values = []
            
            for v in values:
                if key in v:
                    val = v[key]
                    all_values.append(val)
                    if isinstance(val, (int, float)):
                        numeric_values.append(val)
            
            if numeric_values:
                aggregated[key] = {
                    'min': round(min(numeric_values), 2),
                    'max': round(max(numeric_values), 2),
                    'avg': round(statistics.mean(numeric_values), 2)
                }
            elif all_values:
                # For non-numeric, store the most common value
                from collections import Counter
                most_common = Counter(all_values).most_common(1)[0][0]
                aggregated[key] = most_common
        
        result[date] = aggregated
    
    return result


def transform_measurements(input_file, output_dir):
    """Transform measurements to new optimized structure"""
    print(f"Loading {input_file}...")
    
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"Loaded {len(data)} records")
    
    # Group by hex_id
    by_hex = defaultdict(lambda: {
        'Bandwidth': [],
        'Satellite': [],
        'Decibel': [],
        'Radiation': []
    })
    
    measurement_types = ['Bandwidth', 'bandwidth', 'Satellite', 'satellite', 
                        'Decibel', 'decibel', 'Radiation', 'radiation']
    
    for record in data:
        hex_id = record.get('hex_id')
        if not hex_id:
            continue
        
        # Process each measurement type
        for mtype in measurement_types:
            if mtype in record:
                normalized = mtype.capitalize()
                by_hex[hex_id][normalized].extend(record[mtype])
    
    print(f"Found {len(by_hex)} unique hex IDs")
    
    # Map hex IDs to countries
    print("\nMapping hex IDs to countries...")
    hex_to_country = {}
    for hex_id in by_hex.keys():
        hex_to_country[hex_id] = get_country_from_hex(hex_id)
    
    # Group by country
    by_country = defaultdict(list)
    
    # Create aggregated structure
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    
    for hex_id, measurements in by_hex.items():
        record = {'hex_id': hex_id}
        
        # Aggregate each measurement type
        for mtype in ['Bandwidth', 'Satellite', 'Decibel', 'Radiation']:
            if measurements[mtype]:
                record[mtype] = aggregate_daily_values(measurements[mtype])
        
        country = hex_to_country.get(hex_id, 'Unknown')
        by_country[country].append(record)
    
    # Save one file per country
    print(f"\nSaving country-specific files...")
    total_size = 0
    
    for country, records in by_country.items():
        # Sanitize country name for filename
        safe_country = country.replace(' ', '_').replace('/', '_')
        output_file = output_dir / f'PoC.measurements.{safe_country}.json'
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=2)
        
        file_size = output_file.stat().st_size / 1024
        total_size += file_size
        print(f"  {country}: {len(records)} hex IDs → {output_file.name} ({file_size:.1f} KB)")
    
    # Stats
    original_size = Path(input_file).stat().st_size / (1024 * 1024)
    new_size = total_size / 1024
    reduction = ((original_size - new_size) / original_size) * 100
    
    print(f"\n✓ Transformation complete!")
    print(f"  Original: {original_size:.2f} MB")
    print(f"  Optimized: {new_size:.2f} MB (across {len(by_country)} countries)")
    print(f"  Reduction: {reduction:.1f}%")
    print(f"\n  Records: {len(data)} → {sum(len(r) for r in by_country.values())} (grouped by country)")
    
    return by_country


if __name__ == '__main__':
    input_file = r'c:\Users\jimbo\Documents\GitHub\DevTesting\HardwarePoC\measurements\PoC.measurements.json'
    output_dir = r'c:\Users\jimbo\Documents\GitHub\DevTesting\HardwarePoC\measurements'
    
    transform_measurements(input_file, output_dir)
