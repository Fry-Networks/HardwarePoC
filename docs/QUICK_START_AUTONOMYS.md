# Autonomys Integration - Quick Start Guide

## Step 1: Install Dependencies (5 minutes)

```bash
cd C:\Users\jimbo\Documents\GitHub\DevTesting\HardwarePoC
pip install -r requirements-autonomys.txt
```

This installs:
- ✓ h3 (hexagon geospatial indexing)
- ✓ pandas & pyarrow (data processing & parquet files)
- ✓ boto3 (S3-compatible uploads to Autonomys)

## Step 2: Set Up API Key (5 minutes)

### Get Your API Key

1. Visit: https://ai3.storage
2. Sign in (Google/Discord/GitHub)
3. Generate API key
4. **Copy the key** (you'll need it in the next step)

### Store Securely (Choose One)

**Option A: 1Password (Recommended for Production)**

```bash
# Install 1Password CLI
winget install 1Password.CLI

# Sign in
op signin

# Store API key
op item create --category=password --title="Autonomys API Key" credential=YOUR_API_KEY_HERE
```

**Benefits:** Secure, centralized, team-friendly, auditable
**See:** [1PASSWORD_SETUP.md](1PASSWORD_SETUP.md) for detailed guide

**Option B: Environment Variable (For Development/Testing)**

```bash
# Windows Command Prompt
set AUTONOMYS_API_KEY=your-key-here

# Windows PowerShell
$env:AUTONOMYS_API_KEY="your-key-here"

# Or add permanently via System Properties > Environment Variables
```

**Note:** The code automatically checks 1Password first, then falls back to environment variables.

## Step 3: Test the Integration (2 minutes)

```bash
python test_autonomys_integration.py
```

Follow the prompts:
- Enter your miner code (e.g., `IRM`)
- Enter your H3 hex ID (e.g., `871f90151ffffff`)
- Enter your installation ID (optional)

This will:
1. ✓ Check all dependencies are installed
2. ✓ Process today's CSV data into hourly parquet files
3. ✓ Show you the generated folder structure
4. ✓ Optionally upload to Autonomys Auto Drive

## Step 4: Review Generated Files

Check the output directory:

```
C:\ProgramData\FryNetworks\autonomys-measurements\
├── metadata.json
└── res-7\
    └── 871f90151ffffff\
        ├── manifest.json
        └── radiation\
            └── hourly\
                ├── 2026-01-20.parquet
                └── 2026-01-20.meta.json
```

## Step 5: Set Up Daily Processing (10 minutes)

### Option A: Add to Existing Measurement Daemon

If you have a measurement daemon running, add this to your daily job:

```python
# Add to your existing measurement daemon
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

# In your daily job (after midnight)
def daily_job():
    # ... your existing code ...

    # Add Autonomys processing
    try:
        results = process_yesterday_to_autonomys(
            miner_code=MINER_CODE,
            hex_id=YOUR_HEX_ID,  # Get from config
            install_id=YOUR_INSTALL_ID,  # Get from config
            upload_to_cloud=True
        )
        log.info("Autonomys processing: %s", results)
    except Exception as e:
        log.error("Autonomys processing failed: %s", e)
```

### Option B: Create Separate Scheduled Task

Create a file `autonomys_daily_upload.py`:

```python
#!/usr/bin/env python3
"""Daily job to upload yesterday's data to Autonomys."""

import sys
import logging
from pathlib import Path

# Setup paths
sys.path.insert(0, str(Path(__file__).parent))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('C:/ProgramData/FryNetworks/autonomys_upload.log'),
        logging.StreamHandler()
    ]
)

log = logging.getLogger(__name__)

# Import after path setup
from config_profile import MINER_CODE
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

# Configuration (adjust these!)
HEX_ID = "871f90151ffffff"  # Your H3 hex ID
INSTALL_ID = "550e8400-e29b-41d4-a716-446655440000"  # Your installation UUID

def main():
    """Run daily Autonomys upload."""
    log.info("Starting daily Autonomys upload for miner %s", MINER_CODE)

    try:
        results = process_yesterday_to_autonomys(
            miner_code=MINER_CODE,
            hex_id=HEX_ID,
            install_id=INSTALL_ID,
            upload_to_cloud=True
        )

        log.info("Upload complete: %s", results)

        # Check for failures
        failed = [k for k, v in results.items() if not v]
        if failed:
            log.warning("Failed measurements: %s", failed)
            return 1

        return 0

    except Exception as e:
        log.error("Upload failed: %s", e, exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

Then create a scheduled task (Windows):

```bash
schtasks /create /tn "AutonomysUpload" ^
  /tr "python C:\Users\jimbo\Documents\GitHub\DevTesting\HardwarePoC\autonomys_daily_upload.py" ^
  /sc daily /st 00:30 /ru SYSTEM
```

Or use Task Scheduler GUI:
1. Open Task Scheduler
2. Create Basic Task
3. Name: "Autonomys Upload"
4. Trigger: Daily at 00:30
5. Action: Start a program
6. Program: `python`
7. Arguments: `C:\Users\jimbo\Documents\GitHub\DevTesting\HardwarePoC\autonomys_daily_upload.py`
8. Start in: `C:\Users\jimbo\Documents\GitHub\DevTesting\HardwarePoC`

## What Happens Now?

### Dual-Write System

Your measurements now flow through two paths:

```
Measurement Collection (every 10 minutes)
         ↓
    Write to CSV ← (existing)
         ↓
    ┌────┴────┐
    ↓         ↓
MongoDB    (wait until next day)
Upload         ↓
(existing)  Daily Job (00:30)
         ↓
    Autonomys Processing:
    - Read yesterday's CSV
    - Aggregate to hourly
    - Write parquet files
    - Upload to Auto Drive
```

### Storage Costs

**MongoDB (current):**
- ~$X/month for raw data storage
- Ongoing costs as data grows

**Autonomys (new):**
- One-time upload cost (minimal)
- Permanent decentralized storage
- Can sell data to offset costs
- Can eventually reduce/eliminate MongoDB

### Data Monetization

Your data is now structured for sale:

**Pricing Example (Resolution 7 hex):**
- Hourly bandwidth data: $50/month per hex
- Daily radiation data: $15/month × 1.5 = $22.50/month per hex
- Multi-hex bundles: Volume discounts available

## Backfill Historical Data (Optional)

If you want to upload existing historical data:

```python
from measurements.autonomys_orchestrator import backfill_autonomys_data

# Backfill last 30 days
from datetime import datetime, timedelta

end_date = datetime.now()
start_date = end_date - timedelta(days=30)

results = backfill_autonomys_data(
    miner_code="IRM",
    hex_id="871f90151ffffff",
    start_date=start_date.strftime("%Y%m%d"),
    end_date=end_date.strftime("%Y%m%d"),
    upload_to_cloud=True
)

print(f"Backfilled {results} days")
```

**Warning:** Backfilling will upload many files. Start with a small date range to test!

## Monitoring

Check the logs:

```bash
# If using scheduled task with logging
type C:\ProgramData\FryNetworks\autonomys_upload.log

# Check last 20 lines
powershell Get-Content C:\ProgramData\FryNetworks\autonomys_upload.log -Tail 20
```

Check local storage:

```bash
dir C:\ProgramData\FryNetworks\autonomys-measurements /s
```

Check uploaded files (requires Python):

```python
from measurements.autonomys_uploader import get_uploader

uploader = get_uploader()
files = uploader.list_objects("res-7/871f90151ffffff")
print(f"Uploaded {len(files)} files")
for f in files[:10]:  # Show first 10
    print(f"  {f}")
```

## Troubleshooting

### "No module named 'h3'"
→ Run: `pip install -r requirements-autonomys.txt`

### "No data found for bandwidth"
→ Check CSV files exist in `C:\ProgramData\FryNetworks\miner-{CODE}\measurements\`
→ Format: `bandwidth_real_20260120.csv`

### "AUTONOMYS_API_KEY not set"
→ Set environment variable (see Step 2)
→ Restart your terminal/scheduled task

### "Failed to upload to Autonomys"
→ Check API key is valid at https://ai3.storage
→ Check internet connectivity
→ Check Windows Firewall isn't blocking Python

## Next Steps

1. ✓ Test locally with today's data
2. ✓ Verify files are generated correctly
3. ✓ Test upload to Autonomys with small dataset
4. ✓ Set up daily automated job
5. ✓ Monitor for a week to ensure stable
6. Consider backfilling historical data
7. Plan data monetization strategy
8. Eventually reduce MongoDB usage to save costs

## Questions?

- Technical docs: [AUTONOMYS_INTEGRATION.md](AUTONOMYS_INTEGRATION.md)
- Autonomys docs: https://develop.autonomys.xyz/sdk/auto-drive
- H3 hexagons: https://h3geo.org/

## Summary

**What You Built:**
- ✓ Hourly aggregation of 10-minute measurements
- ✓ Efficient parquet file storage
- ✓ Hierarchical folder structure for monetization
- ✓ S3-compatible upload to Autonomys Auto Drive
- ✓ Dual-write system (MongoDB + Autonomys)

**Time to Deploy:** ~20 minutes
**Monthly Savings Potential:** Reduce MongoDB costs
**Revenue Potential:** Sell data to researchers/companies
