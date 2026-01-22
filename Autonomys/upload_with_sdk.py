#!/usr/bin/env python3
"""Python wrapper that uses the Autonomys Node SDK to upload a folder.

It calls the included `upload_folder_with_sdk.js` Node script. The API key
is read in this order: explicit `--api-key`, `AUTONOMYS_API_KEY` env var, or
from 1Password via `op read <path>` if `--use-1pw` is set.

This wrapper passes the API key to the Node process via environment variable
`AUTONOMYS_API_KEY` (safer than command-line args).
"""
import os
import subprocess
import argparse
import sys

def read_api_key_from_1password(op_path: str = "op://DataStorage/AutoDrive/AUTONOMYS_API_KEY", timeout: int = 30):
    try:
        proc = subprocess.run(["op", "read", op_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("1Password read timeout") from e
    except FileNotFoundError:
        raise RuntimeError("1Password CLI 'op' not found in PATH")
    if proc.returncode != 0:
        raise RuntimeError(f"op failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def run_node_folder_upload(folder: str, dest_folder: str | None, api_key: str | None, use_1pw: bool, onepw_path: str | None, debug: bool):
    node_script = os.path.join(os.path.dirname(__file__), 'upload_folder_with_sdk.js')
    if not os.path.exists(node_script):
        raise FileNotFoundError(f"Node script not found: {node_script}")

    cmd = ['node', node_script, '--folder', folder]
    if dest_folder:
        cmd += ['--dest-folder', dest_folder]
    if use_1pw:
        cmd += ['--use-1pw']
        if onepw_path:
            cmd += ['--onepw-path', onepw_path]
    if debug:
        cmd += ['--debug']

    env = os.environ.copy()
    if api_key:
        env['AUTONOMYS_API_KEY'] = api_key

    print('Running Node uploader...')
    proc = subprocess.run(cmd, env=env)
    return proc.returncode


def main():
    parser = argparse.ArgumentParser(description='Upload a folder to Autonomys using the official Node SDK via a Python wrapper.')
    parser.add_argument('--folder', required=False, default=r'C:\ProgramData\FryNetworks\miner-BM\measurements\hourly', help='Local folder to upload')
    parser.add_argument('--dest-folder', required=False, help='Destination folder path in Autonomys Drive (e.g. miner-BM/measurements/hourly)')
    parser.add_argument('--api-key', required=False, help='Autonomys API key')
    parser.add_argument('--use-1pw', action='store_true', help='Read API key from 1Password')
    parser.add_argument('--onepw-path', default='op://DataStorage/AutoDrive/AUTONOMYS_API_KEY', help='1Password path to read the API key')
    parser.add_argument('--debug', action='store_true', help='Show debug info (masked)')

    args = parser.parse_args()

    api_key = args.api_key or os.environ.get('AUTONOMYS_API_KEY')
    if not api_key and args.use_1pw:
        try:
            api_key = read_api_key_from_1password(args.onepw_path)
        except Exception as e:
            print('Failed to read API key from 1Password:', e)
            sys.exit(2)

    if not api_key and not args.use_1pw:
        # We'll still attempt to run; Node script will error if no key
        api_key = None

    rc = run_node_folder_upload(args.folder, args.dest_folder, api_key, args.use_1pw, args.onepw_path, args.debug)
    if rc != 0:
        print('Upload failed (node script exit code', rc, ')')
    sys.exit(rc)

if __name__ == '__main__':
    main()
