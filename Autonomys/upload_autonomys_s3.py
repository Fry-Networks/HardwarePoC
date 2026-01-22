#!/usr/bin/env python3
"""
upload_autonomys_s3.py

Utilities to:
- validate an Autonomys API key via GET /api/accounts/@me
- upload a local folder to Autonomys S3-layer using either:
    - presigned PUT URLs returned by Autonomys, or
    - temporary S3 credentials (boto3)
- notify Autonomys to ingest the uploaded objects (you must supply the exact ingest URL/payload used by Autonomys)

Requirements:
  pip install requests boto3 tqdm

Example flow (presigned URLs):
  1. Call your Autonomys endpoint to create an upload session (not implemented here because the exact endpoint/payload
     depends on Autonomys docs). The server should return either:
       - a map of object keys -> presigned PUT URL, or
       - temporary S3 credentials + bucket/prefix
  2. Use upload_folder_using_presigned() or upload_folder_using_s3_creds()
  3. Call notify_autonomys_ingest() with the ingest URL/details to get the CID.

Adjust constants/URLs as needed.
"""

import os
import json
import threading
import sys
import subprocess
from typing import Callable, Dict, Optional, Tuple, Union, Any
import requests
from tqdm import tqdm

# Optional boto3 import (only used if using S3 credentials flow)
try:
    import boto3
    from boto3.s3.transfer import TransferConfig
except Exception:
    boto3 = None  # lazy
    TransferConfig = None

# --- Helpers ---

def get_account_info(api_key: str, base_url: str = "https://mainnet.auto-drive.autonomys.xyz/api", timeout: int = 10, extra_headers: Optional[Dict[str, str]] = None) -> dict:
    """
    Validate API key and return account info via GET /api/accounts/@me.
    Raise requests.HTTPError on non-2xx.
    """
    url = base_url.rstrip("/") + "/accounts/@me"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if extra_headers and isinstance(extra_headers, dict):
        headers.update(extra_headers)
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def validate_api_key(api_key: str, base_url: str = "https://mainnet.auto-drive.autonomys.xyz/api", timeout: int = 10, extra_headers: Optional[Dict[str, str]] = None) -> Tuple[bool, Optional[dict]]:
    """
    Validate the API key and return (True, account_info) on success or (False, error_info) on failure.
    Error info is a dict with details where possible.
    """
    try:
        info = get_account_info(api_key, base_url=base_url, timeout=timeout, extra_headers=extra_headers)
        return True, info
    except requests.HTTPError as e:
        try:
            return False, {"status_code": e.response.status_code, "body": e.response.text}
        except Exception:
            return False, {"error": str(e)}
    except Exception as e:
        return False, {"error": str(e)}


def probe_api_key_headers(api_key: str, base_url: str = "https://mainnet.auto-drive.autonomys.xyz/api", timeout: int = 10):
    """
    Try common API key header formats against GET /accounts/@me and return list of (name, headers, status, body_or_error).
    Useful for debugging when the API returns 401 for the expected Bearer scheme.
    """
    url = base_url.rstrip("/") + "/accounts/@me"
    variants = [
        ("Authorization: Bearer <key>", {"Authorization": f"Bearer {api_key}"}),
        ("Authorization: <key>", {"Authorization": api_key}),
        ("Authorization: Token <key>", {"Authorization": f"Token {api_key}"}),
        ("Authorization: ApiKey <key>", {"Authorization": f"ApiKey {api_key}"}),
        ("X-API-Key", {"X-API-Key": api_key}),
        ("x-api-key", {"x-api-key": api_key}),
    ]
    results = []
    for name, headers in variants:
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            results.append((name, headers, r.status_code, r.text))
        except Exception as e:
            results.append((name, headers, None, str(e)))
    # also try as query param
    try:
        qurl = url + ("?api_key=" + api_key)
        r = requests.get(qurl, timeout=timeout)
        results.append(("query: ?api_key=", {"_query": True}, r.status_code, r.text))
    except Exception as e:
        results.append(("query: ?api_key=", {"_query": True}, None, str(e)))

    return results


def read_api_key_from_1password(op_path: str = "op://DataStorage/AutoDrive/AUTONOMYS_API_KEY", timeout: int = 30) -> Tuple[bool, Optional[dict]]:
    """
    Read a secret from 1Password using the `op` CLI. Returns (True, {"key":str}) on success
    or (False, {"error": ...}) on failure. This call allows interactive signin (stdin inherited)
    and will time out after `timeout` seconds.
    """
    try:
        proc = subprocess.run(["op", "read", op_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=None, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return False, {"error": "timeout", "detail": str(e)}
    except FileNotFoundError:
        return False, {"error": "op-not-found", "detail": "1Password CLI 'op' not found in PATH"}
    except Exception as e:
        return False, {"error": "failed", "detail": str(e)}

    if proc.returncode != 0:
        return False, {"error": "op-failed", "stderr": proc.stderr.strip()}

    key = proc.stdout.strip()
    if not key:
        return False, {"error": "empty-key"}
    return True, {"key": key}


def read_api_key_from_1password_item(op_item_path: str = "op://DataStorage/AutoDrive/AUTONOMYS_API_KEY", timeout: int = 30) -> Tuple[bool, Optional[dict]]:
    """
    Read a 1Password item as JSON using `op item get <path> --format json` and extract possible API key candidates.
    Returns (True, {"item": parsed_json, "candidates": [{"label":..., "value":...}, ...]})
    or (False, {"error": ...}). Does not print secrets unless caller requests debug output.
    """
    try:
        proc = subprocess.run(["op", "item", "get", op_item_path, "--format", "json"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=None, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return False, {"error": "timeout", "detail": str(e)}
    except FileNotFoundError:
        return False, {"error": "op-not-found", "detail": "1Password CLI 'op' not found in PATH"}
    except Exception as e:
        return False, {"error": "failed", "detail": str(e)}

    if proc.returncode != 0:
        return False, {"error": "op-failed", "stderr": proc.stderr.strip()}

    try:
        item = json.loads(proc.stdout)
    except Exception as e:
        return False, {"error": "invalid-json", "detail": str(e), "stdout": proc.stdout}

    candidates = []
    # Common places: top-level password, notesPlain, fields array, details sections
    if isinstance(item, dict):
        if item.get("password"):
            candidates.append({"label": "password", "value": item.get("password")})
        if item.get("notesPlain"):
            candidates.append({"label": "notesPlain", "value": item.get("notesPlain")})
        # fields is often a list of {"label":"...","value":"..."}
        for f in item.get("fields", []) or []:
            if isinstance(f, dict):
                val = f.get("value") or f.get("text") or f.get("designation")
                label = f.get("label") or f.get("id") or "field"
                if val:
                    candidates.append({"label": f"field:{label}", "value": val})
        # details.sections -> items
        details = item.get("details") or {}
        for sec in (details.get("sections") or []):
            for it in sec.get("fields") or []:
                if isinstance(it, dict):
                    lab = it.get("label") or "section_field"
                    val = it.get("value") or it.get("text")
                    if val:
                        candidates.append({"label": f"section:{lab}", "value": val})

    return True, {"item": item, "candidates": candidates}

def _iter_files(folder_path: str):
    folder_path = os.path.abspath(folder_path)
    for root, _, files in os.walk(folder_path):
        for fname in files:
            local_path = os.path.join(root, fname)
            rel = os.path.relpath(local_path, folder_path).replace(os.sep, "/")
            yield local_path, rel

# --- Presigned URL flow ---

def upload_folder_using_presigned(folder_path: str, presigned_map: Dict[str, Union[str, Tuple[str, Dict[str, str]]]], on_progress: Optional[Callable[[float, str], None]] = None, chunk_size: int = 8*1024):
    """
    Upload files under folder_path using a mapping {key: presigned_put_url}.
    - presigned_map keys should match the object key naming the ingest expects (prefix + relative path).
    - The function performs streaming PUTs and reports per-file progress via on_progress(percent, local_path).
    """
    folder_path = os.path.abspath(folder_path)
    headers = {"Content-Type": "application/octet-stream"}
    for local_path, rel in _iter_files(folder_path):
        # find matching key in presigned_map
        if rel not in presigned_map:
            # try with leading slash removed/added
            if rel.lstrip("/") in presigned_map:
                url = presigned_map[rel.lstrip("/")]
            else:
                raise KeyError(f"No presigned URL for object key '{rel}'. Presigned map keys: {list(presigned_map.keys())[:10]}")
        else:
            url = presigned_map[rel]

        total = os.path.getsize(local_path)
        uploaded = 0
        with open(local_path, "rb") as fh:
            # stream upload
            # allow extra headers to be present in presigned_map as tuple (url, headers)
            if isinstance(url, (list, tuple)):
                put_url, extra_h = url[0], url[1]
                if isinstance(extra_h, dict):
                    headers.update(extra_h)
            else:
                put_url = url

            resp = requests.put(put_url, data=_read_in_chunks(fh, chunk_size, lambda n: (on_progress_callback(on_progress, n, local_path, total), None)[1]), headers=headers, timeout=300)
            # requests.put returns a Response
                # requests.put with streaming generator returns a Response that is not a context manager in all versions;
                # using it as context manager is optional. We'll check status:
            if hasattr(resp, "status_code"):
                if resp.status_code >= 400:
                    raise RuntimeError(f"Failed to PUT {rel}: {resp.status_code} {resp.text}")

def _read_in_chunks(fileobj, chunk_size, progress_cb):
    """
    Generator reading fileobj in chunk_size bytes, calling progress_cb(bytes_read) after yielding each chunk.
    progress_cb should accept number of bytes read (int).
    """
    while True:
        chunk = fileobj.read(chunk_size)
        if not chunk:
            break
        progress_cb(len(chunk))
        yield chunk

def on_progress_callback(on_progress, bytes_inc, local_path, total_bytes):
    if not on_progress:
        return
    # maintain state on the callback function to accumulate bytes per file
    if not hasattr(on_progress, "_progress_state"):
        on_progress._progress_state = {}
    state = on_progress._progress_state.setdefault(local_path, {"seen": 0})
    state["seen"] += bytes_inc
    percent = (state["seen"] / total_bytes) * 100.0 if total_bytes else 100.0
    try:
        on_progress(percent, local_path)
    except Exception:
        pass

# --- S3 credentials flow (boto3) ---

def upload_folder_using_s3_creds(
    folder_path: str,
    access_key: str,
    secret_key: str,
    session_token: Optional[str],
    endpoint_url: str,
    bucket: str,
    prefix: str,
    upload_chunk_size: int = 8*1024*1024,
    on_progress: Optional[Callable[[float, str], None]] = None,
):
    """
    Upload folder to S3-compatible storage using temporary credentials.
    - prefix should be the desired prefix (may be empty or end with '/')
    - Requires boto3 installed.
    """
    if boto3 is None:
        raise RuntimeError("boto3 is required for S3 credentials flow. Install with: pip install boto3")
    session = boto3.Session()
    client = session.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        endpoint_url=endpoint_url,
    )

    if TransferConfig is not None:
        config = TransferConfig(multipart_threshold=upload_chunk_size, multipart_chunksize=upload_chunk_size, use_threads=True)
    else:
        config = None

    # Upload each file
    folder_path = os.path.abspath(folder_path)
    for local_path, rel in _iter_files(folder_path):
        key = (prefix.rstrip("/") + "/" if prefix and not prefix.endswith("/") else prefix) + rel
        if key.startswith("/"):
            key = key.lstrip("/")
        # progress wrapper
        callback = None
        if on_progress:
            callback = _make_boto_progresscallback(local_path, on_progress)
        if config is not None:
            client.upload_file(Filename=local_path, Bucket=bucket, Key=key, Callback=callback, Config=config)
        else:
            client.upload_file(Filename=local_path, Bucket=bucket, Key=key, Callback=callback)

def _make_boto_progresscallback(local_path: str, on_progress: Callable[[float, str], None]):
    total = float(os.path.getsize(local_path))
    lock = threading.Lock()
    state = {"seen": 0}
    def cb(bytes_amount):
        with lock:
            state["seen"] += bytes_amount
            percent = (state["seen"] / total) * 100.0 if total else 100.0
        try:
            on_progress(percent, local_path)
        except Exception:
            pass
    return cb

# --- Notify/ingest call ---

def notify_autonomys_ingest(api_key: str, ingest_url: str, payload: dict, timeout: int = 60) -> dict:
    """
    Call Autonomys ingest/notify endpoint to convert the uploaded S3 objects into an AutoDrive folder/CID.
    - ingest_url: e.g. https://api.autonomys.xyz/drive/v1/s3/import  (use the exact S3-layer URL from Autonomys)
    - payload: dict with fields required by Autonomys (bucket, prefix, options, password, etc.)
    Returns JSON response (may include a CID or an operation id to poll).
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # allow caller to pass additional headers via payload._extra_headers (not ideal, but simple)
    extra = payload.pop("_extra_headers", None) if isinstance(payload, dict) else None
    if extra and isinstance(extra, dict):
        headers.update(extra)
    r = requests.post(ingest_url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def upload_file_presigned(file_path: str, presigned_url: str, extra_headers: Optional[Dict[str, str]] = None, chunk_size: int = 8*1024):
    """
    Upload a single file to a presigned PUT URL. `presigned_url` may be a string or a tuple (url, headers)
    where headers will be merged into the PUT request headers.
    """
    if isinstance(presigned_url, (list, tuple)):
        url = presigned_url[0]
        p_extra = presigned_url[1] or {}
    else:
        url = presigned_url
        p_extra = {}

    headers = {"Content-Type": "application/octet-stream"}
    if extra_headers and isinstance(extra_headers, dict):
        headers.update(extra_headers)
    if p_extra and isinstance(p_extra, dict):
        headers.update(p_extra)

    total = os.path.getsize(file_path)
    with open(file_path, "rb") as fh:
        resp = requests.put(url, data=_read_in_chunks(fh, chunk_size, lambda n: (None, None)[1]), headers=headers, timeout=300)
    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to upload file {file_path}: {resp.status_code} {resp.text}")
    return resp

# --- Example usage helper ---

def example_workflow_presigned(api_key: str, folder_path: str, create_session_url: str, ingest_url: str, on_progress: Optional[Callable[[float, str], None]] = None):
    """
    Example high-level flow when Autonomys provides presigned URLs via a create-session endpoint:
    - create_session_url: endpoint you POST to with info about your upload request (depends on Autonomys docs)
      expected to return JSON: { "presigned": { "object/key1": "https://...", ... }, "ingest_params": {...} }
    - ingest_url: endpoint to call after upload (or create_session may include ingest info already)
    """
    # Step 1: create upload session (the exact body varies by Autonomys API - adapt as needed)
    create_payload = {"files": [], "folder_name": os.path.basename(folder_path)}  # adapt to docs
    headers = {"Authorization": f"Bearer {api_key}"}
    r = requests.post(create_session_url, headers=headers, json=create_payload, timeout=30)
    r.raise_for_status()
    session_resp = r.json()

    # The structure of session_resp depends on Autonomys; try common shapes:
    if "presigned" in session_resp:
        presigned_map = session_resp["presigned"]  # mapping key->url
        upload_folder_using_presigned(folder_path, presigned_map, on_progress=on_progress)
    elif "s3" in session_resp:
        s3 = session_resp["s3"]
        upload_folder_using_s3_creds(
            folder_path,
            access_key=s3["access_key"],
            secret_key=s3["secret_key"],
            session_token=s3.get("session_token"),
            endpoint_url=s3["endpoint"],
            bucket=s3["bucket"],
            prefix=s3.get("prefix", ""),
            on_progress=on_progress,
        )
    else:
        raise RuntimeError("Unexpected create-session response. Provide sample response from Autonomys if you want me to adapt code.")

    # After uploading, request ingest (payload varies)
    s3 = session_resp.get("s3") if isinstance(session_resp, dict) else None
    ingest_payload = {
        "bucket": session_resp.get("bucket") if isinstance(session_resp, dict) and session_resp.get("bucket") else (s3.get("bucket") if s3 and isinstance(s3, dict) else None),
        "prefix": session_resp.get("prefix") if isinstance(session_resp, dict) and session_resp.get("prefix") else (s3.get("prefix") if s3 and isinstance(s3, dict) else None),
    }
    # include other fields from session_resp if required (password, encryption, etc.)
    ingest_response = notify_autonomys_ingest(api_key=api_key, ingest_url=ingest_url, payload=ingest_payload)
    return ingest_response

# --- Utility to reset progress state (optional) ---
def reset_progress_state(cb):
    if hasattr(cb, "_progress_state"):
        del cb._progress_state

# If used as script, show a brief demo (no real endpoints)
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Autonomys S3-layer upload helper (example).")
    parser.add_argument("--api-key", required=False, help="Direct API key. If omitted you can use --use-1pw or AUTONOMYS_API_KEY env var.")
    parser.add_argument("--folder", required=True)
    parser.add_argument("--base-url", default="https://mainnet.auto-drive.autonomys.xyz/api")
    parser.add_argument("--use-1pw", action="store_true", help="Read API key from 1Password using the op CLI.")
    parser.add_argument("--onepw-path", default="op://DataStorage/AutoDrive/AUTONOMYS_API_KEY", help="1Password path (op://...) to read the API key from.")
    parser.add_argument("--onepw-item", action="store_true", help="Use `op item get <path> --format json` and parse fields for the API key (safer when the value is stored in an item field).")
    parser.add_argument("--debug", action="store_true", help="Show debug info about the loaded API key (repr and length). Opt-in; don't use in public logs.")
    parser.add_argument("--probe-headers", action="store_true", help="Try multiple common header formats against /accounts/@me and print results for debugging.")
    parser.add_argument("--x-auth-provider", action="store_true", help="Include header 'X-Auth-Provider: apikey' when validating/ingesting.")
    parser.add_argument("--auth-provider-value", default="apikey", help="Value to use for X-Auth-Provider when --x-auth-provider is set")
    parser.add_argument("--presigned-url", required=False, help="If provided, perform a PUT of --upload-file to this presigned URL (or use --upload-file and --presigned-url together)")
    parser.add_argument("--upload-file", required=False, help="Local file to upload; defaults to the path you requested.")
    args = parser.parse_args()

    # Determine API key: CLI arg > env var > 1Password (if requested)
    api_key = args.api_key or os.environ.get("AUTONOMYS_API_KEY")
    if not api_key and args.use_1pw:
        print(f"Reading API key from 1Password path {args.onepw_path!s} (you have 30s to interact with 'op' if prompted)...")
        if args.onepw_item:
            ok1, resp1 = read_api_key_from_1password_item(args.onepw_path, timeout=30)
            if not ok1:
                try:
                    print("Failed to read item from 1Password:", json.dumps(resp1, indent=2))
                except Exception:
                    print("Failed to read item from 1Password:", resp1)
                sys.exit(3)

            # if debug, show candidate labels (masked)
            candidates = resp1.get("candidates") if isinstance(resp1, dict) else []
            if args.debug:
                print("1Password item parsed. Candidate fields:")
                for c in (candidates or [])[:10]:
                    try:
                        v = c.get("value") or "" if isinstance(c, dict) else ""
                        masked = (v[:4] + "..." + v[-4:]) if len(v) > 8 else ("*" * len(v))
                        print(f" - {c.get('label') if isinstance(c, dict) else str(c)}: {masked} (len={len(v)})")
                    except Exception:
                        print(f" - {c.get('label') if isinstance(c, dict) else str(c)}: (failed to show)")

            # choose first candidate if present
            cand = candidates or []
            if cand and isinstance(cand[0], dict):
                api_key = cand[0].get("value")
            else:
                print("No candidate API key found in 1Password item. Use --debug to inspect item.")
                sys.exit(3)
        else:
            ok1, resp1 = read_api_key_from_1password(args.onepw_path, timeout=30)
            if not ok1:
                try:
                    print("Failed to read from 1Password:", json.dumps(resp1, indent=2))
                except Exception:
                    print("Failed to read from 1Password:", resp1)
                sys.exit(3)
            api_key = resp1.get("key") if isinstance(resp1, dict) else None

    if not api_key:
        print("No API key provided. Use --api-key, set AUTONOMYS_API_KEY, or use --use-1pw.")
        sys.exit(4)

    if args.debug:
        try:
            safe = api_key
            # masked display
            if len(safe) > 8:
                masked = safe[:4] + "..." + safe[-4:]
            else:
                masked = "*" * len(safe)
            print("DEBUG: API key masked:", masked)
            print("DEBUG: API key repr:", repr(api_key))
            print("DEBUG: API key length:", len(api_key))
        except Exception as e:
            print("DEBUG: failed to show key info:", e)

    if args.probe_headers:
        print("Probing common header formats against /accounts/@me ...")
        res = probe_api_key_headers(api_key, base_url=args.base_url)
        for name, headers, status, body in res:
            hdesc = name
            try:
                print(f"- {hdesc}: status={status}")
                # print small body sample
                if isinstance(body, str):
                    sample = body.strip()[:400]
                    print(f"  body: {sample}")
                else:
                    print(f"  body: {repr(body)}")
            except Exception:
                print(f"- {hdesc}: (failed to print response)")
        # exit after probing
        sys.exit(5)

    # build extra headers if requested; these are merged into all outgoing requests
    extra_headers = {}
    if args.x_auth_provider:
        extra_headers["X-Auth-Provider"] = args.auth_provider_value

    print("Validating API key with /accounts/@me ...")
    ok, info = validate_api_key(api_key, base_url=args.base_url, extra_headers=extra_headers if extra_headers else None)
    if ok:
        print("Valid API key. Account info:", json.dumps(info, indent=2))
        # perform presigned file upload if requested
        up_file = args.upload_file or (r"C:\ProgramData\FryNetworks\miner-BM\measurements\hourly\2026-01-21.meta.json" if not args.upload_file else args.upload_file)
        if args.presigned_url:
            print(f"Uploading {up_file} to presigned URL...")
            try:
                resp = upload_file_presigned(up_file, args.presigned_url, extra_headers=extra_headers if extra_headers else None)
                print(f"Upload succeeded: {resp.status_code}")
                sys.exit(0)
            except Exception as e:
                print("Upload failed:", e)
                sys.exit(6)
        sys.exit(0)
    else:
        # print diagnostic information and exit non-zero
        try:
            print("API key validation failed:", json.dumps(info, indent=2))
        except Exception:
            print("API key validation failed:", info)
        sys.exit(2)