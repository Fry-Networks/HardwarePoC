"""
Migrate existing raw measurement data to optimized daily aggregates.

This script can be used to:
1. Convert existing MongoDB collections from raw to aggregated format
2. Convert existing in-memory storage to aggregated format
3. Migrate JSON export files to optimized structure

Run this ONCE to migrate existing data, then the new upload_measurement
will automatically maintain aggregates going forward.
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# Add project paths
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'measurements'))
sys.path.insert(0, str(project_root / 'files-from-External-API'))

from measurement_aggregator import get_aggregator


def migrate_mongodb_measurements(mongo_uri: str = "mongodb://localhost:27017/"):
    """Migrate MongoDB measurements collection from raw to aggregated format."""
    try:
        from pymongo import MongoClient
    except ImportError:
        print("❌ pymongo not installed. Run: pip install pymongo")
        return
    
    print("Connecting to MongoDB...")
    client = MongoClient(mongo_uri)
    db = client['PoC']
    measurements = db['measurements']
    
    print(f"Found {measurements.count_documents({})} hex documents")
    
    aggregator = get_aggregator()
    migrated_count = 0
    
    for doc in measurements.find({}):
        hex_id = doc.get('hex_id')
        if not hex_id:
            continue
        
        print(f"\nProcessing {hex_id}...")
        
        # Check if already aggregated (has country field and no raw arrays)
        if 'country' in doc and not any(
            isinstance(v, list) and v and isinstance(v[0], dict) and 'timestamp' in v[0]
            for k, v in doc.items() if k not in ('_id', 'hex_id', 'country')
        ):
            print(f"  ✓ Already aggregated, skipping")
            continue
        
        # Initialize new aggregated doc
        new_doc = {
            'hex_id': hex_id,
            'country': aggregator.get_country_from_hex(hex_id)
        }
        
        # Process each measurement type
        measurement_types = ['Bandwidth', 'bandwidth', 'Satellite', 'satellite', 
                            'Decibel', 'decibel', 'Radiation', 'radiation']
        
        total_measurements = 0
        for mtype in measurement_types:
            if mtype not in doc or not isinstance(doc[mtype], list):
                continue
            
            measurements_list = doc[mtype]
            if not measurements_list:
                continue
            
            print(f"  Processing {len(measurements_list)} {mtype} measurements...")
            
            # Aggregate all measurements for this type
            normalized_type = mtype.capitalize()
            temp_aggregates = {}
            
            for entry in measurements_list:
                if not isinstance(entry, dict):
                    continue
                
                timestamp = entry.get('timestamp')
                value = entry.get('value', {})
                
                if not timestamp or not value:
                    continue
                
                temp_aggregates = aggregator.aggregate_measurement(
                    temp_aggregates, timestamp, value
                )
                total_measurements += 1
            
            # Finalize and store
            if temp_aggregates:
                new_doc[normalized_type] = aggregator.finalize_aggregates(temp_aggregates)
        
        # Replace the document
        if total_measurements > 0:
            measurements.replace_one(
                {'hex_id': hex_id},
                new_doc,
                upsert=True
            )
            migrated_count += 1
            print(f"  ✓ Migrated {total_measurements} measurements → daily aggregates")
    
    client.close()
    print(f"\n✅ Migration complete! Migrated {migrated_count} hex documents")


def migrate_json_file(input_file: str, output_dir: str = None):
    """Migrate JSON measurement file to optimized format."""
    import json
    
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"❌ File not found: {input_file}")
        return
    
    if output_dir is None:
        output_dir = input_path.parent
    
    print(f"Loading {input_file}...")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"Found {len(data)} hex documents")
    
    aggregator = get_aggregator()
    optimized = []
    
    for doc in data:
        hex_id = doc.get('hex_id')
        if not hex_id:
            continue
        
        # Check if already aggregated
        if 'country' in doc and not any(
            isinstance(v, list) and v and isinstance(v[0], dict) and 'timestamp' in v[0]
            for k, v in doc.items() if k not in ('hex_id', 'country')
        ):
            optimized.append(doc)
            continue
        
        print(f"Processing {hex_id}...")
        
        # Initialize new document
        new_doc = {
            'hex_id': hex_id,
            'country': aggregator.get_country_from_hex(hex_id)
        }
        
        # Process each measurement type
        measurement_types = ['Bandwidth', 'bandwidth', 'Satellite', 'satellite', 
                            'Decibel', 'decibel', 'Radiation', 'radiation']
        
        for mtype in measurement_types:
            if mtype not in doc or not isinstance(doc[mtype], list):
                continue
            
            measurements_list = doc[mtype]
            if not measurements_list:
                continue
            
            # Aggregate all measurements
            normalized_type = mtype.capitalize()
            temp_aggregates = {}
            
            for entry in measurements_list:
                if not isinstance(entry, dict):
                    continue
                
                timestamp = entry.get('timestamp')
                value = entry.get('value', {})
                
                if not timestamp or not value:
                    continue
                
                temp_aggregates = aggregator.aggregate_measurement(
                    temp_aggregates, timestamp, value
                )
            
            # Finalize
            if temp_aggregates:
                new_doc[normalized_type] = aggregator.finalize_aggregates(temp_aggregates)
        
        optimized.append(new_doc)
    
    # Save optimized file
    output_path = Path(output_dir) / f"{input_path.stem}.optimized.json"
    print(f"\nSaving to {output_path}...")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(optimized, f, indent=2)
    
    # Show stats
    original_size = input_path.stat().st_size / (1024 * 1024)
    new_size = output_path.stat().st_size / (1024 * 1024)
    reduction = ((original_size - new_size) / original_size) * 100
    
    print(f"\n✅ Migration complete!")
    print(f"  Original: {original_size:.2f} MB")
    print(f"  Optimized: {new_size:.2f} MB")
    print(f"  Reduction: {reduction:.1f}%")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrate measurements to optimized aggregates")
    parser.add_argument('--mongodb', metavar='URI', help='Migrate MongoDB collection (e.g., mongodb://localhost:27017/)')
    parser.add_argument('--json', metavar='FILE', help='Migrate JSON file')
    parser.add_argument('--output-dir', metavar='DIR', help='Output directory for JSON migration')
    
    args = parser.parse_args()
    
    if args.mongodb:
        migrate_mongodb_measurements(args.mongodb)
    elif args.json:
        migrate_json_file(args.json, args.output_dir)
    else:
        print("Usage:")
        print("  Migrate MongoDB:  python migrate_measurements.py --mongodb mongodb://localhost:27017/")
        print("  Migrate JSON:     python migrate_measurements.py --json measurements/PoC.measurements.json")
        print("\nOr run default migration:")
        
        # Default: migrate the measurements JSON if it exists
        default_json = Path(__file__).parent / 'measurements' / 'PoC.measurements.json'
        if default_json.exists():
            print(f"\nMigrating {default_json}...")
            migrate_json_file(str(default_json))
        else:
            print(f"\n❌ No default file found at {default_json}")
