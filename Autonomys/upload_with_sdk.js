#!/usr/bin/env node
import { spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { createAutoDriveApi, fs as driveFs } from '@autonomys/auto-drive';
import { NetworkId } from '@autonomys/auto-utils';

function read1Password(pathSpec = 'op://DataStorage/AutoDrive/AUTONOMYS_API_KEY', timeout = 30) {
  // Use op read to get the raw secret
  const res = spawnSync('op', ['read', pathSpec], { encoding: 'utf8', timeout: timeout * 1000 });
  if (res.error) throw res.error;
  if (res.status !== 0) throw new Error(`op failed: ${res.stderr}`);
  return res.stdout.trim();
}

async function main() {
  const argv = process.argv.slice(2);
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--use-1pw') args.use1pw = true;
    else if (a === '--onepw-path') args.onepw = argv[++i];
    else if (a === '--file') args.file = argv[++i];
    else if (a === '--api-key') args.apiKey = argv[++i];
    else if (a === '--debug') args.debug = true;
  }

  const filePath = args.file || 'C:/ProgramData/FryNetworks/miner-BM/measurements/hourly/2026-01-21.meta.json';
  if (!fs.existsSync(filePath)) {
    console.error('File not found:', filePath);
    process.exit(2);
  }

  let apiKey = args.apiKey || process.env.AUTONOMYS_API_KEY;
  if (!apiKey && args.use1pw) {
    try {
      apiKey = read1Password(args.onepw || 'op://DataStorage/AutoDrive/AUTONOMYS_API_KEY', 30);
    } catch (e) {
      console.error('Failed to read API key from 1Password:', e.message || e);
      process.exit(3);
    }
  }

  if (!apiKey) {
    console.error('No API key provided. Use --api-key, set AUTONOMYS_API_KEY, or --use-1pw.');
    process.exit(4);
  }

  if (args.debug) {
    console.log('DEBUG: apiKey masked:', apiKey.length > 8 ? apiKey.slice(0,4) + '...' + apiKey.slice(-4) : '*'.repeat(apiKey.length));
  }

  const api = createAutoDriveApi({ apiKey, network: NetworkId.MAINNET });

  try {
    const cid = await driveFs.uploadFileFromFilepath(api, filePath, {
      onProgress: (p) => {
        process.stdout.write(`\rProgress: ${Math.round(p)}%`);
      }
    });
    console.log('\nUploaded. CID:', cid);
    process.exit(0);
  } catch (e) {
    console.error('\nUpload failed:', e);
    process.exit(5);
  }
}

main();
