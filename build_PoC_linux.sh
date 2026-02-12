#!/bin/bash

set -e

VENV_DIR=${VENV_DIR:-".venv"}
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating Python virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
if [ ! -f "$VENV_DIR/bin/activate" ]; then
  echo "Virtual environment activation script not found at $VENV_DIR/bin/activate"
  exit 1
fi
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
PYTHON_BIN="${VENV_DIR}/bin/python3"

# Parameters
CODE=${1^^}  # to upper
VERSION=$2
VERSION_CHECK_SEC=${3:-600}
USE_1PASSWORD=${USE_1PASSWORD:-true}
USE_GITHUB=${USE_GITHUB:-true}
OP_REF_BEARER_TOKEN=${OP_REF_BEARER_TOKEN:-"op://VPS/Hardware_API/API_BEARER_TOKEN"}
OP_REF_SIGNING_KEY=${OP_REF_SIGNING_KEY:-"op://VSCode/hardware_exe/local_signing_key_hex"}
OP_REF_UPDATE_REPO=${OP_REF_UPDATE_REPO:-"op://VSCode/hardware_exe/Github_repo_test"}
OP_REF_GITHUB_TOKEN=${OP_REF_GITHUB_TOKEN:-"op://VSCode/hardware_exe/Github_token"}
OP_REF_AUTONOMYS_API_KEY=${OP_REF_AUTONOMYS_API_KEY:-"op://DataStorage/AutoDrive/AUTONOMYS_API_KEY"}
BEARER_TOKEN=${BEARER_TOKEN:-""}
SIGNING_KEY=${SIGNING_KEY:-""}
GITHUB_TOKEN=${GITHUB_TOKEN:-""}
SOFTWARE_UPTODATE=${SOFTWARE_UPTODATE:-true}
INTERVAL_SECONDS=${INTERVAL_SECONDS:-600}
TLS_CA_FILE=${TLS_CA_FILE:-""}
SIGN=${SIGN:-false}
SIGN_PFX_PATH=${SIGN_PFX_PATH:-""}
SIGN_PFX_PASSWORD=${SIGN_PFX_PASSWORD:-""}
BUILD_GUI=${BUILD_GUI:-false}
SIGN_SUBJECT=${SIGN_SUBJECT:-""}
SIGN_TIMESTAMP_URL=${SIGN_TIMESTAMP_URL:-"http://timestamp.digicert.com"}
USE_1PASSWORD_PFX=${USE_1PASSWORD_PFX:-false}
OPEN_1P=${OPEN_1P:-false}
COMPANY_NAME=${COMPANY_NAME:-"Fry Networks LLC"}
PRODUCT_NAME=${PRODUCT_NAME:-""}
FILE_DESCRIPTION=${FILE_DESCRIPTION:-""}
ARCH=${ARCH:-""}  # Set to "aarch64" for ARM64 Docker cross-build
DOCKER_IMAGE=${DOCKER_IMAGE:-"python:3.11-slim"}

# Install Python packages
"$PYTHON_BIN" -m pip install --upgrade pip

base_packages=(pyinstaller psutil requests cryptography h3 pillow pandas pyarrow)
optional_packages=(sounddevice pyserial numpy matplotlib shapely geoip2)
packages=()
if [ "$BUILD_GUI" = true ]; then
  packages=("${base_packages[@]}" "${optional_packages[@]}" PySide6)
else
  packages=("${base_packages[@]}")
fi

echo "Installing Python packages: ${packages[*]}"
"$PYTHON_BIN" -m pip install "${packages[@]}"

# Metadata maps
declare -A GROUP_MAP=(
  [BM]='Bandwidth'
  [IDM]='Decibel'
  [ODM]='Decibel'
  [ISM]='Satellite'
  [OSM]='Satellite'
  [RDN]='Node'
  [SDN]='Node'
  [SVN]='Node'
  [AEM]='AI'
  [IRM]='Radiation'
)

declare -A DISPLAY_NAME_MAP=(
  [BM]='Bandwidth Miner'
  [IDM]='Indoor Decibel Miner'
  [ODM]='Outdoor Decibel Miner'
  [ISM]='Indoor Satellite Miner'
  [OSM]='Outdoor Satellite Miner'
  [RDN]='Reward Decentralization Node'
  [SDN]='Storage Decentralization Node'
  [SVN]='Storage Validator Node'
  [AEM]='AI Edge Miner'
  [IRM]='Radiation Miner'
)

declare -A METRIC_LABEL_MAP=(
  [BM]='Bandwidth'
  [IDM]='Decibel'
  [ODM]='Decibel'
  [ISM]='GNSS stream'
  [OSM]='GNSS stream'
  [RDN]='Rewards'
  [SDN]='Storage'
  [SVN]='Validation'
  [AEM]='AI inference'
  [IRM]='Radiation'
)

function get_executable_metadata() {
  local code=$1
  local company_name=$2
  local override_product_name=$3
  local override_file_description=$4
  local product_name
  local file_description
  case $code in
    BM)
      product_name="Fry Networks Bandwidth Miner"
      file_description="Fry Networks Bandwidth Miner"
      ;;
    IDM)
      product_name="Fry Networks Indoor Decibel Miner"
      file_description="Fry Networks Indoor Decibel Miner"
      ;;
    ODM)
      product_name="Fry Networks Outdoor Decibel Miner"
      file_description="Fry Networks Outdoor Decibel Miner"
      ;;
    ISM)
      product_name="Fry Networks Indoor Satellite Miner"
      file_description="Fry Networks Indoor Satellite Miner"
      ;;
    OSM)
      product_name="Fry Networks Outdoor Satellite Miner"
      file_description="Fry Networks Outdoor Satellite Miner"
      ;;
    RDN)
      product_name="Fry Networks Reward Decentralization Node"
      file_description="Fry Networks Reward Decentralization Node"
      ;;
    SDN)
      product_name="Fry Networks Storage Decentralization Node"
      file_description="Fry Networks Storage Decentralization Node"
      ;;
    SVN)
      product_name="Fry Networks Storage Validator Node"
      file_description="Fry Networks Storage Validator Node"
      ;;
    AEM)
      product_name="Fry Networks AI Edge Miner"
      file_description="Fry Networks AI Edge Miner"
      ;;
    IRM)
      product_name="Fry Networks Radiation Miner"
      file_description="Fry Networks Radiation Miner"
      ;;
    *)
      product_name="Fry Networks $code"
      file_description="Fry Networks component $code."
      ;;
  esac
  if [ -n "$override_product_name" ]; then
    product_name=$override_product_name
  fi
  if [ -n "$override_file_description" ]; then
    file_description=$override_file_description
  fi
  echo "$company_name|$product_name|$file_description"
}

# Generate config_profile.py
group=${GROUP_MAP[$CODE]}
display_name=${DISPLAY_NAME_MAP[$CODE]}
metric_label=${METRIC_LABEL_MAP[$CODE]}
if [ -z "$group" ]; then group='Unknown'; fi
if [ -z "$display_name" ]; then display_name="$CODE Miner"; fi
if [ -z "$metric_label" ]; then metric_label='Metric'; fi

config_profile_content="# AUTOGENERATED. DO NOT EDIT.
MINER_CODE = \"$CODE\"
VERSION = \"$VERSION\"
GROUP = \"$group\"
DISPLAY_NAME = \"$display_name\"
METRIC_LABEL = \"$metric_label\"
METRIC_UNIT = \"\"
DEV = False
VERSION_CHECK_SEC = $VERSION_CHECK_SEC
"

echo "$config_profile_content" > config_profile.py
echo "Generated config_profile.py: MINER_CODE=$CODE, VERSION=$VERSION"

# Tools dir
tools_dir=""
for t in "./tools" "./miner_GUI/tools"; do
  if [ -d "$t" ]; then
    tools_dir=$t
    break
  fi
done
if [ -z "$tools_dir" ]; then
  echo "Warning: tools/ directory not found in repo root or miner_GUI; some helper scripts may be missing."
fi

# 1Password check
needs_op=false
if [ "$USE_1PASSWORD" = true ] && { [ -z "$BEARER_TOKEN" ] || [ -z "$SIGNING_KEY" ]; } && { [ -n "$OP_REF_BEARER_TOKEN" ] || [ -n "$OP_REF_SIGNING_KEY" ]; }; then
  needs_op=true
fi
if [ "$needs_op" = true ] && ! command -v op >/dev/null; then
  echo "Error: 1Password CLI 'op' not found on PATH."
  exit 1
fi

echo "=== Building $CODE v$VERSION ==="

# API base
api_base_url='https://hardwareapi.frynetworks.com'

# Resolve tokens
bearer_token_value=""
if [ -n "$BEARER_TOKEN" ]; then
  bearer_token_value=$BEARER_TOKEN
elif [ "$USE_1PASSWORD" = true ] && [ -n "$OP_REF_BEARER_TOKEN" ]; then
  bearer_token_value=$(op read "$OP_REF_BEARER_TOKEN" | tr -d '\n')
fi

sign_key_value=""
if [ -n "$SIGNING_KEY" ]; then
  sign_key_value=$SIGNING_KEY
elif [ "$USE_1PASSWORD" = true ] && [ -n "$OP_REF_SIGNING_KEY" ]; then
  sign_key_value=$(op read "$OP_REF_SIGNING_KEY" | tr -d '\n')
fi

repo=""
if [ "$USE_1PASSWORD" = true ] && [ -n "$OP_REF_UPDATE_REPO" ]; then
  repo=$(op read "$OP_REF_UPDATE_REPO" 2>/dev/null | tr -d '\n' || echo "")
  if [ -n "$repo" ] && [[ $repo != */* ]]; then
    echo "Error: Update repo must be 'owner/repo'"
    exit 1
  fi
fi

github_tok=""
if [ "$USE_GITHUB" = true ]; then
  if [ -n "$GITHUB_TOKEN" ]; then
    github_tok=$GITHUB_TOKEN
  elif [ "$USE_1PASSWORD" = true ] && [ -n "$OP_REF_GITHUB_TOKEN" ]; then
    github_tok=$(op read "$OP_REF_GITHUB_TOKEN" 2>/dev/null | tr -d '\n' || echo "")
  fi
  if [ -z "$github_tok" ]; then
    echo "Warning: No GitHub token provided, disabling GitHub integration"
    USE_GITHUB=false
  fi
fi

# Validate signing key
if [ -n "$sign_key_value" ]; then
  if ! [[ $sign_key_value =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo "Error: Signing key must be 64 hex chars (32 bytes)"
    exit 1
  fi
  echo "Signing key OK"
fi

# Autonomys API key (1Password reference)
AUTONOMYS_API_KEY_VAL=""
if [ "$USE_1PASSWORD" = true ] && [ -n "$OP_REF_AUTONOMYS_API_KEY" ]; then
  AUTONOMYS_API_KEY_VAL=$(op read "$OP_REF_AUTONOMYS_API_KEY" 2>/dev/null | tr -d '\n' || echo "")
fi
if [ -n "$AUTONOMYS_API_KEY_VAL" ]; then
  echo "Autonomys API key configured"
fi

# Create config
tmp_cfg="_tmp_config.json"
"$PYTHON_BIN" - <<EOF > "$tmp_cfg"
import json
use_github = "${USE_GITHUB,,}" == "true"
software_uptodate = "${SOFTWARE_UPTODATE,,}" == "true"
cfg = {
    "external_api": {
        "base_url": "$api_base_url",
        "timeout": 10.0
    },
    "interval_seconds": $INTERVAL_SECONDS,
    "use_github": use_github,
    "software_uptodate": software_uptodate
}
bearer = "$bearer_token_value"
if bearer:
    cfg["external_api"]["bearer_token"] = bearer
tls = "$TLS_CA_FILE"
if tls:
    cfg["tlsCAFile"] = "$(basename "$tls")"
sign = "$sign_key_value"
if sign:
    cfg["local_signing_key_hex"] = sign
r = "$repo"
if r:
    cfg["update_repo"] = r
gh = "$github_tok"
if gh:
    cfg["github_token"] = gh
autonomys_key = "${AUTONOMYS_API_KEY_VAL}"
if autonomys_key:
  cfg.setdefault("tool_credentials", {})["autonomys_api_key"] = autonomys_key
  cfg["autonomys_upload_enabled"] = True
print(json.dumps(cfg))
EOF

if [ -n "$bearer_token_value" ]; then
  echo "Bearer token configured for API authentication"
fi

# Encrypt config
make_encrypted="$tools_dir/make_encrypted_config.py"
if [ ! -f "$make_encrypted" ]; then
  echo "Error: make_encrypted_config.py not found in tools directory; cannot embed secrets."
  exit 1
fi
enc_tmp="enc_tmp.json"
"$PYTHON_BIN" "$make_encrypted" --in "$tmp_cfg" --json > "$enc_tmp"
if [ $? -ne 0 ]; then
  echo "Error: Failed to run make_encrypted_config.py"
  exit 1
fi

# Parse encrypted values
dlt=$("$PYTHON_BIN" -c "import json; print(json.load(open('$enc_tmp'))['dlt'])")
dlp=$("$PYTHON_BIN" -c "import json; print(json.load(open('$enc_tmp'))['dlp'])")
knt=$("$PYTHON_BIN" -c "import json; print(json.load(open('$enc_tmp'))['knt'])")
knp=$("$PYTHON_BIN" -c "import json; print(json.load(open('$enc_tmp'))['knp'])")

# Patch miner_online_simple.py
src_path="miner_online_simple.py"
bak_path="$src_path.bak"
cp "$src_path" "$bak_path"
patched=true

# Find and replace the placeholder line
line_num=$(grep -n "dlt=b''; dlp=b''; knt=b''; knp=b''" "$src_path" | cut -d: -f1)
if [ -z "$line_num" ]; then
  echo "Error: Placeholder line not found in miner_online_simple.py"
  exit 1
fi
"$PYTHON_BIN" -c "
import sys
line_num = int(sys.argv[1])
dlt = sys.argv[2]
dlp = sys.argv[3]
knt = sys.argv[4]
knp = sys.argv[5]
src_path = sys.argv[6]
with open(src_path, 'r') as f:
    lines = f.readlines()
lines[line_num - 1] = f'    dlt = {dlt}\n'
lines.insert(line_num, f'    dlp = {dlp}\n')
lines.insert(line_num + 1, f'    knt = {knt}\n')
lines.insert(line_num + 2, f'    knp = {knp}\n')
with open(src_path, 'w') as f:
    f.writelines(lines)
" $line_num "$dlt" "$dlp" "$knt" "$knp" "$src_path"

# Build service
arch_suffix=""
if [ -n "$ARCH" ]; then
  arch_suffix="_linux_${ARCH}"
fi
svc_name="FRY_PoC_${CODE}_v${VERSION}${arch_suffix}"
svc_dist_dir="dist/svc/${CODE}"
mkdir -p "$svc_dist_dir"
svc_args=(--clean --onefile --noconsole --noconfirm --name "$svc_name" --distpath "$svc_dist_dir")

# For GUI/full bundles include native collects; for service-only builds add explicit excludes
if [ "$BUILD_GUI" = true ]; then
  svc_args+=(--collect-binaries h3 --collect-all shapely --collect-all geoip2 --collect-all pyarrow)
else
  svc_args+=(--collect-all pyarrow --exclude-module PySide6 --exclude-module matplotlib --exclude-module numpy --exclude-module sounddevice --exclude-module portaudio --exclude-module pyside6 --exclude-module numba --exclude-module pyarrow.tests)
fi

# Icon resolution
icon_base=""
case $CODE in
  BM) icon_base='BM' ;;
  IDM|ODM) icon_base='DB' ;;
  ISM|OSM) icon_base='GNSS' ;;
  RDN|SVN|SDN) icon_base='NODE' ;;
  AEM) icon_base='AEM' ;;
  IRM) icon_base='RAD' ;;
  *) icon_base='frynetworks_logo' ;;
esac
icon_dir='images'
icon_png="$icon_dir/$icon_base.png"
if [ -f "$icon_png" ]; then
  svc_args+=(--icon "$icon_png")
fi

# GeoLite
geo_lite_path=""
if [ -n "$MAXMIND_DB_PATH" ]; then
  geo_lite_path=$MAXMIND_DB_PATH
elif [ -f "$tools_dir/GeoLite2-Country.mmdb" ]; then
  geo_lite_path="$tools_dir/GeoLite2-Country.mmdb"
fi
if [ -n "$geo_lite_path" ]; then
  svc_args+=(--add-data "$geo_lite_path:.")
  echo "Bundling GeoLite2 database from $geo_lite_path"
else
  echo "Warning: GeoLite2-Country.mmdb not found; runtime country checks will require external MAXMIND_DB_PATH"
fi
if [ -n "$TLS_CA_FILE" ]; then
  svc_args+=(--add-data "$TLS_CA_FILE:.")
fi

# Build
if [ "$ARCH" = "aarch64" ]; then
  echo "=== Docker cross-build for linux/arm64 ==="
  if ! command -v docker >/dev/null; then
    echo "Error: Docker is required for aarch64 cross-builds but was not found on PATH."
    exit 1
  fi
  project_dir="$(pwd)"
  docker run --rm --platform linux/arm64 \
    -v "$project_dir:/build" \
    -w /build \
    -e DEBIAN_FRONTEND=noninteractive \
    "$DOCKER_IMAGE" bash -c "
      set -e
      apt-get update -qq && apt-get install -y -qq binutils >/dev/null 2>&1
      pip install --quiet --upgrade pip
      pip install --quiet ${packages[*]}
      python3 -m PyInstaller ${svc_args[*]} miner_online_simple.py
    "
else
  "$PYTHON_BIN" -m PyInstaller "${svc_args[@]}" miner_online_simple.py
fi
svc_built="$svc_dist_dir/$svc_name"

# Signing (placeholder for Linux)
function invoke_code_signing() {
  local path=$1
  if [ ! -f "$path" ]; then return; fi
  echo "Signing not implemented for Linux"
}
if [ "$SIGN" = true ]; then
  invoke_code_signing "$svc_built"
fi

# Move to release
out="release/$VERSION"
mkdir -p "$out"
mv "$svc_built" "$out/$svc_name"

# Generate sha256 checksum
(cd "$out" && sha256sum "$svc_name" > "$svc_name.sha256")
echo "SHA256 checksum written to $out/$svc_name.sha256"

# Clean up
if [ "$patched" = true ]; then
  mv "$bak_path" "$src_path"
  rm -f "$tmp_cfg"
  rm -f "$enc_tmp"
fi

if [ -n "$ARCH" ]; then
  echo "Build complete for $CODE v$VERSION (arch: $ARCH)"
else
  echo "Build complete for $CODE v$VERSION"
fi
