###############################################################################
# build.ps1 — Build the Diiisco node Docker image (Windows)
#
# Usage:
#   .\build.ps1                         # Uses 1Password CLI
#   .\build.ps1 -Manual                 # Prompts for credentials
#   $env:ALGO_ADDRESS="..."; $env:ALGO_MNEMONIC="..."; .\build.ps1  # Env vars
###############################################################################

param(
    [switch]$Manual
)

$ErrorActionPreference = "Stop"

$IMAGE_NAME = "diiisco-node"
$IMAGE_TAG  = "latest"
$API_KEY    = if ($env:API_KEY) { $env:API_KEY } else { "sk-diiisco-prod-$(New-Guid)" }

Write-Host ""
Write-Host "  Diiisco Node - Docker Image Builder" -ForegroundColor Cyan
Write-Host "  =====================================" -ForegroundColor Cyan
Write-Host ""

# ── Resolve credentials ────────────────────────────────────────────────

if ($env:ALGO_ADDRESS -and $env:ALGO_MNEMONIC) {
    Write-Host "  [OK] Using credentials from environment variables" -ForegroundColor Green
    $AlgoAddress  = $env:ALGO_ADDRESS
    $AlgoMnemonic = $env:ALGO_MNEMONIC

} elseif ($Manual) {
    Write-Host "  Manual mode — enter credentials:" -ForegroundColor Yellow
    $AlgoAddress  = Read-Host "    Algorand address"
    $AlgoMnemonic = Read-Host "    Algorand mnemonic (24 words)" -AsSecureString |
                    ForEach-Object { [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($_)) }

} else {
    Write-Host "  Fetching credentials from 1Password..." -ForegroundColor Yellow

    $OpVault = if ($env:OP_VAULT) { $env:OP_VAULT } else { "Private" }
    $OpItem  = if ($env:OP_ITEM)  { $env:OP_ITEM }  else { "Diiisco Wallet" }

    try {
        $AlgoAddress  = & op item get $OpItem --vault $OpVault --fields "address" 2>$null
        $AlgoMnemonic = & op item get $OpItem --vault $OpVault --fields "mnemonic" 2>$null
        Write-Host "  [OK] Credentials loaded from 1Password" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] Could not fetch from 1Password." -ForegroundColor Red
        Write-Host "         Make sure 'op' is installed and signed in: op signin" -ForegroundColor Red
        Write-Host "         Or run: .\build.ps1 -Manual" -ForegroundColor Red
        exit 1
    }
}

# ── Validate ───────────────────────────────────────────────────────────

if (-not $AlgoAddress -or -not $AlgoMnemonic) {
    Write-Host "  [FAIL] Address and mnemonic must not be empty." -ForegroundColor Red
    exit 1
}

$WordCount = ($AlgoMnemonic -split '\s+').Count
if ($WordCount -ne 25) {
    Write-Host "  [WARN] Mnemonic has $WordCount words (expected 25 for Algorand)." -ForegroundColor Yellow
    $confirm = Read-Host "         Continue anyway? [y/N]"
    if ($confirm -notmatch '^[Yy]$') { exit 1 }
}

$AddrPreview = $AlgoAddress.Substring(0,8) + "..." + $AlgoAddress.Substring($AlgoAddress.Length - 6)
$MnemonicFirst = ($AlgoMnemonic -split '\s+')[0]
Write-Host "  Address:  $AddrPreview"
Write-Host "  Mnemonic: $MnemonicFirst ... [redacted]"
Write-Host ""

# ── Build ──────────────────────────────────────────────────────────────

Write-Host "  Building Docker image: ${IMAGE_NAME}:${IMAGE_TAG}" -ForegroundColor Cyan
Write-Host ""

docker build `
    --build-arg "ALGO_ADDRESS=$AlgoAddress" `
    --build-arg "ALGO_MNEMONIC=$AlgoMnemonic" `
    --build-arg "API_KEY=$API_KEY" `
    -t "${IMAGE_NAME}:${IMAGE_TAG}" `
    ./diiisco-node

Write-Host ""
Write-Host "  [OK] Build complete: ${IMAGE_NAME}:${IMAGE_TAG}" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    1. Push to your registry:"
Write-Host "       docker tag ${IMAGE_NAME}:${IMAGE_TAG} your-registry/${IMAGE_NAME}:${IMAGE_TAG}"
Write-Host "       docker push your-registry/${IMAGE_NAME}:${IMAGE_TAG}"
Write-Host "    2. Deploy to customers:"
Write-Host "       docker compose up -d"
Write-Host ""
