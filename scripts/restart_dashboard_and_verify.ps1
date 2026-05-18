$log = "C:\poly\detect-temperature\logs\dashboard_server.log"
$before = if (Test-Path $log) { (Get-Item $log).Length } else { 0 }
"BEFORE size: $before bytes"

Stop-ScheduledTask -TaskName PolymarketDashboardServer -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName PolymarketDashboardServer).State
"after stop: state=$state"

Start-ScheduledTask -TaskName PolymarketDashboardServer
Start-Sleep -Seconds 6
$state2 = (Get-ScheduledTask -TaskName PolymarketDashboardServer).State
$info = Get-ScheduledTaskInfo -TaskName PolymarketDashboardServer
"after start: state=$state2 last_run=$($info.LastRunTime) result=$($info.LastTaskResult)"

# Hit the dashboard a few times to generate request log lines
foreach ($path in @("/api/status", "/", "/dashboard")) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:8765$path" -UseBasicParsing -TimeoutSec 5 | Out-Null
        "curl $path : ok"
    } catch {
        "curl $path : FAIL ($_)"
    }
}

Start-Sleep -Seconds 2
$after = (Get-Item $log).Length
"AFTER size: $after bytes (delta: $($after - $before))"
""
"=== last 20 lines of dashboard_server.log ==="
Get-Content $log -Tail 20
