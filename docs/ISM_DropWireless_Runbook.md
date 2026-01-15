# ISM PoC – DropWireless (Linux Only)

This guide walks you through preparing and running the ISM FryNetworks Proof of Connectivity bundle for DropWireless on Linux. You’ll generate required encrypted configs and acquire a lease using FryNetworks APIs. For questions or troubleshooting, see Support (Section 6).

---

## 0. Package Contents (What Fry Ships)

- **Binary:** `release/ISM/FRY_PoC_ISM_v1.0.0`
- **Hash:** `release/ISM/FRY_PoC_ISM_v1.0.0.sha256`
- **Helper Scripts:**
  - `create_miner_config.py`
  - `create_install_config.py`
- **Documentation:** (this file)

## 1. Verify and Stage Files

Verify checksum:
```bash
sha256sum -c FRY_PoC_ISM_v1.0.0.sha256
```
Create a runtime directory with a `config` subfolder (example):
```bash
sudo mkdir -p /opt/frynetworks/miner-ISM/config
```
Copy the following files into the runtime directory:
- `FRY_PoC_ISM_v1.0.0`
- `FRY_PoC_ISM_v1.0.0.sha256`
- `create_miner_config.py`
- `create_install_config.py`

*If you prefer not to use sudo, place the runtime dir under your home directory (e.g., `~/Documents/miner-ISM/config`) and adjust paths below accordingly.*

Make the binary executable:
```bash
chmod +x FRY_PoC_ISM_v1.0.0
```

## 2. Generate Encrypted Configs

You generate both config files locally.
**Keep all plaintext values (miner key and install ID) in your secure vault.**

### 2.1 Miner Config
```bash
python3 create_miner_config.py create ISM-DROPM82ED6LMQZ6STUPLEO5W3IFWUGOE --output miner_config.enc
```

### 2.2 Install ID (UUID v4)
Generate a persistent UUIDv4:
```bash
python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
```

### 2.3 Acquire Lease via FryNetworks Hardware API

- **Auth Token:** API_BEARER_TOKEN_DROPWIRELESS – provided by FryNetworks
- **Base URL:** `https://hardwareapi.frynetworks.com/`
- **Miner key:** Provided by FryNetworks
- **Install ID:** The UUID you generated above

Set variables:
```bash
API_BASE="https://hardwareapi.frynetworks.com"
API_TOKEN="API_BEARER_TOKEN_DROPWIRELESS"
MINER_KEY="ISM-DROPM82ED6LMQZ6STUPLEO5W3IFWUGOE"
INSTALL_ID="c8425ebe-4657-4dcd-86c0-2bdc89baedb8"
```

Check current lease state:
```bash
curl -s -H "Authorization: Bearer ${API_TOKEN}" \
  "${API_BASE}/installations/${MINER_KEY}/leases/current"
```
- If lease is active **and** `install_id != INSTALL_ID`: someone else owns the lease. Do not proceed until cleared by Fry.

Acquire (first time setup):
```bash
curl -i -X POST -H "Authorization: Bearer ${API_TOKEN}" \
  "${API_BASE}/installations/${MINER_KEY}/leases/${INSTALL_ID}"
```
Renew (subsequent runs):
```bash
curl -i -X PATCH -H "Authorization: Bearer ${API_TOKEN}" \
  "${API_BASE}/installations/${MINER_KEY}/leases/${INSTALL_ID}"
```

- Success → 2xx  
- `409 Conflict` → lease held by another install_id  
- **Do not rotate your INSTALL_ID once created!**

### 2.4 Install Config
```bash
python3 create_install_config.py create \
  --install-id "c8425ebe-4657-4dcd-86c0-2bdc89baedb8" \
  --output install_config.enc
```

### 2.5 Permissions
```bash
chmod 600 *.enc
# Place configs where the binary reads them
sudo mv miner_config.enc install_config.enc /opt/frynetworks/miner-ISM/config/
# Ensure the runtime user can read the files (adjust user as needed)
sudo chown "$USER":"$USER" /opt/frynetworks/miner-ISM/config/miner_config.enc /opt/frynetworks/miner-ISM/config/install_config.enc
```

## 3. Running the Miner
```bash
./FRY_PoC_ISM_v1.0.0 # or sudo ./FRY_PoC_ISM_v1.0.0
```

- Configs must live at `config/miner_config.enc` and `config/install_config.enc` under your app directory (e.g., `/opt/frynetworks/miner-ISM/config/` or `~/Documents/miner-ISM/config`)
- Data/output: `/var/lib/frynetworks/miner-ISM/`
- Status files: `/var/lib/frynetworks/miner-ISM/status/status-YYYYMMDD.json`
- Measurements: `/var/lib/frynetworks/miner-ISM/measurements/`
- Ensure the miner’s service user can write to these directories:
```bash
  sudo mkdir -p /var/lib/frynetworks/miner-ISM/{status,measurements}
  sudo chown "$USER":"$USER" /var/lib/frynetworks/miner-ISM -R
```
  Do this once. Afterwards, if your runtime dir is under your home directory, you won't need sudo for normal operation.
- The program exits immediately if configs are missing or lease not held.
- For persistence, wrap in `systemd` or `supervisor`.

## 4. Measurement Files (Satellite)

- **Location:** `/var/lib/frynetworks/miner-ISM/measurements/measurements-Satellite-latest.json.enc`
- **Encryption:** Fernet; key derived from miner_key using PBKDF2-HMAC-SHA256 (salt: `b'measurements_key_v1'`, 100k iterations); no separate key file required.
- **Decrypted JSON shape:**
  ```json
  {
    "timestamp": "2025-11-23T12:00:05.123456Z",
    "miner_key": "ISM-DROPM82ED6LMQZ6STUPLEO5W3IFWUGOE",
    "group": "Satellite",
    "measurement": {
      "sats": 8,
      "fix": "GPS",
      "lat": 37.7749,
      "lon": -122.4194,
      "alt": 45.2,
      "hdop": 1.2
    }
  }
  ```
- **Fields:** `sats` (count), `fix` (NONE/GPS/DGPS/etc.), and optional `lat`/`lon`/`alt`/`hdop`.

## 5. Support and Recovery

- **Lease resets:** Contact FryNetworks  
- **Regenerating configs:** Use same `INSTALL_ID`  
- **Security:** Keep your miner key & install ID secure

---

## Common Issues

| Error                | Likely Cause                | Solution                                   |
|----------------------|-----------------------------|--------------------------------------------|
| Permission denied    | Missing exec bit/file perms | Run `chmod +x`, check ownership            |
| 409 Conflict         | Lease held by another ID    | Contact FryNetworks                        |
| Exits immediately    | Config missing/wrong lease  | Check file locations, verify lease         |

---

If you have further questions or need assistance, contact FryNetworks.