# FryNetworks Hardware Miner Service

Automated monitoring service for FryNetworks miners that reports Proof of Coverage (PoC), Proof of Location (PoL), and hardware status metrics.

## Overview

This service runs on miner hardware to:
- Monitor online/offline status every 10 minutes (configurable)
- Track daily uptime percentages (24-hour rolling window)
- Verify location consistency (IP country vs registered H3 hex location)
- Report MAC address matching with registered hardware
- Validate software version compliance
- Maintain local encrypted cache with tamper detection
- Enforce single-active-installation per miner key globally

---

## Supported Miner Types

| Code | Description |
|------|-------------|
| **BM** | Bandwidth Miner |
| **IDM** | Indoor Decibel Miner |
| **ODM** | Outdoor Decibel Miner |
| **ISM** | Indoor Satellite Miner |
| **OSM** | Outdoor Satellite Miner |
| **RDN** | Reward Decentralization Node |
| **SDN** | Storage Decentralization Node |
| **SVN** | Storage Validator Node |
| **AEM** | AI Edge Miner |
| **IRM** | Indoor Radiation Miner |

---

## Security Architecture

### Two-Layer Security Model

**1. Build-Time Security (API Credentials)**
- API bearer token embedded in executable via 1Password CLI
- Local signing keys for cache integrity
- Double-layer encryption (ChaCha20 + Fernet)
- Zero plaintext credentials in source code

**2. Deploy-Time Security (Miner Keys)**
- Miner keys stored in encrypted `miner_config.enc` file
- Created by installer per deployment
- Fernet encryption with PBKDF2 key derivation
- Prevents casual tampering and key visibility

### Why Two Separate Systems?

- **API credentials** are shared across all miners → embed at build time
- **Miner keys** are unique per deployment → encrypt at install time
- This separation allows one generic executable for all deployments

---

## Building the Service

### Prerequisites

**Required:**
- Python 3.8+ with pip
- 1Password CLI (`op`) installed and authenticated
- PyInstaller and dependencies
- Access to `op://VPS/Hardware_API/API_BEARER_TOKEN` in 1Password

**Optional:**
- Code signing certificate (`.pfx` file) for production builds
- `signtool.exe` for Windows signing
- GeoLite2 country database for location verification

### Build Command (Linux)

```bash
# Native x86_64 build
./build_PoC_linux.sh ISM 2.0.0

# Cross-build for aarch64 (ARM64) via Docker
ARCH=aarch64 ./build_PoC_linux.sh ISM 2.0.0
```

The aarch64 build requires Docker with `--platform linux/arm64` support (Docker Desktop or `binfmt_misc` + QEMU on Linux). Output: `release/ISM/FRY_PoC_ISM_v2.0.0_linux_aarch64`.

### Build Command (Windows)

```powershell
# Basic build for ISM miner v5.5.4
.\build_windows.ps1 -Code ISM -Version 5.5.4

# With signing
.\build_windows.ps1 -Code ISM -Version 5.5.4 -Sign -SignPfxPath .\cert.pfx -SignPfxPassword (Read-Host -AsSecureString)

# With 1Password-based signing
.\build_windows.ps1 -Code ISM -Version 5.5.4 -Sign -Use1PasswordPfx -OPRefPfxPassword "op://path/to/pfx/password"
```

### What Gets Built

The build process produces:
- **Service executable**: `FRY_PoC_{CODE}_v{VERSION}.exe`
  - Example: `FRY_PoC_ISM_v5.5.4.exe`
  - Contains embedded API credentials (from 1Password)
  - Includes version resource metadata
  - Optionally code-signed
  - Generic (works for all deployments of that miner type)

**Output location:** `release\{CODE}\FRY_PoC_{CODE}_v{VERSION}.exe`

### Build Script Features

`build_windows.ps1` parameters:
- `-Code` (required) - Miner code (BM, ISM, etc.)
- `-Version` (required) - Semantic version (e.g., 5.5.4)
- `-Use1Password` (default: true) - Embed credentials from 1Password
- `-Sign` - Enable code signing
- `-SignPfxPath` - Path to code signing certificate
- `-SignPfxPassword` - Certificate password (SecureString)
- `-Use1PasswordPfx` - Retrieve PFX password from 1Password
- `-IntervalSeconds` (default: 600) - Status check interval
- `-CompanyName` (default: "Fry Networks LLC") - Company metadata

---

## Installer Integration

### What the Installer Must Do

**1. Include Required Tools**
```
installer_package/
├── FRY_PoC_{CODE}_v{VERSION}.exe    # Built executable (from release/)
├── create_miner_config.py           # Miner key encryption utility
├── create_install_config.py         # Install ID encryption utility (NEW)
└── tools/                           # Dependencies for utilities
```

**2. Acquire Global Installation Lease (CRITICAL - NEW)**

The installer **MUST** acquire the global lease for the miner_key **BEFORE** starting the service. This ensures only one active installation per miner.

```python
# Pseudo-code for installer lease acquisition
import uuid
from datetime import datetime, timedelta, timezone

# Generate unique install_id
install_id = str(uuid.uuid4())

# Connect to API (installer needs API credentials)
client = connect_mongo(api_base_url, api_token)

# Verify miner_key exists
if not client["main"]["devices"].find_one({"miner_key": miner_key}):
    raise InstallError(f"Miner key {miner_key} not found in database")

# Check for existing active lease
existing_lease = client["PoC"]["installations"].find_one({
    "_id": f"lease:{miner_key}",
    "lease_expires_at": {"$gt": datetime.now(timezone.utc)}
})

if existing_lease:
    # Prompt user: abort, or force takeover?
    if not user_confirms_takeover():
        raise InstallError("Another installation is active")

# Acquire lease (atomic upsert)
lease_seconds = 900  # 15 minutes
expiry = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)

client["PoC"]["installations"].update_one(
    {"_id": f"lease:{miner_key}"},
    {"$set": {
        "miner_key": miner_key,
        "install_id": install_id,
        "lease_install_id": install_id,
        "lease_expires_at": expiry,
        "installed_at": datetime.now(timezone.utc),
        "hostname": platform.node()
    }},
    upsert=True
)

print(f"✅ Lease acquired for {miner_key} (install_id: {install_id})")
```

**3. Create Encrypted Configurations**

```bash
# Validate miner key format
python create_miner_config.py validate REDACTED_ROTATE_ME

# Create encrypted miner config
python create_miner_config.py create REDACTED_ROTATE_ME --output "C:\Program Files\FryNetworks\BM Miner\miner_config.enc"

# Create encrypted install config with the install_id from step 2 (NEW)
python create_install_config.py create --install-id "550e8400-e29b-41d4-a716-446655440000" --output "C:\Program Files\FryNetworks\BM Miner\install_config.enc"
```

**4. Deploy File Structure**

**Windows:**
```
C:\Program Files\FryNetworks\{MinerType} Miner\
├── FRY_PoC_{CODE}_v{VERSION}.exe     # Service executable
├── miner_config.enc                  # Encrypted miner key (installer creates)
└── install_config.enc                # Encrypted install_id (installer creates)

# Runtime data (auto-created by service)
C:\ProgramData\FryNetworks\miner-{CODE}\
├── status-YYYYMMDD.json              # Daily cache files
├── status-YYYYMMDD.lock              # Lock files
└── service.lock                      # Service lock
```

**Linux:**
```
/opt/frynetworks/{minertype}-miner/
├── FRY_PoC_{CODE}_v{VERSION}         # Service executable
├── miner_config.enc                  # Encrypted miner key (installer creates)
└── install_config.enc                # Encrypted install_id (installer creates)

# Runtime data (auto-created by service)
/var/lib/frynetworks/miner-{CODE}/
├── status-YYYYMMDD.json              # Daily cache files
└── service.lock                      # Service lock
```

**5. Verify Configuration (Optional)**
```bash
# Read back the encrypted miner config
python create_miner_config.py read "C:\Program Files\FryNetworks\BM Miner\miner_config.enc"
# Output: 📋 Miner Key: REDACTED_ROTATE_ME

# Read back the encrypted install config (NEW)
python create_install_config.py read "C:\Program Files\FryNetworks\BM Miner\install_config.enc"
# Output: Install ID: 550e8400-e29b-41d4-a716-446655440000
#         Hostname: PC-NAME
#         OS: Windows-10-10.0.19045-SP0
#         Created: 2024-01-15T10:30:00
```

### Miner Key Format

**Format:** `{CODE}-{32_ALPHANUMERIC_CHARS}`
- CODE must be one of: BM, IDM, ODM, ISM, OSM, RDN, SDN, SVN, AEM, IRM
- 32 characters must be uppercase A-Z and 0-9
- Example: `ISM-ABC123DEFG456HIJK789LMNOP012QRST`

---

## Service Behavior

### Startup Sequence

1. **Read encrypted miner config** (`miner_config.enc`)
2. **Read encrypted install config** (`install_config.enc`)
   - **REQUIRED** - service exits with code 3 if missing
3. **Load embedded API credentials** (decrypted from executable)
4. **Verify miner key exists** in backend database
5. **Verify installation lease ownership**
   - Checks lease exists for (miner_key, install_id) pair
   - Verifies lease not expired
   - **Does NOT acquire** - installer already acquired it
   - Exits with code 9 if verification fails
6. **Begin monitoring loop** every 10 minutes (default)

### Runtime Operations

**Every interval (default 10 minutes):**
- Check internet connectivity
- Update local cache with online/offline status
- Compute daily uptime percentage
- Report to backend API via MongoProxyClient
- Renew installation lease
- Refresh required software version

**Once per day:**
- Verify location (IP country vs registered H3 hex)
- Check MAC address match
- Finalize previous day's cache

**Continuous:**
- Maintain rolling 24-hour cache
- Sign cache files for tamper detection
- Monitor for concurrent installations (exits if detected)

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Normal exit |
| 1 | Fatal unhandled error |
| 2 | Invalid miner key format |
| 3 | Required config file not found (miner_config.enc or install_config.enc) |
| 4 | General check error |
| 6 | Miner key not found in main.devices |
| 7 | Software version outdated (--check-version) |
| 8 | Concurrent installation detected / lease conflict (deprecated) |
| 9 | Lease verification failed (installer must acquire lease first) |

---

## CLI Utilities

### Check Commands (For Installers)

**Verify miner key exists:**
```bash
FRY_PoC_ISM_v5.5.4.exe --check-key ISM-ABC123DEFG456HIJK789LMNOP012QRST
# Exit code: 0=exists, 3=not found, 2=bad format
```

**Check software version:**
```bash
FRY_PoC_ISM_v5.5.4.exe --check-version ISM-ABC123DEFG456HIJK789LMNOP012QRST
# Prints JSON: {"ok": true/false, "needed": "5.6.0", "installed": "5.5.4"}
# Exit code: 0=up-to-date, 7=outdated
```

**Check for concurrent installations:**
```bash
FRY_PoC_ISM_v5.5.4.exe --check-concurrency ISM-ABC123DEFG456HIJK789LMNOP012QRST
# Prints JSON with conflict details if another host is active
# Exit code: 0=clear, 8=conflict
```

**Debug lease information:**
```bash
FRY_PoC_ISM_v5.5.4.exe --lease-dump ISM-ABC123DEFG456HIJK789LMNOP012QRST
# Prints all lease records for this miner key
```

---

## Troubleshooting

### Common Issues

**"Miner key not found in miner_config.enc file"**
- The installer didn't create `miner_config.enc`
- File is corrupted or missing
- Solution: Use `create_miner_config.py` to recreate

**Exit code 8: Concurrent installation detected**
- Another machine is actively running this miner key
- The global lease is held by another installation
- Check with `--check-concurrency` to see conflict details
- Only one active installation per miner key is allowed globally

**Exit code 6: Miner key not found in main.devices**
- The miner key doesn't exist in the backend database
- Verify with `--check-key`

**"1Password embedding failed"**
- 1Password CLI not installed or not authenticated
- Run `op signin` before building
- Verify access to required secrets in 1Password

**"API connect failed"**
- Network connectivity issues
- API base URL incorrect (should be https://hardwareapi.frynetworks.com)
- Bearer token invalid or expired

### Viewing Logs

**Windows:**
- Service logs to stdout/stderr (capture via NSSM or Task Scheduler)
- Check data directory: `C:\ProgramData\FryNetworks\miner-{CODE}\`

**Linux:**
- Use `journalctl` if running as systemd service
- Check data directory: `/var/lib/frynetworks/miner-{CODE}/`

---

## Development

### Repository Structure

```
HardwarePoC/
├── miner_online_simple.py          # Main service code
├── config_profile.py               # Miner code and version constants
├── external_api.py                 # HTTP API client
├── mongo_api_proxy.py              # MongoDB proxy client
├── cache_integrity.py              # Local cache signing/verification
├── build_windows.ps1               # Windows build script
├── build_with_embedded_config.py   # Alternative Python build script
├── create_miner_config.py          # Miner config encryption utility
├── tools/
│   ├── make_encrypted_config.py    # API credential encryption
│   ├── make_profile.py             # Profile generation
│   └── GeoLite2-Country.mmdb       # GeoIP database (optional)
├── images/                         # Icons for different miner types
└── docs/
    ├── BUILD_GUIDE.md              # Detailed build instructions
    └── ENCRYPTED_MINER_CONFIG_SOLUTION.md  # Security architecture
```

### Key Dependencies

**Python packages:**
- `requests` - HTTP client
- `cryptography` - Encryption (Fernet, ChaCha20)
- `h3` - H3 geospatial indexing
- `shapely` - Polygon geometry
- `geoip2` - IP geolocation
- `psutil` - System information
- `pyinstaller` - Executable packaging

**External tools:**
- 1Password CLI (`op`) - Required for builds
- `signtool.exe` - Optional for code signing
- NSSM - Optional for Windows service management (provided by installer)

---

## Manual Deployment (Without Installer)

If deploying manually without an installer, you must replicate all installer steps:

### Step 1: Create Encrypted Miner Config
```bash
python create_miner_config.py create REDACTED_ROTATE_ME --output miner_config.enc
```

### Step 2: Acquire Installation Lease

**CRITICAL:** You must acquire the global lease before the service starts.

```python
# acquire_lease.py (example script)
import uuid
import sys
from datetime import datetime, timedelta, timezone
from mongo_api_proxy import MongoProxyClient

# Configuration
API_BASE_URL = "https://your-api-url.com"
API_TOKEN = "your-api-token"  # From 1Password or secure source
miner_key = sys.argv[1]  # e.g., "BM-ABC123..."
install_id = str(uuid.uuid4())

# Connect and verify miner exists
client = MongoProxyClient(API_BASE_URL, API_TOKEN)
if not client["main"]["devices"].find_one({"miner_key": miner_key}):
    print(f"❌ Miner key {miner_key} not found in database")
    sys.exit(1)

# Check for existing lease
existing = client["PoC"]["installations"].find_one({
    "_id": f"lease:{miner_key}",
    "lease_expires_at": {"$gt": datetime.now(timezone.utc)}
})

if existing:
    response = input(f"⚠️  Active lease found (install_id: {existing.get('install_id')}). Force takeover? [y/N]: ")
    if response.lower() != 'y':
        print("Aborted")
        sys.exit(0)

# Acquire lease
expiry = datetime.now(timezone.utc) + timedelta(seconds=900)
client["PoC"]["installations"].update_one(
    {"_id": f"lease:{miner_key}"},
    {"$set": {
        "miner_key": miner_key,
        "install_id": install_id,
        "lease_install_id": install_id,
        "lease_expires_at": expiry,
        "installed_at": datetime.now(timezone.utc)
    }},
    upsert=True
)

print(f"✅ Lease acquired!")
print(f"📋 Install ID: {install_id}")
print(f"⚠️  Save this install_id - you need it for step 3")
```

### Step 3: Create Encrypted Install Config
```bash
# Use the install_id from step 2
python create_install_config.py create --install-id "550e8400-e29b-41d4-a716-446655440000" --output install_config.enc
```

### Step 4: Deploy Files and Run
```bash
# Place both config files next to the executable
FRY_PoC_BM_v5.5.5.exe
miner_config.enc
install_config.enc

# Run the service
.\FRY_PoC_BM_v5.5.5.exe
```

**Important:** Both `miner_config.enc` and `install_config.enc` are **REQUIRED**. If either is missing, the service will exit immediately with code 3. The install_id in `install_config.enc` must match the one used when acquiring the lease, or the service will exit with code 9 (lease verification failed).

---

## Security Notes

### What's Encrypted

**Build-time (embedded in executable):**
- API bearer token (from 1Password)
- Local signing key for cache integrity (from 1Password)
- GitHub token for updates (optional, from 1Password)

**Install-time (created by installer):**
- Miner key (in `miner_config.enc` - unique per deployment)
- Install ID (in `install_config.enc` - unique per installation) - NEW

### What's NOT Encrypted

- Miner type code (visible in filename and metadata)
- Version number (visible in filename and metadata)
- Daily cache files (signed but not encrypted - stored locally)
- Log output (may contain diagnostic information)

### Threat Model

**Protected against:**
- ✅ Casual inspection of credentials
- ✅ Simple file copying/renaming attacks
- ✅ Unauthorized modification of miner keys
- ✅ Cache tampering (detected via signatures)
- ✅ Multiple installations of same miner key

**NOT protected against:**
- ❌ Advanced reverse engineering of executable
- ❌ Memory dumps of running process
- ❌ Root/admin access to host system

**Why this is acceptable:**
- Miner keys are business identifiers, not cryptographic secrets
- Backend API validates all operations server-side
- Physical and OS-level security expected on miner hardware
- Cache signatures detect tampering for audit purposes

---

## License

Proprietary - Fry Networks LLC

---

## Support

For build issues or installer integration questions, refer to:
- `docs/BUILD_GUIDE.md` - Comprehensive build instructions
- `docs/ENCRYPTED_MINER_CONFIG_SOLUTION.md` - Security architecture details
- `docs/ISM_DropWireless_Runbook.md` - DropWireless ISM deployment guide (Linux aarch64)
