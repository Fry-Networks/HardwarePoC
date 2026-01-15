# Integration Config Examples

These examples show the minimal JSON shapes each integration expects. In production builds, files under ProgramData are written by the service via the ops queue (`enqueue_write_config`); during development you can write them directly under `config/` in the repo.

## Diiisco (`diiisco_config.json`)
- Required to enable: set `enabled` to true.
- `node_key` (identity): Embedded at build time from 1Password
- Optional: `api_port` (default 8080), `docker_container_name` (default `diiisco-node`), `network` (`mainnet` default).

```json
{
  "enabled": true,
  "api_port": 8080,
  "docker_container_name": "diiisco-node",
  "network": "mainnet"
}
```

## Presearch (`presearch_config.json`)
- Required to start: `enabled` true.
- Embedded at build time from 1Password: `registration_code`, `api_key`
- Optional: `docker_container_name` (default `presearch-node`).

```json
{
  "enabled": true,
  "docker_container_name": "presearch-node"
}
```

## Honeygain (two files: `honeygain.json` + encrypted `honeygain.enc`)
- `honeygain.json` holds non-secret settings: `enabled`, optional `sdk_root`/`library_path`/`log_dir`, `poll_seconds`.
- `honeygain.enc` stores the API key encrypted. The decrypted payload is a JSON object with `api_key` (and optional `config_version`). Do not hand-edit the encrypted file.

`honeygain.json`
```json
{
  "enabled": true,
  "sdk_root": "C:/ProgramData/FryNetworks/SDK/windows-honeygain-sdk",
  "library_path": "C:/ProgramData/FryNetworks/SDK/windows-honeygain-sdk/x64/bin/hgsdk.dll",
  "log_dir": "C:/ProgramData/FryNetworks/logs/honeygain",
  "poll_seconds": 60
}
```

Decrypted shape of `honeygain.enc`
```json
{
  "api_key": "HG-API-KEY-123",
  "config_version": "1.0"
}
```

## Bright (`brd_config.json`)
- Required: `app_id` from BrightData and `enabled` true.
- Optional: `app_name`, `logo_link` (or `app_logo`), `language`, `consent` boolean.

```json
{
  "enabled": true,
  "app_id": "bright-app-id-123",
  "app_name": "Fry Networks",
  "logo_link": "https://example.com/logo.png",
  "language": "en",
  "consent": true
}
```

## Where to place configs
- Service/runtime: `%ProgramData%/FryNetworks/<product>/config/` (written via ops queue when paths live under ProgramData).
- Development: repo `config/` folder or `app_dir()/config` fallback. Use the same file names above.
