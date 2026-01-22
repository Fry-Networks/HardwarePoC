# API Access for Original Measurement Data

## Overview

This document explains how FryNetworks team members and paying customers can access **original, unredacted measurement data** through API endpoints. The original data never goes to Autonomys - it stays on local machines or in private cloud storage (S3, etc.).

---

## Why Build an API?

### The Problem

1. **Original data is local** - Stored on individual miner machines at `C:\ProgramData\FryNetworks\measurements\`
2. **Customers need access** - Professional/Enterprise tier buyers need programmatic access
3. **Team needs access** - Internal analysis, debugging, customer support
4. **Security required** - Must protect high-value data with authentication

### The Solution

Build a **private REST API** that:
- Serves original data from local storage or S3
- Requires authentication (API keys, JWT tokens)
- Supports multiple access patterns (HTTP, WebSocket, bulk download)
- Implements rate limiting and access logging

---

## Architecture Options

### Option 1: Centralized API Server (Recommended)

```
Miner Machines                   Central Server                  Customers
┌─────────────┐                 ┌──────────────┐               ┌──────────┐
│ Miner 1     │                 │              │               │ Customer │
│ measurements\│───upload──────>│  S3 Bucket   │<───query────  │ API Key  │
└─────────────┘                 │              │               └──────────┘
                                └──────────────┘
┌─────────────┐                        │
│ Miner 2     │                        │
│ measurements\│───upload──────>       │
└─────────────┘                        ↓
                                ┌──────────────┐
┌─────────────┐                 │  FastAPI /   │
│ Miner 3     │                 │  Flask API   │
│ measurements\│───upload──────> │  Server      │
└─────────────┘                 └──────────────┘
                                       │
                                       ↓
                                ┌──────────────┐
                                │ PostgreSQL   │
                                │ (metadata)   │
                                └──────────────┘
```

**Pros:**
- Single point of access
- Easy to manage authentication
- Can aggregate data from multiple miners
- Better for customers (one API to integrate)

**Cons:**
- Requires server infrastructure
- Need to upload original data to S3
- Additional cost for hosting

---

### Option 2: Direct Miner Access (Decentralized)

```
Customer                    Miner Machine
┌──────────┐               ┌─────────────────┐
│ API Key  │──request──────>│ Local FastAPI   │
└──────────┘               │ (port 8000)     │
                           │                 │
                           │ measurements\   │
                           │   decibel\      │
                           │     hourly\     │
                           │       *.parquet │
                           └─────────────────┘
```

**Pros:**
- No cloud upload needed
- Lower operating cost
- Data never leaves machine

**Cons:**
- Must expose miner to internet (security risk)
- Complex networking (NAT, firewalls)
- Harder for customers (multiple endpoints)

---

### Option 3: Hybrid (Best of Both)

```
                           ┌──────────────┐
                           │  Central API │◄──── Customers (easy access)
                           │  Gateway     │
                           └──────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ↓              ↓              ↓
              ┌─────────┐    ┌─────────┐   ┌─────────┐
              │ Miner 1 │    │ Miner 2 │   │   S3    │
              │ Local   │    │ Local   │   │ Backup  │
              │ API     │    │ API     │   │         │
              └─────────┘    └─────────┘   └─────────┘
```

**Pros:**
- Flexible deployment
- Can query live miners or archived data
- Failover capabilities

**Cons:**
- Most complex to implement

---

## Implementation: FastAPI Example

### Step 1: Install Dependencies

```bash
pip install fastapi uvicorn pandas pyarrow python-jose[cryptography] passlib[bcrypt]
```

### Step 2: Create API Server

**File:** `measurements/data_api.py`

```python
"""REST API for serving original measurement data.

This API provides authenticated access to original (unredacted) measurement data
for Professional and Enterprise tier customers.
"""

from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pathlib import Path
from typing import Optional, List
from datetime import datetime, date
import pandas as pd
import logging

log = logging.getLogger("measurements.data_api")

app = FastAPI(title="FryNetworks Measurement Data API", version="1.0.0")
security = HTTPBearer()

# Base path to original measurements
MEASUREMENTS_BASE = Path(r"C:\ProgramData\FryNetworks\measurements")

# In production, load from database or encrypted config
API_KEYS = {
    "REDACTED_ROTATE_ME": {"customer": "Acme Corp", "tier": "professional"},
    "sk_live_customer2_abc": {"customer": "BigTech Inc", "tier": "enterprise"},
    "sk_test_internal_123": {"customer": "FryNetworks Team", "tier": "internal"}
}


def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security)) -> dict:
    """Verify API key from Authorization header."""
    token = credentials.credentials

    if token not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return API_KEYS[token]


@app.get("/")
def read_root():
    """Health check endpoint."""
    return {
        "service": "FryNetworks Measurement Data API",
        "status": "operational",
        "version": "1.0.0"
    }


@app.get("/v1/measurements/{measurement_type}/hourly/{date_str}")
def get_hourly_data(
    measurement_type: str,
    date_str: str,
    hex_id: Optional[str] = None,
    customer: dict = Depends(verify_api_key)
):
    """Get original hourly measurement data for a specific date.

    Args:
        measurement_type: Type (bandwidth, decibel, radiation, satellite, aem)
        date_str: Date in YYYY-MM-DD format
        hex_id: Optional hex ID filter (res-7)

    Returns:
        JSON with full-precision measurement data

    Example:
        GET /v1/measurements/decibel/hourly/2026-01-22
        Authorization: Bearer REDACTED_ROTATE_ME
    """
    log.info(f"Data request from {customer['customer']}: {measurement_type}/{date_str}")

    # Validate measurement type
    valid_types = ["bandwidth", "decibel", "radiation", "satellite", "aem"]
    if measurement_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid measurement type")

    # Parse and validate date
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format (use YYYY-MM-DD)")

    # Construct file path
    parquet_file = MEASUREMENTS_BASE / measurement_type / "hourly" / f"{date_str}.parquet"

    if not parquet_file.exists():
        raise HTTPException(status_code=404, detail="Data not found for specified date")

    try:
        # Read original data (full precision, all metadata)
        df = pd.read_parquet(parquet_file)

        # Optional: Filter by hex_id if provided
        if hex_id and 'hex_id' in df.columns:
            df = df[df['hex_id'] == hex_id]
            if df.empty:
                raise HTTPException(status_code=404, detail="No data for specified hex_id")

        # Convert to JSON
        data = df.to_dict(orient='records')

        return {
            "measurement_type": measurement_type,
            "date": date_str,
            "hex_id": hex_id,
            "record_count": len(data),
            "customer": customer['customer'],
            "tier": customer['tier'],
            "data": data,
            "metadata": {
                "resolution": "res-7",
                "precision": "full",
                "note": "Original unredacted data - authorized access only"
            }
        }

    except Exception as e:
        log.error(f"Error reading data: {e}")
        raise HTTPException(status_code=500, detail="Error reading measurement data")


@app.get("/v1/measurements/{measurement_type}/hourly/range")
def get_hourly_range(
    measurement_type: str,
    start_date: str,
    end_date: str,
    hex_id: Optional[str] = None,
    customer: dict = Depends(verify_api_key)
):
    """Get hourly data for a date range.

    Args:
        measurement_type: Type (bandwidth, decibel, etc.)
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        hex_id: Optional hex ID filter

    Returns:
        Combined data for all days in range

    Example:
        GET /v1/measurements/decibel/hourly/range?start_date=2026-01-20&end_date=2026-01-25
    """
    log.info(f"Range request from {customer['customer']}: {start_date} to {end_date}")

    # Parse dates
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    # Limit range (prevent abuse)
    if (end - start).days > 90:
        raise HTTPException(status_code=400, detail="Date range too large (max 90 days)")

    # Collect data for each day
    all_data = []
    current = start

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        parquet_file = MEASUREMENTS_BASE / measurement_type / "hourly" / f"{date_str}.parquet"

        if parquet_file.exists():
            try:
                df = pd.read_parquet(parquet_file)
                if hex_id and 'hex_id' in df.columns:
                    df = df[df['hex_id'] == hex_id]
                all_data.extend(df.to_dict(orient='records'))
            except Exception as e:
                log.warning(f"Error reading {date_str}: {e}")

        current += pd.Timedelta(days=1)

    return {
        "measurement_type": measurement_type,
        "start_date": start_date,
        "end_date": end_date,
        "hex_id": hex_id,
        "record_count": len(all_data),
        "data": all_data
    }


@app.get("/v1/measurements/list")
def list_available_data(
    measurement_type: Optional[str] = None,
    customer: dict = Depends(verify_api_key)
):
    """List all available measurement data.

    Args:
        measurement_type: Optional filter by type

    Returns:
        List of available dates per measurement type
    """
    available = {}

    types = [measurement_type] if measurement_type else ["bandwidth", "decibel", "radiation", "satellite", "aem"]

    for mtype in types:
        hourly_dir = MEASUREMENTS_BASE / mtype / "hourly"
        if hourly_dir.exists():
            files = sorted([f.stem for f in hourly_dir.glob("*.parquet")])
            if files:
                available[mtype] = {
                    "dates": files,
                    "first_date": files[0],
                    "last_date": files[-1],
                    "count": len(files)
                }

    return {
        "customer": customer['customer'],
        "available_data": available
    }


if __name__ == "__main__":
    import uvicorn
    # Run server on localhost:8000
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
```

---

### Step 3: Run the API Server

```bash
# Development mode
python measurements/data_api.py

# Production mode with Uvicorn
uvicorn measurements.data_api:app --host 0.0.0.0 --port 8000 --workers 4

# Or with Gunicorn (for production)
gunicorn measurements.data_api:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## Client Usage Examples

### Python Client

```python
import requests
import pandas as pd

# API configuration
API_BASE = "https://api.frynetworks.com"
API_KEY = "REDACTED_ROTATE_ME"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}

# Example 1: Get single day of data
response = requests.get(
    f"{API_BASE}/v1/measurements/decibel/hourly/2026-01-22",
    headers=headers
)

data = response.json()
print(f"Retrieved {data['record_count']} records")
print(f"Precision: {data['metadata']['precision']}")

# Convert to DataFrame
df = pd.DataFrame(data['data'])
print(df.head())

# Output:
#   hour  dbfs_avg  dbfs_min  dbfs_max  sample_count  hex_id
# 0    0    -41.77     -45.1     -38.7             6  871f90151ffffff
# 1    1    -40.23     -44.8     -37.2             6  871f90151ffffff


# Example 2: Get date range
response = requests.get(
    f"{API_BASE}/v1/measurements/decibel/hourly/range",
    params={
        "start_date": "2026-01-20",
        "end_date": "2026-01-25",
        "hex_id": "871f90151ffffff"
    },
    headers=headers
)

data = response.json()
df = pd.DataFrame(data['data'])
print(f"Total records: {len(df)}")  # 144 hours (6 days × 24 hours)


# Example 3: List available data
response = requests.get(
    f"{API_BASE}/v1/measurements/list",
    headers=headers
)

available = response.json()
print("Available data:")
for mtype, info in available['available_data'].items():
    print(f"  {mtype}: {info['count']} days ({info['first_date']} to {info['last_date']})")
```

---

### JavaScript/Node.js Client

```javascript
const axios = require('axios');

const API_BASE = 'https://api.frynetworks.com';
const API_KEY = 'REDACTED_ROTATE_ME';

const headers = {
  'Authorization': `Bearer ${API_KEY}`,
  'Accept': 'application/json'
};

// Get single day
async function getDecibelData(date) {
  const response = await axios.get(
    `${API_BASE}/v1/measurements/decibel/hourly/${date}`,
    { headers }
  );

  console.log(`Retrieved ${response.data.record_count} records`);
  return response.data.data;
}

// Get range
async function getDateRange(startDate, endDate) {
  const response = await axios.get(
    `${API_BASE}/v1/measurements/decibel/hourly/range`,
    {
      headers,
      params: { start_date: startDate, end_date: endDate }
    }
  );

  return response.data.data;
}

// Usage
getDecibelData('2026-01-22').then(data => {
  console.log('First record:', data[0]);
});
```

---

### cURL Examples

```bash
# Get single day
curl -H "Authorization: Bearer REDACTED_ROTATE_ME" \
     https://api.frynetworks.com/v1/measurements/decibel/hourly/2026-01-22

# Get date range
curl -H "Authorization: Bearer REDACTED_ROTATE_ME" \
     "https://api.frynetworks.com/v1/measurements/decibel/hourly/range?start_date=2026-01-20&end_date=2026-01-25"

# List available data
curl -H "Authorization: Bearer REDACTED_ROTATE_ME" \
     https://api.frynetworks.com/v1/measurements/list
```

---

## Authentication & Security

### API Key Management

**Generate API Keys:**

```python
import secrets

def generate_api_key(prefix="sk_live"):
    """Generate secure API key."""
    random_part = secrets.token_urlsafe(32)
    return f"{prefix}_{random_part}"

# Example keys:
# sk_live_abc123xyz789...  (Production, paying customers)
# sk_test_def456uvw012...  (Testing environment)
# sk_internal_ghi789rst... (Internal team use)
```

**Store Keys Securely:**

```python
# In production, store in database with hashing
import hashlib
import hmac

def hash_api_key(key: str, salt: str) -> str:
    """Hash API key before storing in database."""
    return hmac.new(salt.encode(), key.encode(), hashlib.sha256).hexdigest()

# Database schema:
# CREATE TABLE api_keys (
#     id SERIAL PRIMARY KEY,
#     key_hash VARCHAR(64) NOT NULL UNIQUE,
#     customer_id INT NOT NULL,
#     tier VARCHAR(20) NOT NULL,
#     created_at TIMESTAMP DEFAULT NOW(),
#     expires_at TIMESTAMP,
#     last_used_at TIMESTAMP
# );
```

---

### Rate Limiting

```python
from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.get("/v1/measurements/{measurement_type}/hourly/{date_str}")
@limiter.limit("100/hour")  # 100 requests per hour
def get_hourly_data(request: Request, ...):
    # ... existing code ...
    pass
```

---

### Access Tiers

| Tier | Rate Limit | Features | Price |
|------|-----------|----------|-------|
| **Free (Autonomys)** | N/A | Redacted data only | $0 |
| **Professional** | 100 req/hour | Original data, HTTP API | $50-75/month |
| **Enterprise** | 1000 req/hour | + WebSocket, bulk download | $500-1000/month |
| **Internal** | Unlimited | Full access, all features | Internal only |

---

## Advanced Features

### WebSocket for Real-Time Data (Enterprise Tier)

```python
from fastapi import WebSocket

@app.websocket("/v1/measurements/stream")
async def websocket_stream(websocket: WebSocket, token: str):
    """Stream real-time measurements via WebSocket."""
    await websocket.accept()

    # Verify token
    if token not in API_KEYS:
        await websocket.close(code=1008)
        return

    try:
        while True:
            # Read latest data as it arrives
            # This would integrate with your measurement collection system
            data = await get_latest_measurement()
            await websocket.send_json(data)
            await asyncio.sleep(60)  # Send every minute
    except Exception as e:
        await websocket.close()
```

**Client:**

```python
import websockets
import asyncio

async def stream_data():
    uri = f"wss://api.frynetworks.com/v1/measurements/stream?token=sk_live_xyz"
    async with websockets.connect(uri) as ws:
        while True:
            data = await ws.recv()
            print(f"Received: {data}")

asyncio.run(stream_data())
```

---

### Bulk Download (S3 Pre-signed URLs)

```python
import boto3
from datetime import timedelta

@app.get("/v1/measurements/{measurement_type}/bulk/{date_str}/download")
def get_bulk_download_url(
    measurement_type: str,
    date_str: str,
    customer: dict = Depends(verify_api_key)
):
    """Generate pre-signed S3 URL for bulk download."""

    # Verify customer has bulk download access
    if customer['tier'] not in ['enterprise', 'internal']:
        raise HTTPException(status_code=403, detail="Bulk download requires Enterprise tier")

    s3 = boto3.client('s3')
    bucket = 'frynetworks-original-data'
    key = f"measurements/{measurement_type}/hourly/{date_str}.parquet"

    # Generate pre-signed URL (valid for 1 hour)
    url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket, 'Key': key},
        ExpiresIn=3600
    )

    return {
        "download_url": url,
        "expires_in": 3600,
        "file_size_mb": get_file_size(bucket, key) / (1024 * 1024)
    }
```

---

## Deployment

### Docker Deployment

**Dockerfile:**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY measurements/ ./measurements/

EXPOSE 8000

CMD ["uvicorn", "measurements.data_api:app", "--host", "0.0.0.0", "--port", "8000"]
```

**docker-compose.yml:**

```yaml
version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/frynetworks
    volumes:
      - /opt/frynetworks/measurements:/data/measurements:ro
    depends_on:
      - db

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: frynetworks
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

---

## Summary

### For FryNetworks Team Members:

**Access Original Data:**
1. **Directly on miner** - File system access to `C:\ProgramData\FryNetworks\measurements\`
2. **Via internal API** - Use `sk_internal_*` key with unlimited access
3. **From S3 backup** - AWS CLI or boto3 for bulk operations

### For Paying Customers:

**Professional Tier ($50-75/month):**
- REST API access (100 req/hour)
- Original data (res-7, full precision)
- All identifying metadata
- Single hex ID

**Enterprise Tier ($500-1000/month):**
- REST + WebSocket API (1000 req/hour)
- Bulk downloads via S3
- Multiple hex IDs
- Real-time streaming
- Custom integration support

### Security Checklist:

- ✅ API keys instead of passwords
- ✅ HTTPS only (TLS 1.3)
- ✅ Rate limiting per customer
- ✅ Access logging and auditing
- ✅ Key expiration and rotation
- ✅ IP whitelisting (optional)
- ✅ Separate keys per customer/environment
