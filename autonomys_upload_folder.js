#!/usr/bin/env node
/**
 * Autonomys Auto Drive Folder Upload
 *
 * Uploads an entire folder structure (e.g., hexID/bandwidth/hourly/) to Auto Drive.
 * This maintains the folder hierarchy automatically.
 */

import { createAutoDriveApi, fs } from '@autonomys/auto-drive';
import { NetworkId } from '@autonomys/auto-utils';
import * as fsNode from 'fs';
import * as path from 'path';

// Parse command line arguments
function parseArgs() {
    const args = process.argv.slice(2);
    const options = {
        apiKey: null,
        folderPath: null,
        encrypt: false,
        password: null,
        network: 'mainnet'
    };

    for (let i = 0; i < args.length; i++) {
        switch (args[i]) {
            case '--api-key':
                options.apiKey = args[++i];
                break;
            case '--folder':
                options.folderPath = args[++i];
                break;
            case '--encrypt':
                options.encrypt = args[++i] === 'true';
                break;
            case '--password':
                options.password = args[++i];
                break;
            case '--network':
                options.network = args[++i]; // 'mainnet' or 'testnet'
                break;
            case '--help':
                printHelp();
                process.exit(0);
        }
    }

    return options;
}

function printHelp() {
    console.log(`
Autonomys Auto Drive Folder Upload

Usage:
  node autonomys_upload_folder.js --api-key KEY --folder PATH [OPTIONS]

Required:
  --api-key KEY       Autonomys API key
  --folder PATH       Local folder path to upload (e.g., "temp_upload/851f9017fffffff")

Optional:
  --encrypt BOOL      Encrypt folder (true/false, default: false)
  --password PASS     Encryption password (required if --encrypt true)
  --network NAME      Network to use (mainnet/testnet, default: mainnet)
  --help              Show this help message

Example (no encryption):
  node autonomys_upload_folder.js \\
    --api-key "your-api-key" \\
    --folder "temp_upload/851f9017fffffff"

This will upload the entire folder structure, maintaining the hierarchy:
  851f9017fffffff/
    └── bandwidth/
         └── hourly/
              ├── 2026-01-21.parquet
              └── 2026-01-21.meta.json
`);
}

async function uploadFolder(options) {
    try {
        // Validate required options
        if (!options.apiKey) {
            throw new Error('API key is required (--api-key)');
        }
        if (!options.folderPath) {
            throw new Error('Folder path is required (--folder)');
        }
        if (options.encrypt && !options.password) {
            throw new Error('Password is required when encryption is enabled (--password)');
        }

        // Check if folder exists
        if (!fsNode.existsSync(options.folderPath)) {
            throw new Error(`Folder not found: ${options.folderPath}`);
        }

        // Verify it's a directory
        const stats = fsNode.statSync(options.folderPath);
        if (!stats.isDirectory()) {
            throw new Error(`Path is not a directory: ${options.folderPath}`);
        }

        console.log('Initializing Autonomys Auto Drive...');

        // Determine network ID
        const networkId = options.network === 'testnet' ? NetworkId.TAURUS : NetworkId.MAINNET;

        const api = createAutoDriveApi({
            apiKey: options.apiKey,
            network: networkId
        });

        console.log(`Uploading folder: ${options.folderPath}`);
        console.log(`  Network: ${options.network}`);
        console.log(`  Encryption: ${options.encrypt ? 'YES' : 'NO'}`);

        // Prepare upload options
        const uploadOptions = {
            uploadChunkSize: 1024 * 1024, // 1 MB chunks
            onProgress: (progress) => {
                const percent = Math.round(progress);
                if (percent % 10 === 0) { // Log every 10%
                    console.log(`  Upload progress: ${percent}%`);
                }
            }
        };

        // Add password if encryption is enabled
        if (options.encrypt) {
            uploadOptions.password = options.password;
        }

        // Upload the folder
        console.log('Starting upload...');
        const folderCID = await fs.uploadFolderFromFolderPath(
            api,
            options.folderPath,
            uploadOptions
        );

        console.log('Upload successful!');
        console.log(`  Folder CID: ${folderCID}`);
        console.log(`  Folder path: ${path.basename(options.folderPath)}`);

        // Output JSON result for Python to parse
        const output = {
            success: true,
            cid: folderCID,
            folderPath: options.folderPath,
            encrypted: options.encrypt
        };
        console.log('RESULT_JSON:', JSON.stringify(output));

        process.exit(0);

    } catch (error) {
        console.error('Upload failed:', error.message);
        console.error('Stack:', error.stack);

        // Output JSON error for Python to parse
        const output = {
            success: false,
            error: error.message,
            stack: error.stack
        };
        console.log('RESULT_JSON:', JSON.stringify(output));

        process.exit(1);
    }
}

// Main execution
const options = parseArgs();

if (!options.apiKey && !options.folderPath) {
    printHelp();
    process.exit(1);
}

uploadFolder(options);
