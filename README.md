# HardwarePoC - FryNetworks Miner Service

Automated monitoring service for FryNetworks miners. Reports Proof of Coverage (PoC), Proof of Location (PoL), and hardware status metrics to the FryNetworks backend.

## Supported Miners

| Code | Type | Description |
|------|------|-------------|
| BM | Bandwidth | Bandwidth measurement + SDK tools |
| IDM / ODM | Decibel | Indoor/outdoor noise monitoring |
| ISM / OSM | Satellite | Indoor/outdoor satellite signal |
| IRM | Radiation | Indoor radiation measurement |
| AEM | AI Edge | Olostep Browser AI workloads |
| RDN | Node | Presearch + Diiisco (Docker) |
| SDN | Storage | Space Acres (Autonomys network) |
| SVN | Validator | Storage validation node |

## Quick Start

### Build (requires 1Password CLI)

```powershell
# Windows
.\build_PoC_windows.ps1 -Code RDN -Version 1.9.12

# Linux
./build_PoC_linux.sh RDN 1.9.12
```

### Deploy

1. Build the service executable
2. Create encrypted configs via installer (`miner_config.enc`, `install_config.enc`)
3. Acquire installation lease via backend API
4. Run as Windows service (NSSM) or systemd unit

## Project Structure

```
miner_online_simple.py       # Main service (measurement loop, IPC, tool management)
config_profile.py            # Miner code/version constants (auto-generated at build)
external_api.py              # Backend HTTP client
mongo_api_proxy.py           # MongoDB proxy client
cache_integrity.py           # Local cache signing/verification
poi_monitor_aem.py           # Olostep Browser metrics for AEM
measurements/                # Measurement pipeline
  collector.py               #   CSV collection + backend upload
  csv_writer.py              #   CSV schemas per miner type
  tools.py                   #   Tool polling (presearch, diiisco, space acres)
  autonomys_*.py             #   Autonomys data pipeline (aggregate, redact, upload)
  secrets_manager.py         #   1Password credential retrieval
docker/                      # Diiisco Docker build files
docs/                        # Architecture, integration guides, runbooks
tests/                       # Unit and integration tests
scripts/                     # Smoke tests
tools/
  make_encrypted_config.py   # API credential encryption utility
build_PoC_windows.ps1        # Windows build script
build_PoC_linux.sh           # Linux build script
build_with_embedded_config.py # Python build helper
create_miner_config.py       # Miner key encryption utility
create_install_config.py     # Install ID encryption utility
```

## Documentation

See [docs/](docs/) for detailed guides:

- [docs/README.md](docs/README.md) - Full service documentation
- [docs/README_GUI_Integration.md](docs/README_GUI_Integration.md) - GUI integration guide
- [docs/README_ServiceIPC.md](docs/README_ServiceIPC.md) - Service IPC protocol
- [docs/GUI_SPACE_ACRES.md](docs/GUI_SPACE_ACRES.md) - Space Acres (SDN)
- [docs/DIIISCO_INTEGRATION.md](docs/DIIISCO_INTEGRATION.md) - Diiisco (RDN)
- [docs/1PASSWORD_SETUP.md](docs/1PASSWORD_SETUP.md) - Credential management

## License

Proprietary - Fry Networks LLC
