<#
.SYNOPSIS
    Build all PoC miner executables for Windows.

.DESCRIPTION
    This script builds Windows service executables for all miner types (BM, IDM, ODM, ISM, OSM, RDN, SDN, SVN, AEM, IRM).
    Each build embeds API credentials from 1Password and creates a standalone executable.

.PARAMETER Version
    The version number for all builds (e.g., "5.5.6"). Required.

.PARAMETER OPRefBearerToken
    1Password reference for API bearer token. Defaults to "op://VPS/Hardware_API/API_BEARER_TOKEN".

.PARAMETER OPRefSigningKey
    1Password reference for local signing key. Defaults to "op://VPS/Hardware_API/LOCAL_SIGNING_KEY".

.PARAMETER IntervalSeconds
    Monitoring interval in seconds. Default: 600 (10 minutes).

.PARAMETER Sign
    If set, code-sign the executables (requires signtool.exe in PATH).

.PARAMETER Parallel
    If set, build all miner types in parallel (faster but more resource intensive).

.PARAMETER MinerTypes
    Array of miner type codes to build. Default: all types (BM, IDM, ODM, ISM, OSM, RDN, SDN, SVN, AEM, IRM).

.EXAMPLE
    .\build_all_PoC_windows.ps1 -Version "5.5.6"
    Build all miner types with version 5.5.6

.EXAMPLE
    .\build_all_PoC_windows.ps1 -Version "5.5.6" -MinerTypes @("BM", "ISM", "OSM")
    Build only BM, ISM, and OSM miners

.EXAMPLE
    .\build_all_PoC_windows.ps1 -Version "5.5.6" -Parallel -Sign
    Build all miners in parallel and sign them
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$Version,
    
    [string]$OPRefBearerToken = "op://VPS/Hardware_API/API_BEARER_TOKEN",
    
    [string]$OPRefSigningKey = "op://VSCode/hardware_exe/local_signing_key_hex",
    
    [int]$IntervalSeconds = 600,
    
    [switch]$Sign,
    
    [switch]$Parallel,
    
    [string[]]$MinerTypes = @("BM", "IDM", "ODM", "ISM", "OSM", "RDN", "SDN", "SVN", "AEM", "IRM")
)

$ErrorActionPreference = "Stop"

# Validate version format
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must be in format X.Y.Z (e.g., 5.5.6)"
}

# Check if build_PoC_windows.ps1 exists
$buildScript = Join-Path $PSScriptRoot "build_PoC_windows.ps1"
if (-not (Test-Path $buildScript)) {
    throw "Build script not found: $buildScript"
}

# Check 1Password CLI availability
try {
    $null = & op --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "[OK] 1Password CLI detected" -ForegroundColor Green
} catch {
    throw "1Password CLI (op) not found. Install from: https://1password.com/downloads/command-line/"
}

# Resolve 1Password secrets ONCE (before parallel builds to avoid multiple password prompts)
Write-Host "Retrieving secrets from 1Password..." -ForegroundColor Yellow
$bearerToken = $null
$signingKey = $null

if ($OPRefBearerToken -and $OPRefBearerToken -ne "") {
    try {
        $bearerToken = & op read $OPRefBearerToken 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] Bearer token retrieved" -ForegroundColor Green
        } else {
            Write-Warning "Failed to retrieve bearer token: $bearerToken"
        }
    } catch {
        Write-Warning "Failed to retrieve bearer token: $_"
    }
}

if ($OPRefSigningKey -and $OPRefSigningKey -ne "") {
    try {
        $signingKey = & op read $OPRefSigningKey 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] Signing key retrieved" -ForegroundColor Green
        } else {
            Write-Warning "Failed to retrieve signing key: $signingKey"
        }
    } catch {
        Write-Warning "Failed to retrieve signing key: $_"
    }
}

# Build function for a single miner type
function Build-Miner {
    param(
        [string]$Code,
        [string]$Version,
        [hashtable]$BuildParams
    )
    
    $startTime = Get-Date
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "Building $Code v$Version" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    
    try {
        $params = @{
            Code = $Code
            Version = $Version
            IntervalSeconds = $BuildParams.IntervalSeconds
            Use1Password = $false  # CRITICAL: Disable 1Password lookups since we're passing values directly
            UseGithub = $false     # Disable GitHub integration for batch builds
        }
        
        # Pass actual token/key values (not 1Password references)
        if ($BuildParams.BearerToken) {
            $params.BearerToken = $BuildParams.BearerToken
        }
        if ($BuildParams.SigningKey) {
            $params.SigningKey = $BuildParams.SigningKey
        }
        
        if ($BuildParams.Sign) {
            $params.Sign = $true
        }
        
        & $buildScript @params
        
        if ($LASTEXITCODE -ne 0) {
            throw "Build failed with exit code $LASTEXITCODE"
        }
        
        $elapsed = (Get-Date) - $startTime
        Write-Host "[OK] $Code build completed in $($elapsed.TotalSeconds.ToString('F1'))s" -ForegroundColor Green
        
        return @{
            Code = $Code
            Success = $true
            Duration = $elapsed
            Error = $null
        }
    }
    catch {
        $elapsed = (Get-Date) - $startTime
        Write-Host "[FAIL] $Code build failed: $_" -ForegroundColor Red
        
        return @{
            Code = $Code
            Success = $false
            Duration = $elapsed
            Error = $_.Exception.Message
        }
    }
}

# Prepare build parameters (with resolved secret values, not 1Password references)
$buildParams = @{
    BearerToken = $bearerToken
    SigningKey = $signingKey
    IntervalSeconds = $IntervalSeconds
    Sign = $Sign
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  Building PoC Miners - Version $Version" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Write-Host "`nMiner types to build: $($MinerTypes -join ', ')"
Write-Host "Build mode: $(if ($Parallel) { 'Parallel' } else { 'Sequential' })"
Write-Host "Code signing: $(if ($Sign) { 'Enabled' } else { 'Disabled' })"
Write-Host ""

$overallStart = Get-Date
$results = @()

if ($Parallel) {
    # Parallel builds using jobs
    Write-Host "Starting parallel builds..." -ForegroundColor Yellow
    
    $jobs = @()
    $workspaceRoot = $PSScriptRoot  # Capture workspace directory
    
    foreach ($code in $MinerTypes) {
        $jobs += Start-Job -ScriptBlock {
            param($BuildScript, $Code, $Version, $BuildParams, $WorkDir)
            
            # Set working directory to workspace root (critical for parallel jobs)
            Set-Location $WorkDir
            
            $params = @{
                Code = $Code
                Version = $Version
                IntervalSeconds = $BuildParams.IntervalSeconds
                Use1Password = $false  # CRITICAL: Disable 1Password lookups since we're passing values directly
                UseGithub = $false     # Disable GitHub integration for batch builds
            }
            
            # Pass actual token/key values (not 1Password references)
            if ($BuildParams.BearerToken) {
                $params.BearerToken = $BuildParams.BearerToken
            }
            if ($BuildParams.SigningKey) {
                $params.SigningKey = $BuildParams.SigningKey
            }
            
            if ($BuildParams.Sign) {
                $params.Sign = $true
            }
            
            $startTime = Get-Date
            try {
                & $BuildScript @params 2>&1 | Out-String
                $elapsed = (Get-Date) - $startTime
                
                return @{
                    Code = $Code
                    Success = ($LASTEXITCODE -eq 0)
                    Duration = $elapsed
                    Error = if ($LASTEXITCODE -ne 0) { "Exit code: $LASTEXITCODE" } else { $null }
                }
            }
            catch {
                $elapsed = (Get-Date) - $startTime
                return @{
                    Code = $Code
                    Success = $false
                    Duration = $elapsed
                    Error = $_.Exception.Message
                }
            }
        } -ArgumentList $buildScript, $code, $Version, $buildParams, $workspaceRoot
    }
    
    # Wait for all jobs and collect results
    $completed = 0
    while ($jobs.Count -gt 0) {
        $finished = $jobs | Where-Object { $_.State -ne 'Running' }
        
        foreach ($job in $finished) {
            $result = Receive-Job -Job $job
            Remove-Job -Job $job
            $jobs = $jobs | Where-Object { $_.Id -ne $job.Id }
            
            if ($result) {
                $results += ,$result  # Force array wrapping with comma operator
                $completed++
                
                $status = if ($result.Success) { "[OK]" } else { "[FAIL]" }
                $color = if ($result.Success) { "Green" } else { "Red" }
                Write-Host "[$completed/$($MinerTypes.Count)] $status $($result.Code) - $($result.Duration.TotalSeconds.ToString('F1'))s" -ForegroundColor $color
            }
        }
        
        if ($jobs.Count -gt 0) {
            Start-Sleep -Milliseconds 500
        }
    }
}
else {
    # Sequential builds
    foreach ($code in $MinerTypes) {
        $result = Build-Miner -Code $code -Version $Version -BuildParams $buildParams
        $results += ,$result  # Force array wrapping with comma operator
    }
}

$overallElapsed = (Get-Date) - $overallStart

# Summary report
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  Build Summary"
Write-Host "============================================================" -ForegroundColor Cyan

$successful = @($results | Where-Object { $_.Success })
$failed = @($results | Where-Object { -not $_.Success })

Write-Host "`nTotal builds: $($results.Count)"
Write-Host "Successful: $($successful.Count)" -ForegroundColor Green
Write-Host "Failed: $($failed.Count)" -ForegroundColor $(if ($failed.Count -gt 0) { "Red" } else { "Gray" })
Write-Host "Total time: $($overallElapsed.TotalMinutes.ToString('F1')) minutes"

if ($successful.Count -gt 0) {
    Write-Host "`n[OK] Successful builds:" -ForegroundColor Green
    foreach ($result in $successful) {
        $exePath = "release\$($result.Code)\FRY_PoC_$($result.Code)_v$Version.exe"
        $exists = Test-Path $exePath
        $status = if ($exists) { "[OK]" } else { "[?]" }
        Write-Host "  $status $($result.Code) ($($result.Duration.TotalSeconds.ToString('F1'))s)" -ForegroundColor Green
    }
}

if ($failed.Count -gt 0) {
    Write-Host "`n[FAIL] Failed builds:" -ForegroundColor Red
    foreach ($result in $failed) {
        Write-Host "  [FAIL] $($result.Code): $($result.Error)" -ForegroundColor Red
    }
}

# List all created executables
$releaseDir = Join-Path $PSScriptRoot "release"
if (Test-Path $releaseDir) {
    Write-Host "`nCreated executables:" -ForegroundColor Cyan
    Get-ChildItem -Path $releaseDir -Recurse -Filter "FRY_PoC_*_v$Version.exe" | ForEach-Object {
        $sizeMB = [math]::Round($_.Length / 1MB, 1)
        Write-Host "  $($_.FullName.Replace($PSScriptRoot + '\', '')) ($sizeMB MB)" -ForegroundColor Gray
    }
}

Write-Host ""

# Exit with appropriate code
if ($failed.Count -gt 0) {
    Write-Host "Build completed with errors." -ForegroundColor Red
    exit 1
}
else {
    Write-Host "All builds completed successfully!" -ForegroundColor Green
    exit 0
}
