#!/usr/bin/env python3
"""Test lease verification to debug the issue."""

import datetime as dt
import sys
from mongo_api_proxy import MongoProxyClient
from external_api import ExternalApiClient
from miner_online_simple import verify_installation_lease, now_utc

# Use timezone-aware UTC
UTC = getattr(dt, "UTC", dt.timezone.utc)

# Test parameters
miner_key = "REDACTED_ROTATE_ME"
install_id = "5d0056b3-879a-423e-820d-3537f95dc61c"

print("=== Lease Verification Test ===")
print(f"Miner Key: {miner_key}")
print(f"Install ID: {install_id}")
print()

# Get bearer token from embedded config or prompt
try:
    from miner_online_simple import decrypt_config
    cfg = decrypt_config()
    bearer_token = cfg.get("api_base_url") and cfg.get("bearer_token")
    api_base = cfg.get("api_base_url", "https://hardwareapi.frynetworks.com")
except Exception:
    api_base = "https://hardwareapi.frynetworks.com"
    bearer_token = input("Enter bearer token: ").strip()
    if not bearer_token:
        print("ERROR: Bearer token required")
        sys.exit(1)

print(f"API Base: {api_base}")
print()

# Connect to MongoDB via API
try:
    api_client = ExternalApiClient(api_base, token=bearer_token)
    mongo_client = MongoProxyClient(api_client)
    print("✓ Connected to MongoDB API")
except Exception as e:
    print(f"✗ Failed to connect: {e}")
    sys.exit(1)

# Get current UTC time
now = now_utc()
print(f"Current UTC time: {now} (timezone: {now.tzinfo})")
print()

# Query the installations collection directly
print("=== Direct MongoDB Query ===")
all_docs = []
coll = None
try:
    # Try both possible collections
    for db_name, coll_name in [("PoC", "installations"), ("main", "installations"), ("creds", "installations")]:
        print(f"Checking collection: {db_name}.{coll_name}")
        coll = mongo_client[db_name][coll_name]
        
        # Find all documents with matching miner_key
        all_docs = list(coll.find({"miner_key": miner_key}))
        print(f"  Found {len(all_docs)} document(s) with miner_key: {miner_key}")
        
        if all_docs:
            print(f"  ✓ Found lease documents in {db_name}.{coll_name}!")
            break
    print()
    
    # Use the collection where we found data
    if not all_docs:
        print("✗ No lease documents found in any collection!")
        print("Trying to find ANY document with _lease=True...")
        for db_name, coll_name in [("PoC", "installations"), ("main", "installations"), ("creds", "installations")]:
            coll = mongo_client[db_name][coll_name]
            cursor = coll.find({"_lease": True})
            any_lease = []
            for i, doc in enumerate(cursor):
                if i >= 5:
                    break
                any_lease.append(doc)
            if any_lease:
                print(f"  Found {len(any_lease)} lease document(s) in {db_name}.{coll_name}")
                all_docs = [d for d in any_lease if d.get("miner_key") == miner_key]
                if all_docs:
                    print(f"  ✓ Found matching miner_key in {db_name}.{coll_name}!")
                    break
        print()
    
    if not all_docs or not coll:
        print("ERROR: Cannot find lease document for this miner_key in any collection")
        sys.exit(1)
    
    for i, doc in enumerate(all_docs, 1):
        print(f"--- Document {i} ---")
        print(f"  _id: {doc.get('_id')}")
        print(f"  miner_key: {doc.get('miner_key')}")
        print(f"  install_id: {doc.get('install_id')}")
        print(f"  lease_install_id: {doc.get('lease_install_id')}")
        print(f"  _lease: {doc.get('_lease')}")
        print(f"  first_installed_at: {doc.get('first_installed_at')}")
        print(f"  last_seen_at: {doc.get('last_seen_at')}")
        
        lease_expires = doc.get('lease_expires_at')
        print(f"  lease_expires_at: {lease_expires}")
        
        if lease_expires:
            # Check if it's a datetime object
            print(f"  lease_expires_at type: {type(lease_expires)}")
            
            # Try to determine if it's timezone-aware
            if isinstance(lease_expires, dt.datetime):
                print(f"  lease_expires_at timezone: {lease_expires.tzinfo}")
                
                # Calculate time difference
                if lease_expires > now:
                    diff = (lease_expires - now).total_seconds()
                    print(f"  ✓ Lease is VALID (expires in {diff:.1f} seconds)")
                else:
                    diff = (now - lease_expires).total_seconds()
                    print(f"  ✗ Lease is EXPIRED (expired {diff:.1f} seconds ago)")
            else:
                print(f"  ⚠ lease_expires_at is not a datetime object")
        print()
    
    # Now test with the specific install_id
    print(f"=== Searching for lease with install_id: {install_id} ===")
    lease_doc = coll.find_one({
        "miner_key": miner_key,
        "lease_install_id": install_id,
        "_lease": True
    })
    
    if lease_doc:
        print("✓ Found lease document (without expiration check)")
        lease_expires = lease_doc.get('lease_expires_at')
        print(f"  lease_expires_at: {lease_expires}")
        print(f"  lease_expires_at type: {type(lease_expires)}")
        if isinstance(lease_expires, dt.datetime):
            print(f"  lease_expires_at timezone: {lease_expires.tzinfo}")
        print(f"  Current time: {now}")
        print(f"  Current time type: {type(now)}")
        print(f"  Current time timezone: {now.tzinfo}")
        
        if isinstance(lease_expires, dt.datetime):
            print(f"  Comparison: {lease_expires} > {now} = {lease_expires > now}")
    else:
        print("✗ No lease document found with matching miner_key, lease_install_id, and _lease=True")
    print()
    
    # Now test with expiration check
    print("=== Testing with expiration check ($gt comparison) ===")
    valid_lease = coll.find_one({
        "miner_key": miner_key,
        "lease_install_id": install_id,
        "_lease": True,
        "lease_expires_at": {"$gt": now}
    })
    
    if valid_lease:
        print("✓ Found VALID lease (not expired)")
    else:
        print("✗ No valid lease found (either doesn't exist or is expired)")
    print()
    
except Exception as e:
    print(f"✗ Query failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test the actual verify_installation_lease function
print("=== Testing verify_installation_lease() function ===")
result = verify_installation_lease(mongo_client, miner_key, install_id)
print(f"Result: {result}")
print()

if result:
    print("✓✓✓ LEASE VERIFICATION SUCCESSFUL ✓✓✓")
else:
    print("✗✗✗ LEASE VERIFICATION FAILED ✗✗✗")
