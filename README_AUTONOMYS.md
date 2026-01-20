# Autonomys Auto Drive Integration for FryNetworks

> **Decentralized storage and data monetization for measurement data**

This integration enables FryNetworks to store measurement data on Autonomys Auto Drive (decentralized storage) and monetize it through a structured, multi-tier pricing model.

## 🎯 Quick Start (5 minutes)

```bash
# 1. Install dependencies
pip install -r requirements-autonomys.txt

# 2. Store API key in 1Password (recommended)
op item create --category=password --title="Autonomys API Key" credential=YOUR_KEY

# OR use environment variable (dev/test)
set AUTONOMYS_API_KEY=your-key

# 3. Test it works
python test_autonomys_integration.py
```

**That's it!** The system is ready to process and upload measurements.

## 📚 Documentation

| Document | Purpose | Audience |
|----------|---------|----------|
| **[QUICK_START_AUTONOMYS.md](docs/QUICK_START_AUTONOMYS.md)** | Step-by-step deployment | Developers |
| **[1PASSWORD_SETUP.md](docs/1PASSWORD_SETUP.md)** | Secure API key management | DevOps/SRE |
| **[AUTONOMYS_INTEGRATION.md](docs/AUTONOMYS_INTEGRATION.md)** | Complete technical reference | Developers |
| **[AUTONOMYS_SUMMARY.md](AUTONOMYS_SUMMARY.md)** | Implementation overview | Project managers |
| **[ARCHITECTURE_DIAGRAM.txt](docs/ARCHITECTURE_DIAGRAM.txt)** | Visual architecture | All |

## 🏗️ What It Does

### Before (MongoDB Only)
```
Measurements → CSV → MongoDB → $$$$ monthly storage costs
```

### After (Dual-Write with Autonomys)
```
Measurements → CSV → MongoDB (existing, continues working)
                  ↓
                  Autonomys Auto Drive (new)
                  ├─ Decentralized storage
                  ├─ Lower costs
                  ├─ Permanent storage
                  └─ Monetizable data products
```

## ✨ Key Features

✅ **Zero Disruption** - Existing MongoDB uploads continue unchanged
✅ **Hourly Aggregation** - 10-min data → 60-min aggregates (6x reduction)
✅ **Efficient Storage** - Parquet format with compression (~10x smaller)
✅ **Secure Secrets** - 1Password integration for API key management
✅ **H3 Hexagons** - Geospatial indexing for location-based pricing
✅ **Monetization-Ready** - Structured for $15-150/month per hex
✅ **Well-Documented** - Comprehensive guides and examples

## 📦 What Was Built

### Core Modules (5 files)
- `autonomys_writer.py` - Folder structure & manifest management
- `autonomys_aggregator.py` - Hourly aggregation & parquet generation
- `autonomys_uploader.py` - S3-compatible uploads to Autonomys
- `autonomys_orchestrator.py` - Main integration orchestrator
- `secrets_manager.py` - 1Password integration for secure credentials

### Documentation (5 files)
- Complete technical reference (API docs, examples, troubleshooting)
- Quick start deployment guide
- 1Password security setup guide
- Implementation summary & roadmap
- Visual architecture diagrams

### Testing
- Interactive test script
- Dependency verification
- Local processing validation
- Upload testing

## 🚀 Deployment

### For Development/Testing

```bash
# Set API key as environment variable
set AUTONOMYS_API_KEY=your-key

# Test locally
python test_autonomys_integration.py
```

### For Production

```bash
# 1. Install 1Password CLI
winget install 1Password.CLI

# 2. Sign in
op signin

# 3. Store API key
op item create --category=password --title="Autonomys API Key" credential=YOUR_KEY

# 4. Test retrieval
python -m measurements.secrets_manager

# 5. Deploy service
# (API key automatically retrieved from 1Password)
```

See [1PASSWORD_SETUP.md](docs/1PASSWORD_SETUP.md) for detailed guide.

## 📊 Data Monetization

### Folder Structure

```
autonomys-measurements/
├── metadata.json                    # Network-wide catalog
└── res-7/                          # Resolution 7 hexagons (~5 km²)
    └── 871f90151ffffff/            # Individual hex cell
        ├── manifest.json           # Hex metadata
        ├── bandwidth/
        │   └── hourly/
        │       ├── 2026-01-20.parquet      # Efficient binary data
        │       └── 2026-01-20.meta.json    # Human-readable summary
        ├── radiation/
        ├── satellite/
        └── ...
```

### Pricing Model

```
Price = base_price × hex_resolution × time_granularity × measurement_type × duration

Example: Professional Bandwidth Package
$50/month = $50 (base) × 1.0 (res-7) × 1.0 (hourly) × 1.0 (bandwidth) × 1 month
```

### Revenue Potential

With **100 active hexes**:
- Conservative (10% sold): **$500/month**
- Moderate (30% sold): **$1,500/month**
- Optimistic (50% sold): **$2,500/month**

## 🔄 How It Works

### Daily Processing Flow

```
00:00 - Day ends, yesterday's data complete
00:30 - Daily job runs
    ↓
1. Read yesterday's CSV data (10-min intervals)
2. Aggregate to hourly (avg/min/max)
3. Generate parquet files (compressed)
4. Create metadata files (summaries)
5. Update manifests (coverage tracking)
6. Upload to Autonomys Auto Drive
    ↓
Done! Data now available for monetization
```

### API Key Security

```
Service starts
    ↓
get_uploader() called
    ↓
Check 1: API key parameter? → Use it
Check 2: 1Password item exists? → Retrieve from 1Password
Check 3: Environment variable? → Use environment variable
    ↓
API key retrieved securely
    ↓
Upload to Autonomys
```

## 🛠️ Integration with Existing Code

### Option 1: Add to Measurement Daemon

```python
from measurements.autonomys_orchestrator import process_yesterday_to_autonomys

# In your daily job (runs at 00:30)
def daily_autonomys_job():
    results = process_yesterday_to_autonomys(
        miner_code=MINER_CODE,
        hex_id=config['hex_id'],
        install_id=config['install_id'],
        upload_to_cloud=True
    )
    log.info("Autonomys upload: %s", results)
```

### Option 2: Scheduled Task

```bash
# Create Windows scheduled task
schtasks /create /tn "AutonomysUpload" ^
  /tr "python autonomys_daily_upload.py" ^
  /sc daily /st 00:30
```

See [QUICK_START_AUTONOMYS.md](docs/QUICK_START_AUTONOMYS.md) for complete examples.

## 🔐 Security

### Production (Recommended)
- ✅ **1Password CLI** - Secure, centralized credential management
- ✅ **No secrets in code** - API key retrieved at runtime
- ✅ **Audit trail** - Track who accesses secrets
- ✅ **Easy rotation** - Update key in 1Password, no code changes

### Development/Testing (Fallback)
- ✅ **Environment variables** - Quick setup for local testing
- ✅ **Automatic fallback** - Works without 1Password for dev

See [1PASSWORD_SETUP.md](docs/1PASSWORD_SETUP.md) for security best practices.

## 📈 Roadmap

### ✅ Phase 1: Complete (Now)
- Core integration modules
- 1Password security integration
- Hourly aggregation pipeline
- Parquet file generation
- S3-compatible uploads
- Comprehensive documentation

### 🎯 Phase 2: Deployment (Week 1)
- Test on one miner type
- Monitor daily uploads
- Verify data quality
- Roll out to all miner types

### 🚀 Phase 3: Monetization (Month 1-2)
- Backfill 3-6 months historical data
- Create data product catalog
- Build buyer portal/API
- Launch first data products

### 📊 Phase 4: Scale (Month 3+)
- Add weekly/monthly aggregations
- Support multiple hex resolutions (res-6, res-8)
- Analytics dashboard for buyers
- Data marketplace

## 🆘 Support

### Common Issues

**"No module named 'h3'"**
```bash
pip install -r requirements-autonomys.txt
```

**"API key not found"**
```bash
# Check 1Password
python -m measurements.secrets_manager

# Or set environment variable
set AUTONOMYS_API_KEY=your-key
```

**"No CSV data found"**
- Verify files exist: `C:\ProgramData\FryNetworks\miner-{CODE}\measurements\`
- Check naming: `{type}_real_{YYYYMMDD}.csv`
- Ensure files contain data

See [AUTONOMYS_INTEGRATION.md](docs/AUTONOMYS_INTEGRATION.md) for complete troubleshooting guide.

### Get Help

- **Technical docs**: [AUTONOMYS_INTEGRATION.md](docs/AUTONOMYS_INTEGRATION.md)
- **Quick start**: [QUICK_START_AUTONOMYS.md](docs/QUICK_START_AUTONOMYS.md)
- **Security setup**: [1PASSWORD_SETUP.md](docs/1PASSWORD_SETUP.md)
- **Autonomys docs**: https://develop.autonomys.xyz/sdk/auto-drive

## 📝 Dependencies

```txt
h3>=4.0.0              # H3 hexagon geospatial indexing
pandas>=2.0.0          # Data processing
pyarrow>=14.0.0        # Parquet file format
boto3>=1.34.0          # S3-compatible client
botocore>=1.34.0       # AWS SDK core
```

## 🔗 Resources

- **Autonomys Auto Drive**: https://develop.autonomys.xyz/sdk/auto-drive
- **API Key Dashboard**: https://ai3.storage
- **H3 Hexagons**: https://h3geo.org/
- **1Password CLI**: https://developer.1password.com/docs/cli/
- **Parquet Format**: https://parquet.apache.org/

## ✅ Status

**Implementation:** Complete ✅
**Documentation:** Complete ✅
**Security:** Production-ready with 1Password ✅
**Testing:** Interactive test script included ✅
**Ready for:** Deployment and testing 🚀

---

**Next Step:** Run `python test_autonomys_integration.py` to get started!
