# GUI → BM Service Handoff: Minimum Requirements

Use this as the single-page contract for GUI integration with the BM service (v1.6.4).

## IPC Basics
- Requests: `C:\ProgramData\FryNetworks\miner-BM\ops_queue\<id>.json`
- Responses: `C:\ProgramData\FryNetworks\miner-BM\ops_processed\<id>.done.json`
- Supported ops: `reload_config`, `write_config`, `write_measurement`, `add_firewall_rule`, `remove_firewall_rule`, `setup_mysterium_firewall`, `setup_presearch_firewall`, `setup_diiisco_firewall`, `setup_spaceacres_firewall`

## Critical Rules (must follow)
- **Encoding:** All config writes must be UTF-8 **without BOM**. BOM will be rejected/ignored.
- **JSON validation:** Validate client-side before `write_config`. Service will silently keep the last good config if JSON is bad.
- **Reload required:** After any `write_config`, immediately send `reload_config` to activate changes.
- **Timestamps:** All measurements must use UTC (e.g., `2026-01-09T00:36:24+00:00`).
- **Tool names by miner type:**
	- BM: `mysterium`, `bright`, `honeygain`
	- SDN: `spaceacres`
	- SVN: `presearch`, `diiisco`
- **Result check:** Always read the `done.json` and honor `success`/`error`.

## Minimal Request Shapes
- `reload_config`
```json
{ "op": "reload_config" }
```
- `write_config`
```json
{ "op": "write_config", "relative_path": "miner_config.json", "content": "{...json...}" }
```
- `write_measurement`
```json
{ "op": "write_measurement", "tool": "mysterium", "data_b64": "<base64 of encrypted measurement payload>" }
```

## Expected Behaviors
- Bad config: Service logs warning, skips file, keeps previous config, still returns `success: true` on reload (design for availability).
- Concurrency: Safe to batch (~17–18 ops/sec observed). Responses always written to `ops_processed`.
- Performance: ~1.6% CPU, ~6.5 MB RAM; negligible impact from GUI calls.

## GUI Responsibilities
1) Validate JSON and encoding before sending.
2) Send `write_config`, then `reload_config`; confirm `success` in `done.json`.
3) Use UTC timestamps for all measurements.
4) Only include enabled/valid tools per miner type (BM: mysterium/bright/honeygain).
5) Surface errors from `done.json` to the user (e.g., permission issues for firewall ops).

## Quick Health Checks (optional)
- Read `C:\ProgramData\FryNetworks\miner-BM\cache\latest.json` for latest measurement info.
- Read `C:\ProgramData\FryNetworks\miner-BM\logs\service.err.log` for warnings.

## Reference Docs
- GUI_DEVELOPER_GUIDE.md (full API + patterns)
- IPC_API_QUICK_REFERENCE.md (copy/paste snippets)
- TEST_CAMPAIGN_SUMMARY.md (expected behaviors & metrics)
