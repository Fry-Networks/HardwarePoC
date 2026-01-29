# ISM PoC – DropWireless (Linux aarch64)

This guide covers setting up and running the FryNetworks ISM (Indoor Satellite Miner) Proof of Connectivity binary on DropWireless aarch64 devices.

**Drop Wireless is responsible for:**

- Running the satellite/GNSS measurement hardware
- Placing measurement files in the expected input directory so the FryNetworks binary can read them
- Keeping the binary running (via `systemd` or `supervisor`)

The FryNetworks binary handles everything else (processing, reporting, uploads) automatically.

---

## 0. Package Contents (What Fry Ships)

- **Binary:** `FRY_PoC_ISM_v{VERSION}_linux_aarch64` (Linux ARM64)
- **Hash:** `FRY_PoC_ISM_v{VERSION}_linux_aarch64.sha256`
- **Helper Scripts:**
  - `create_miner_config.py`
  - `create_install_config.py`
- **Documentation:** (this file)

## 1. Verify and Stage Files

Verify checksum:
```bash
sha256sum -c FRY_PoC_ISM_v{VERSION}_linux_aarch64.sha256
```
Create a runtime directory with a `config` subfolder (example):
```bash
sudo mkdir -p /opt/frynetworks/miner-ISM/config
```
Copy the following files into the runtime directory:
- `FRY_PoC_ISM_v{VERSION}_linux_aarch64`
- `FRY_PoC_ISM_v{VERSION}_linux_aarch64.sha256`
- `create_miner_config.py`
- `create_install_config.py`

*If you prefer not to use sudo, place the runtime dir under your home directory (e.g., `~/Documents/miner-ISM/config`) and adjust paths below accordingly.*

Make the binary executable:
```bash
chmod +x FRY_PoC_ISM_v{VERSION}_linux_aarch64
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

- Success: 2xx
- `409 Conflict`: lease held by another install_id
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

## 3. Measurement Input (Drop Wireless Responsibility)

The FryNetworks binary expects satellite measurement files to be placed in:

```
/var/lib/frynetworks/miner-ISM/measurements/
```

Drop Wireless must ensure their GNSS/GPS hardware writes measurement data to this directory. The binary reads from this location on each interval cycle.

Create the directory and set permissions:
```bash
sudo mkdir -p /var/lib/frynetworks/miner-ISM/{status,measurements}
sudo chown "$USER":"$USER" /var/lib/frynetworks/miner-ISM -R
```

### Expected measurement format

The binary reads a CSV file from the measurements directory, one per day:

- `satellite_real_YYYYMMDD.csv` — measurement data (every ~10 minutes) used for backend upload

**CSV schema:**
```
timestamp,sats,fix,hdop,lat,lon
2025-11-23T12:00:05.123456Z,8,GPS,1.2,37.7749,-122.4194
2025-11-23T12:10:07.345678Z,7,GPS,1.4,37.7749,-122.4194
```

**Fields:**

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | ISO 8601 UTC | Measurement time |
| `sats` | int | Satellites in view |
| `fix` | string | Fix type: `NONE`, `GPS`, `DGPS`, `PPS`, `RTK`, `FLOAT_RTK` |
| `hdop` | float | Horizontal dilution of precision (optional) |
| `lat` | float | Latitude in decimal degrees (optional) |
| `lon` | float | Longitude in decimal degrees (optional) |

Files are append-only and rotate daily (new file per UTC date). The header row is written automatically on first write.

## 4. Running the Binary

```bash
./FRY_PoC_ISM_v{VERSION}_linux_aarch64
```

- Configs must live at `config/miner_config.enc` and `config/install_config.enc` under your app directory (e.g., `/opt/frynetworks/miner-ISM/config/` or `~/Documents/miner-ISM/config`)
- The program exits immediately if configs are missing or lease not held.
- For persistence, wrap in `systemd` or `supervisor`.

The binary automatically handles processing, backend reporting, and data uploads on each interval (default 10 minutes). No additional action is required from Drop Wireless once the binary is running and measurement files are being written to the expected directory.

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
| No measurements      | Files not in expected dir   | Verify GNSS hardware writes to `/var/lib/frynetworks/miner-ISM/measurements/` |
| exec format error    | Wrong architecture binary   | Ensure you are using the `_linux_aarch64` build on ARM64 hardware |

---

If you have further questions or need assistance, contact FryNetworks.
