# Register the three daily automation tasks for paper trading.
#
# Intended for a Windows PC that runs the collector 24/7. Combined with
# the existing PolymarketCollector* tasks and PolymarketCalibrationRefresh,
# this closes the loop so a human does not need to touch the dashboard
# for the normal daily cycle.
#
# Schedule (all times UTC - Task Scheduler stores local, but the content
# of the script is independent of timezone; set your PC clock correctly):
#
#   PolymarketDailyOpenTrades     22:00 daily  (~1-2 min run)
#   PolymarketNearCloseRefresh    01:00-04:30 every 30 min (8 runs)
#   PolymarketDailySettle         06:00 daily  (~30-60 sec run)
#
# Re-run this script any time to refresh the schedule definitions.

$ErrorActionPreference = "Stop"

$root      = "C:\poly\detect-temperature"
$python    = Join-Path $root ".venv\Scripts\python.exe"
$openShot  = Join-Path $root "scripts\daily_open_trades.py"
$settle    = Join-Path $root "scripts\daily_settle.py"

if (-not (Test-Path $python))   { throw "python venv missing: $python" }
if (-not (Test-Path $openShot)) { throw "daily_open_trades.py missing" }
if (-not (Test-Path $settle))   { throw "daily_settle.py missing" }

$currentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $currentUserSid `
    -LogonType Interactive -RunLevel Limited

function Register-DailyTask {
    param(
        [string]$Name,
        [ScriptBlock]$TriggerFactory,
        [string]$Action,
        [string]$ActionArgs,
        [int]$TimeoutMinutes
    )
    $taskAction = New-ScheduledTaskAction `
        -Execute $Action `
        -Argument $ActionArgs `
        -WorkingDirectory $root

    $triggers = & $TriggerFactory

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $TimeoutMinutes)

    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }

    Register-ScheduledTask -TaskName $Name `
        -Action $taskAction `
        -Trigger $triggers `
        -Settings $settings `
        -Principal $principal `
        -Description ("Polymarket automation - " + $Name) | Out-Null

    Write-Host "registered: $Name"
}

# 1) daily_open_trades - 22:00 UTC once a day
Register-DailyTask `
    -Name "PolymarketDailyOpenTrades" `
    -TriggerFactory { @(New-ScheduledTaskTrigger -Daily -At "22:00") } `
    -Action $python `
    -ActionArgs ("`"{0}`"" -f $openShot) `
    -TimeoutMinutes 10

# 2) near_close_refresh - every 30 minutes between 01:00 and 04:30 UTC
#    (8 firings per night; handles the bulk of Asian/European market closes)
$nearCloseTriggers = 0..7 | ForEach-Object {
    $minutes = $_ * 30
    $hour    = 1 + [math]::Floor($minutes / 60)
    $minute  = $minutes % 60
    $at      = "{0:00}:{1:00}" -f $hour, $minute
    New-ScheduledTaskTrigger -Daily -At $at
}
$nearCloseAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ("-m detect_temperature.cli refresh-open-positions --bankroll-usdc 100") `
    -WorkingDirectory $root

$nearCloseSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

if (Get-ScheduledTask -TaskName "PolymarketNearCloseRefresh" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "PolymarketNearCloseRefresh" -Confirm:$false
}

Register-ScheduledTask -TaskName "PolymarketNearCloseRefresh" `
    -Action $nearCloseAction `
    -Trigger $nearCloseTriggers `
    -Settings $nearCloseSettings `
    -Principal $principal `
    -Description "Polymarket automation - PolymarketNearCloseRefresh" | Out-Null
Write-Host "registered: PolymarketNearCloseRefresh"

# 3) daily_settle - 06:00 UTC once a day
Register-DailyTask `
    -Name "PolymarketDailySettle" `
    -TriggerFactory { @(New-ScheduledTaskTrigger -Daily -At "06:00") } `
    -Action $python `
    -ActionArgs ("`"{0}`"" -f $settle) `
    -TimeoutMinutes 10

Write-Host ""
Write-Host "All tasks:"
Get-ScheduledTask -TaskName "Polymarket*" |
    Select-Object TaskName, State | Format-Table -AutoSize
