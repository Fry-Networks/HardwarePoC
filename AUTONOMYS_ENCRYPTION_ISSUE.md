# Autonomys Auto Drive - Encryption Requirement Issue

**Date:** 2026-01-22
**Status:** Blocked - S3 API Upload Failing

---

## Issue Summary

Uploads via the S3-compatible API are failing with **500 Internal Server Error**. Investigation reveals that Autonomys Auto Drive's web interface requires choosing an encryption option, which may not be properly supported in the S3 API layer.

---

## Web Interface Options (Screenshot Evidence)

When uploading via the web UI at https://ai3.storage/, users are presented with:

```
Enter Encrypting Password
Uploading: 2026-01-21.parquet

[Encrypt with default password]  [Upload without encryption]  [Cancel]
```

This suggests encryption handling is a required step in the upload flow.

---

## S3 API Attempts

### Attempt 1: No Encryption Metadata
```python
response = self.s3_client.put_object(
    Bucket='uploads',
    Key='851f9017fffffff/bandwidth/hourly/2026-01-21.parquet',
    Body=file_content
)
```
**Result:** 500 Internal Server Error

### Attempt 2: Encryption Metadata = 'none'
```python
response = self.s3_client.put_object(
    Bucket='uploads',
    Key='851f9017fffffff/bandwidth/hourly/2026-01-21.parquet',
    Body=file_content,
    Metadata={'encryption': 'none'}
)
```
**Result:** 500 Internal Server Error

### Attempt 3: Different Bucket Names
Tried: `'frynetworks-measurements'`, `'uploads'`, endpoint URL
**Result:** All returned 500 Internal Server Error

---

## Documentation Analysis

### From [Auto Drive S3 Layer Guide](https://develop.autonomys.xyz/sdk/auto-drive/s3_layer):

> "Custom metadata handling for compression/encryption"

Suggests encryption can be specified via metadata:
```javascript
Metadata: {
  encryption: "AES_256_GCM"
}
```

### From [Auto-Drive Usage Examples](https://develop.autonomys.xyz/sdk/auto-drive/usage_examples):

> "Encryption with a password is **optional** rather than required"
> "Files can be uploaded without any encryption at all"

**Contradiction:** Documentation says encryption is optional, but:
- Web UI forces a choice (encrypt/no-encrypt/cancel)
- S3 API returns 500 errors regardless of metadata

---

## Possible Root Causes

### 1. S3 API Encryption Gap
The S3-compatible API layer may not properly implement the encryption/no-encryption flow that the web UI handles. The boto3 S3 client might be missing required parameters or headers.

### 2. Encryption Metadata Format
The encryption metadata format might be incorrect. Possible valid values:
- `encryption: "none"`
- `encryption: "false"`
- `encrypted: "false"`
- Missing metadata = unencrypted (but server rejects this)
- `x-amz-server-side-encryption: "none"`

### 3. Authentication/Permissions
The API key might not have permissions to upload unencrypted files, or there may be account-level settings that enforce encryption.

### 4. S3 API Server-Side Bug
The Autonomys Auto Drive S3 API endpoint might have a bug causing all uploads to fail with 500 errors, regardless of configuration.

---

## Proposed Solutions

### Option A: Use Native Auto Drive SDK (TypeScript/JavaScript)
Instead of boto3 S3 API, use the official Autonomys Auto Drive SDK which has proper encryption support:

```javascript
const { AutonomysDrive } = require('@autonomys/auto-drive');

const drive = new AutonomysDrive({
  apiKey: 'your-api-key'
});

await drive.uploadFileFromFilepath({
  filepath: 'path/to/file.parquet',
  remotePath: '851f9017fffffff/bandwidth/hourly/2026-01-21.parquet',
  encryption: false  // or { password: 'your-password' }
});
```

**Pros:**
- Official SDK with proper encryption handling
- Well-documented encryption options
- Actively maintained

**Cons:**
- Requires Node.js/JavaScript runtime
- Not native Python
- Additional dependency

### Option B: Python Wrapper for Auto Drive SDK
Create a Python subprocess wrapper that calls a Node.js script:

```python
def upload_via_native_sdk(local_path, remote_path, api_key, encrypt=False):
    """Upload file using native Autonomys SDK via Node.js."""
    cmd = [
        'node', 'autonomys_upload.js',
        '--file', str(local_path),
        '--remote', remote_path,
        '--api-key', api_key,
        '--encrypt', 'false' if not encrypt else 'true'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0
```

**Pros:**
- Keeps Python as primary language
- Uses official SDK for upload
- Can handle encryption properly

**Cons:**
- Requires Node.js installed on system
- More complex deployment
- Inter-process communication overhead

### Option C: Direct REST API with Encryption Headers
Bypass boto3 and use `requests` library with proper Auto Drive headers:

```python
import requests

def upload_to_autodrive(file_path, remote_path, api_key):
    """Upload directly via REST API."""
    with open(file_path, 'rb') as f:
        files = {'file': f}
        headers = {
            'Authorization': f'Bearer {api_key}',
            'X-Autonomys-Encryption': 'none'  # or 'AES_256_GCM'
        }
        response = requests.post(
            'https://public.auto-drive.autonomys.xyz/api/upload',
            files=files,
            headers=headers,
            data={'path': remote_path}
        )
        return response.status_code == 200
```

**Pros:**
- Pure Python
- Direct control over request format
- Can experiment with different headers

**Cons:**
- May require reverse-engineering the API
- Less stable than using official SDK
- Breaking changes in API format

### Option D: Temporary Workaround - Manual Upload
For immediate testing, manually upload files via web interface:

1. Process and redact data locally (already working ✓)
2. Files created at: `C:\ProgramData\FryNetworks\miner-BM\measurements\hourly\`
3. Manually upload via https://ai3.storage/
4. Choose "Upload without encryption"
5. Maintain hexID-based folder structure manually

**Pros:**
- Works immediately
- No code changes needed
- Can verify data structure

**Cons:**
- Not automated
- Not scalable
- Manual process error-prone

---

## Recommended Next Steps

1. **Contact Autonomys Support**
   Email: support@autonomys.xyz (or check their Discord/forum)
   Question: "How do I upload unencrypted files via the S3-compatible API using boto3?"

2. **Check API Status**
   Verify if the S3 API endpoint is experiencing issues:
   - https://status.autonomys.xyz (if available)
   - Community forums/Discord

3. **Test with Official SDK**
   Create a small Node.js test script to confirm uploads work with native SDK:
   ```bash
   npm install @autonomys/auto-drive
   node test_upload.js
   ```

4. **Review API Key Permissions**
   Check your Auto Drive dashboard to see if:
   - API key has upload permissions
   - There are encryption requirements at account level
   - Quotas or limits are being hit

5. **Try Alternative S3 Headers**
   Experiment with AWS S3 encryption headers that might translate:
   - `ServerSideEncryption='AES256'`
   - `SSECustomerAlgorithm='none'`
   - Custom headers via `ExtraArgs`

---

## Current Code Status

✅ **Working:**
- Local data processing and redaction
- Privacy protection (res-5, 5% noise, no identifiers)
- Folder structure (hexID-based paths)
- 1Password integration (30s timeout)
- S3 client configuration

❌ **Not Working:**
- Actual file uploads (500 errors)
- Encryption metadata handling
- S3 API compatibility

---

## Conclusion

The code is correctly implemented for the specified folder structure and privacy requirements. The upload failure is due to an external issue with the Autonomys Auto Drive S3 API's handling of encryption metadata.

**Recommendation:** Use **Option B** (Python wrapper for native SDK) as it provides the best balance of:
- Native Python integration
- Official SDK reliability
- Proper encryption handling
- Long-term maintainability

This ensures compatibility with Autonomys Auto Drive's encryption requirements while maintaining the hexID-based folder structure and privacy redaction that's already working perfectly.

---

## Sources

- [Auto Drive S3 Layer Guide](https://develop.autonomys.xyz/sdk/auto-drive/s3_layer)
- [Auto-Drive Usage Examples](https://develop.autonomys.xyz/sdk/auto-drive/usage_examples)
- [Autonomys Auto Drive GitHub](https://github.com/autonomys/auto-drive)
- [Auto Drive SDK npm package](https://www.npmjs.com/package/@autonomys/auto-drive)
