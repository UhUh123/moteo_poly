# Register the long-running dashboard server on Windows.
#
# Runs windows_dashboard_server.py forever, auto-restarts on failure,
# starts on boot and on user logon. Also installs a Windows Firewall
# rule that allows inbound TCP 8765 only from the Tailscale CGNAT
# range (100.64.0.0/10) so the port is effectively private to the
# tailnet.
#
# After running this, the mac can hit
#   http://<windows-tailscale-ip>:8765/
# directly, no ssh port-forward, no local server on mac.

$ErrorActionPreference = "Stop"

$root     = "C:\poly\detect-temperature"
$python   = Join-Path $root ".venv\Scripts\python.exe"
$launcher = Join-Path $root "scripts\windows_dashboard_server.py"
$taskName = "PolymarketDashboardServer"
$ruleName = "PolymarketDashboard-8765-Tailscale"

if (-not (Test-Path $python))   { throw "python venv missing: $python" }
if (-not (Test-Path $launcher)) { throw "dashboard launcher missing: $launcher" }

# ---- Firewall rule ----------------------------------------------------------

if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -Name $ruleName
}

New-NetFirewallRule `
    -Name $ruleName `
    -DisplayName "Polymarket dashboard (port 8765, Tailscale only)" `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 8765 `
    -RemoteAddress "100.64.0.0/10" `
    -Profile Any `
    -Enabled True | Out-Null
Write-Host "firewall rule created: $ruleName (TCP 8765 from 100.64.0.0/10)"

# ---- Scheduled task ---------------------------------------------------------

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument ("`"{0}`"" -f $launcher) `
    -WorkingDirectory $root

# Two triggers so the server comes back on both boot and user logon
$boot  = New-ScheduledTaskTrigger -AtStartup
$logon = New-ScheduledTaskTrigger -AtLogOn

# Long-running: no execution time limit, auto-restart on failure
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 999 `
    -ExecutionTimeLimit ([TimeSpan]::FromMinutes(0))

$currentUserSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$principal = New-ScheduledTaskPrincipal -UserId $currentUserSid `
    -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask   -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName `
    -Action $action `
    -Trigger @($boot, $logon) `
    -Settings $settings `
    -Principal $principal `
    -Description "Polymarket paper dashboard HTTP server (long-running)." | Out-Null

Write-Host "registered: $taskName"

# Start it right now so the user doesn't have to log out/in
Start-ScheduledTask -TaskName $taskName
Write-Host "started: $taskName"

Start-Sleep -Seconds 3

$info = Get-ScheduledTaskInfo -TaskName $taskName
Get-ScheduledTask -TaskName $taskName |
    Select-Object TaskName, State | Format-Table -AutoSize
"last_run={0}  next_run={1}  last_result={2}" -f $info.LastRunTime, $info.NextRunTime, $info.LastTaskResult | Write-Host
