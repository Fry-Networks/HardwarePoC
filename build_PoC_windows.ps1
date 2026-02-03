param(
  [Parameter(Mandatory=$true)][string]$Code,
  [Parameter(Mandatory=$true)][string]$Version,
  [int]$VersionCheckSec = 600,
  [switch]$Use1Password = $true,
  [switch]$UseGithub = $true,
  [string]$OPRefBearerToken = "op://HardwareAPI/Hardware_API/API_BEARER_TOKEN",
  [string]$OPRefSigningKey = "op://VSCode/hardware_exe/local_signing_key_hex",
  [string]$OPRefUpdateRepo = "op://VSCode/hardware_exe/Github_repo_test",
  [string]$OPRefGithubToken = "op://VSCode/hardware_exe/Github_token",
  [string]$BearerToken = "",  # Direct bearer token value (bypasses 1Password)
  [string]$SigningKey = "",   # Direct signing key value (bypasses 1Password)
  [string]$GithubToken = "",
  [bool]$SoftwareUptodate = $true,
  [SecureString]$OPRefPfxPassword = $null,
  # Tool Credentials (embedded at build time from 1Password)
  [string]$OPRefPresearchRegCode = "op://Hardware/Presearch/registration_code",
  [string]$OPRefPresearchApiKey = "op://Hardware/Presearch/api_key",
  [string]$OPRefDiiiscoApiKey = "op://Hardware/Diiisco/api_key",
  [string]$OPRefDiiiscoNodeKey = "op://Hardware/Diiisco/node_key",
  [string]$OPRefSpaceAcresFarmerKey = "op://Hardware/SpaceAcres/farmer_key",
  [string]$OPRefSpaceAcresRewardAddr = "op://Hardware/SpaceAcres/reward_address",
  [string]$OPRefBrightApiToken = "op://Bandwidth Miners/Bright Data SDK Login/APP_ID",
  [string]$OPRefHoneygainApiKey = "op://Bandwidth Miners/Honeygain SDK API/credential",

  [string]$OPRefMystPayoutAddr = "op://Bandwidth Miners/Mysterium SDK API/MYST_PAYOUT_ADDR",
  [string]$OPRefMystRegistrationToken = "op://Bandwidth Miners/Mysterium SDK API/MYST_REG_TOKEN",
  [string]$OPRefMystApiKey = "op://Bandwidth Miners/Mysterium SDK API/MYST_API_KEY",

  # Autonomys Auto Drive
  [string]$OPRefAutonomysApiKey = "op://DataStorage/AutoDrive/AUTONOMYS_API_KEY",
  [string]$AutonomysApiKey = "",           # Direct Autonomys API key value (bypasses 1Password)
  [bool]$AutonomysUploadEnabled = $true,  # Enable Autonomys upload at runtime

  [int]$IntervalSeconds = 600,
  [string]$TlsCAFile = "",
  [switch]$Sign = $false,
  [string]$SignPfxPath = "",
  [SecureString]$SignPfxPassword = $null,
  [switch]$BuildGUI = $false,
  [string]$SignSubject = "",
  [string]$SignTimestampUrl = "http://timestamp.digicert.com",
  [switch]$Use1PasswordPfx = $false,
  [switch]$Open1P = $false,
  [string]$CompanyName = "Fry Networks LLC",
  [string]$ProductName = "",
  [string]$FileDescription = ""
)

py -m pip install --upgrade pip

# ---------- Group Map (needed early for per-group package selection) ----------
$groupMap = @{
  'BM'  = 'Bandwidth'
  'IDM' = 'Decibel'
  'ODM' = 'Decibel'
  'ISM' = 'Satellite'
  'OSM' = 'Satellite'
  'RDN' = 'Node'
  'SDN' = 'Node'
  'SVN' = 'Node'
  'AEM' = 'AI'
  'IRM' = 'Radiation'
}
$group = $groupMap[$Code.ToUpper()]
if (-not $group) { $group = 'Unknown' }

# Install Python packages per miner group to minimise bundle size.
$corePackages = @('pyinstaller','psutil','requests','cryptography','h3','pillow','pandas','pyarrow')
$commonOptional = @('shapely','geoip2','boto3')

# Per-group extra packages (hardware-specific)
$groupExtraPackages = @{
  'Bandwidth' = @()
  'Decibel'   = @('sounddevice','numpy')
  'Satellite' = @('pyserial')
  'Node'      = @()
  'AI'        = @()
  'Radiation' = @('pyserial')
}
$extraPkgs = $groupExtraPackages[$group]
if (-not $extraPkgs) { $extraPkgs = @() }

if ($BuildGUI) {
  $packages = $corePackages + $commonOptional + $extraPkgs + @('numpy','matplotlib','PySide6')
} else {
  $packages = $corePackages + $commonOptional + $extraPkgs
}
# Deduplicate
$packages = $packages | Select-Object -Unique

Write-Host "Installing Python packages for $($Code.ToUpper()) ($group): $($packages -join ', ')"
py -m pip install $packages

# ---------- Metadata Mapping ----------
$MetaByCode = @{
  'BM'  = @{ ProductName = 'Fry Networks Bandwidth Miner';  FileDescription = 'Fry Networks Bandwidth Miner' }
  'IDM' = @{ ProductName = 'Fry Networks Indoor Decibel Miner';    FileDescription = 'Fry Networks Indoor Decibel Miner' }
  'ODM' = @{ ProductName = 'Fry Networks Outdoor Decibel Miner';    FileDescription = 'Fry Networks Outdoor Decibel Miner' }
  'ISM' = @{ ProductName = 'Fry Networks Indoor Satellite Miner';  FileDescription = 'Fry Networks Indoor Satellite Miner' }
  'OSM' = @{ ProductName = 'Fry Networks Outdoor Satellite Miner';  FileDescription = 'Fry Networks Outdoor Satellite Miner' }
  'RDN' = @{ ProductName = 'Fry Networks Reward Decentralization Node';      FileDescription = 'Fry Networks Reward Decentralization Node' }
  'SDN' = @{ ProductName = 'Fry Networks Storage Decentralization Node';     FileDescription = 'Fry Networks Storage Decentralization Node' }
  'SVN' = @{ ProductName = 'Fry Networks Storage Validator Node';   FileDescription = 'Fry Networks Storage Validator Node' }
  'AEM' = @{ ProductName = 'Fry Networks AI Edge Miner';    FileDescription = 'Fry Networks AI Edge Miner' }
  'IRM' = @{ ProductName = 'Fry Networks Radiation Miner';  FileDescription = 'Fry Networks Radiation Miner' }
}
function Get-ExecutableMetadata {
  param(
    [Parameter(Mandatory)][string]$Code,
    [Parameter(Mandatory)][string]$CompanyName,
    [string]$OverrideProductName = "",
    [string]$OverrideFileDescription = ""
  )
  $base = $MetaByCode[$Code.ToUpper()]
  if (-not $base) {
    $base = @{ ProductName = "Fry Networks $Code"; FileDescription = "Fry Networks component $Code." }
  }
  @{
    CompanyName    = $CompanyName
    ProductName    = if ($OverrideProductName) { $OverrideProductName } else { $base.ProductName }
    FileDescription= if ($OverrideFileDescription) { $OverrideFileDescription } else { $base.FileDescription }
  }
}

# ---------- PyInstaller Version File Writer ----------
function New-VersionFile {
  <#
    Creates a temporary version resource file for PyInstaller (--version-file).
    Returns: full path to the version file.
  #>
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$CompanyName,
    [Parameter(Mandatory)][string]$ProductName,
    [Parameter(Mandatory)][string]$FileDescription,
    [Parameter(Mandatory)][string]$FileVersion,     # e.g., 5.3.0
    [Parameter(Mandatory)][string]$InternalName,    # e.g., FRY_PoC_BM_v5.3.0
    [Parameter(Mandatory)][string]$OriginalFilename # e.g., FRY_PoC_BM_v5.3.0.exe
  )

  # Convert semantic version "5.3.0" to "5,3,0,0" for VSVersionInfo
  $verParts = ($FileVersion -split '\.') + @('0','0','0','0')
  $verTuple = ($verParts[0..3] | ForEach-Object { [int]$_ }) -join ', '

  $content = @"
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($verTuple),
    prodvers=($verTuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [
            StringStruct(u'CompanyName', u'$CompanyName'),
            StringStruct(u'FileDescription', u'$FileDescription'),
            StringStruct(u'FileVersion', u'$FileVersion'),
            StringStruct(u'InternalName', u'$InternalName'),
            StringStruct(u'OriginalFilename', u'$OriginalFilename'),
            StringStruct(u'ProductName', u'$ProductName'),
            StringStruct(u'ProductVersion', u'$FileVersion'),
            StringStruct(u'LegalCopyright', u'© $(Get-Date -Format yyyy) $CompanyName')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@

  try {
  Set-Content -Path $Path -Value $content -NoNewline -Encoding utf8NoBOM
  } catch {
    # Fallback for PS 5.1: write via .NET to avoid BOM
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $content, $utf8NoBom)
  }
}


function Test-HexKey { param([string]$Hex)
  if (-not $Hex) { return $false }
  if ($Hex -notmatch '^[0-9a-fA-F]{64}$') { return $false }
  try { [void][Convert]::ToByte($Hex.Substring(0,2),16); return $true } catch { return $false }
}
function Convert-SecretKeyToMasked { param([string]$Hex)
  if (-not $Hex) { return '-' }
  try { return ("{0}�{1}" -f $Hex.Substring(0,6), $Hex.Substring(58)) } catch { return '-' }
}

# 1Password helpers for PFX password (opt-in)
# Note: OPRefPfxPassword may be provided as a SecureString or a plain string; normalize
if ($Use1PasswordPfx -and $Sign -and $SignPfxPath -and (-not $SignPfxPassword) -and $OPRefPfxPassword) {
  if (-not (Get-Command op -ErrorAction SilentlyContinue)) { Write-Error "1Password CLI 'op' not found on PATH."; exit 1 }
  try {
    # If OPRefPfxPassword was passed as a SecureString, convert to plain for the 'op' CLI read
    $OPRefPfxPasswordPlain = $null
    if ($OPRefPfxPassword -is [System.Security.SecureString]) {
      $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($OPRefPfxPassword)
      try { $OPRefPfxPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    } else {
      $OPRefPfxPasswordPlain = $OPRefPfxPassword
    }

    $pwPlain = (& op read $OPRefPfxPasswordPlain).Trim(); if (-not $pwPlain) { throw "Empty password" }
    $SignPfxPassword = ConvertTo-SecureString $pwPlain -AsPlainText -Force
  } catch { Write-Error "Failed to read PFX password: $_"; exit 1 }
}

Write-Host "=== Building $Code v$Version ==="

# Generate config_profile.py for the specific miner type and version
$configProfilePath = Join-Path $PWD "config_profile.py"
# $groupMap already defined above (used for per-group package selection)
$displayNameMap = @{
  'BM'  = 'Bandwidth Miner'
  'IDM' = 'Indoor Decibel Miner'
  'ODM' = 'Outdoor Decibel Miner'
  'ISM' = 'Indoor Satellite Miner'
  'OSM' = 'Outdoor Satellite Miner'
  'RDN' = 'Reward Decentralization Node'
  'SDN' = 'Storage Decentralization Node'
  'SVN' = 'Storage Validator Node'
  'AEM' = 'AI Edge Miner'
  'IRM' = 'Radiation Miner'
}
$metricLabelMap = @{
  'BM'  = 'Bandwidth'
  'IDM' = 'Decibel'
  'ODM' = 'Decibel'
  'ISM' = 'GNSS stream'
  'OSM' = 'GNSS stream'
  'RDN' = 'Rewards'
  'SDN' = 'Storage'
  'SVN' = 'Validation'
  'AEM' = 'AI inference'
  'IRM' = 'Radiation'
}

# $group already resolved above
$displayName = $displayNameMap[$Code.ToUpper()]
$metricLabel = $metricLabelMap[$Code.ToUpper()]
if (-not $displayName) { $displayName = "$Code Miner" }
if (-not $metricLabel) { $metricLabel = 'Metric' }

$configProfileContent = @"
# AUTOGENERATED. DO NOT EDIT.
MINER_CODE = "$($Code.ToUpper())"
VERSION = "$Version"
GROUP = "$group"
DISPLAY_NAME = "$displayName"
METRIC_LABEL = "$metricLabel"
METRIC_UNIT = ""
DEV = False
VERSION_CHECK_SEC = $VersionCheckSec
GEOIP_SHARED_SUBPATH = "FryNetworks/GeoLite2/GeoLite2-Country.mmdb"

"@

Set-Content -Path $configProfilePath -Value $configProfileContent -Encoding UTF8
Write-Host "Generated config_profile.py: MINER_CODE=$($Code.ToUpper()), VERSION=$Version"

# Locate tooling scripts (support repo-root tools/ or miner_GUI/tools/)
$toolCandidates = @('.\\tools', '.\\miner_GUI\\tools')
$toolsDir = $null
foreach ($t in $toolCandidates) {
  if (Test-Path $t) { $toolsDir = $t; break }
}
if (-not $toolsDir) { Write-Warning "tools/ directory not found in repo root or miner_GUI; some helper scripts may be missing." }

$patched = $false

# Determine if we need to check 1Password CLI (only if using references and not direct values)
$needsOP = ($Use1Password -and (-not $BearerToken -or -not $SigningKey) -and ($OPRefBearerToken -or $OPRefSigningKey))
if ($needsOP -and -not (Get-Command op -ErrorAction SilentlyContinue)) {
  Write-Error "1Password CLI 'op' not found on PATH."; exit 1
}

try {
  # Fixed API base URL per product policy
  $apiBaseUrl = 'https://hardwareapi.frynetworks.com'
  
  # Resolve bearer token: direct value > 1Password reference > null
  $bearerTokenValue = $null
  if ($BearerToken) {
    $bearerTokenValue = $BearerToken.Trim()
  } elseif ($Use1Password -and $OPRefBearerToken) {
    $bearerTokenValue = (& op read $OPRefBearerToken).Trim()
  }
  
  $tmpCfg = Join-Path $PWD "_tmp_config.json"
  $cfg = @{ 
    external_api = @{ 
      base_url = $apiBaseUrl; 
      timeout = 10.0 
    }; 
    interval_seconds = $IntervalSeconds; 
    use_github = [bool]$UseGithub 
  }
  if ($bearerTokenValue) { 
    $cfg.external_api.bearer_token = $bearerTokenValue
    Write-Host "Bearer token configured for API authentication"
  }
  if ($UseGithub) { $cfg.github_token = '' }
  $cfg.software_uptodate = [bool]$SoftwareUptodate
  if ($TlsCAFile) { $cfg.tlsCAFile = [IO.Path]::GetFileName($TlsCAFile) }
  
  # Resolve signing key: direct value > 1Password reference > null
  $signKeyValue = $null
  if ($SigningKey) {
    $signKeyValue = $SigningKey.Trim()
  } elseif ($Use1Password -and $OPRefSigningKey) {
    $signKeyValue = (& op read $OPRefSigningKey).Trim()
  }
  
  if ($signKeyValue) {
    if (-not $signKeyValue) { throw "Empty signing key" }
    if (-not (Test-HexKey $signKeyValue)) { throw "Signing key must be 64 hex chars (32 bytes)" }
    $cfg.local_signing_key_hex = $signKeyValue
    Write-Host ("Signing key OK (fingerprint {0})" -f (Convert-SecretKeyToMasked $signKeyValue))
  }
  
  if ($Use1Password -and $OPRefUpdateRepo) {
    $repo = (& op read $OPRefUpdateRepo).Trim()
    if (-not $repo -or ($repo -notmatch '/')) { throw "Update repo must be 'owner/repo'" }
    $cfg.update_repo = $repo
  }
  
  if ($UseGithub) {
    $githubTok = ''
    if ($GithubToken) {
      $githubTok = $GithubToken.Trim()
    } elseif ($Use1Password -and $OPRefGithubToken) {
      $githubTok = (& op read $OPRefGithubToken).Trim()
    }
    if (-not $githubTok) { throw "Provide GitHub token via -OPRefGithubToken or -GithubToken when -UseGithub is set" }
    $cfg.github_token = $githubTok
  }
  
  # Tool Credentials (embedded from 1Password)
  if ($Use1Password) {
    $toolCreds = @{}
    
    # Mysterium
    try {
      if ($OPRefMysteriumApiKey) {
        $val = (& op read $OPRefMysteriumApiKey 2>$null).Trim()
        if ($val) { 
          $toolCreds.mysterium_api_key = $val
          Write-Host "Mysterium API key configured"
        }
      }
      if ($OPRefMysteriumIdentity) {
        $val = (& op read $OPRefMysteriumIdentity 2>$null).Trim()
        if ($val) { 
          $toolCreds.mysterium_identity = $val
          Write-Host "Mysterium identity configured"
        }
      }
    } catch { Write-Warning "Mysterium credentials unavailable: $_" }
    
    # Presearch
    try {
      if ($OPRefPresearchRegCode) {
        $val = (& op read $OPRefPresearchRegCode 2>$null).Trim()
        if ($val) { 
          $toolCreds.presearch_registration_code = $val
          Write-Host "Presearch registration code configured"
        }
      }
      if ($OPRefPresearchApiKey) {
        $val = (& op read $OPRefPresearchApiKey 2>$null).Trim()
        if ($val) { 
          $toolCreds.presearch_api_key = $val
          Write-Host "Presearch API key configured"
        }
      }
    } catch { Write-Warning "Presearch credentials unavailable: $_" }
    
    # Diiisco
    try {
      if ($OPRefDiiiscoApiKey) {
        $val = (& op read $OPRefDiiiscoApiKey 2>$null).Trim()
        if ($val) { 
          $toolCreds.diiisco_api_key = $val
          Write-Host "Diiisco API key configured"
        }
      }
      if ($OPRefDiiiscoNodeKey) {
        $val = (& op read $OPRefDiiiscoNodeKey 2>$null).Trim()
        if ($val) { 
          $toolCreds.diiisco_node_key = $val
          Write-Host "Diiisco node key configured"
        }
      }
    } catch { Write-Warning "Diiisco credentials unavailable: $_" }
    
    # Space Acres
    try {
      if ($OPRefSpaceAcresFarmerKey) {
        $val = (& op read $OPRefSpaceAcresFarmerKey 2>$null).Trim()
        if ($val) { 
          $toolCreds.spaceacres_farmer_key = $val
          Write-Host "Space Acres farmer key configured"
        }
      }
      if ($OPRefSpaceAcresRewardAddr) {
        $val = (& op read $OPRefSpaceAcresRewardAddr 2>$null).Trim()
        if ($val) { 
          $toolCreds.spaceacres_reward_address = $val
          Write-Host "Space Acres reward address configured"
        }
      }
    } catch { Write-Warning "Space Acres credentials unavailable: $_" }
    
    # Bright
    try {
      if ($OPRefBrightApiToken) {
        $val = (& op read $OPRefBrightApiToken 2>$null).Trim()
        if ($val) { 
          $toolCreds.bright_api_token = $val
          Write-Host "Bright API token configured"
        }
      }
    } catch { Write-Warning "Bright credentials unavailable: $_" }
    
    # Honeygain
    try {
      if ($OPRefHoneygainApiKey) {
        $val = (& op read $OPRefHoneygainApiKey 2>$null).Trim()
        if ($val) {
          $toolCreds.honeygain_api_key = $val
          Write-Host "Honeygain API key configured"
        }
      }
    } catch { Write-Warning "Honeygain credentials unavailable: $_" }

    # Autonomys Auto Drive: direct value > 1Password reference
    try {
      if ($AutonomysApiKey) {
        $toolCreds.autonomys_api_key = $AutonomysApiKey.Trim()
        Write-Host "Autonomys API key configured (direct)"
      } elseif ($OPRefAutonomysApiKey) {
        $val = (& op read $OPRefAutonomysApiKey 2>$null).Trim()
        if ($val) {
          $toolCreds.autonomys_api_key = $val
          Write-Host "Autonomys API key configured"
        }
      }
    } catch { Write-Warning "Autonomys credentials unavailable: $_" }

    # If we embedded an Autonomys API key or caller enabled uploads, flag it
    try {
      if ($AutonomysUploadEnabled -or $toolCreds.ContainsKey('autonomys_api_key')) {
        $cfg.autonomys_upload_enabled = $true
        Write-Host "Autonomys upload enabled (embedded)"
      }
    } catch {}

    if ($toolCreds.Count -gt 0) {
      $cfg.tool_credentials = $toolCreds
    }
  }

  # Handle direct Autonomys values when 1Password is disabled (e.g., batch builds)
  if (-not $Use1Password -and $AutonomysApiKey) {
    if (-not $cfg.tool_credentials) { $cfg.tool_credentials = @{} }
    $cfg.tool_credentials.autonomys_api_key = $AutonomysApiKey.Trim()
    Write-Host "Autonomys API key configured (direct, no 1Password)"
    if ($AutonomysUploadEnabled) {
      $cfg.autonomys_upload_enabled = $true
      Write-Host "Autonomys upload enabled (direct)"
    }
  }

  $cfg | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 $tmpCfg

  $makeEncrypted = $null
  if ($toolsDir) { $makeEncrypted = Join-Path $toolsDir 'make_encrypted_config.py' }
  if (-not $makeEncrypted -or -not (Test-Path $makeEncrypted)) { throw "make_encrypted_config.py not found in tools directory; cannot embed secrets." }
  $enc = & py $makeEncrypted --in $tmpCfg --json
  if ($LASTEXITCODE -ne 0) { throw "Failed to run make_encrypted_config.py" }
  
  $json = ConvertFrom-Json ($enc | Out-String)
  $dlt=$json.dlt; $dlp=$json.dlp; $knt=$json.knt; $knp=$json.knp
  $srcPath = Join-Path $PWD "miner_online_simple.py"
  $bakPath = "$srcPath.bak"
  Copy-Item -Force $srcPath $bakPath
  $srcLines = Get-Content -Path $srcPath -Encoding UTF8
  $idx = $null
  for ($i=0; $i -lt $srcLines.Count; $i++) {
    if ($srcLines[$i].Trim() -eq "dlt=b''; dlp=b''; knt=b''; knp=b''") {
      $idx = $i
      break
    }
  }
  if ($null -eq $idx) { throw "Placeholder line not found in miner_online_simple.py" }
  $leading = ($srcLines[$idx] -replace '^(\s*).*','$1')
  $srcLines[$idx] = "${leading}dlt=$dlt; dlp=$dlp; knt=$knt; knp=$knp"
  Set-Content -Path $srcPath -Value $srcLines -Encoding UTF8
  $patched = $true
} catch {
  Write-Error "Config embedding failed: $_"
  exit 1
}

function Get-SignTool { $st = Get-Command signtool.exe -ErrorAction SilentlyContinue; if ($st) { return $st.Path }; return $null }
function Invoke-CodeSigning { param([string]$Path) if (-not (Test-Path $Path)) { return } $signtool = Get-SignTool; if (-not $signtool) { return }; & $signtool sign /fd SHA256 /td SHA256 /tr $SignTimestampUrl $Path | Write-Verbose }

# Build service
$SvcName = "FRY_PoC_${Code}_v${Version}"; 
$svcDistDir = Join-Path $PWD ("dist\\svc\\${Code}")
$svcArgs = @('--clean','--onefile','--noconsole','--noconfirm','--name', $SvcName, '--distpath', $svcDistDir)

# Ensure critical geo/PoL dependencies are bundled (service builds too)
$svcArgs += @('--collect-all','h3','--collect-all','shapely','--collect-all','geoip2')

# For GUI/full bundles include native collects; for service-only builds exclude per miner group
if ($BuildGUI) {
  $svcArgs += @('--collect-binaries','h3')
} else {
  # All heavy modules that can be excluded when a miner group doesn't need them
  $allHeavyModules = @('sounddevice','_sounddevice_data','portaudio',
                       'pyserial','serial',
                       'matplotlib','PySide6','pyside6',
                       'tkinter','_tkinter','unittest','test','xmlrpc','pydoc')

  # Modules each group NEEDS (beyond core). Everything else gets excluded.
  $groupRequiredModules = @{
    'Bandwidth' = @()
    'Decibel'   = @('sounddevice','_sounddevice_data','portaudio','numpy')
    'Satellite' = @('pyserial','serial')
    'Node'      = @()
    'AI'        = @()
    'Radiation' = @('pyserial','serial')
  }
  $requiredMods = $groupRequiredModules[$group]
  if (-not $requiredMods) { $requiredMods = @() }

  $excludeModules = $allHeavyModules | Where-Object { $_ -notin $requiredMods }
  foreach ($mod in $excludeModules) {
    $svcArgs += @('--exclude-module', $mod)
  }
  Write-Host "Excluding modules for $($Code.ToUpper()) ($group): $($excludeModules -join ', ')"
}
  
 # ----- Icon resolution & conversion (supports .ico, .png, .jpg) -----
  $iconBase = switch ($Code.ToUpper()) { 'BM'{'BM'} 'IDM'{'DB'} 'ODM'{'DB'} 'ISM'{'GNSS'} 'OSM'{'GNSS'} 'RDN'{'NODE'} 'SVN'{'NODE'} 'SDN'{'NODE'} 'AEM'{'AEM'} 'IRM' {'RAD'} default{'frynetworks_logo'} }
  $IconDir  = Join-Path $PWD 'images'
  $IconIco  = Join-Path $IconDir "$iconBase.ico"
  $IconPng  = Join-Path $IconDir "$iconBase.png"
  $IconJpg  = Join-Path $IconDir "$iconBase.jpg"

  # If no .ico yet, try to convert from .png then .jpg
  if (-not (Test-Path $IconIco)) {
    $srcIcon = $null
    if (Test-Path $IconPng) { $srcIcon = $IconPng }
    elseif (Test-Path $IconJpg) { $srcIcon = $IconJpg }

    if ($srcIcon) {
      try {
        py -c "from PIL import Image, sys; im=Image.open(sys.argv[1]); im.save(sys.argv[2], format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])" `
          "$srcIcon" "$IconIco" 2>$null
      } catch {
        Write-Warning ("Icon conversion failed for {0}: {1}" -f $srcIcon, $_)
      }
    }
  }
  if (Test-Path $IconIco) { $svcArgs += @('-i', $IconIco) }

  $geoLiteCandidates = @()
  if ($env:MAXMIND_DB_PATH) { $geoLiteCandidates += $env:MAXMIND_DB_PATH }
  $geoLiteCandidates += @(Join-Path $toolsDir 'GeoLite2-Country.mmdb')
  $geoLitePath = $null
  foreach ($candidate in $geoLiteCandidates) {
    if (-not $candidate) { continue }
    try {
      $resolved = Resolve-Path $candidate -ErrorAction Stop
      if (Test-Path $resolved) { $geoLitePath = $resolved; break }
    } catch {}
  }
  if ($geoLitePath) {
    $svcArgs += @('--add-data', ("{0};." -f $geoLitePath))
    Write-Host "Bundling GeoLite2 database from $geoLitePath"
  } else {
    Write-Warning "GeoLite2-Country.mmdb not found; runtime country checks will require external MAXMIND_DB_PATH"
  }
  if ($TlsCAFile) { $svcArgs += @('--add-data', ("{0};." -f $TlsCAFile)) }
  # ---------- NEW: per-code metadata + version file for Service ----------
  $meta = Get-ExecutableMetadata -Code $Code -CompanyName $CompanyName -OverrideProductName $ProductName -OverrideFileDescription $FileDescription
  $svcVerFile = Join-Path $PWD ("_tmp_version_svc_${Code}.txt")
  New-VersionFile -Path $svcVerFile `
                  -CompanyName $meta.CompanyName `
                  -ProductName $meta.ProductName `
                  -FileDescription $meta.FileDescription `
                  -FileVersion $Version `
                  -InternalName $SvcName `
                  -OriginalFilename ("{0}.exe" -f $SvcName) | Out-Null
  $svcArgs += @('--version-file', $svcVerFile)

  py -m PyInstaller @svcArgs miner_online_simple.py
  $svcBuilt = Join-Path $svcDistDir ("{0}.exe" -f $SvcName)

  # (Generic EXE build removed)

  # miner_GUI build removed per user request. Keeping service build, signing,
  # metadata, and release steps intact.

  # Define GUI-related variables to null so later cleanup/signing checks are safe
  $GuiName = $null
  $guiDistDir = $null
  $guiVerFile = $null

  # Sign outputs (optional)
  if ($Sign) {
    if ($guiDistDir -and $GuiName) {
      $guiBuilt = Join-Path $guiDistDir ("{0}.exe" -f $GuiName)
      if (Test-Path $guiBuilt) { Invoke-CodeSigning -Path $guiBuilt }
    }
    if (Test-Path $svcBuilt) { Invoke-CodeSigning -Path $svcBuilt }
  }

  # Move to release
  $out = "release\$Code"; New-Item -ItemType Directory -Force -Path $out | Out-Null
  if ($guiDistDir -and $GuiName) {
    $guiBuilt = Join-Path $guiDistDir ("{0}.exe" -f $GuiName)
    if (Test-Path $guiBuilt) { Move-Item -Force $guiBuilt "$out\$GuiName.exe" }
  }
  if (Test-Path $svcBuilt) { Move-Item -Force $svcBuilt "$out\$SvcName.exe" }

  # Always clean version files
  try {
    $verFiles = @()
    if ($svcVerFile) { $verFiles += $svcVerFile }
    if ($guiVerFile) { $verFiles += $guiVerFile }
    if ($verFiles.Count -gt 0) { Remove-Item -Force $verFiles -ErrorAction SilentlyContinue }
  } catch {}

  # Only revert/clean 1Password patch artifacts if we patched
  if ($patched) {
    try {
      Move-Item -Force "$PWD\miner_online_simple.py.bak" "$PWD\miner_online_simple.py"
      Remove-Item -Force "$PWD\_tmp_config.json" -ErrorAction SilentlyContinue
    } catch {}
  }

Write-Host "Build complete for $Code v$Version"
