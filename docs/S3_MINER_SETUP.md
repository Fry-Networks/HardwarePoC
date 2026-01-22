# S3 Upload Setup for Miners

## Overview

This guide explains how to configure miners to upload original (unredacted) measurement data to S3 for centralized API access.

**Two separate upload systems:**
1. **Autonomys uploads** - Redacted data (res-5, rounded) → Public
2. **S3 uploads** - Original data (res-7, full precision) → Private

---

## Architecture

```
┌────────────────┐
│ Miner Machine  │
│                │
│ measurements\  │  Original data (local storage)
│   decibel\     │
│     hourly\    │
│       *.parquet│
└────────┬───────┘
         │
         │ Daily upload (full precision)
         ↓
┌────────────────┐
│   AWS S3       │
│                │
│ s3://bucket/   │
│ original-data/ │
│   miner-001/   │
│     decibel/   │
│       hourly/  │
│         *.parquet
└────────┬───────┘
         │
         │ API queries
         ↓
┌────────────────┐
│  API Server    │  (separate repo)
│                │
│ FastAPI +      │
│ PostgreSQL     │
└────────┬───────┘
         │
         │ Authenticated access
         ↓
┌────────────────┐
│   Customers    │  Professional/Enterprise tiers
└────────────────┘
```

---

## Prerequisites

### 1. Install boto3

```bash
pip install boto3
```

### 2. Create S3 Bucket

```bash
# Using AWS CLI
aws s3 mb s3://frynetworks-measurements --region us-east-1

# Enable versioning (optional but recommended)
aws s3api put-bucket-versioning \
  --bucket frynetworks-measurements \
  --versioning-configuration Status=Enabled

# Enable server-side encryption
aws s3api put-bucket-encryption \
  --bucket frynetworks-measurements \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'
```

### 3. Create IAM User or Role

**Option A: IAM User (for manual credentials)**

```bash
# Create user
aws iam create-user --user-name frynetworks-miner-uploader

# Create access key
aws iam create-access-key --user-name frynetworks-miner-uploader
# Save the AccessKeyId and SecretAccessKey
```

**Option B: IAM Role (for EC2 instances)**

```bash
# Attach role to EC2 instance (preferred for security)
# No need to store credentials on the machine
```

### 4. Create IAM Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:HeadObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::frynetworks-measurements/*",
        "arn:aws:s3:::frynetworks-measurements"
      ]
    }
  ]
}
```

Save as `miner-s3-policy.json`, then:

```bash
# Create policy
aws iam create-policy \
  --policy-name FryNetworksMinerS3Upload \
  --policy-document file://miner-s3-policy.json

# Attach to user (if using IAM user)
aws iam attach-user-policy \
  --user-name frynetworks-miner-uploader \
  --policy-arn arn:aws:iam::ACCOUNT_ID:policy/FryNetworksMinerS3Upload
```

---

## Configuration

### Method 1: Configuration File (Recommended)

Create `s3_config.json`:

```json
{
  "s3_upload": {
    "enabled": true,
    "bucket_name": "frynetworks-measurements",
    "miner_id": "miner-001",
    "aws_region": "us-east-1",
    "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "prefix": "original-data"
  }
}
```

**Security Note:** Protect this file!

```bash
# Windows
icacls s3_config.json /inheritance:r /grant:r "%USERNAME%:F"

# Linux
chmod 600 s3_config.json
```

### Method 2: Environment Variables

```bash
# Windows (PowerShell)
$env:S3_UPLOAD_ENABLED = "true"
$env:S3_BUCKET_NAME = "frynetworks-measurements"
$env:S3_MINER_ID = "miner-001"
$env:S3_AWS_REGION = "us-east-1"
$env:AWS_ACCESS_KEY_ID = "AKIA..."
$env:AWS_SECRET_ACCESS_KEY = "..."
$env:S3_PREFIX = "original-data"

# Linux
export S3_UPLOAD_ENABLED=true
export S3_BUCKET_NAME=frynetworks-measurements
export S3_MINER_ID=miner-001
export S3_AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export S3_PREFIX=original-data
```

### Method 3: IAM Role (Most Secure)

If running on EC2, attach an IAM role and only set:

```bash
export S3_UPLOAD_ENABLED=true
export S3_BUCKET_NAME=frynetworks-measurements
export S3_MINER_ID=miner-001
export S3_AWS_REGION=us-east-1
# No credentials needed - uses IAM role
```

---

## Usage

### Daily Upload (Automatic)

Run this once per day to upload yesterday's data:

```bash
python measurements/daily_s3_backup.py --miner-code IDM
```

**Output:**
```
2026-01-22 00:05:00 - INFO - Uploading measurements for 2026-01-21
2026-01-22 00:05:01 - INFO - Uploading decibel/2026-01-21
2026-01-22 00:05:03 - INFO - ✓ decibel/2026-01-21 uploaded successfully
2026-01-22 00:05:03 - INFO - Daily backup complete: 1/1 files uploaded
```

### Schedule with Windows Task Scheduler

1. Open Task Scheduler
2. Create Basic Task
3. **Trigger:** Daily at 00:05 (5 minutes after midnight)
4. **Action:** Start a program
5. **Program:** `python.exe`
6. **Arguments:**
   ```
   C:\Users\youruser\HardwarePoC\measurements\daily_s3_backup.py --miner-code IDM
   ```
7. **Start in:** `C:\Users\youruser\HardwarePoC`

### Schedule with Linux cron

```bash
# Run daily at 00:05
crontab -e

# Add line:
5 0 * * * cd /opt/hardwarepoc && python3 measurements/daily_s3_backup.py --miner-code IDM >> /var/log/s3-backup.log 2>&1
```

---

## Backfill Historical Data

Upload historical data (e.g., last 30 days):

```bash
python measurements/daily_s3_backup.py \
  --backfill \
  --start-date 2026-01-01 \
  --end-date 2026-01-21 \
  --miner-code IDM
```

**Output:**
```
2026-01-22 10:00:00 - INFO - Processing 2026-01-01
2026-01-22 10:00:01 - INFO - ✓ decibel/2026-01-01
2026-01-22 10:00:02 - INFO - Processing 2026-01-02
2026-01-22 10:00:03 - INFO - ✓ decibel/2026-01-02
...
2026-01-22 10:01:25 - INFO - Backfill complete: 21/21 files uploaded
```

---

## Update Miner Metadata

Upload miner metadata (location, types, etc.):

```bash
python measurements/daily_s3_backup.py \
  --update-metadata \
  --miner-code IDM \
  --hex-id 871f90151ffffff \
  --lat 40.712776 \
  --lon -74.005974
```

Creates `s3://bucket/original-data/miner-001/metadata.json`:

```json
{
  "miner_id": "miner-001",
  "miner_code": "IDM",
  "hex_id": "871f90151ffffff",
  "location": {
    "lat": 40.712776,
    "lon": -74.005974,
    "resolution": 7
  },
  "measurement_types": ["decibel"],
  "first_upload": "2026-01-01",
  "last_upload": "2026-01-21",
  "last_updated": "2026-01-22T10:05:00Z"
}
```

---

## Verify Uploads

### Check S3 via AWS CLI

```bash
# List all uploads for a miner
aws s3 ls s3://frynetworks-measurements/original-data/miner-001/ --recursive

# Output:
# original-data/miner-001/decibel/hourly/2026-01-20.parquet
# original-data/miner-001/decibel/hourly/2026-01-21.parquet
# original-data/miner-001/decibel/hourly/2026-01-22.parquet
# original-data/miner-001/metadata.json
```

### Verify via Python

```python
from measurements.s3_uploader import get_s3_uploader

uploader = get_s3_uploader()

# Check specific file
exists = uploader.file_exists("decibel", "2026-01-21")
print(f"File exists: {exists}")

# List all uploaded dates
dates = uploader.list_uploaded_files("decibel")
print(f"Uploaded dates: {dates}")
```

---

## S3 Storage Structure

```
s3://frynetworks-measurements/
└── original-data/
    ├── miner-001/
    │   ├── metadata.json
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
    │   ├── metadata.json
    │   └── decibel/
    │       └── hourly/
    │           └── 2026-01-20.parquet
    │
    └── miner-003/
        ├── metadata.json
        └── radiation/
            └── hourly/
                └── 2026-01-20.parquet
```

---

## Costs

### S3 Storage Costs (us-east-1)

- **Standard storage:** $0.023 per GB/month
- **Typical file size:** ~100-200 KB per day per measurement type
- **Monthly per miner:** ~6 MB/month = **$0.00014/month** (~$0.001/year)

**Example (100 miners):**
- Storage: 100 miners × 6 MB × $0.023/GB = **$0.014/month** ($0.17/year)
- PUT requests: 100 miners × 30 days × $0.005/1000 = **$0.015/month**
- **Total: ~$0.03/month** for 100 miners

### Data Transfer Costs

- **Uploads from miners:** FREE (data transfer IN is free)
- **API downloads:** $0.09/GB (data transfer OUT)
- With 100 miners × 6 MB/month = 600 MB/month OUT = **$0.05/month**

**Total estimated cost for 100 miners: $0.08/month** (~$1/year)

---

## Troubleshooting

### Error: boto3 not installed

```bash
pip install boto3
```

### Error: Unable to locate credentials

```bash
# Check environment variables
echo $AWS_ACCESS_KEY_ID
echo $AWS_SECRET_ACCESS_KEY

# Or use aws configure
aws configure
```

### Error: Access Denied

Check IAM policy allows:
- `s3:PutObject`
- `s3:GetObject`
- `s3:HeadObject`
- `s3:ListBucket`

### Files not uploading

Check logs:

```bash
python measurements/daily_s3_backup.py --miner-code IDM 2>&1 | tee upload.log
```

Common issues:
- Wrong miner code (no data for that type)
- Files don't exist in local storage
- S3 credentials expired
- Network connectivity issues

---

## Security Best Practices

### 1. Use IAM Roles (EC2/Server)

Don't store credentials on the machine:

```bash
# Attach IAM role to EC2 instance
aws ec2 associate-iam-instance-profile \
  --instance-id i-1234567890abcdef0 \
  --iam-instance-profile Name=FryNetworksMinerRole
```

### 2. Rotate Access Keys

```bash
# Create new key
aws iam create-access-key --user-name frynetworks-miner-uploader

# Update config
# Delete old key
aws iam delete-access-key --user-name frynetworks-miner-uploader --access-key-id OLD_KEY_ID
```

### 3. Use S3 Bucket Policies

Restrict access to specific prefixes:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::ACCOUNT_ID:user/miner-uploader"},
    "Action": ["s3:PutObject", "s3:GetObject"],
    "Resource": "arn:aws:s3:::frynetworks-measurements/original-data/*"
  }]
}
```

### 4. Enable S3 Access Logs

```bash
aws s3api put-bucket-logging \
  --bucket frynetworks-measurements \
  --bucket-logging-status '{
    "LoggingEnabled": {
      "TargetBucket": "frynetworks-logs",
      "TargetPrefix": "s3-access-logs/"
    }
  }'
```

### 5. Encrypt Configuration Files

```bash
# Use AWS Secrets Manager
aws secretsmanager create-secret \
  --name frynetworks/miner-001/s3-config \
  --secret-string file://s3_config.json

# Or use encrypted environment variables
```

---

## Summary

### Setup Checklist

- [ ] Install boto3: `pip install boto3`
- [ ] Create S3 bucket
- [ ] Create IAM user/role with S3 permissions
- [ ] Configure credentials (config file or env vars)
- [ ] Test upload: `python measurements/daily_s3_backup.py --miner-code IDM`
- [ ] Schedule daily uploads (Task Scheduler or cron)
- [ ] Backfill historical data (optional)
- [ ] Update miner metadata

### Daily Workflow

```
1. Miner collects data → measurements\decibel\hourly\YYYY-MM-DD.parquet (res-7, full precision)
2. Daily backup script runs at 00:05
3. Uploads yesterday's file to S3
4. API server queries S3 when customer requests data
5. Customer gets full-precision data via API
```

### Two Upload Systems

| System | Data | Destination | Purpose |
|--------|------|-------------|---------|
| **Autonomys** | Redacted (res-5, rounded) | Public IPFS network | Lead generation (free) |
| **S3** | Original (res-7, full precision) | Private S3 bucket | Paid API access |

Both systems run independently. Autonomys uploads happen via `autonomys_orchestrator.py`, S3 uploads happen via `daily_s3_backup.py`.
