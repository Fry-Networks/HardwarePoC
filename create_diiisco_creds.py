#!/usr/bin/env python3
"""
Create encrypted diiisco credentials file.
This tool encrypts Algorand credentials (address + mnemonic) into a
diiisco_creds.enc file that the service decrypts at runtime.

Usage:
  # Encrypt (one-time, then store .enc in 1Password):
  python create_diiisco_creds.py create --algo-add <ADDRESS> --algo-pp <MNEMONIC>

  # Verify (prints address only, never the mnemonic):
  python create_diiisco_creds.py read diiisco_creds.enc
"""

import os
import sys
import json
import argparse

# Add tools directory to path
tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
if os.path.exists(tools_dir):
    sys.path.insert(0, tools_dir)

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

SALT = b'diiisco_creds_salt_v1'
PASSWORD = "diiisco_creds_key_v1"
ITERATIONS = 100000


def _derive_key() -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SALT,
        iterations=ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(PASSWORD.encode()))


def encrypt_diiisco_creds(algo_add: str, algo_pp: str) -> dict:
    """Encrypt diiisco credentials and return the .enc payload."""
    key = _derive_key()
    payload = {"diiisco_algo_add": algo_add, "diiisco_algo_pp": algo_pp}
    encrypted = Fernet(key).encrypt(json.dumps(payload).encode())
    return {"data": encrypted.decode(), "version": "1.0"}


def decrypt_diiisco_creds(encrypted_data: dict) -> dict:
    """Decrypt a diiisco_creds.enc payload."""
    key = _derive_key()
    decrypted = Fernet(key).decrypt(encrypted_data['data'].encode())
    return json.loads(decrypted)


def create(args):
    enc = encrypt_diiisco_creds(args.algo_add, args.algo_pp)
    output = args.output or "diiisco_creds.enc"
    out_dir = os.path.dirname(os.path.abspath(output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output, 'w') as f:
        json.dump(enc, f)
    print(f"Created encrypted diiisco credentials: {output}")
    print(f"Algorand address: {args.algo_add}")
    print("Store this .enc file in 1Password (never the raw mnemonic).")


def read(args):
    with open(args.config_file, 'r') as f:
        encrypted_data = json.load(f)
    creds = decrypt_diiisco_creds(encrypted_data)
    print(f"Algorand address: {creds.get('diiisco_algo_add', '(missing)')}")
    pp = creds.get('diiisco_algo_pp', '')
    if pp:
        words = pp.split()
        print(f"Mnemonic: {words[0]} ... ({len(words)} words)")
    else:
        print("Mnemonic: (missing)")


def main():
    parser = argparse.ArgumentParser(description="Manage encrypted diiisco credentials")
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    create_parser = subparsers.add_parser('create', help='Create encrypted diiisco creds')
    create_parser.add_argument('--algo-add', required=True, help='Algorand address')
    create_parser.add_argument('--algo-pp', required=True, help='Algorand mnemonic')
    create_parser.add_argument('--output', '-o', help='Output file path (default: diiisco_creds.enc)')

    read_parser = subparsers.add_parser('read', help='Read encrypted diiisco creds')
    read_parser.add_argument('config_file', help='Path to encrypted .enc file')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == 'create':
            create(args)
        elif args.command == 'read':
            read(args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
