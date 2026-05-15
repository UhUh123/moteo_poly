# Register the METAR archive collector as a Windows scheduled task.
#
# Polls aviationweather.gov every 10 minutes for the latest METAR
# observation of each station in data/training_stations.json. Reports
# are appended to data/metar_history/<UTC-day>.csv with deduplication
# on (station_id, observed_at).
#
# Cadence: METAR cycles run every 30 to 60 minutes per station. SPECI
# (special) reports fire when conditions change quickly. Polling at
# 10 min catches every regular report once and SPECIs within 10 min
# of issue. Faster is wasted bandwidth on a public NWS endpoint.
#
# Re-run this script any time to refresh the schedule.

$ErrorActionPreference = "Stop"

$root      = "C:\poly\detect-temperature"
$python    = Join-Path $root ".venv\Scripts\python.exe"
$collector = Join-Path $root "scripts\windows_collector.py"
$taskName  = "PolymarketCollectorMetar"

if (-not (Test-Path $python))    { throw "python venv missing: $python" }
if (-not (Test-Path $collector)) { throw "windows_collector.py missing" }

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ("`"{0}`" --mode metar" -f $collector) `
    -WorkingDirectory $root

# Every 10 minutes, indefinitely (pick a 7-min anchor so we don't all
# fire on :00/:30 at once with the other tasks).
$trigger = New-ScheduledTaskTrigger -Once -At "00:07" `
    -RepetitionInterval (New-TimeSpan -Minutes 10)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$settings.WakeToRun = $true

$currentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $currentUserSid `
    -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description ("Polymarket automation - " + $taskName) | Out-Null

Write-Host ("registered: {0}" -f $taskName)
Write-Host ""
Write-Host "All Polymarket tasks:"
Get-ScheduledTask -TaskName "Polymarket*" |
    Select-Object TaskName, State | Format-Table -AutoSize
