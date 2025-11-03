# Building FryNetworks Miner Services with Embedded Credentials

This guide explains how to build any FryNetworks miner service with embedded, encrypted API credentials for secure deployment.

## Supported Miner Types
- **BM** = Bandwidth Miner
- **IDM** = Indoor Decibel Miner  
- **ODM** = Outdoor Decibel Miner
- **ISM** = Indoor Satellite Miner
- **OSM** = Outdoor Satellite Miner
- **RDN** = Reward Decentralization Node
- **SDN** = Storage Decentralization Node
- **SVN** = Storage Validator Node
- **AEM** = AI Edge Miner
- **IRM** = Indoor Radiation Miner

## Overview

The service uses **1Password-only** credential management:

1. **Build Time**: API token retrieved from 1Password using CLI
2. **Runtime**: Token encrypted and embedded in the binary
3. **Security**: No plaintext credentials in any files

## Building for Production

### Step 1: Create Production Config

Create a `production_config.json` file with 1Password reference:

```json
{
  "api_base_url": "https://hardwareapi.frynetworks.com",
  "api_token": "op://VPS/Hardware_API/API_BEARER_TOKEN",
  "interval_seconds": 600,
  "lease_seconds": 900,
  "api_timeout": 10.0,
  "local_signing_key_hex": "your_64_char_hex_key_for_cache_integrity"
}
```

**1Password Setup:**
1. Install 1Password CLI: `brew install 1password-cli` (or download from 1password.com)
2. Authenticate: `op signin`
3. Verify access: `op read "op://VPS/Hardware_API/API_BEARER_TOKEN"`

### Step 2: Build with Embedded Config

```bash
python build_with_embedded_config.py production_config.json ./dist/
```

This will:
- ✅ Encrypt your credentials using double-layer encryption
- ✅ Embed them directly into the Python source code
- ✅ Copy all dependencies to the output directory
- ✅ Create a secure build ready for compilation

### Step 3: Compile to Executable

The executable name format is: `FRY_{MINER_CODE}_v{VERSION}`

**Examples for different miner types:**
```bash
cd dist/
pip install pyinstaller

# For Indoor Satellite Miner (ISM)
pyinstaller --onefile --name FRY_ISM_v5.5.4 miner_online_simple.py

# For Bandwidth Miner (BM)  
pyinstaller --onefile --name FRY_BM_v5.5.4 miner_online_simple.py

# For AI Edge Miner (AEM)
pyinstaller --onefile --name FRY_AEM_v5.5.4 miner_online_simple.py
```

Or for Windows with icon:
```bash
pyinstaller --onefile --windowed --icon=icon.ico --name FRY_ISM_v5.5.4 miner_online_simple.py
```

## Security Features

### Double-Layer Encryption
1. **First Layer**: Config is encrypted with Fernet (AES 128 in CBC mode)
2. **Second Layer**: Fernet key is encrypted with ChaCha20
3. **Result**: Even if someone extracts the encrypted blobs, they need both layers

### No External Config Files
- ✅ No `config.json` file needed in production
- ✅ No environment variables required
- ✅ Credentials cannot be easily extracted or modified
- ✅ Self-contained executable

## What the Installer Needs to Provide

After building with embedded credentials, the installer needs to provide the miner key using **encrypted configuration**:

### Encrypted Miner Configuration (Recommended)
Create an encrypted configuration file containing the miner key:

```bash
# Create encrypted miner config for specific deployment
# Format: {MINER_CODE}-{32_CHAR_KEY}
python create_miner_config.py create ISM-ABC123DEFG456HIJK789LMNOP012QRST
```

**This creates:** `miner_config.enc` - An encrypted file containing the miner key

**Advantages:**
- 🔒 **Fully Encrypted** - Miner key is completely hidden from view
- 📦 **Branded Executable** - FRY_{MINER_CODE}_v{VERSION} for specific miner type
- ✅ **Secure** - No visible miner keys anywhere  
- 🚫 **Tamper-Proof** - Encrypted file prevents easy modification
- 🎯 **Installer-Friendly** - Simple command-line tool

### Fallback: minerkey.txt File
If no encrypted config is found, the service will look for:

1. **`minerkey.txt`** - Plain text miner key (e.g., `ISM-ABC123...`)

### That's it!
- ❌ No `config.json` needed
- ❌ No API tokens to manage  
- ❌ No visible miner keys
- ✅ One branded miner executable per miner type for all deployments

## File Locations

The installer should place files as follows:

**Windows Examples:**
```
# Indoor Satellite Miner (ISM)
C:\Program Files\FryNetworks\ISM Miner\
├── FRY_ISM_v5.5.4.exe         # ISM Branded Miner executable (versioned)
├── miner_config.enc           # Encrypted miner configuration (installer-created)
└── minerkey.txt (optional)    # Fallback if encrypted config not used

# Bandwidth Miner (BM)
C:\Program Files\FryNetworks\BM Miner\
├── FRY_BM_v5.5.4.exe          # BM Branded Miner executable (versioned)
├── miner_config.enc           # Encrypted miner configuration (installer-created)
└── minerkey.txt (optional)    # Fallback if encrypted config not used

# Runtime data directories (auto-created by service)
C:\ProgramData\FryNetworks\miner-ISM\
C:\ProgramData\FryNetworks\miner-BM\
C:\ProgramData\FryNetworks\miner-AEM\
```

**Linux Examples:**
```
# Indoor Satellite Miner (ISM)
/opt/frynetworks/ism-miner/
├── FRY_ISM_v5.5.4             # ISM Branded Miner executable (versioned)
├── miner_config.enc          # Encrypted miner configuration (installer-created)  
└── minerkey.txt (optional)   # Fallback if encrypted config not used

# AI Edge Miner (AEM)
/opt/frynetworks/aem-miner/
├── FRY_AEM_v5.5.4             # AEM Branded Miner executable (versioned)
├── miner_config.enc          # Encrypted miner configuration (installer-created)  
└── minerkey.txt (optional)   # Fallback if encrypted config not used

# Runtime data directories (auto-created by service)
/var/lib/frynetworks/miner-ISM/
/var/lib/frynetworks/miner-AEM/
/var/lib/frynetworks/miner-BM/
```

## 1Password Requirements

**Mandatory for all builds:**

1. ✅ 1Password CLI installed and configured
2. ✅ Authenticated session: `op signin`
3. ✅ Access to `op://VPS/Hardware_API/API_BEARER_TOKEN`
4. ❌ No fallback mechanisms available

## Verification

To verify your build is secure:

1. ✅ Search the executable for your API token - it should NOT be found in plain text
2. ✅ The executable should run without requiring any config files except `minerkey.txt`
3. ✅ Check that encrypted blobs are present but unreadable

## Example Build Process

```bash
# 1. Setup 1Password CLI
op signin

# 2. Create production config
cat > production_config.json << EOF
{
  "api_base_url": "https://hardwareapi.frynetworks.com",
  "api_token": "op://VPS/Hardware_API/API_BEARER_TOKEN",
  "interval_seconds": 300,
  "lease_seconds": 1800
}
EOF

# 3. Build with embedded config (retrieves token from 1Password)
python build_with_embedded_config.py production_config.json ./release/

# 4. Compile (example for ISM miner)
cd release/
pyinstaller --onefile --name FRY_ISM_v5.5.4 miner_online_simple.py

# 5. Test with encrypted miner config
python ../create_miner_config.py create ISM-TESTKEY123456789ABCDEF012QRS --output dist/miner_config.enc

# 6. Test
cd dist/
./FRY_ISM_v5.5.4

# 7. Distribute
# Distribute: FRY_ISM_v5.5.4.exe (ISM miner executable with version)
# For other miner types: FRY_BM_v5.5.4.exe, FRY_AEM_v5.5.4.exe, etc.
# Installer creates: miner_config.enc (per deployment)

## Installer Utility Usage Examples

The `create_miner_config.py` script provides commands for managing encrypted miner configurations:

**Create encrypted miner config (examples for different miner types):**
```bash
# Indoor Satellite Miner (ISM)
python create_miner_config.py create ISM-ABC123DEFG456HIJK789LMNOP012QRST

# Bandwidth Miner (BM)
python create_miner_config.py create REDACTED_ROTATE_ME

# AI Edge Miner (AEM)  
python create_miner_config.py create AEM-XYZ789ABCD012EFGH345IJKL678MNOP

# Creates: miner_config.enc (encrypted configuration)
```

**Create config in specific location:**
```bash
python create_miner_config.py create ISM-ABC123DEFG456HIJK789LMNOP012QRST --output /path/to/miner_config.enc
```

**Read encrypted config (for verification):**
```bash
python create_miner_config.py read miner_config.enc
# Output: 📋 Miner Key: ISM-ABC123DEFG456HIJK789LMNOP012QRST
```

**Validate miner key format (works for all miner types):**
```bash
python create_miner_config.py validate REDACTED_ROTATE_ME
# Output: ✅ Valid miner key: REDACTED_ROTATE_ME
```
```

## Security Notes

- 🔒 **1Password Mandatory** - Only source of API credentials
- 🔒 **Build-time only** - Token retrieved from 1Password during build, never at runtime
- 🔒 **Zero plaintext** - No credentials stored in any files
- 🔒 **No fallbacks** - System fails safely if 1Password unavailable
- 🔒 **Audit friendly** - All token access logged in 1Password

This approach ensures maximum security while simplifying deployment!