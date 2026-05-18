$ErrorActionPreference = "Continue"
$ROOT = "C:\poly\detect-temperature"

Write-Host "=== ALL TASK STATES ==="
$h = Get-Content (Join-Path $ROOT "status\health.json") -Raw | ConvertFrom-Json
$h.tasks.PSObject.Properties | ForEach-Object {
    $t = $_.Value
    $err = if ($t.error) { $t.error } else { "" }
    "{0,-32}  code={1}  last={2}  outcome={3}  err={4}" -f $_.Name, $t.code, $t.last_run, $t.outcome, $err
}

Write-Host ""
Write-Host "=== LAST 15 ALERTS ==="
if ($h.alerts) {
    $h.alerts | Select-Object -Last 15 | ForEach-Object {
        "{0,-25} {1,-22} {2}" -f $_.timestamp, $_.task, $_.message
    }
} else { Write-Host "(no alerts field)" }

Write-Host ""
Write-Host "=== HOT COLLECTOR ON 2026-05-15 ==="
$hot15 = Get-ChildItem (Join-Path $ROOT "data\history\2026-05-15") -Directory -ErrorAction SilentlyContinue |
         Where-Object { $_.Name -like "*-hot" }
"hot snapshots on 2026-05-15: count={0}" -f $hot15.Count
Get-Content (Join-Path $ROOT "logs\collector.log") | Select-String -Pattern "^2026-05-15.*(hot|skip_no_closing|hot orderbook)" |
    Select-Object -First 5

Write-Host ""
Write-Host "=== REGULAR GAPS ON 2026-05-11 ==="
$snaps = Get-ChildItem (Join-Path $ROOT "data\history\2026-05-11") -Directory |
         Where-Object { $_.Name -like "*-regular" } | Sort-Object Name
$prev = $null
foreach ($s in $snaps) {
    $hh = [int]$s.Name.Substring(0,2); $mm = [int]$s.Name.Substring(2,2); $ss = [int]$s.Name.Substring(4,2)
    $cur = $hh * 3600 + $mm * 60 + $ss
    if ($prev -ne $null) {
        $delta = $cur - $prev
        if ($delta -gt 360) { "  gap at {0}: delta={1}s" -f $s.Name, $delta }
    }
    $prev = $cur
}

Write-Host ""
Write-Host "=== STATION CALIBRATION FRESHNESS ==="
$f = Get-Item (Join-Path $ROOT "data\station_calibration.csv")
"file age: {0:N1} h, last_write={1}" -f ((Get-Date).Subtract($f.LastWriteTime).TotalHours), $f.LastWriteTime
$cal = $h.tasks.calibration_refresh
"calibration_refresh task: code={0}  last_run={1}" -f $cal.code, $cal.last_run

Write-Host ""
Write-Host "=== DASHBOARD HTML AGE ==="
$d = Get-Item (Join-Path $ROOT "artifacts\paper_dashboard.html") -ErrorAction SilentlyContinue
if ($d) { "paper_dashboard.html age: {0:N1} h, last_write={1}" -f ((Get-Date).Subtract($d.LastWriteTime).TotalHours), $d.LastWriteTime }

Write-Host ""
Write-Host "=== ACTUAL_STATUS BACKLOG IN PAPER PORTFOLIO ==="
$p = Import-Csv (Join-Path $ROOT "artifacts\paper_portfolio.csv")
$p | Group-Object actual_status | Sort-Object Count -Descending |
    Select-Object Name, Count | Format-Table -AutoSize

Write-Host ""
Write-Host "=== POSITIONS WITH BLANK / INVALID FIELDS ==="
"target_date_blank = {0}" -f ($p | Where-Object { -not $_.target_date }).Count
"station_id_blank  = {0}" -f ($p | Where-Object { -not $_.station_id }).Count
"interval_lower_blank = {0}" -f ($p | Where-Object { -not $_.interval_lower }).Count

Write-Host ""
Write-Host "=== AT_RISK POSITIONS ==="
$p | Where-Object { $_.status -eq "at_risk" } |
    Select-Object event_slug, opened_at, near_close_refreshed_at | Format-Table -AutoSize

Write-Host ""
Write-Host "=== DAILY_SETTLE.LOG TAIL (last 30 lines) ==="
Get-Content (Join-Path $ROOT "logs\daily_settle.log") -Tail 30
