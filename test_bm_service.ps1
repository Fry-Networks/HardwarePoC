#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Automated test script for FRY_PoC_BM service functionality
    
.DESCRIPTION
    Tests all autonomous service features:
    - Configuration reading
    - Measurement collection
    - PoD cache writing
    - IPC queue operations
    - Daemon health monitoring
    
.PARAMETER Code
    Miner code (default: BM)
    
.PARAMETER WaitForMeasurements
    How long to wait for first measurement (default: 90 seconds)
    
.EXAMPLE
    .\test_bm_service.ps1
    
.EXAMPLE
    .\test_bm_service.ps1 -Code BM -WaitForMeasurements 120
#>

param(
    [string]$Code = "BM",
    [int]$WaitForMeasurements = 90
)

$ErrorActionPreference = "Continue"

# Colors for output
function Write-Success { Write-Host "✓ $args" -ForegroundColor Green }
function Write-Failure { Write-Host "✗ $args" -ForegroundColor Red }
function Write-Info { Write-Host "ℹ $args" -ForegroundColor Cyan }
function Write-Section { Write-Host "`n=== $args ===" -ForegroundColor Yellow }

# Determine data directory
$dataDir = "$env:PROGRAMDATA\FryNetworks\miner-$Code"
$configDir = "$dataDir\config"
$measureDir = "$dataDir\measurements"
$statusDir = "$dataDir\status"
$queueDir = "$dataDir\ops_queue"
$processedDir = "$dataDir\ops_processed"
$logsDir = "$dataDir\logs"

Write-Section "BM Service Test Suite"
Write-Info "Data Directory: $dataDir"
Write-Info "Test Start: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# Test counters
$passed = 0
$failed = 0
$warnings = 0

#region Phase 1: Directory Structure
Write-Section "Phase 1: Directory Structure"

$requiredDirs = @($configDir, $measureDir, $statusDir, $queueDir, $processedDir, $logsDir)
foreach ($dir in $requiredDirs) {
    if (Test-Path $dir) {
        Write-Success "Directory exists: $dir"
        $passed++
    } else {
        Write-Failure "Directory missing: $dir"
        $failed++
        # Create it for subsequent tests
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Write-Info "Created directory: $dir"
    }
}
#endregion

#region Phase 2: Service/Process Check
Write-Section "Phase 2: Service/Process Check"

$process = Get-Process -Name "FRY_PoC_${Code}_v*" -ErrorAction SilentlyContinue
if ($process) {
    Write-Success "Service process running: $($process.ProcessName) (PID: $($process.Id))"
    Write-Info "  Started: $($process.StartTime)"
    Write-Info "  Memory: $([math]::Round($process.WorkingSet64/1MB, 2)) MB"
    $passed++
} else {
    Write-Failure "Service process not running"
    Write-Info "Start the service executable in another terminal first:"
    Write-Info "  .\release\$Code\FRY_PoC_${Code}_v*.exe"
    $failed++
}
#endregion

#region Phase 3: Create Test Configuration
Write-Section "Phase 3: Configuration Setup"

$testConfig = @{
    measurement_interval = 60  # 1 minute for testing
    mysterium_enabled = $true
    presearch_enabled = $false
    diiisco_enabled = $false
    spaceacres_enabled = $false
    bright_enabled = $false
    honeygain_enabled = $false
    log_level = "DEBUG"
}

try {
    $configPath = "$configDir\miner_config.json"
    $testConfig | ConvertTo-Json -Depth 10 | Set-Content $configPath -Encoding UTF8
    Write-Success "Created test configuration: $configPath"
    Write-Info "  Measurement interval: 60 seconds"
    $passed++
} catch {
    Write-Failure "Failed to create config: $_"
    $failed++
}
#endregion

#region Phase 4: Send reload_config IPC
Write-Section "Phase 4: IPC Config Reload"

try {
    $requestId = [guid]::NewGuid().ToString()
    $request = @{
        id = $requestId
        op = "reload_config"
    }
    
    $requestPath = "$queueDir\$requestId.json"
    $request | ConvertTo-Json | Set-Content $requestPath -Encoding UTF8
    Write-Info "Sent reload_config IPC request: $requestId"
    
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
            Write-Success "Config reload succeeded (${waited}s)"
            $passed++
        } else {
            Write-Failure "Config reload failed: $($result.error)"
            $failed++
        }
    } else {
        Write-Failure "Config reload timeout after ${timeout}s"
        Write-Info "Check if IPC queue daemon is running"
        $failed++
    }
} catch {
    Write-Failure "IPC request error: $_"
    $failed++
}
#endregion

#region Phase 5: Check IPC Daemon Health
Write-Section "Phase 5: IPC Daemon Health"

try {
    $healthPath = "$processedDir\health.json"
    
    if (Test-Path $healthPath) {
        $health = Get-Content $healthPath | ConvertFrom-Json
        $lastPoll = [datetime]::Parse($health.last_poll)
        $age = (Get-Date) - $lastPoll
        
        if ($health.daemon_status -eq "running") {
            Write-Success "IPC daemon status: running"
            $passed++
        } else {
            Write-Failure "IPC daemon status: $($health.daemon_status)"
            $failed++
        }
        
        if ($age.TotalSeconds -lt 120) {
            Write-Success "Last poll: $($age.TotalSeconds.ToString('F1'))s ago"
            $passed++
        } else {
            Write-Failure "Last poll too old: $($age.TotalSeconds.ToString('F1'))s ago"
            $failed++
        }
        
        Write-Info "  Requests processed: $($health.requests_processed)"
        Write-Info "  Requests failed: $($health.requests_failed)"
        
    } else {
        Write-Failure "Health file not found: $healthPath"
        $failed++
    }
} catch {
    Write-Failure "Health check error: $_"
    $failed++
}
#endregion

#region Phase 6: Wait for Measurements
Write-Section "Phase 6: Measurement Collection (waiting ${WaitForMeasurements}s)"

Write-Info "Waiting for first measurement cycle..."
Write-Info "Default interval is 600s, but we set 60s in config"
Write-Info "After reload, next cycle should be within 60-120s"

$startWait = Get-Date
$measurePath = "$measureDir\latest.json"

# Wait for latest.json to appear or update
$checkInterval = 5
$checks = [math]::Ceiling($WaitForMeasurements / $checkInterval)

for ($i = 1; $i -le $checks; $i++) {
    if (Test-Path $measurePath) {
        $file = Get-Item $measurePath
        $age = (Get-Date) - $file.LastWriteTime
        
        if ($age.TotalSeconds -lt $WaitForMeasurements) {
            Write-Success "Found fresh measurement (${i}/${checks}): $($age.TotalSeconds.ToString('F1'))s old"
            break
        } else {
            Write-Info "Check ${i}/${checks}: measurement is $($age.TotalSeconds.ToString('F1'))s old (waiting for fresh one...)"
        }
    } else {
        Write-Info "Check ${i}/${checks}: waiting for latest.json to be created..."
    }
    
    Start-Sleep -Seconds $checkInterval
}

if (Test-Path $measurePath) {
    try {
        $measurement = Get-Content $measurePath | ConvertFrom-Json
        
        # Check timestamp
        $timestamp = [datetime]::Parse($measurement.timestamp)
        $measureAge = (Get-Date) - $timestamp
        
        if ($measureAge.TotalSeconds -lt $WaitForMeasurements) {
            Write-Success "Measurement timestamp: $($measurement.timestamp) ($($measureAge.TotalSeconds.ToString('F1'))s ago)"
            $passed++
        } else {
            Write-Failure "Measurement too old: $($measureAge.TotalSeconds.ToString('F1'))s"
            $failed++
        }
        
        # Check software version
        if ($measurement.software_version) {
            Write-Success "Software version: $($measurement.software_version)"
            $passed++
        } else {
            Write-Failure "Software version missing"
            $failed++
        }
        
        # Check hardware stats
        if ($measurement.hardware) {
            Write-Success "Hardware stats present"
            Write-Info "  CPU: $($measurement.hardware.cpu_percent)%"
            Write-Info "  Memory: $($measurement.hardware.memory_percent)%"
            Write-Info "  Disk: $($measurement.hardware.disk_percent)%"
            $passed++
        } else {
            Write-Failure "Hardware stats missing"
            $failed++
        }
        
        # Check PoD status
        if ($null -ne $measurement.pod) {
            Write-Success "PoD status present: $($measurement.pod.status)"
            $passed++
        } else {
            Write-Failure "PoD status missing"
            $failed++
        }
        
        # Check enabled PoCs
        if ($measurement.enabled_pocs) {
            Write-Success "Enabled PoCs: $($measurement.enabled_pocs -join ', ')"
            $passed++
        } else {
            Write-Failure "Enabled PoCs missing"
            $failed++
        }
        
    } catch {
        Write-Failure "Failed to parse measurement: $_"
        $failed++
    }
} else {
    Write-Failure "No measurement file created after ${WaitForMeasurements}s"
    Write-Info "Check service logs for errors"
    $failed++
}
#endregion

#region Phase 7: Check PoD Cache
Write-Section "Phase 7: PoD Cache Verification"

try {
    # Use UTC date since cache files are based on UTC (for global consistency)
    $today = [System.DateTime]::UtcNow.ToString("yyyyMMdd")
    $cachePath = "$statusDir\status-$today.json"
    
    if (Test-Path $cachePath) {
        $cache = Get-Content $cachePath | ConvertFrom-Json
        
        if ($cache.podHours) {
            Write-Success "PoD cache (podHours) exists"
            $passed++
            
            # Use UTC hour to match the UTC-based cache
            $currentHour = [System.DateTime]::UtcNow.Hour
            $hourKey = $currentHour.ToString()
            
            if ($cache.podHours.$hourKey) {
                $slots = $cache.podHours.$hourKey.slots
                $slotCount = $slots.Count
                Write-Success "Current UTC hour ($currentHour) has $slotCount slot(s)"
                
                # Show recent slots
                $recentSlots = $slots | Select-Object -Last 5
                Write-Info "  Recent slots: $($recentSlots -join ', ')"
                $passed++
            } else {
                Write-Failure "Current UTC hour ($currentHour) not in cache"
                $failed++
            }
        } else {
            Write-Failure "podHours structure missing from cache"
            $failed++
        }
    } else {
        Write-Failure "Status cache not found: $cachePath"
        $failed++
    }
} catch {
    Write-Failure "PoD cache check error: $_"
    $failed++
}
#endregion

#region Phase 8: Check Encrypted Measurements
Write-Section "Phase 8: Encrypted Measurement Files"

try {
    $encFiles = Get-ChildItem "$measureDir\measurements_*.enc" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    
    if ($encFiles) {
        Write-Success "Found $($encFiles.Count) encrypted measurement file(s)"
        $passed++
        
        # Show most recent
        $recent = $encFiles | Select-Object -First 3
        foreach ($file in $recent) {
            $age = (Get-Date) - $file.LastWriteTime
            Write-Info "  $($file.Name) ($($age.TotalMinutes.ToString('F1'))m ago)"
        }
    } else {
        Write-Info "No encrypted measurement files yet (expected after first collection)"
        $warnings++
    }
} catch {
    Write-Failure "Encrypted file check error: $_"
    $failed++
}
#endregion

#region Phase 9: Service Logs
Write-Section "Phase 9: Service Logs"

try {
    $logPath = "$logsDir\service.err.log"
    
    if (Test-Path $logPath) {
        $logFile = Get-Item $logPath
        Write-Success "Log file exists: $($logFile.Length) bytes"
        $passed++
        
        # Check for critical errors
        $errors = Select-String -Path $logPath -Pattern "ERROR|CRITICAL|Exception" -CaseSensitive:$false | Select-Object -Last 5
        
        if ($errors) {
            Write-Info "Recent errors/exceptions found:"
            foreach ($err in $errors) {
                Write-Info "  Line $($err.LineNumber): $($err.Line.Substring(0, [Math]::Min(100, $err.Line.Length)))"
            }
            $warnings++
        } else {
            Write-Success "No errors/exceptions in logs"
            $passed++
        }
        
        # Check for key events
        $events = @(
            @{Pattern = "measurement_daemon_start"; Name = "Measurement daemon started"},
            @{Pattern = "ops_daemon_start"; Name = "IPC daemon started"},
            @{Pattern = "measurements_collected"; Name = "Measurements collected"},
            @{Pattern = "service_config_reloaded"; Name = "Config reloaded"}
        )
        
        foreach ($event in $events) {
            $found = Select-String -Path $logPath -Pattern $event.Pattern -Quiet
            if ($found) {
                Write-Success "Event logged: $($event.Name)"
                $passed++
            } else {
                Write-Info "Event not found: $($event.Name)"
                $warnings++
            }
        }
        
    } else {
        Write-Failure "Log file not found: $logPath"
        $failed++
    }
} catch {
    Write-Failure "Log check error: $_"
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
if ($warnings -gt 0) {
    Write-Host "⚠ Warnings: $warnings" -ForegroundColor Yellow
}
Write-Host "Success Rate: ${successRate}%" -ForegroundColor $(if ($successRate -ge 80) { "Green" } elseif ($successRate -ge 60) { "Yellow" } else { "Red" })

Write-Host "`nTest completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Gray

if ($failed -eq 0) {
    Write-Host "`n🎉 All tests passed! Service is working correctly." -ForegroundColor Green
    exit 0
} elseif ($failed -le 3) {
    Write-Host "`n⚠️  Some tests failed. Check logs for details." -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "`n❌ Multiple test failures. Service may not be functioning correctly." -ForegroundColor Red
    exit 2
}
#endregion
