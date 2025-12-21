FryNetworks Hardware PoC - Recent Changes
=========================================

This readme summarizes the changes added in PR #1.

NEW FEATURES
------------

1. MEASUREMENT QUEUING
   - Retains last N measurements locally for resilience
   - Prevents data loss during network outages
   - Configurable queue size per measurement type

2. INTEGRITY HASHES
   - SHA-256 hash computed alongside encrypted measurements
   - Ensures data integrity during transmission
   - Allows verification of measurement authenticity

3. BATCH UPLOADS
   - Multiple measurements uploaded in single API call
   - Improved efficiency and reduced API overhead
   - Automatic batching based on queue thresholds

4. CONFIGURABLE UPLOAD INTERVALS
   - Per-measurement-group interval settings
   - Fine-grained control over upload frequency
   - Optimizes bandwidth usage for different data types

NEW FILES
---------

measurement_manager.py
   Enhanced measurement handling module that implements:
   - Measurement queuing with configurable retention
   - Integrity hash generation (SHA-256)
   - Batch upload functionality
   - Per-group upload interval configuration

create_sdk_config.py
   SDK approval encryption utility for BM (Bandwidth Miner) devices:
   - Encrypts SDK approval configurations
   - Manages BM-specific settings

CLI UTILITIES
-------------

New command-line arguments added to miner_online_simple.py:

  --check-key <miner_key>
      Verify miner key exists in the database
      Exit codes: 0=exists, 3=not found, 2=invalid format

  --check-version <miner_key>
      Check software version requirements
      Prints JSON with version compliance status
      Exit codes: 0=up-to-date, 7=outdated

  --check-concurrency <miner_key>
      Detect concurrent installations of the same miner key
      Prints conflict details if another host is active
      Exit codes: 0=clear, 8=conflict detected

  --lease-dump <miner_key>
      Debug utility to print all lease records for a miner key
      Useful for troubleshooting installation issues

  --version
      Print version information

MODIFIED FILES
--------------

- ENCRYPTED_MEASUREMENTS.md : Updated documentation with new features
- README.md                 : Added usage examples for new CLI utilities
- miner_online_simple.py    : Added CLI argument parsing and utilities
