#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Robustness, Edge Case, and Performance Testing for BM Service
    
.DESCRIPTION
    Tests:
    1. Edge cases (invalid config, missing dirs, corrupted files)
    2. Service restart recovery
    3. Concurrent IPC operations
    4. Measurement collection reliability
    5. Performance baselines (CPU, memory, throughput)
    6. Log file growth
    
.EXAMPLE
    .\test_robustness_perf.ps1
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
function Write-Warning { Write-Host "⚠ $args" -ForegroundColor Yellow }

# Determine data directory
$dataDir = "$env:PROGRAMDATA\FryNetworks\miner-$Code"
$configDir = "$dataDir\config"
$measureDir = "$dataDir\measurements"
$queueDir = "$dataDir\ops_queue"
$processedDir = "$dataDir\ops_processed"
$logsDir = "$dataDir\logs"
$statusDir = "$dataDir\status"

Write-Section "Robustness & Performance Testing"
Write-Info "Test Start: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

$passed = 0
$failed = 0
$findings = @()

function Record-Finding {
    param([string]$Category, [string]$Finding, [string]$Severity)
    $findings += @{ Category = $Category; Finding = $Finding; Severity = $Severity }
    Write-Warning "$Severity - ${Category}: $Finding"
}

#region Test 1: Edge Cases - Invalid Config
Write-Section "Test 1: Edge Cases - Invalid Configuration"

try {
    Write-Step "Testing with malformed JSON config"
    
    $badConfigPath = "$configDir\bad_config.json"
    "{ invalid json }" | Set-Content $badConfigPath -Encoding UTF8
    
    # Trigger reload with bad config
    $reloadId = [guid]::NewGuid().ToString()
    $reloadRequest = @{
        id = $reloadId
        op = "reload_config"
    }
    
    $reloadPath = "$queueDir\$reloadId.json"
    $reloadRequest | ConvertTo-Json | Set-Content $reloadPath -Encoding UTF8
    
    Start-Sleep -Milliseconds 1000
    $reloadResult = "$processedDir\$reloadId.done.json"
    
    if (Test-Path $reloadResult) {
        $result = Get-Content $reloadResult | ConvertFrom-Json
        if ($result.success -eq $false) {
            Write-Success "Service gracefully handled bad config (error: $($result.error | Select-Object -First 50))"
            $passed++
        } else {
            Record-Finding "Config Validation" "Service accepted invalid JSON config" "CRITICAL"
            $failed++
        }
    } else {
        Write-Failure "No response to bad config"
        $failed++
    }
    
    # Clean up
    Remove-Item $badConfigPath -ErrorAction SilentlyContinue
    
} catch {
    Record-Finding "Config Validation" "Exception during bad config test: $_" "HIGH"
    $failed++
}

#region Test 2: Edge Cases - Missing Fields
Write-Section "Test 2: Edge Cases - Missing Required Fields"

try {
    Write-Step "Testing write_config with missing required fields"
    
    $badOpId = [guid]::NewGuid().ToString()
    $badOp = @{
        id = $badOpId
        op = "write_config"
        # Missing: relative_path and content
    }
    
    $badPath = "$queueDir\$badOpId.json"
    $badOp | ConvertTo-Json | Set-Content $badPath -Encoding UTF8
    
    Start-Sleep -Milliseconds 500
    $badResult = "$processedDir\$badOpId.done.json"
    
    if (Test-Path $badResult) {
        $result = Get-Content $badResult | ConvertFrom-Json
        if ($result.success -eq $false) {
            Write-Success "Service correctly rejected missing required fields"
            $passed++
        } else {
            Record-Finding "Input Validation" "Service accepted write_config without required fields" "CRITICAL"
            $failed++
        }
    } else {
        Record-Finding "Input Validation" "Service didn't respond to invalid write_config" "HIGH"
        $failed++
    }
    
} catch {
    Record-Finding "Input Validation" "Exception during missing fields test: $_" "HIGH"
    $failed++
}

#endregion

#region Test 3: Concurrent Operations
Write-Section "Test 3: Concurrent IPC Operations"

try {
    Write-Step "Sending 10 concurrent reload_config operations"
    
    $operationIds = @()
    $startTime = Get-Date
    
    # Queue 10 operations rapidly
    for ($i = 1; $i -le 10; $i++) {
        $opId = [guid]::NewGuid().ToString()
        $operationIds += $opId
        
        $op = @{
            id = $opId
            op = "reload_config"
        }
        
        $opPath = "$queueDir\$opId.json"
        $op | ConvertTo-Json | Set-Content $opPath -Encoding UTF8
    }
    
    Write-Info "  Queued 10 operations in $((Get-Date) - $startTime).TotalMilliseconds ms"
    
    # Wait for all to complete
    $allCompleted = $true
    $timeout = 20
    $waited = 0
    $completedCount = 0
    
    while ($waited -lt $timeout) {
        $completedCount = 0
        foreach ($opId in $operationIds) {
            if (Test-Path "$processedDir\$opId.done.json") {
                $completedCount++
            }
        }
        
        if ($completedCount -eq 10) {
            break
        }
        
        Start-Sleep -Milliseconds 500
        $waited += 0.5
    }
    
    $totalTime = (Get-Date) - $startTime
    
    if ($completedCount -eq 10) {
        Write-Success "All 10 concurrent operations completed in $($totalTime.TotalSeconds)s"
        Write-Info "  Throughput: $([math]::Round(10 / $totalTime.TotalSeconds, 2)) ops/sec"
        $passed++
    } else {
        Record-Finding "Concurrency" "Only $completedCount/10 concurrent ops completed" "HIGH"
        $failed++
    }
    
    # Check for data integrity
    $anyFailed = $false
    foreach ($opId in $operationIds) {
        $result = Get-Content "$processedDir\$opId.done.json" | ConvertFrom-Json
        if ($result.success -ne $true) {
            $anyFailed = $true
            break
        }
    }
    
    if (-not $anyFailed) {
        Write-Success "All operations succeeded without conflicts"
        $passed++
    } else {
        Record-Finding "Concurrency" "Some concurrent operations failed or had data integrity issues" "HIGH"
        $failed++
    }
    
} catch {
    Record-Finding "Concurrency" "Exception during concurrent ops test: $_" "HIGH"
    $failed++
}

#endregion

#region Test 4: Service Restart Recovery
Write-Section "Test 4: Service Restart Recovery"

try {
    Write-Step "Testing service restart and recovery"
    
    # Get current process info
    $process = Get-Process -Name "FRY_PoC_${Code}_v*" | Select-Object -First 1
    $originalPid = $process.Id
    Write-Info "  Original PID: $originalPid"
    
    # Record current state
    $beforeRestart = @{
        LogSize = (Get-Item "$logsDir\service.err.log").Length
        MeasurementCount = (Get-ChildItem "$measureDir\*.enc" | Measure-Object).Count
    }
    
    # Stop service
    Write-Step "Stopping service..."
    Stop-Process -Name "FRY_PoC_${Code}_v*" -Force
    Start-Sleep -Seconds 2
    
    # Restart service
    Write-Step "Restarting service..."
    $exePath = Get-ChildItem -Path "$dataDir\FRY_PoC_${Code}_*.exe" | Select-Object -First 1
    Start-Process $exePath.FullName -WindowStyle Minimized
    Start-Sleep -Seconds 5
    
    # Verify recovery
    $newProcess = Get-Process -Name "FRY_PoC_${Code}_v*" -ErrorAction SilentlyContinue
    
    if ($newProcess) {
        $newPid = $newProcess.Id
        Write-Success "Service restarted successfully (new PID: $newPid)"
        $passed++
        
        # Test IPC functionality after restart
        $testOpId = [guid]::NewGuid().ToString()
        $testOp = @{
            id = $testOpId
            op = "reload_config"
        }
        
        $testPath = "$queueDir\$testOpId.json"
        $testOp | ConvertTo-Json | Set-Content $testPath -Encoding UTF8
        
        Start-Sleep -Seconds 2
        
        if (Test-Path "$processedDir\$testOpId.done.json") {
            $result = Get-Content "$processedDir\$testOpId.done.json" | ConvertFrom-Json
            if ($result.success -eq $true) {
                Write-Success "Service operational after restart"
                $passed++
            } else {
                Record-Finding "Restart Recovery" "Service didn't process IPC after restart" "MEDIUM"
                $failed++
            }
        } else {
            Record-Finding "Restart Recovery" "No IPC response after service restart" "HIGH"
            $failed++
        }
        
        # Check log continuity
        $afterRestart = @{
            LogSize = (Get-Item "$logsDir\service.err.log").Length
        }
        
        if ($afterRestart.LogSize -gt $beforeRestart.LogSize) {
            Write-Success "Service logged recovery events"
            $passed++
        } else {
            Record-Finding "Restart Recovery" "Service didn't log recovery events" "MEDIUM"
            $failed++
        }
        
    } else {
        Record-Finding "Restart Recovery" "Service failed to restart" "CRITICAL"
        $failed++
    }
    
} catch {
    Record-Finding "Restart Recovery" "Exception during restart test: $_" "HIGH"
    $failed++
}

#endregion

#region Test 5: Performance Baseline - CPU & Memory
Write-Section "Test 5: Performance Baseline - CPU & Memory"

try {
    Write-Step "Measuring CPU and memory usage (idle + under load)"
    
    # Idle measurement (wait for service to stabilize)
    Start-Sleep -Seconds 5
    
    $process = Get-Process -Name "FRY_PoC_${Code}_v*" | Select-Object -First 1
    $cpuIdleSnapshots = @()
    $memIdleSnapshots = @()
    
    Write-Info "  Capturing idle metrics (5 samples)..."
    for ($i = 1; $i -le 5; $i++) {
        $cpuIdleSnapshots += $process.CPU
        $memIdleSnapshots += ($process.WorkingSet64 / 1MB)
        Start-Sleep -Seconds 1
    }
    
    $idleCpuAvg = ($cpuIdleSnapshots | Measure-Object -Average).Average
    $idleMemAvg = ($memIdleSnapshots | Measure-Object -Average).Average
    $idleMemMax = ($memIdleSnapshots | Measure-Object -Maximum).Maximum
    
    Write-Info "  Idle CPU: $([math]::Round($idleCpuAvg, 2))% avg"
    Write-Info "  Idle Memory: $([math]::Round($idleMemAvg, 1)) MB avg, $([math]::Round($idleMemMax, 1)) MB max"
    
    # Load measurement (concurrent IPC operations)
    Write-Step "Measuring under concurrent load..."
    $cpuLoadSnapshots = @()
    $memLoadSnapshots = @()
    
    # Queue operations
    for ($i = 1; $i -le 5; $i++) {
        $opId = [guid]::NewGuid().ToString()
        $op = @{
            id = $opId
            op = "write_config"
            relative_path = "perf_test_$i.json"
            content = '{"test":"data"}'
        }
        
        $opPath = "$queueDir\$opId.json"
        $op | ConvertTo-Json | Set-Content $opPath -Encoding UTF8
    }
    
    # Monitor while operations are processed
    for ($i = 1; $i -le 10; $i++) {
        $cpuLoadSnapshots += $process.CPU
        $memLoadSnapshots += ($process.WorkingSet64 / 1MB)
        Start-Sleep -Milliseconds 500
    }
    
    $loadCpuAvg = ($cpuLoadSnapshots | Measure-Object -Average).Average
    $loadMemAvg = ($memLoadSnapshots | Measure-Object -Average).Average
    $loadMemMax = ($memLoadSnapshots | Measure-Object -Maximum).Maximum
    
    Write-Info "  Load CPU: $([math]::Round($loadCpuAvg, 2))% avg"
    Write-Info "  Load Memory: $([math]::Round($loadMemAvg, 1)) MB avg, $([math]::Round($loadMemMax, 1)) MB max"
    
    Write-Success "Performance baseline captured"
    $passed++
    
    # Check for memory leaks (simple heuristic)
    if ($loadMemMax -gt $idleMemMax * 2) {
        Record-Finding "Performance" "Memory usage increased >200% under load - investigate for leaks" "MEDIUM"
    } else {
        Write-Success "Memory usage increase within acceptable range"
        $passed++
    }
    
} catch {
    Record-Finding "Performance" "Exception during performance test: $_" "HIGH"
    $failed++
}

#endregion

#region Test 6: Log File Growth
Write-Section "Test 6: Log File Growth Analysis"

try {
    Write-Step "Analyzing log file growth"
    
    $logFile = "$logsDir\service.err.log"
    $logSize = (Get-Item $logFile).Length
    $logLineCount = (Get-Content $logFile | Measure-Object -Line).Lines
    
    $avgLineSize = $logSize / $logLineCount
    
    Write-Info "  Current size: $([math]::Round($logSize / 1MB, 2)) MB"
    Write-Info "  Line count: $logLineCount"
    Write-Info "  Average line size: $([math]::Round($avgLineSize, 1)) bytes"
    
    # Project to 30 days at 10-min measurement interval
    # 144 measurements per day * 30 days = 4,320 measurements
    $projectedLines = 4320  # measurements
    $projectedLines += 4320 * 5  # 5 log lines per measurement estimate
    $projectedSize = $projectedLines * $avgLineSize
    
    Write-Info "  Projected 30-day size: $([math]::Round($projectedSize / 1MB, 1)) MB"
    
    if ($projectedSize / 1MB -gt 100) {
        Record-Finding "Storage" "Projected 30-day log size >100MB - consider log rotation" "MEDIUM"
    } else {
        Write-Success "Log file growth within acceptable range"
        $passed++
    }
    
} catch {
    Record-Finding "Log Analysis" "Exception during log analysis: $_" "MEDIUM"
    $failed++
}

#endregion

#region Test 7: Measurement Reliability
Write-Section "Test 7: Measurement Collection Reliability"

try {
    Write-Step "Checking measurement collection consistency"
    
    $measurementFiles = Get-ChildItem "$measureDir\measurements_*.enc" | Sort-Object LastWriteTime -Descending | Select-Object -First 10
    
    if ($measurementFiles.Count -lt 2) {
        Write-Warning "Not enough measurement history for analysis (need at least 2)"
        $warnings++
    } else {
        $intervals = @()
        for ($i = 0; $i -lt $measurementFiles.Count - 1; $i++) {
            $interval = ($measurementFiles[$i].LastWriteTime - $measurementFiles[$i + 1].LastWriteTime).TotalSeconds
            $intervals += $interval
        }
        
        $avgInterval = ($intervals | Measure-Object -Average).Average
        $maxInterval = ($intervals | Measure-Object -Maximum).Maximum
        $minInterval = ($intervals | Measure-Object -Minimum).Minimum
        
        Write-Info "  Average interval: $([math]::Round($avgInterval, 1))s"
        Write-Info "  Range: $([math]::Round($minInterval, 1))s - $([math]::Round($maxInterval, 1))s"
        
        # Check if intervals are consistent (within 20% of expected 60s)
        $expectedInterval = 60
        $tolerance = $expectedInterval * 0.2
        $inconsistent = @($intervals | Where-Object { $_ -lt ($expectedInterval - $tolerance) -or $_ -gt ($expectedInterval + $tolerance) }).Count
        
        if ($inconsistent -eq 0) {
            Write-Success "Measurement intervals consistent and reliable"
            $passed++
        } else {
            Record-Finding "Reliability" "$inconsistent out of $($intervals.Count) intervals outside tolerance" "LOW"
        }
    }
    
} catch {
    Record-Finding "Reliability" "Exception during measurement reliability test: $_" "MEDIUM"
    $failed++
}

#endregion

#region Summary & Findings
Write-Section "Test Summary"

$total = $passed + $failed
$successRate = if ($total -gt 0) { [math]::Round(($passed / $total) * 100, 1) } else { 0 }

Write-Host "`nTest Results:" -ForegroundColor White
Write-Success "Passed: $passed"
Write-Failure "Failed: $failed"
Write-Host "Success Rate: ${successRate}%" -ForegroundColor $(if ($successRate -ge 80) { "Green" } elseif ($successRate -ge 60) { "Yellow" } else { "Red" })

if ($findings.Count -gt 0) {
    Write-Section "Findings for GUI Dev Guide"
    
    $findingsBySeverity = @{
        "CRITICAL" = @()
        "HIGH" = @()
        "MEDIUM" = @()
        "LOW" = @()
    }
    
    foreach ($finding in $findings) {
        $findingsBySeverity[$finding.Severity] += $finding
    }
    
    foreach ($severity in @("CRITICAL", "HIGH", "MEDIUM", "LOW")) {
        if ($findingsBySeverity[$severity].Count -gt 0) {
            Write-Host "`n$severity Findings:" -ForegroundColor $(
                switch ($severity) {
                    "CRITICAL" { "Red" }
                    "HIGH" { "Yellow" }
                    "MEDIUM" { "Cyan" }
                    default { "Gray" }
                }
            )
            
            foreach ($item in $findingsBySeverity[$severity]) {
                Write-Host "  • $($item.Category): $($item.Finding)" -ForegroundColor $(
                    switch ($severity) {
                        "CRITICAL" { "Red" }
                        "HIGH" { "Yellow" }
                        "MEDIUM" { "Cyan" }
                        default { "Gray" }
                    }
                )
            }
        }
    }
} else {
    Write-Success "No critical issues found!"
}

Write-Host "`nTest completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Gray

if ($failed -eq 0) {
    Write-Host "`n✅ Robustness testing passed! Service is production-ready." -ForegroundColor Green
    exit 0
} else {
    Write-Host "`n⚠️  Review findings above before production deployment." -ForegroundColor Yellow
    exit 1
}

#endregion
