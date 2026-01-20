# Autonomys API Key - 1Password Setup for Production Builds

This guide shows how to add the Autonomys API key to your existing 1Password structure for automatic embedding during builds.

## Quick Setup

Your build script (`build_PoC_windows.ps1`) already has 1Password integration. You just need to add the Autonomys API key to the right location.

### Step 1: Get Your Autonomys API Key

1. Visit: https://ai3.storage
2. Sign in (Google/Discord/GitHub)
3. Generate API key
4. **Copy the key** (you'll need it in the next step)

### Step 2: Store in 1Password

The build script expects the key at: `op://Hardware/Autonomys/api_key`

**Using 1Password CLI:**

```bash
# Create the Autonomys item in your Hardware vault
op item create \
  --vault Hardware \
  --category login \
  --title Autonomys \
  api_key=YOUR_API_KEY_HERE
```

**Using 1Password App:**

1. Open 1Password desktop app
2. Navigate to **Hardware** vault
3. Click **New Item** → **Login** (or **Password**)
4. Set **Title**: `Autonomys`
5. Add a field:
   - **Label**: `api_key`
   - **Value**: Your API key from ai3.storage
6. Click **Save**

### Step 3: Verify It Works

Test that the build script can read it:

```powershell
op read "op://Hardware/Autonomys/api_key"
```

Should output your API key.

### Step 4: Build Your Executable

Now when you run your build script, it will automatically embed the Autonomys API key:

**Single Build:**
```powershell
.\build_PoC_windows.ps1 -Code IRM -Version 1.7.8 -Use1Password
```

**All Builds:**
```powershell
.\build_all_PoC_windows.ps1 -Version 1.7.8
```

You should see:
```
Autonomys API key configured
```

**Note:** `build_all_PoC_windows.ps1` automatically uses `-Use1Password` internally, so the Autonomys key will be embedded in all builds.

## How It Works

### Build Time

When you run `build_PoC_windows.ps1` with `-Use1Password`:

```
build_PoC_windows.ps1
       ↓
Read: op://Hardware/Autonomys/api_key
       ↓
Embed in tool_credentials
       ↓
Encrypt into executable
       ↓
FRY_PoC_IRM_v1.7.8.exe (with embedded key)
```

### Runtime

When your executable runs:

```
Your Service starts
       ↓
Autonomys uploader initializes
       ↓
secrets_manager.get_autonomys_api_key()
       ↓
Check 1: Embedded credentials → Found! ✓
       ↓
Use embedded API key
       ↓
Upload to Autonomys Auto Drive
```

## Lookup Priority

The `secrets_manager.py` checks in this order:

1. ✅ **Embedded credentials** (from build - PRODUCTION)
   - Retrieved from `tool_credentials` in encrypted config
   - Best for production deployments

2. ✅ **1Password CLI** (`op://Hardware/Autonomys/api_key`)
   - Direct read from 1Password
   - Used if no embedded credentials

3. ✅ **1Password item** ("Autonomys API Key")
   - Manual setup for standalone use
   - Fallback option

4. ✅ **Environment variable** (`AUTONOMYS_API_KEY`)
   - Development/testing
   - Quick local setup

## Existing 1Password Structure

Your build script already retrieves these from 1Password:

```
op://VPS/Hardware_API/API_BEARER_TOKEN
op://VSCode/hardware_exe/local_signing_key_hex
op://Hardware/Presearch/registration_code
op://Hardware/Diiisco/api_key
op://Hardware/SpaceAcres/farmer_key
op://Hardware/Bright/api_token
op://Hardware/Honeygain/api_key
op://Bandwidth Miners/Mysterium SDK API/MYST_API_KEY
```

The Autonomys key follows the same pattern:

```
op://Hardware/Autonomys/api_key  ← NEW
```

## Benefits

✅ **Secure** - API key never in source code or config files
✅ **Centralized** - One place to manage the credential
✅ **Automatic** - Embedded during build, no manual config needed
✅ **Team-friendly** - Share builds without sharing credentials
✅ **Easy rotation** - Update in 1Password, rebuild, deploy

## Development vs Production

### For Development (Local Testing)

```bash
# Quick setup - no 1Password needed
set AUTONOMYS_API_KEY=your-test-key

python test_autonomys_integration.py
```

### For Production (Builds)

```bash
# Store in 1Password
op item create --vault Hardware --title Autonomys api_key=YOUR_KEY

# Build with embedded credentials
.\build_PoC_windows.ps1 -Code IRM -Version 1.7.8 -Use1Password

# Distribute executable
# (No configuration needed on target machines!)
```

## Verification

### Verify 1Password Setup

```powershell
# Check the item exists
op item list --vault Hardware | Select-String "Autonomys"

# Read the value
op read "op://Hardware/Autonomys/api_key"
```

### Verify Embedded in Build

After building, the console output should show:

```
Tool Credentials (embedded from 1Password)
...
Autonomys API key configured
...
```

### Verify Runtime

Check your service logs for:

```
INFO: Using Autonomys API key from embedded build credentials
```

## Troubleshooting

### "Autonomys credentials unavailable"

During build:
- Check 1Password CLI: `op --version`
- Verify signed in: `op signin`
- Verify item exists: `op read "op://Hardware/Autonomys/api_key"`
- Check vault name is "Hardware" (case-sensitive)

### Runtime: "API key not found"

The executable should find embedded credentials automatically. If not:
- Rebuild with `-Use1Password` flag
- Check build console output for "Autonomys API key configured"
- As fallback, set environment variable

### Different 1Password Structure

If your vaults/items are named differently, update the parameter in `build_PoC_windows.ps1`:

```powershell
param(
  ...
  [string]$OPRefAutonomysApiKey = "op://YourVault/YourItem/your_field",
  ...
)
```

## Summary

**One-time setup:**
1. Get API key from https://ai3.storage
2. Store in 1Password: `op://Hardware/Autonomys/api_key`
3. Build normally with `-Use1Password`

**Every build after:**
- Autonomys API key automatically embedded
- No manual configuration on target machines
- Secure credential distribution

**For development:**
- Use environment variable: `set AUTONOMYS_API_KEY=key`
- No 1Password needed for local testing

---

**Next:** Build your executable and the Autonomys integration will work automatically!
