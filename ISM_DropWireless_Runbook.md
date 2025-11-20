# ISM Partner PoC – DropWireless (Linux Only)

Partner-facing runbook for DropWireless. We provide the Linux-ready ISM PoC bundle; DropWireless generates their own configs and acquires the lease.

---

## 0. Package Contents (what Fry ships)

- Binary: `release/ISM/FRY_PoC_ISM_v1.0.0`
- Hash: `release/ISM/FRY_PoC_ISM_v1.0.0.sha256`
- Helper scripts:
  - `create_miner_config.py`
  - `create_install_config.py`
- Documentation (this file)

---

## 1. Verify and Stage Files (DropWireless)

1. Verify checksum:
   ```bash
   sha256sum -c FRY_PoC_ISM_v1.0.0.sha256
   ```
2. Create a runtime directory (example: `/opt/frynetworks/ism-miner`).
3. Copy in:
   - `FRY_PoC_ISM_v1.0.0`
   - `FRY_PoC_ISM_v1.0.0.sha256`
   - `create_miner_config.py`
   - `create_install_config.py`
4. Make the binary executable:
   ```bash
   chmod +x FRY_PoC_ISM_v1.0.0
   ```

---

## 2. Generate Encrypted Configs (DropWireless)

You generate both configs locally.  
**Keep all plaintext values (miner key + install ID) in your secure vault.**

### 2.1 Miner config
```bash
python3 create_miner_config.py create ISM-DROPM82ED6LMQZ6STUPLEO5W3IFWUGOE --output miner_config.enc
```

### 2.2 Install ID
Generate a persistent UUIDv4:
```bash
python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
```

### 2.3 Acquire lease (required before creating install config)
Follow Section 3. Once the lease is held by your `INSTALL_ID`, proceed.

### 2.4 Install config
```bash
python3 create_install_config.py create   --install-id "c8425ebe-4657-4dcd-86c0-2bdc89baedb8"   --output install_config.enc
```

### 2.5 Permissions
```bash
chmod 600 *.enc
```

---

## 3. Lease Acquisition via Hardware API (DropWireless)

- **Auth token** (stored in 1Password):  
  `op://VPS/Hardware_API/API_BEARER_TOKEN_DROPWIRELESS`
- **Base URL:** `https://hardwareapi.frynetworks.com/`
- **Miner key:** provided by Fry  
- **Install ID:** the UUID you generated above

```bash
API_BASE="https://hardwareapi.frynetworks.com"
API_TOKEN="API_BEARER_TOKEN_DROPWIRELESS"
MINER_KEY="ISM-DROPM82ED6LMQZ6STUPLEO5W3IFWUGOE"
INSTALL_ID="c8425ebe-4657-4dcd-86c0-2bdc89baedb8"

# 1) Check current lease state
curl -s -H "Authorization: Bearer ${API_TOKEN}"   "${API_BASE}/installations/${MINER_KEY}/leases/current"

# If active && install_id != INSTALL_ID → someone else owns the lease.
# Do not proceed until cleared by Fry.

# 2) Acquire (first time)
curl -i -X POST -H "Authorization: Bearer ${API_TOKEN}"   "${API_BASE}/installations/${MINER_KEY}/leases/${INSTALL_ID}"

# 3) Renew (subsequent runs)
curl -i -X PATCH -H "Authorization: Bearer ${API_TOKEN}"   "${API_BASE}/installations/${MINER_KEY}/leases/${INSTALL_ID}"
```

- Success → 2xx  
- `409 Conflict` → lease held by another install_id  
- Do **not** rotate `INSTALL_ID` once created

---

## 4. Running the Miner

```bash
./FRY_PoC_ISM_v1.0.0
```

- Exits immediately if configs are missing or lease not held  
- Wrap in systemd/supervisor for persistence

---

## 5. Support and Recovery

- Lease resets: contact Fry Networks  
- Regenerating configs: use same `INSTALL_ID`  
- Secure the miner key + install ID

---

## 6. Partner Self-Service Lease Flow (Optional)

If a partner self-manages their lease:

```bash
API_BASE="https://hardwareapi.frynetworks.com"
API_TOKEN="$(op read op://VPS/Hardware_API/API_BEARER_TOKEN_DROPWIRELESS)"
MINER_KEY="ISM-REPLACE_ME"
INSTALL_ID="GENERATED_UUID"
```

Same check → acquire → renew flow as Section 3.

---

## 7. DropWireless Operational Summary

1. Verify checksum  
2. Create runtime directory  
3. Place encrypted configs  
4. Run binary  
5. Contact Fry for lease resets

---

## 8. Internal Delivery Checklist

- [ ] Git state clean  
- [ ] `config_profile.py` updated  
- [ ] Binary built and present  
- [ ] SHA256 stored  
- [ ] Scripts + docs included  
- [ ] Tarball validated  
- [ ] Partner ticket/email updated  
