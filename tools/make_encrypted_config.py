#!/usr/bin/env python3
import argparse, os, json, base64, secrets
from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

def enc_blob():
    key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(16)
    return key, nonce

def owen_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> bytes:
    cipher = Cipher(algorithms.ChaCha20(key, nonce), mode=None, backend=default_backend())
    enc = cipher.encryptor()
    return nonce + enc.update(plaintext) + enc.finalize()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", required=True, help="config.json with mongo_uri, tlsCAFile (optional), interval_seconds")
    ap.add_argument("--json", dest="as_json", action="store_true", help="emit JSON with Python byte repr strings")
    args = ap.parse_args()

    # Accept UTF-8 files with or without BOM written by various tools
    cfg = json.load(open(args.infile, "r", encoding="utf-8-sig"))
    fkey = Fernet.generate_key()
    token = Fernet(fkey).encrypt(json.dumps(cfg, separators=(",", ":")).encode("utf-8"))

    k1, n1 = enc_blob(); dlp = owen_encrypt(k1, n1, fkey)          # encrypt Fernet key
    k2, n2 = enc_blob(); knp = owen_encrypt(k2, n2, token)         # encrypt config token

    if args.as_json:
        out = {"dlt": repr(k1), "dlp": repr(dlp), "knt": repr(k2), "knp": repr(knp)}
        print(json.dumps(out, separators=(",", ":")))
    else:
        print("Copy these 4 Python byte literals into miner_online_simple.decrypt_config():")
        print("dlt =", repr(k1))
        print("dlp =", repr(dlp))
        print("knt =", repr(k2))
        print("knp =", repr(knp))

if __name__ == "__main__":
    main()

