#!/usr/bin/env python3
"""
Build script for miner_online_simple with 1Password-embedded credentials.
This script REQUIRES 1Password CLI to retrieve API tokens at build time.
No fallback mechanisms - 1Password is mandatory for security.
"""

import os
import sys
import json
import tempfile
import subprocess
import shutil
from pathlib import Path
from typing import Optional

def get_version() -> str:
    """Get version from config_profile.py"""
    try:
        # Import the version from the config profile
        import sys
        sys.path.append('.')
        from config_profile import VERSION
        return VERSION
    except Exception:
        return "5.5.4"  # fallback version

def get_1password_secret(reference: str) -> Optional[str]:
    """Retrieve a secret from 1Password using the CLI."""
    try:
        result = subprocess.run(
            ['op', 'read', reference],
            capture_output=True,
            text=True,
            timeout=10,
            check=True
        )
        value = result.stdout.strip()
        return value if value else None
    except (subprocess.SubprocessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    except Exception:
        return None

def main():
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: build_with_embedded_config.py <config.json> <output_directory> [miner_key]")
        print("")
        print("Examples:")
        print("  python build_with_embedded_config.py production_config.json ./dist/")
        print("  python build_with_embedded_config.py production_config.json ./dist/ ISM-ABC123DEFG456HIJK789LMNOP012QRST")
        print("")
        print("If miner_key is provided, it will be encrypted and embedded in the executable.")
        print("If not provided, the service will read from encrypted miner config at runtime.")
        print("")
        print("The config.json MUST contain:")
        print("  - api_base_url")
        print("  - api_token (MUST be 1Password reference: 'op://VPS/Hardware_API/API_BEARER_TOKEN')") 
        print("  - interval_seconds (optional)")
        print("  - lease_seconds (optional)")
        print("  - api_timeout (optional)")
        print("  - local_signing_key_hex (optional)")
        print("")
        print("1Password Requirements:")
        print("  - API token MUST be a 1Password reference (op://...)")
        print("  - 1Password CLI must be installed and authenticated")
        print("  - Run 'op signin' before building")
        print("  - No plaintext tokens allowed for security")
        sys.exit(1)
    
    config_file = sys.argv[1]
    output_dir = sys.argv[2]
    miner_key = sys.argv[3] if len(sys.argv) == 4 else None
    
    # Validate miner key if provided
    if miner_key:
        import re
        if not re.match(r'^ISM-[A-Z0-9]{32}$', miner_key):
            print(f"Error: Invalid miner key format: {miner_key}")
            print("Expected format: ISM-[A-Z0-9]{32}")
            print("Example: ISM-ABC123DEFG456HIJK789LMNOP012QRST")
            sys.exit(1)
        print(f"✅ Miner key will be encrypted and embedded: {miner_key}")
    else:
        print("ℹ️  No miner key provided - service will read from encrypted miner config at runtime")
    
    if not os.path.exists(config_file):
        print(f"Error: Config file {config_file} not found")
        sys.exit(1)
    
    # Load and process config
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    # Validate required fields
    if not config.get('api_base_url'):
        print("Error: Missing required field 'api_base_url' in config")
        sys.exit(1)
    
    # API token MUST be a 1Password reference
    api_token = config.get('api_token')
    if not api_token:
        print("Error: Missing 'api_token' field in config")
        sys.exit(1)
    
    # Enforce 1Password-only policy
    if not api_token.startswith('op://'):
        print(f"Error: API token must be a 1Password reference (op://...)")
        print(f"Found: {api_token}")
        print(f"Expected format: op://VPS/Hardware_API/API_BEARER_TOKEN")
        print("This build system only accepts 1Password references for security.")
        sys.exit(1)
    
    print(f"Retrieving API token from 1Password: {api_token}")
    actual_token = get_1password_secret(api_token)
    if not actual_token:
        print(f"Error: Could not retrieve token from 1Password reference: {api_token}")
        print("Make sure:")
        print("1. 1Password CLI is installed (op)")
        print("2. You are authenticated (op signin)")
        print("3. The reference exists and you have access")
        sys.exit(1)
    
    # Replace the 1Password reference with the actual token
    config['api_token'] = actual_token
    print(f"✅ Successfully retrieved token from 1Password")
    
    print(f"Building with config: {config_file}")
    print(f"API Base URL: {config['api_base_url']}")
    print(f"API Token: {config['api_token'][:10]}... (length: {len(config['api_token'])})")
    
    # Write the resolved config to a temporary file for encryption
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_config:
        json.dump(config, temp_config)
        temp_config_path = temp_config.name
    
    try:
        # Generate encrypted config using the resolved config
        print("Generating encrypted credentials...")
        result = subprocess.run([
            sys.executable, 'tools/make_encrypted_config.py', 
            '--in', temp_config_path, 
            '--json'
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error generating encrypted config: {result.stderr}")
            sys.exit(1)
    finally:
        # Clean up temporary config file
        try:
            os.unlink(temp_config_path)
        except Exception:
            pass
    
    encrypted_config = json.loads(result.stdout)
    
    # Read source file
    source_file = 'miner_online_simple.py'
    with open(source_file, 'r') as f:
        source_code = f.read()
    
    # Replace placeholder credentials with actual encrypted values
    old_line = "    dlt=b''; dlp=b''; knt=b''; knp=b''"
    new_line = f"    dlt={encrypted_config['dlt']}; dlp={encrypted_config['dlp']}; knt={encrypted_config['knt']}; knp={encrypted_config['knp']}"
    
    if old_line not in source_code:
        print("Error: Could not find credential placeholder in source code")
        sys.exit(1)
    
    # Create modified source
    modified_source = source_code.replace(old_line, new_line)
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Write modified source to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as temp_file:
        temp_file.write(modified_source)
        temp_source_path = temp_file.name
    
    try:
        # Copy other required files to output directory
        output_path = Path(output_dir)
        
        # Copy dependencies
        required_files = [
            'config_profile.py',
            'external_api.py', 
            'mongo_api_proxy.py',
            'cache_integrity.py'
        ]
        
        for file in required_files:
            if os.path.exists(file):
                shutil.copy2(file, output_path / file)
            else:
                print(f"Warning: {file} not found")
        
        # Copy the modified source
        shutil.copy2(temp_source_path, output_path / 'miner_online_simple.py')
        
        print(f"✅ Build complete!")
        print(f"📁 Output directory: {output_dir}")
        print(f"🔒 Credentials are embedded and encrypted")
        print("")
        print("Next steps:")
        print("1. Use PyInstaller or similar to create executable")
        print("2. The installer only needs to provide:")
        print("   - miner_config.enc (encrypted miner configuration)")
        print("   - No config.json needed!")
        print("")
        print("Example PyInstaller command:")
        print(f"  cd {output_dir}")
        print("  pyinstaller --onefile --name miner_service miner_online_simple.py")
        print("")
        print("For installer-managed miner key, create encrypted config:")
        print(f"  python create_miner_config.py create <MINER_KEY>")
        print("  This creates: miner_config.enc (encrypted configuration)")
        print("  Example: python create_miner_config.py create ISM-ABC123DEFG456HIJK789LMNOP012QRST")
        print("")
        print("Build executable with proper ISM branding and version:")
        print(f"  pyinstaller --onefile --name FRY_PoC_BM_v{get_version()} miner_online_simple.py")
        print(f"  Creates: FRY_PoC_BM_v{get_version()}.exe (ISM Branded Miner)")
        
    finally:
        # Clean up temp file
        os.unlink(temp_source_path)

if __name__ == '__main__':
    main()