# HardwarePoC
Service executed automatically to get PoC - PoL - PoD

## Overview

This service monitors FryNetworks miner hardware and reports Proof of Coverage (PoC), Proof of Location (PoL), and Proof of Deployment (PoD) metrics.

## Security Model

The service uses encrypted miner configurations for maximum security:
- **API credentials** are embedded in the executable at build time using 1Password
- **Miner keys** are provided via encrypted configuration files created by the installer
- **No plaintext credentials** are stored anywhere in the deployment

## Installer Responsibilities

The installer **MUST** create an encrypted miner configuration file for each deployment:

### Step 1: Validate Miner Key Format
Miner keys must follow the format: `{MINER_CODE}-{32_CHARACTER_KEY}`
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

Example: `ISM-ABC123DEFG456HIJK789LMNOP012QRST`

### Step 2: Create Encrypted Configuration
```bash
# Include create_miner_config.py in your installer package
python create_miner_config.py create ISM-ABC123DEFG456HIJK789LMNOP012QRST --output /install/path/miner_config.enc
```

### Step 3: Deploy Files
Place the following files in the installation directory:
- `FRY_PoC_{MINER_CODE}_v{VERSION}.exe` - The branded miner executable
- `miner_config.enc` - The encrypted miner configuration (created by installer)

### Step 4: Acquire Installation Lease
The installer should acquire the installation lease before starting the service:
```bash
# The service will automatically verify lease ownership on startup
# If the installer doesn't acquire the lease, the service will fail to start
```

## Build Process

(The build instructions were simplified/removed — use `build_windows.ps1` to build `miner_online_simple.py`.)

## Required Tools
