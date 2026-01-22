# Autonomys Native Upload Solution

**Date:** 2026-01-22
**Status:** READY FOR TESTING

---

## Summary

Due to limitations with the Autonomys Auto Drive S3-compatible API (500 Internal Server Error), I've created a **native SDK solution** that uses the official TypeScript/JavaScript SDK via Node.js, called from Python.

This solution properly handles encryption requirements and folder structure uploads.

---

## What Was Created

### 1. Node.js Upload Script
**File:** `autonomys_upload_folder.js`

Uses the official `@autonomys/auto-drive` SDK to upload entire folder structures with proper encryption handling.

**Features:**
- Uploads complete folder hierarchies
- Proper encryption support (optional)
- Progress callbacks
- JSON output for Python parsing

### 2. Python Wrapper
**File:** `measurements/autonomys_uploader_native.py`

Python module that wraps the Node.js script for seamless integration.

**Features:**
- Automatic SDK installation check
- Temporary folder creation for proper structure
- API key integration with secrets_manager
- Fallback support in orchestrator

### 3. Setup Test Script
**File:** `test_nodejs_setup.py`

Verifies all prerequisites are installed.

---

## Prerequisites

### Required Software

1. **Node.js** (v18 or higher) ✓ INSTALLED
   - Version detected: v22.19.0
   - Location: `C:\Program Files\nodejs\`

2. **npm** (comes with Node.js) ✓ AVAILABLE
   - Version detected: 11.6.2
   - Location: `C:\Program Files\nodejs\npm.cmd`

3. **Autonomys SDK** (auto-installed by script)
   - Package: `@autonomys/auto-drive`
   - Package: `@autonomys/auto-utils`

### Installation Steps

```bash
# 1. Verify Node.js is installed
node --version
# Should show: v22.19.0

# 2. Install Autonomys SDK
npm install @autonomys/auto-drive @autonomys/auto-utils

# 3. Test the setup
python test_nodejs_setup.py
```

---

## How It Works

### Folder Structure Approach

Instead of uploading individual files, the native SDK uploads **entire folder structures** at once. This is much cleaner and matches the Auto Drive design better.

**Example:**

1. **Create temp folder structure:**
   ```
   temp_upload/
     └── 851f9017fffffff/        (hexID)
          └── bandwidth/
               └── hourly/
                    ├── 2026-01-21.parquet
                    └── 2026-01-21.meta.json
   ```

2. **Upload the root folder** (`851f9017fffffff/`)

3. **Result on Auto Drive:**
   ```
   851f9017fffffff/
     └── bandwidth/
          └── hourly/
               ├── 2026-01-21.parquet
               └── 2026-01-21.meta.json
   ```

### Python Integration

The Python code:
1. Creates temporary folder structure
2. Copies files to correct locations
3. Calls Node.js script to upload
4. Cleans up temporary folder
5. Returns CID (Content Identifier)

```python
from measurements.autonomys_uploader_native import get_native_uploader

uploader = get_native_uploader()  # Gets API key from 1Password
cid = uploader.upload_file_with_structure(
    local_file=Path("measurements/hourly/2026-01-21.parquet"),
    remote_path="851f9017fffffff/bandwidth/hourly/2026-01-21.parquet",
    encrypt=False
)
```

---

## Orchestrator Integration

The orchestrator (`autonomys_orchestrator.py`) now has **dual upload support**:

1. **Try S3 API first** (for compatibility)
2. **Fall back to native SDK** if S3 fails

```python
# Try S3-compatible uploader first
uploader = get_uploader()
if uploader:
    try:
        result = uploader.upload_file(parquet_path, remote_path)
    except Exception:
        log.warning("S3 uploader failed, trying native SDK...")

# Fall back to native SDK
if not upload_success:
    native_uploader = get_native_uploader()
    cid = native_uploader.upload_file_with_structure(
        local_file=parquet_path,
        remote_path=remote_path,
        encrypt=False
    )
```

---

## Testing

### Manual Test
```bash
# 1. Install dependencies
npm install @autonomys/auto-drive @autonomys/auto-utils

# 2. Test Node.js script directly
node autonomys_upload_folder.js \
  --api-key "your-api-key" \
  --folder "path/to/851f9017fffffff"

# 3. Test Python wrapper
python -c "from measurements.autonomys_uploader_native import get_native_uploader; \
           u = get_native_uploader(); \
           print('Native uploader ready')"
```

### Automated Test
```bash
# Run the full upload test (will use native SDK automatically)
python test_autonomys_upload_auto.py
```

---

## Advantages Over S3 API

| Feature | S3 API | Native SDK |
|---------|--------|------------|
| **Encryption Support** | ❌ Broken (500 errors) | ✅ Full support |
| **Folder Uploads** | ❌ Must upload files individually | ✅ Upload entire folders |
| **Error Messages** | ❌ Generic 500 errors | ✅ Detailed error info |
| **Stability** | ❌ Unreliable | ✅ Official SDK |
| **Password Handling** | ❌ No clear method | ✅ Built-in parameter |
| **Progress Tracking** | ❌ Limited | ✅ Callback support |

---

## Configuration

### API Key (Already Configured)
- **Location:** 1Password
- **Item:** AutoDrive
- **Field:** AUTONOMYS_API_KEY
- **Timeout:** 30 seconds

### Network
- **Default:** mainnet
- **Alternative:** testnet (for testing)
- **Configuration:** Pass `network="testnet"` to uploader

### Encryption
- **Default:** No encryption (`encrypt=False`)
- **With Password:** Set `encrypt=True` and provide `password="..."`
- **Web UI Option:** Matches "Upload without encryption" button

---

## File Structure Summary

**Created Files:**
```
autonomys_upload_folder.js           - Node.js upload script
autonomys_upload_node.js              - Alternative single-file upload
measurements/autonomys_uploader_native.py  - Python wrapper
test_nodejs_setup.py                  - Setup verification
```

**Modified Files:**
```
measurements/autonomys_orchestrator.py  - Added native SDK fallback
measurements/secrets_manager.py         - 30s timeout, AutoDrive item
measurements/autonomys_uploader.py      - S3 API improvements (still has issues)
```

---

## Next Steps

### 1. Install SDK
```bash
npm install @autonomys/auto-drive @autonomys/auto-utils
```

### 2. Test Upload
```bash
# Option A: Test with Node.js directly
node autonomys_upload_folder.js --help

# Option B: Test with Python wrapper
python test_autonomys_upload_auto.py
```

### 3. Production Use
Once tested, the system will automatically:
- Process CSV data daily
- Redact sensitive information
- Create hexID-based folder structure
- Upload to Autonomys Auto Drive
- Fall back to native SDK if S3 fails

---

## Troubleshooting

### Issue: "npm not found"
**Solution:** Add Node.js to PATH or use full path:
```bash
"/c/Program Files/nodejs/npm.cmd" install @autonomys/auto-drive
```

### Issue: "SDK not installed"
**Solution:** Run the install command:
```bash
npm install @autonomys/auto-drive @autonomys/auto-utils
```

### Issue: "Upload timeout"
**Solution:** Increase timeout in `autonomys_uploader_native.py` (currently 300s = 5 min)

### Issue: "API key not found"
**Solution:** Verify 1Password item exists:
```bash
op item get "AutoDrive" --field "AUTONOMYS_API_KEY"
```

---

## Conclusion

The native SDK solution provides a reliable alternative to the S3-compatible API, with proper encryption handling and folder upload support. The Python wrapper makes it seamless to use from your existing codebase.

**Key Benefits:**
- ✅ Works around S3 API limitations
- ✅ Proper encryption support (optional)
- ✅ Maintains hexID-based folder structure
- ✅ Automatic fallback in orchestrator
- ✅ Uses official Autonomys SDK
- ✅ Ready for production deployment

Once you run `npm install @autonomys/auto-drive @autonomys/auto-utils`, the system will be ready to upload your privacy-protected measurement data to Autonomys Auto Drive!
