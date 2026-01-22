#!/usr/bin/env node
import { spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { createAutoDriveApi, fs as driveFs } from '@autonomys/auto-drive';
import { NetworkId } from '@autonomys/auto-utils';

function read1Password(pathSpec = 'op://DataStorage/AutoDrive/AUTONOMYS_API_KEY', timeout = 30) {
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
    else if (a === '--folder') args.folder = argv[++i];
    else if (a === '--dest-folder') args.destFolder = argv[++i];
    else if (a === '--api-key') args.apiKey = argv[++i];
    else if (a === '--debug') args.debug = true;
  }

  const folderPath = args.folder || 'C:/ProgramData/FryNetworks/miner-BM/measurements/hourly';
  if (!fs.existsSync(folderPath)) {
    console.error('Folder not found:', folderPath);
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
    // Build options similar to the example: allow chunk size and optional
    // encryption password, and provide a progress callback.
    const options = {
      uploadChunkSize: args.chunkSize || 1024 * 1024,
      password: args.password || undefined,
      onProgress: (progress) => {
        process.stdout.write(`\rProgress: ${Math.round(progress)}%`);
      }
    };

    // If a destFolder was explicitly provided, include it (normalized)
    if (args.destFolder) {
      // normalize to avoid leading/trailing slashes which some APIs mishandle
      options.destinationFolder = args.destFolder.replace(/^\/+|\/+$/g, '');
    }

    // Summarize files to be uploaded (count + total bytes) to help debug failures
    function walkSync(dir) {
      const list = [];
      const items = fs.readdirSync(dir, { withFileTypes: true });
      for (const it of items) {
        const p = path.join(dir, it.name);
        if (it.isDirectory()) {
          list.push(...walkSync(p));
        } else if (it.isFile()) {
          const st = fs.statSync(p);
          list.push({ path: p, size: st.size });
        }
      }
      return list;
    }

    let files = [];
    try {
      files = walkSync(folderPath);
    } catch (e) {
      // ignore walking errors — we still try the upload which will report
    }

    const totalBytes = files.reduce((s, f) => s + f.size, 0);
    if (args.debug) {
      console.log('\nDEBUG: Upload parameters:');
      console.log('  folderPath:', folderPath);
      console.log('  fileCount:', files.length);
      console.log('  totalBytes:', totalBytes);
      console.log('  options:', JSON.stringify(Object.assign({}, options, { password: options.password ? '***' : undefined }), null, 2));
      if (files.length <= 20) console.log('  files:', files.map(f => ({ p: f.path, size: f.size })));
    }

    // Retry loop for transient failures
    const maxTries = 3;
    let lastErr = null;
    for (let attempt = 1; attempt <= maxTries; attempt++) {
      try {
        if (attempt > 1) console.log(`\nRetrying upload (attempt ${attempt}/${maxTries})...`);
        const cid = await driveFs.uploadFolderFromFolderPath(api, folderPath, options);
        console.log('\nUploaded folder. CID:', cid);
        process.exit(0);
      } catch (e) {
        lastErr = e;
        console.error(`\nUpload attempt ${attempt} failed:`, e && e.message ? e.message : e);
        // small backoff
        await new Promise(r => setTimeout(r, 1000 * attempt));
      }
    }
    console.error('\nUpload failed (all retries):', lastErr);
    process.exit(5);
  } catch (e) {
    console.error('\nUnexpected failure preparing upload:', e);
    process.exit(5);
  }
}

main();
