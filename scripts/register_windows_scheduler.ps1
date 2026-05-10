# Register Polymarket collector in Windows Task Scheduler.
#
# Creates two tasks:
#   PolymarketCollectorRegular - every 5 minutes
#   PolymarketCollectorHot     - every 1 minute (skips when nothing closing)
#
# Both run under the current user while logged on. This is intentional for the
# paper/research phase: network and Python env are in the user profile, no need
# to hard-code passwords. Re-run this script to refresh task definitions.

$ErrorActionPreference = "Stop"

$root = "C:\poly\detect-temperature"
$python = Join-Path $root ".venv\Scripts\python.exe"
$collector = Join-Path $root "scripts\windows_collector.py"

if (-not (Test-Path $python)) {
    throw "python venv not found at $python"
}
if (-not (Test-Path $collector)) {
    throw "collector not found at $collector"
}

function Register-CollectorTask {
    param(
        [string]$Name,
        [string]$Mode,
        [int]$IntervalMinutes,
        [int]$HotWindowMin = 60
    )

    $argList = "`"$collector`" --mode $Mode"
    if ($Mode -eq "hot") {
        $argList += " --hot-window-min $HotWindowMin"
    }

    $action = New-ScheduledTaskAction `
        -Execute $python `
        -Argument $argList `
        -WorkingDirectory $root

    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 4)

    $currentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $principal = New-ScheduledTaskPrincipal -UserId $currentUserSid `
        -LogonType Interactive -RunLevel Limited

    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }

    Register-ScheduledTask -TaskName $Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Polymarket weather $Mode collector (every $IntervalMinutes min)" | Out-Null

    Write-Host "registered: $Name (every $IntervalMinutes min, mode=$Mode)"
}

Register-CollectorTask -Name "PolymarketCollectorRegular" -Mode "regular" -IntervalMinutes 5
Register-CollectorTask -Name "PolymarketCollectorHot"     -Mode "hot"     -IntervalMinutes 1 -HotWindowMin 60

Write-Host ""
Write-Host "Scheduler status:"
Get-ScheduledTask -TaskName "PolymarketCollector*" | `
    Select-Object TaskName, State | Format-Table -AutoSize
