# Miner S3 Integration - Summary

## What Was Created

You now have a complete miner-side implementation for uploading original measurement data to S3 for centralized API access.

---

## Files Created

### 1. Core S3 Uploader
**[measurements/s3_uploader.py](../measurements/s3_uploader.py)**
- `S3Uploader` class - Handles all S3 operations
- `get_s3_uploader()` - Factory function (reads config)
- Features:
  - Upload single files
  - Upload entire directories
  - Check if files exist (skip duplicates)
  - List uploaded files
  - Upload miner metadata
  - Server-side encryption (AES256)
  - Custom metadata tagging

### 2. Daily Backup Script
**[measurements/daily_s3_backup.py](../measurements/daily_s3_backup.py)**
- Automated daily uploads
- Backfill historical data
- Update miner metadata
- Command-line interface
- Designed to run via Task Scheduler/cron

### 3. Configuration Template
**[s3_config.template.json](../s3_config.template.json)**
- JSON config file template
- Copy to `s3_config.json` and fill in your credentials

### 4. Setup Guide
**[docs/S3_MINER_SETUP.md](S3_MINER_SETUP.md)**
- Complete setup instructions
- AWS configuration
- Scheduling guidelines
- Troubleshooting
- Security best practices

### 5. Example Script
**[example_s3_upload.py](../example_s3_upload.py)**
- Working examples
- Manual uploads
- Config-based uploads
- Checking upload status

---

## Quick Start

### 1. Install Dependencies

```bash
pip install boto3
```

### 2. Configure AWS Credentials

**Option A: Config File**

Copy template and edit:
```bash
cp s3_config.template.json s3_config.json
# Edit s3_config.json with your credentials
```

**Option B: Environment Variables**

```bash
export S3_UPLOAD_ENABLED=true
export S3_BUCKET_NAME=frynetworks-measurements
export S3_MINER_ID=miner-001
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
```

### 3. Test Upload

```bash
# Upload yesterday's data
python measurements/daily_s3_backup.py --miner-code IDM

# Backfill last 7 days
python measurements/daily_s3_backup.py \
  --backfill \
  --start-date 2026-01-15 \
  --end-date 2026-01-21 \
  --miner-code IDM
```

### 4. Schedule Daily Uploads

**Windows Task Scheduler:**
- Run daily at 00:05
- Program: `python.exe`
- Arguments: `measurements\daily_s3_backup.py --miner-code IDM`

**Linux cron:**
```bash
5 0 * * * cd /opt/hardwarepoc && python3 measurements/daily_s3_backup.py --miner-code IDM >> /var/log/s3-backup.log 2>&1
```

---

## How It Works

### Data Flow

```
1. Miner collects data
   └─> C:\ProgramData\FryNetworks\measurements\decibel\hourly\2026-01-22.parquet
       (Original: res-7, full precision -41.77 dBFS)

2. Daily script runs (00:05)
   └─> Uploads to S3

3. S3 Storage
   └─> s3://frynetworks-measurements/original-data/miner-001/decibel/hourly/2026-01-22.parquet
       (Same file, full precision preserved)

4. API Server (separate repo)
   └─> Queries S3 when customer requests data
       Returns full-precision data to authorized customers
```

### Two Independent Systems

| System | Data | Upload To | Purpose | Script |
|--------|------|-----------|---------|--------|
| **Autonomys** | Redacted (res-5, -41.8) | IPFS/Autonomys | Free preview | `autonomys_orchestrator.py` |
| **S3** | Original (res-7, -41.77) | Private S3 | Paid API | `daily_s3_backup.py` |

Both run independently. No conflicts.

---

## S3 Storage Structure

```
s3://frynetworks-measurements/
└── original-data/
    ├── miner-001/
    │   ├── metadata.json              # Miner info
    │   ├── decibel/
    │   │   └── hourly/
    │   │       ├── 2026-01-20.parquet
    │   │       ├── 2026-01-21.parquet
    │   │       └── 2026-01-22.parquet
    │   └── bandwidth/
    │       └── hourly/
    │           └── 2026-01-20.parquet
    │
    ├── miner-002/
    │   └── ...
    │
    └── miner-003/
        └── ...
```

---

## Usage Examples

### Python API

```python
from measurements.s3_uploader import get_s3_uploader
from pathlib import Path

# Get uploader (reads config automatically)
uploader = get_s3_uploader()

# Upload single file
uploader.upload_file(
    local_path=Path("measurements/decibel/hourly/2026-01-22.parquet"),
    measurement_type="decibel",
    date_str="2026-01-22"
)

# Upload entire directory
uploader.upload_directory(
    local_dir=Path("measurements/decibel/hourly"),
    measurement_type="decibel"
)

# Check if file exists (skip re-uploads)
if not uploader.file_exists("decibel", "2026-01-22"):
    uploader.upload_file(...)

# List uploaded dates
dates = uploader.list_uploaded_files("decibel")
print(f"Uploaded: {dates[0]} to {dates[-1]}")
```

### Command Line

```bash
# Daily upload (run via scheduler)
python measurements/daily_s3_backup.py --miner-code IDM

# Backfill date range
python measurements/daily_s3_backup.py \
  --backfill \
  --start-date 2026-01-01 \
  --end-date 2026-01-21 \
  --miner-code IDM

# Update miner metadata
python measurements/daily_s3_backup.py \
  --update-metadata \
  --miner-code IDM \
  --hex-id 871f90151ffffff \
  --lat 40.712776 \
  --lon -74.005974
```

---

## Configuration Options

### Config File (`s3_config.json`)

```json
{
  "s3_upload": {
    "enabled": true,
    "bucket_name": "frynetworks-measurements",
    "miner_id": "miner-001",
    "aws_region": "us-east-1",
    "aws_access_key_id": "AKIA...",
    "aws_secret_access_key": "...",
    "prefix": "original-data"
  }
}
```

### Environment Variables

```bash
S3_UPLOAD_ENABLED=true
S3_BUCKET_NAME=frynetworks-measurements
S3_MINER_ID=miner-001
S3_AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
S3_PREFIX=original-data
```

### IAM Role (Most Secure)

For EC2 instances, attach IAM role - no credentials needed:

```bash
S3_UPLOAD_ENABLED=true
S3_BUCKET_NAME=frynetworks-measurements
S3_MINER_ID=miner-001
# Credentials from IAM role automatically
```

---

## Security Features

### Built-in Security

- ✅ **Server-side encryption** (AES256) - Data encrypted at rest
- ✅ **Secure credentials** - Config file or env vars (not hardcoded)
- ✅ **IAM role support** - No credentials on disk (EC2)
- ✅ **Minimal permissions** - Only S3 bucket access required
- ✅ **Metadata tagging** - Track uploads with custom metadata
- ✅ **Duplicate detection** - Skip files already uploaded

### Best Practices

1. **Use IAM roles** for EC2 instances (no credentials on disk)
2. **Rotate access keys** every 90 days
3. **Enable S3 versioning** for data recovery
4. **Enable S3 access logs** for audit trail
5. **Use bucket policies** to restrict access
6. **Encrypt config files** (Windows DPAPI, Linux file permissions)

---

## Costs (100 miners)

### S3 Storage
- ~6 MB/month per miner
- 100 miners × 6 MB = 600 MB/month
- $0.023/GB = **$0.014/month** ($0.17/year)

### PUT Requests
- 1 file per day per miner
- 100 miners × 30 days × $0.005/1000 = **$0.015/month**

### Data Transfer
- Uploads: FREE (IN traffic is free)
- Downloads (via API): $0.09/GB
- 600 MB OUT/month = **$0.05/month**

**Total: ~$0.08/month** for 100 miners (~$1/year)

---

## Next Steps

### For Miner Setup

1. ✅ Install boto3
2. ✅ Configure AWS credentials
3. ✅ Test manual upload
4. ✅ Schedule daily uploads
5. ✅ Backfill historical data
6. ✅ Monitor logs

### For API Server (Separate Repo)

1. ⏳ Create FastAPI server
2. ⏳ Query S3 for customer data
3. ⏳ Implement authentication (API keys)
4. ⏳ Add rate limiting
5. ⏳ Deploy to production

API server will:
- Read from same S3 bucket
- Serve data to authenticated customers
- Handle Professional/Enterprise tiers
- Provide REST and WebSocket APIs

---

## Integration with Existing Code

### No Changes Needed

Your existing measurement collection continues as before:

```python
# Existing code - still works
from measurements.csv_writer import append_row

append_row("decibel", "IDM", {
    "timestamp": "2026-01-22T14:00:00Z",
    "dbfs": -41.77
}, dataset="real")

# Stored locally at:
# C:\ProgramData\FryNetworks\measurements\decibel\hourly\2026-01-22.parquet
```

### Separate Upload Process

S3 uploads run independently via scheduled task:

```bash
# Run once per day (scheduled)
python measurements/daily_s3_backup.py --miner-code IDM
```

### Both Systems Run in Parallel

```
measurements\           → Local storage (original)
    ↓
autonomys_orchestrator.py → Autonomys upload (redacted)
    ↓
daily_s3_backup.py      → S3 upload (original)
```

No conflicts, no code changes needed.

---

## Troubleshooting

### Common Issues

**"boto3 not installed"**
```bash
pip install boto3
```

**"Unable to locate credentials"**
- Check `s3_config.json` exists
- Or set environment variables
- Or configure `aws configure`

**"Access Denied"**
- Check IAM policy allows S3 operations
- Verify bucket name is correct
- Check bucket policy

**"Files not uploading"**
- Check miner code matches data type
- Verify files exist in local storage
- Check network connectivity
- Review logs for errors

### Debug Mode

```bash
# Run with verbose logging
python measurements/daily_s3_backup.py --miner-code IDM 2>&1 | tee upload.log
```

---

## Summary

### What You Have Now

✅ Complete miner-side S3 upload implementation
✅ Automated daily backup script
✅ Backfill capability for historical data
✅ Metadata management
✅ Configuration system (file or env vars)
✅ Security features (encryption, IAM)
✅ Documentation and examples

### What You Need to Build (Separate Repo)

⏳ API server to serve data to customers
⏳ Authentication system (API keys)
⏳ Customer database
⏳ Billing integration
⏳ Rate limiting
⏳ Monitoring/analytics

### The Complete Picture

```
[Miner] → Local Storage (res-7, full precision)
           ↓
           ├─> Autonomys (redacted, public, free)
           └─> S3 (original, private)
                 ↓
              [API Server] → Authenticated customers (paid)
```

**Miner part: ✅ COMPLETE**
**API server: ⏳ Next phase (separate repo)**
