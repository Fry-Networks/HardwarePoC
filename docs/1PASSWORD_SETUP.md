# 1Password Integration for Autonomys API Key

This guide explains how to securely manage the Autonomys API key using 1Password for production deployments.

## Why 1Password?

✅ **Secure** - API keys never stored in code or config files
✅ **Centralized** - Single source of truth for credentials
✅ **Auditable** - Track who accesses secrets and when
✅ **Team-friendly** - Share access without sharing keys directly
✅ **Fallback support** - Still works with environment variables for dev/testing

## Prerequisites

1. **1Password Account** - Corporate or personal account
2. **1Password CLI** - Command-line tool for programmatic access

## Step 1: Install 1Password CLI

### Windows

Download from: https://developer.1password.com/docs/cli/get-started/

Or using winget:
```bash
winget install 1Password.CLI
```

Or using Chocolatey:
```bash
choco install 1password-cli
```

### Verify Installation

```bash
op --version
```

Should output something like: `2.x.x`

## Step 2: Sign In to 1Password

```bash
op signin
```

Follow the prompts to sign in with your 1Password account.

This creates a session that allows the CLI to access your vault.

## Step 3: Get Your Autonomys API Key

1. Visit https://ai3.storage
2. Sign in (Google/Discord/GitHub)
3. Navigate to API keys section
4. Generate a new API key
5. **Copy the key** (you'll need it in the next step)

## Step 4: Store API Key in 1Password

### Option A: Using CLI (Recommended)

```bash
op item create ^
  --category=password ^
  --title="Autonomys API Key" ^
  --tags=frynetworks,autonomys,api ^
  credential=YOUR_API_KEY_HERE
```

Replace `YOUR_API_KEY_HERE` with your actual API key.

### Option B: Using 1Password App

1. Open 1Password desktop app
2. Click **New Item** → **Password**
3. Set **Title**: `Autonomys API Key`
4. Set **Password/Credential**: Your API key
5. Add **Tags**: `frynetworks`, `autonomys`, `api`
6. Click **Save**

## Step 5: Verify It Works

Test that the integration works:

```bash
python -c "from measurements.secrets_manager import get_autonomys_api_key; print('✓ API key found!' if get_autonomys_api_key() else '✗ Not found')"
```

Or run the test script:

```bash
python -m measurements.secrets_manager
```

Expected output:
```
Testing 1Password integration...

✓ 1Password CLI is working
✓ Autonomys API key found: REDACTED_ROTATE_ME
```

## Step 6: Configure Your Service

The integration works automatically! No code changes needed.

When you run the Autonomys uploader:

```python
from measurements.autonomys_uploader import get_uploader

# Automatically retrieves API key from 1Password
uploader = get_uploader()

# Or explicitly from 1Password
from measurements.secrets_manager import get_autonomys_api_key
api_key = get_autonomys_api_key()
uploader = get_uploader(api_key=api_key)
```

**Lookup Priority:**
1. ✅ API key provided as parameter
2. ✅ 1Password item "Autonomys API Key"
3. ✅ Environment variable `AUTONOMYS_API_KEY`

## How It Works

### In Production (Service/Daemon)

```
Your Service starts
       ↓
Calls: get_uploader()
       ↓
autonomys_uploader.py checks for API key
       ↓
secrets_manager.py queries 1Password CLI
       ↓
Runs: op item get "Autonomys API Key" --field credential
       ↓
Returns API key securely
       ↓
Service uploads to Autonomys Auto Drive
```

### Security Features

- ✅ **Never in source code** - API key stored only in 1Password
- ✅ **Never in logs** - Only first/last 4 chars logged
- ✅ **Session-based** - 1Password CLI uses time-limited sessions
- ✅ **Audit trail** - 1Password logs all access
- ✅ **Revocable** - Update key in 1Password, all services pick up new key

## Development & Testing

For local development, you can still use environment variables:

```bash
# Windows Command Prompt
set AUTONOMYS_API_KEY=your-test-key

# Windows PowerShell
$env:AUTONOMYS_API_KEY="your-test-key"

# Add permanently (System Properties > Environment Variables)
```

The code automatically falls back to environment variables if 1Password is not available.

## Service Integration

### Windows Service with 1Password

When running as a Windows Service, ensure:

1. **Service runs as a user with 1Password CLI access**
2. **User is signed into 1Password** (session active)
3. **Service has permission to run `op` command**

#### Keep Session Active

1Password CLI sessions expire. To keep them active:

**Option 1: Service Account Integration**

Use 1Password Service Accounts (for automation):
https://developer.1password.com/docs/service-accounts/

```bash
# Set service account token
op signin --account TEAM_ACCOUNT

# This creates a long-lived token for automation
```

**Option 2: Refresh Session in Service**

Add to your service startup:

```python
import subprocess

def refresh_1password_session():
    """Refresh 1Password session for service account."""
    try:
        subprocess.run(["op", "signin", "--raw"], check=True)
        log.info("1Password session refreshed")
    except Exception as e:
        log.warning("Failed to refresh 1Password session: %s", e)

# Call during service initialization
refresh_1password_session()
```

### Scheduled Task with 1Password

If running via Windows Task Scheduler:

1. Task runs as your user account
2. Ensure you're signed into 1Password before scheduling
3. Session should persist across task runs

## Updating the API Key

To rotate/update the API key:

### Using CLI

```bash
op item edit "Autonomys API Key" credential=NEW_API_KEY_HERE
```

### Using App

1. Open 1Password app
2. Find "Autonomys API Key" item
3. Click **Edit**
4. Update the credential field
5. Click **Save**

**Important:** No code changes needed! All services will automatically use the new key on next retrieval.

## Troubleshooting

### "1Password CLI not found"

**Solution:**
- Install 1Password CLI: https://developer.1password.com/docs/cli/get-started/
- Verify with: `op --version`
- Add to PATH if needed

### "Not currently signed in"

**Solution:**
```bash
op signin
```

Follow prompts to authenticate.

### "Item not found"

**Solution:**
1. Verify item exists: `op item list | findstr "Autonomys"`
2. Check exact title: Must be `Autonomys API Key` (case-sensitive)
3. Recreate if needed (see Step 4)

### "Service can't access 1Password"

**Solution:**
1. Ensure service runs as user with 1Password access
2. Sign into 1Password as that user
3. Consider using 1Password Service Account for automation
4. As fallback, use environment variable:
   ```bash
   sc config "YourServiceName" binPath="... AUTONOMYS_API_KEY=key"
   ```

### Falls back to environment variable

This is expected if:
- 1Password CLI not installed
- Not signed in
- Item not found

The code logs which method it's using:
```
INFO: Using Autonomys API key from 1Password
```
or
```
INFO: Using Autonomys API key from environment variable
```

## Additional Secrets

You can extend this for other credentials:

### MongoDB Credentials

```bash
op item create ^
  --category=database ^
  --title="MongoDB Credentials" ^
  username=your_username ^
  password=your_password ^
  server=mongodb.example.com
```

Then in code:

```python
from measurements.secrets_manager import get_mongodb_credentials

creds = get_mongodb_credentials()
# Returns: {'username': '...', 'password': '...', 'host': '...'}
```

### Other API Keys

```bash
op item create ^
  --category=password ^
  --title="HardwareAPI Key" ^
  credential=your_api_key
```

Then add to `secrets_manager.py`:

```python
def get_hardware_api_key() -> Optional[str]:
    """Get HardwareAPI key from 1Password."""
    return get_from_1password("HardwareAPI Key", "credential")
```

## Best Practices

✅ **Use 1Password in production** - More secure than environment variables
✅ **Use environment variables in development** - Faster iteration
✅ **Rotate keys regularly** - Update in 1Password, no code changes needed
✅ **Tag items** - Use consistent tags (`frynetworks`, `autonomys`)
✅ **Document items** - Add notes in 1Password about what uses each key
✅ **Audit access** - Review 1Password activity logs periodically
✅ **Use service accounts** - For automated services/CI-CD

## Security Checklist

- [ ] 1Password CLI installed and working
- [ ] Signed in with correct account
- [ ] API key stored in 1Password (not in code)
- [ ] Service can access 1Password CLI
- [ ] Session stays active (or uses service account)
- [ ] Fallback to environment variable works
- [ ] Logs don't expose full API key
- [ ] Team members have appropriate vault access
- [ ] API key rotation process documented
- [ ] Backup access method configured (environment variable)

## Resources

- **1Password CLI Docs**: https://developer.1password.com/docs/cli/
- **Service Accounts**: https://developer.1password.com/docs/service-accounts/
- **Autonomys API Dashboard**: https://ai3.storage
- **Secrets Manager Code**: [measurements/secrets_manager.py](../measurements/secrets_manager.py)

---

**Status:** Ready for Production
**Security:** ✅ Enterprise-grade secret management
**Fallback:** ✅ Environment variables for development
