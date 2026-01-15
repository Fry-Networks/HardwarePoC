# Tool Credentials Security Update

**Date:** January 9, 2026  
**Version:** BM v1.6.4+  
**Status:** Implemented

## Overview

Critical tool credentials (API keys, payout addresses, wallet addresses) are now embedded at build time from 1Password instead of being stored in runtime configuration files. This eliminates security risks from user-modifiable config files and ensures credentials are protected.

## Terminology Clarification

- **PoC** = Proof of Connectivity (the concept that a device is connected and provides data)
- **Tools** = Mysterium, Bright, Honeygain, Presearch, Diiisco, Space Acres, etc. (the actual applications that provide connectivity)

## What Changed

### Before (Insecure)
- GUI wrote tool-specific config files: `mysterium_config.json`, `presearch_config.json`, etc.
- Files contained sensitive data: API keys, wallet addresses, payout addresses
- Users could potentially modify or intercept these credentials
- Service waited for these files to be present before starting tools

### After (Secure)
- Credentials embedded during build from 1Password vault
- GUI only writes `miner_config.json` with enable/disable flags
- Service reads credentials from encrypted embedded config
- No sensitive data exposed in filesystem

## Build Script Changes

### New Parameters Added to `build_PoC_windows.ps1`

```powershell
# Tool Credentials (embedded at build time from 1Password)
[string]$OPRefMysteriumApiKey = "op://Hardware/Mysterium/api_key"
[string]$OPRefMysteriumIdentity = "op://Hardware/Mysterium/identity"
[string]$OPRefPresearchRegCode = "op://Hardware/Presearch/registration_code"
[string]$OPRefDiiiscoApiKey = "op://Hardware/Diiisco/api_key"
[string]$OPRefSpaceAcresFarmerKey = "op://Hardware/SpaceAcres/farmer_key"
[string]$OPRefSpaceAcresRewardAddr = "op://Hardware/SpaceAcres/reward_address"
[string]$OPRefBrightApiToken = "op://Hardware/Bright/api_token"
[string]$OPRefHoneygainApiKey = "op://Hardware/Honeygain/api_key"
```

### Embedded Config Structure

The build script now creates a `tool_credentials` object in the embedded config:

```json
{
  "external_api": { ... },
  "tool_credentials": {
    "mysterium_api_key": "...",
    "mysterium_identity": "0x...",
    "presearch_registration_code": "...",
    "diiisco_api_key": "...",
    "spaceacres_farmer_key": "...",
    "spaceacres_reward_address": "...",
    "bright_api_token": "...",
    "honeygain_api_key": "..."
  }
}
```

## Service Changes

### New Function: `_get_tool_credentials()`

```python
def _get_tool_credentials() -> Dict[str, Any]:
    """Get tool credentials from embedded config.
    
    Returns:
        Dictionary of tool credentials (empty if not embedded)
    """
    try:
        cfg = load_config()
        return cfg.get('tool_credentials', {})
    except Exception as e:
        log.warning("Failed to load tool credentials: %s", e)
        return {}
```

### Removed: Tool Config File Reading

The service no longer reads these files:
- `mysterium_config.json`
- `presearch_config.json`
- `diiisco_config.json`
- `spaceacres_config.json`
- `bright_config.json`
- `honeygain_config.json`

## GUI Changes Required

### Before (Deprecated)
```python
# GUI wrote multiple config files
mysterium_config = {
    "enabled": True,
    "api_key": "secret_key",  # ❌ Security risk
    "identity": "0xwallet"     # ❌ Security risk
}
write_config("mysterium_config.json", mysterium_config)
```

### After (Secure)
```python
# GUI only writes enable/disable flags
miner_config = {
    "mysterium_enabled": True,
    "presearch_enabled": False,
    "log_level": "INFO"
}
write_config("miner_config.json", miner_config)
```

## Documentation Updates

Updated files:
- `docs/README_GUI_Integration.md` - Removed PoC config examples, added security notes
- Directory structure simplified (no PoC-specific config files)
- Added note about build-time credential embedding

## Security Benefits

1. **No credential exposure**: API keys and payout addresses never written to filesystem
2. **Tamper-proof**: Users cannot modify embedded credentials
3. **Centralized management**: All credentials managed through 1Password vault
4. **Build-time validation**: Credentials verified during build, not runtime
5. **Reduced attack surface**: Fewer files containing sensitive data

## Migration Path

### For Developers
1. Update 1Password vault with required credentials:
   - `Hardware/Mysterium/api_key`
   - `Hardware/Mysterium/identity`
   - `Hardware/Presearch/registration_code`
   - `Hardware/Diiisco/api_key`
   - `Hardware/SpaceAcres/farmer_key`
   - `Hardware/SpaceAcres/reward_address`
   - `Hardware/Bright/api_token`
   - `Hardware/Honeygain/api_key`

2. Rebuild with updated script:
```powershell
pwsh .\build_PoC_windows.ps1 -Code BM -Version 1.6.5
```

3. Verify embedded credentials:
```python
from external_api import get_build_config_info
info = get_build_config_info()
print(info)  # Should show credentials configured
```

### For GUI Developers
1. Remove code that writes PoC-specific config files
2. Update to only write `miner_config.json` with enable/disable flags
3. Remove credential input fields from GUI
4. Update documentation to reflect simplified config

## Testing

### Verify Embedded Credentials
```python
# In miner_online_simple.py
creds = _get_poc_credentials()
assert 'mysterium_api_key' in creds
assert 'mysterium_identity' in creds
# etc.
```

### Verify Config Simplification
```powershell
# Should only see miner_config.json
Get-ChildItem "$env:PROGRAMDATA\FryNetworks\miner-BM\config"
```

## Backward Compatibility

**Breaking Change:** Existing PoC config files will be ignored. Services built with this version require credentials to be embedded at build time.

**Transition Period:** None required - old config files are simply ignored if present.

## Questions?

Contact: Development Team  
Reference: BM v1.6.4 Security Update
