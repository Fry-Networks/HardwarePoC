#!/usr/bin/env node
/**
 * Autonomys Auto Drive Upload Script
 *
 * Uses the official @autonomys/auto-drive SDK to upload files.
 * Called from Python for reliable uploads with proper encryption handling.
 */

const fs = require('fs');
const path = require('path');

// Parse command line arguments
function parseArgs() {
    const args = process.argv.slice(2);
    const options = {
        apiKey: null,
        localPath: null,
        remotePath: null,
        encrypt: false,
        password: null
    };

    for (let i = 0; i < args.length; i++) {
        switch (args[i]) {
            case '--api-key':
                options.apiKey = args[++i];
                break;
            case '--local':
                options.localPath = args[++i];
                break;
            case '--remote':
                options.remotePath = args[++i];
                break;
            case '--encrypt':
                options.encrypt = args[++i] === 'true';
                break;
            case '--password':
                options.password = args[++i];
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
Autonomys Auto Drive Upload Script

Usage:
  node autonomys_upload_node.js --api-key KEY --local PATH --remote PATH [OPTIONS]

Required:
  --api-key KEY       Autonomys API key
  --local PATH        Local file path to upload
  --remote PATH       Remote path on Auto Drive (e.g., "851f.../bandwidth/hourly/2026-01-21.parquet")

Optional:
  --encrypt BOOL      Encrypt file (true/false, default: false)
  --password PASS     Encryption password (required if --encrypt true)
  --help              Show this help message

Example (no encryption):
  node autonomys_upload_node.js \\
    --api-key "your-api-key" \\
    --local "C:/data/file.parquet" \\
    --remote "851f9017fffffff/bandwidth/hourly/2026-01-21.parquet"

Example (with encryption):
  node autonomys_upload_node.js \\
    --api-key "your-api-key" \\
    --local "C:/data/file.parquet" \\
    --remote "851f9017fffffff/bandwidth/hourly/2026-01-21.parquet" \\
    --encrypt true \\
    --password "my-secret"
`);
}

async function uploadFile(options) {
    try {
        // Validate required options
        if (!options.apiKey) {
            throw new Error('API key is required (--api-key)');
        }
        if (!options.localPath) {
            throw new Error('Local path is required (--local)');
        }
        if (!options.remotePath) {
            throw new Error('Remote path is required (--remote)');
        }
        if (options.encrypt && !options.password) {
            throw new Error('Password is required when encryption is enabled (--password)');
        }

        // Check if file exists
        if (!fs.existsSync(options.localPath)) {
            throw new Error(`File not found: ${options.localPath}`);
        }

        // Dynamic import of the Auto Drive SDK
        const { AutonomysDrive } = await import('@autonomys/auto-drive');

        console.log('Initializing Autonomys Auto Drive...');
        const drive = new AutonomysDrive({
            apiKey: options.apiKey
        });

        console.log(`Uploading: ${path.basename(options.localPath)}`);
        console.log(`  From: ${options.localPath}`);
        console.log(`  To: ${options.remotePath}`);
        console.log(`  Encryption: ${options.encrypt ? 'YES' : 'NO'}`);

        // Prepare upload options
        const uploadOptions = {
            compression: true
        };

        // Add password if encryption is enabled
        if (options.encrypt) {
            uploadOptions.password = options.password;
        }

        // Upload the file
        const result = await drive.uploadFileFromFilepath({
            filepath: options.localPath,
            remotePath: options.remotePath,
            ...uploadOptions
        });

        console.log('Upload successful!');
        console.log(`  CID: ${result.cid || 'N/A'}`);
        console.log(`  Remote path: ${options.remotePath}`);

        // Output JSON result for Python to parse
        const output = {
            success: true,
            cid: result.cid,
            remotePath: options.remotePath,
            encrypted: options.encrypt
        };
        console.log('RESULT_JSON:', JSON.stringify(output));

        process.exit(0);

    } catch (error) {
        console.error('Upload failed:', error.message);

        // Output JSON error for Python to parse
        const output = {
            success: false,
            error: error.message
        };
        console.log('RESULT_JSON:', JSON.stringify(output));

        process.exit(1);
    }
}

// Main execution
const options = parseArgs();

if (!options.apiKey && !options.localPath) {
    printHelp();
    process.exit(1);
}

uploadFile(options);
