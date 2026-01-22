# Autonomys SDK uploader

This small Node.js helper uses the official Autonomys `@autonomys/auto-drive` SDK to upload a local file by calling `uploadFileFromFilepath`.

Prerequisites

- Node.js (v16+ recommended)
- `op` (1Password CLI) if you want to read the API key from 1Password

Setup

1. Install dependencies:

```bash
cd Autonomys
npm install
```

2. Run the uploader (examples):

- Use 1Password to read the API key (default path `op://DataStorage/AutoDrive/AUTONOMYS_API_KEY`):

```bash
node upload_with_sdk.js --use-1pw --file "C:\ProgramData\FryNetworks\miner-BM\measurements\hourly\2026-01-21.meta.json"
```

- Provide the API key directly:

```bash
node upload_with_sdk.js --api-key YOUR_API_KEY --file "C:\...\2026-01-21.meta.json"
```

- Show debug masked key:

```bash
node upload_with_sdk.js --use-1pw --debug --file "C:\...\2026-01-21.meta.json"
```

Notes

- The uploader prints a progress indicator and the resulting CID on success.
- The script uses the SDK's `createAutoDriveApi` to authenticate requests; `X-Auth-Provider` is not required when using the SDK because the SDK sets correct headers.

If you want, I can run the uploader here using your 1Password API key (the script will call `op read`), or adapt the script to use a presigned / create-session flow instead. Choose which you'd like me to do next.
