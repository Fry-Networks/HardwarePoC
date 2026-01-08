# FryNetworks Service IPC Queue - Developer README

This guide describes how the privileged service should process GUI write requests via a simple filesystem queue under ProgramData. The goal is to eliminate UAC prompts while keeping privileged writes inside the service.

## Overview

- GUI (user mode) enqueues write requests into `%PROGRAMDATA%\FryNetworks\miner-{CODE}\ops_queue`.
- Service (LocalSystem via NSSM) runs `miner_GUI.services.privileged_ops_daemon` to process these requests.
- Results are recorded under `ops_processed` and errors are logged via `log_step`.

## Queue Layout

```
%PROGRAMDATA%\FryNetworks\miner-{CODE}\
  ops_queue\            # Users: Modify (installer sets ACL)
  ops_processed\        # Service-owned
  config\               # Service-owned
  status\               # Service-owned
  measurements\         # Service-owned
```

## Request File Format (JSON)

Filename: `op_<timestamp_ms>_<uuid>_<op>.json`

```jsonc
{
  "id": "b6a0b0ce7f3f4f2a9e2c4c6a6e8a3b1f",
  "op": "write_config",               // or "write_measurement"
  "relative_path": "config/bright.json",  // for write_config
  "content": "{\"enabled\":true}",      // JSON string
  "group": "mysterium",                // for write_measurement
  "data_b64": "...",                   // base64(encrypted bytes)
  "created_at": "2026-01-08T12:34:56Z"
}
```

## Daemon Responsibilities

- Watch `ops_queue` every 500ms and process all `*.json` files.
- Validate payload, execute operation, write result to `ops_processed/<id>.done.json`.
- Move processed request to `ops_processed/<filename>.processed` or `.error`.
- Log outcomes via `log_step("ops_daemon_processed", {op, success})`.

Daemon entry point: `miner_GUI.services.privileged_ops_daemon.run()`

## Operations Implemented

- `write_config`: calls `privileged_ops.write_config_file(relative_path, content)`
- `write_measurement`: calls `privileged_ops.write_measurement_file(group, encrypted_bytes)`

## Error Handling

- Malformed JSON or missing fields → `.error` file written, logged.
- Base64 decode errors → `.error` file written, logged.
- Filesystem write errors → `.error` file written, logged.
- Processing loop exceptions → logged and loop continues.

## Logging

All events are logged via `miner_GUI.utils.data.log_step()` with keys:
- `ops_daemon_start`, `ops_daemon_processed`, `ops_daemon_handle_failed`, `ops_daemon_loop_error`
- `privileged_config_file_written`, `privileged_measurement_file_written`, and failure variants

## Local Development Notes

- The GUI uses `miner_GUI.utils.ops_queue_client` to enqueue requests.
- If an operation is not under ProgramData (e.g., dev path), GUI writes directly and bypasses the queue.
- The queue client ensures directories exist but will log if ACLs block creation.

## Manual Testing

1. Create a test request:
```powershell
$pd = "$env:ProgramData\FryNetworks\miner-BM\ops_queue"
$req = @{
  id = [guid]::NewGuid().ToString('N')
  op = 'write_config'
  relative_path = 'config/test.json'
  content = '{"hello": "world"}'
  created_at = (Get-Date).ToUniversalTime().ToString('s') + 'Z'
} | ConvertTo-Json -Compress
$fn = "op_" + [int](([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())) + "_" + ([guid]::NewGuid().ToString('N')) + "_write_config.json"
$req | Set-Content -Path (Join-Path $pd $fn) -Encoding UTF8
```
2. Start the daemon (in service context) and observe `ops_processed` and `config/test.json`.

## Performance & Robustness

- Polling at 500ms balances responsiveness and overhead.
- Atomic writes (temp → rename) minimize corruption risks.
- Processed files retained for audit; consider periodic cleanup.
- For higher throughput, consider using `ReadDirectoryChangesW` or a lightweight message bus.

## Next Enhancements (Optional)

- Add `read_config` and `delete_config` ops as needed.
- Add `status` updates batching.
- Add exponential backoff for repeated failures.
- Add healthcheck file under `ops_processed/health.json` with last loop timestamp.

## Ownership & Security

- Service owns and writes all ProgramData files.
- GUI may only write into `ops_queue` (installer sets Users: Modify on that folder).
- No direct elevation or token passing; filesystem queue is the trust boundary.

