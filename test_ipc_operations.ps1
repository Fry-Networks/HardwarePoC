#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Comprehensive IPC operations test script
    
.DESCRIPTION
    Tests:
    1. write_measurement operation with encrypted data
    2. Multiple PoCs enabled simultaneously (mysterium + presearch + diiisco + spaceacres)
    3. Mock GUI integration scenarios
    
.EXAMPLE
    .\test_ipc_operations.ps1
#>

param(
    [string]$Code = "BM"
)

$ErrorActionPreference = "Continue"

# Colors for output
function Write-Success { Write-Host "✓ $args" -ForegroundColor Green }
function Write-Failure { Write-Host "✗ $args" -ForegroundColor Red }
function Write-Info { Write-Host "ℹ $args" -ForegroundColor Cyan }
function Write-Section { Write-Host "`n=== $args ===" -ForegroundColor Yellow }
function Write-Step { Write-Host "→ $args" -ForegroundColor Magenta }

# Determine data directory
$dataDir = "$env:PROGRAMDATA\FryNetworks\miner-$Code"
$configDir = "$dataDir\config"
$measureDir = "$dataDir\measurements"
$queueDir = "$dataDir\ops_queue"
$processedDir = "$dataDir\ops_processed"

Write-Section "IPC Operations Test Suite"
Write-Info "Test Start: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

$passed = 0
$failed = 0

#region Test 1: write_measurement Operation
Write-Section "Test 1: write_measurement IPC Operation"

try {
    Write-Step "Testing write_measurement with encrypted data"
    
    $requestId = [guid]::NewGuid().ToString()
    
    # Create some test encrypted data (base64 encoded)
    $testData = "This is test measurement data from GUI"
    $encryptedBytes = [System.Text.Encoding]::UTF8.GetBytes($testData)
    $base64Data = [System.Convert]::ToBase64String($encryptedBytes)
    
    $request = @{
        id = $requestId
        op = "write_measurement"
        tool = "TestMeasurement"
        data_b64 = $base64Data
    }
    
    $requestPath = "$queueDir\$requestId.json"
    $request | ConvertTo-Json | Set-Content $requestPath -Encoding UTF8
    Write-Info "  Sent write_measurement request: $requestId"
    Write-Info "  Tool: TestMeasurement"
    Write-Info "  Data size: $($encryptedBytes.Length) bytes"
    
    # Wait for response (max 10 seconds)
    $resultPath = "$processedDir\$requestId.done.json"
    $timeout = 10
    $waited = 0
    
    while (-not (Test-Path $resultPath) -and $waited -lt $timeout) {
        Start-Sleep -Milliseconds 500
        $waited += 0.5
    }
    
    if (Test-Path $resultPath) {
        $result = Get-Content $resultPath | ConvertFrom-Json
        if ($result.success -eq $true) {
            Write-Success "write_measurement succeeded (${waited}s)"
            
            # Verify file was created
            $measFile = "$measureDir\measurements-TestMeasurement-latest.json.enc"
            if (Test-Path $measFile) {
                $fileSize = (Get-Item $measFile).Length
                Write-Success "Measurement file created: $measFile ($fileSize bytes)"
                $passed += 2
            } else {
                Write-Failure "Measurement file not found at expected location"
                $failed++
            }
        } else {
            Write-Failure "write_measurement failed: $($result.error)"
            $failed++
        }
    } else {
        Write-Failure "write_measurement timeout after ${timeout}s"
        $failed++
    }
} catch {
    Write-Failure "write_measurement test error: $_"
    $failed++
}
#endregion

#region Test 2: Multiple PoCs Enabled
Write-Section "Test 2: Multiple PoCs Configuration"

try {
    Write-Step "Configuring multiple PoCs (mysterium, presearch, diiisco, spaceacres)"
    
    $multiConfig = @{
        measurement_interval = 60
        mysterium_enabled = $true
        presearch_enabled = $true
        diiisco_enabled = $true
        spaceacres_enabled = $true
        bright_enabled = $false
        honeygain_enabled = $false
        log_level = "DEBUG"
    }
    
    $configPath = "$configDir\miner_config.json"
    $multiConfig | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
    Write-Success "Created multi-PoC configuration"
    Write-Info "  Enabled: mysterium, presearch, diiisco, spaceacres"
    $passed++
    
    # Trigger reload via IPC
    Write-Step "Sending reload_config to apply multi-PoC config"
    $reloadId = [guid]::NewGuid().ToString()
    $reloadRequest = @{
        id = $reloadId
        op = "reload_config"
    }
    
    $reloadPath = "$queueDir\$reloadId.json"
    $reloadRequest | ConvertTo-Json | Set-Content $reloadPath -Encoding UTF8
    
    # Wait for reload (max 10 seconds)
    $reloadResult = "$processedDir\$reloadId.done.json"
    $timeout = 10
    $waited = 0
    
    while (-not (Test-Path $reloadResult) -and $waited -lt $timeout) {
        Start-Sleep -Milliseconds 500
        $waited += 0.5
    }
    
    if (Test-Path $reloadResult) {
        $result = Get-Content $reloadResult | ConvertFrom-Json
        if ($result.success -eq $true) {
            Write-Success "Config reload succeeded (${waited}s)"
            $passed++
        } else {
            Write-Failure "Config reload failed: $($result.error)"
            $failed++
        }
    } else {
        Write-Failure "Config reload timeout"
        $failed++
    }
    
    # Wait for next measurement with multiple PoCs
    Write-Step "Waiting for measurement with multiple PoCs enabled..."
    # Wait longer to ensure config is reloaded and new measurement collected (60s+ cycle)
    Start-Sleep -Seconds 20
    
    $measurePath = "$measureDir\latest.json"
    if (Test-Path $measurePath) {
        try {
            $measurement = Get-Content $measurePath | ConvertFrom-Json
            
            if ($measurement.enabled_pocs) {
                $pocCount = if ($measurement.enabled_pocs -is [array]) { $measurement.enabled_pocs.Count } else { 1 }
                Write-Success "Measurement shows enabled PoCs: $($measurement.enabled_pocs -join ', ')"
                Write-Info "  Total PoCs: $pocCount"
                $passed++
                
                # For BM service, only mysterium should be present (even if other PoCs are enabled in config)
                # This tests that the service correctly filters PoCs by miner type
                $pocArray = if ($measurement.enabled_pocs -is [array]) { $measurement.enabled_pocs } else { @($measurement.enabled_pocs) }
                
                if ($pocArray -contains "mysterium") {
                    Write-Success "BM correctly reports mysterium as enabled PoC"
                    $passed++
                    
                    # Verify other PoCs are NOT included (since BM doesn't support them)
                    $incorrectPocs = $pocArray | Where-Object { $_ -in @("presearch", "diiisco", "spaceacres") }
                    if ($incorrectPocs) {
                        Write-Failure "BM incorrectly includes non-BM PoCs: $($incorrectPocs -join ', ')"
                        $failed++
                    } else {
                        Write-Success "BM correctly excludes SDN/SVN PoCs"
                        $passed++
                    }
                } else {
                    Write-Failure "BM should include mysterium"
                    $failed++
                }
            } else {
                Write-Failure "enabled_pocs field not present in measurement"
                $failed++
            }
        } catch {
            Write-Failure "Failed to parse measurement: $_"
            $failed++
        }
    } else {
        Write-Failure "No measurement file found"
        $failed++
    }
    
} catch {
    Write-Failure "Multi-PoC test error: $_"
    $failed++
}
#endregion

#region Test 3: Mock GUI Integration
Write-Section "Test 3: Mock GUI Integration Scenario"

try {
    Write-Step "Simulating GUI operations workflow"
    
    # Step 1: GUI reads current measurements
    Write-Step "Step 1: GUI reads latest measurements"
    $measurePath = "$measureDir\latest.json"
    
    if (Test-Path $measurePath) {
        $measurement = Get-Content $measurePath | ConvertFrom-Json
        Write-Success "GUI successfully read measurements"
        Write-Info "  Timestamp: $($measurement.timestamp)"
        Write-Info "  Software: $($measurement.software_version)"
        Write-Info "  PoCs: $($measurement.enabled_pocs -join ', ')"
        Write-Info "  CPU: $($measurement.hardware.cpu_percent)%"
        Write-Info "  Memory: $($measurement.hardware.memory_percent)%"
        $passed++
    } else {
        Write-Failure "GUI failed to read measurements"
        $failed++
    }
    
    # Step 2: GUI modifies config and sends write_config
    Write-Step "Step 2: GUI modifies config and sends write_config"
    
    $guiConfig = @{
        measurement_interval = 120
        mysterium_enabled = $true
        presearch_enabled = $false
        diiisco_enabled = $true
        spaceacres_enabled = $false
        bright_enabled = $false
        honeygain_enabled = $false
        log_level = "INFO"
    }
    
    $writeConfigId = [guid]::NewGuid().ToString()
    $writeConfigRequest = @{
        id = $writeConfigId
        op = "write_config"
        relative_path = "gui_test_config.json"
        content = ($guiConfig | ConvertTo-Json)
    }
    
    $writePath = "$queueDir\$writeConfigId.json"
    $writeConfigRequest | ConvertTo-Json | Set-Content $writePath -Encoding UTF8
    Write-Info "  Sent write_config request"
    
    # Wait for response
    $writeResult = "$processedDir\$writeConfigId.done.json"
    $timeout = 10
    $waited = 0
    
    while (-not (Test-Path $writeResult) -and $waited -lt $timeout) {
        Start-Sleep -Milliseconds 500
        $waited += 0.5
    }
    
    if (Test-Path $writeResult) {
        $result = Get-Content $writeResult | ConvertFrom-Json
        if ($result.success -eq $true) {
            Write-Success "GUI write_config succeeded"
            
            # Verify file was created
            $guiConfigPath = "$configDir\gui_test_config.json"
            if (Test-Path $guiConfigPath) {
                $configContent = Get-Content $guiConfigPath | ConvertFrom-Json
                Write-Success "GUI config file created and readable"
                Write-Info "  Measurement interval: $($configContent.measurement_interval)s"
                Write-Info "  Enabled PoCs: mysterium=$($configContent.mysterium_enabled), diiisco=$($configContent.diiisco_enabled)"
                $passed += 2
            } else {
                Write-Failure "GUI config file not found"
                $failed++
            }
        } else {
            Write-Failure "GUI write_config failed: $($result.error)"
            $failed++
        }
    } else {
        Write-Failure "GUI write_config timeout"
        $failed++
    }
    
    # Step 3: GUI sends reload_config
    Write-Step "Step 3: GUI sends reload_config to apply changes"
    
    $reloadId = [guid]::NewGuid().ToString()
    $reloadRequest = @{
        id = $reloadId
        op = "reload_config"
    }
    
    $reloadPath = "$queueDir\$reloadId.json"
    $reloadRequest | ConvertTo-Json | Set-Content $reloadPath -Encoding UTF8
    
    # Wait for response
    $reloadResult = "$processedDir\$reloadId.done.json"
    $timeout = 10
    $waited = 0
    
    while (-not (Test-Path $reloadResult) -and $waited -lt $timeout) {
        Start-Sleep -Milliseconds 500
        $waited += 0.5
    }
    
    if (Test-Path $reloadResult) {
        $result = Get-Content $reloadResult | ConvertFrom-Json
        if ($result.success -eq $true) {
            Write-Success "GUI reload_config succeeded"
            $passed++
        } else {
            Write-Failure "GUI reload_config failed: $($result.error)"
            $failed++
        }
    } else {
        Write-Failure "GUI reload_config timeout"
        $failed++
    }
    
    # Step 4: GUI polls measurements after reload
    Write-Step "Step 4: GUI polls measurements after config change"
    Start-Sleep -Seconds 5
    
    if (Test-Path $measurePath) {
        $measurement = Get-Content $measurePath | ConvertFrom-Json
        Write-Success "GUI polled fresh measurements post-reload"
        Write-Info "  Timestamp: $($measurement.timestamp)"
        Write-Info "  PoCs: $($measurement.enabled_pocs -join ', ')"
        $passed++
    } else {
        Write-Failure "GUI failed to poll measurements"
        $failed++
    }
    
    # Step 5: GUI reads PoD cache
    Write-Step "Step 5: GUI reads PoD cache from status directory"
    
    $today = [System.DateTime]::UtcNow.ToString("yyyyMMdd")
    $cachePath = "$dataDir\status\status-$today.json"
    
    if (Test-Path $cachePath) {
        $cache = Get-Content $cachePath | ConvertFrom-Json
        if ($cache.podHours) {
            Write-Success "GUI successfully read PoD cache"
            $hourCount = @($cache.podHours.PSObject.Properties).Count
            Write-Info "  Hours with data: $hourCount"
            Write-Info "  Sample hour 23: $($cache.podHours.'23'.slots.Count) slots"
            $passed++
        } else {
            Write-Failure "PoD cache missing podHours structure"
            $failed++
        }
    } else {
        Write-Failure "PoD cache file not found"
        $failed++
    }
    
} catch {
    Write-Failure "Mock GUI integration test error: $_"
    $failed++
}
#endregion

#region Summary
Write-Section "Test Summary"

$total = $passed + $failed
$successRate = if ($total -gt 0) { [math]::Round(($passed / $total) * 100, 1) } else { 0 }

Write-Host "`nResults:" -ForegroundColor White
Write-Success "Passed: $passed"
Write-Failure "Failed: $failed"
Write-Host "Success Rate: ${successRate}%" -ForegroundColor $(if ($successRate -ge 90) { "Green" } elseif ($successRate -ge 70) { "Yellow" } else { "Red" })

Write-Host "`nTest completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Gray

if ($failed -eq 0) {
    Write-Host "`n🎉 All IPC operations and GUI scenarios working!" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`n⚠️  Some tests failed. Check logs for details." -ForegroundColor Yellow
    exit 1
}
#endregion
