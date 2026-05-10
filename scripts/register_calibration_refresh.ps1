# Register the weekly calibration refresh as a Windows scheduled task.
#
# Runs every Monday at 04:17 UTC (avoids the common :00/:30 spike so we
# don't collide with Open-Meteo's rate limiter). StartWhenAvailable
# catches up if the PC was off at the scheduled time. Each refresh
# pulls the last 180 days of Open-Meteo pairs, merges into
# training_real.csv, retrains the GBM, and rebuilds
# station_calibration.csv + predictions_gbm.csv + market_signals.csv.

$ErrorActionPreference = "Stop"

$root = "C:\poly\detect-temperature"
$python = Join-Path $root ".venv\Scripts\python.exe"
$refresh = Join-Path $root "scripts\refresh_calibration.py"
$name = "PolymarketCalibrationRefresh"

if (-not (Test-Path $python)) { throw "python venv not found at $python" }
if (-not (Test-Path $refresh)) { throw "refresh script not found at $refresh" }

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "`"$refresh`" --window-days 180" `
    -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger `
    -Weekly -DaysOfWeek Monday -At "04:17"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$currentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $currentUserSid `
    -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}

Register-ScheduledTask -TaskName $name `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Weekly retrain of temperature GBM + per-station calibration." | Out-Null

Write-Host "registered: $name (Mondays 04:17, --window-days 180)"
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State | Format-Table -AutoSize
