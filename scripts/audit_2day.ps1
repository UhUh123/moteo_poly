$ROOT = "C:\poly\detect-temperature"
$cutoff = (Get-Date).ToUniversalTime().AddDays(-2)
$cutoffStr = $cutoff.ToString("yyyy-MM-ddTHH:mm:ssZ")

"=== AUDIT WINDOW ==="
"now_utc       = " + (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
"cutoff_utc    = $cutoffStr"
""

"=== HEALTH.JSON age of each task vs now ==="
$h = Get-Content (Join-Path $ROOT "status\health.json") -Raw | ConvertFrom-Json
$now = (Get-Date).ToUniversalTime()
$h.tasks.PSObject.Properties | ForEach-Object {
    $t = $_.Value
    if ($t.last_run) {
        $age_min = [int]($now - [datetime]::Parse($t.last_run)).TotalMinutes
    } else { $age_min = "?" }
    "{0,-32}  code={1}  age_min={2,5}  outcome={3}" -f $_.Name, $t.code, $age_min, $t.outcome
}
""

"=== LAST 2 DAYS: collector cadence ==="
$logTail = Get-Content (Join-Path $ROOT "logs\collector.log") -Tail 5000
$reg2d = $logTail | Select-String "^2026-05-1[78].*mode=regular" | Measure-Object
$hot2d = $logTail | Select-String "^2026-05-1[78].*mode=hot" | Measure-Object
$mtr2d = $logTail | Select-String "^2026-05-1[78].*mode=metar" | Measure-Object
$reg_err = $logTail | Select-String "^2026-05-1[78].*scan failed|orderbook fetch failed" | Measure-Object
$hot_err = $logTail | Select-String "^2026-05-1[78].*hot orderbook fetch failed" | Measure-Object
$mtr_err = $logTail | Select-String "^2026-05-1[78].*metar fetch failed" | Measure-Object
"regular runs (last 2d): $($reg2d.Count)   errors: $($reg_err.Count)"
"hot     runs (last 2d): $($hot2d.Count)   errors: $($hot_err.Count)"
"metar   runs (last 2d): $($mtr2d.Count)   errors: $($mtr_err.Count)"
""

"=== SNAPSHOT COUNTS for last 2 days ==="
foreach ($day in @("2026-05-17", "2026-05-18")) {
    $dpath = Join-Path $ROOT "data\history\$day"
    if (-not (Test-Path $dpath)) { "$day  (no dir)"; continue }
    $snaps = Get-ChildItem $dpath -Directory
    $reg = ($snaps | Where-Object { $_.Name -like "*-regular" }).Count
    $hot = ($snaps | Where-Object { $_.Name -like "*-hot" }).Count
    $first_reg = ($snaps | Where-Object { $_.Name -like "*-regular" } | Sort-Object Name | Select-Object -First 1).Name
    $last_reg  = ($snaps | Where-Object { $_.Name -like "*-regular" } | Sort-Object Name | Select-Object -Last 1).Name
    "{0}  regular={1,3}  hot={2,3}  first_reg={3}  last_reg={4}" -f $day, $reg, $hot, $first_reg, $last_reg
}
""

"=== METAR archive coverage (last 2 days) ==="
foreach ($day in @("2026-05-17", "2026-05-18")) {
    $f = Join-Path $ROOT "data\metar_history\$day.csv"
    if (Test-Path $f) {
        $rows = (Import-Csv $f).Count
        $size = (Get-Item $f).Length
        "{0}  rows={1,5}  size={2,7} bytes" -f $day, $rows, $size
    } else { "$day  (no file)" }
}
""

"=== DAILY_SETTLE last 2 days ==="
Get-Content (Join-Path $ROOT "logs\daily_settle.log") -Tail 60 |
    Select-String "^2026-05-1[78]" | ForEach-Object { $_.Line }
""

"=== DAILY_OPEN_TRADES last 2 days ==="
Get-Content (Join-Path $ROOT "logs\daily_open_trades.log") -Tail 60 -ErrorAction SilentlyContinue |
    Select-String "^2026-05-1[78]" | ForEach-Object { $_.Line }
""

"=== NEAR_CLOSE_REFRESH last 2 days ==="
Get-Content (Join-Path $ROOT "logs\near_close_refresh.log") -Tail 60 -ErrorAction SilentlyContinue |
    Select-String "^2026-05-1[78]" | ForEach-Object { $_.Line }
""

"=== PORTFOLIO STATE (now) ==="
$p = Import-Csv (Join-Path $ROOT "artifacts\paper_portfolio.csv")
"total: $($p.Count)"
$p | Group-Object status | Format-Table Name, Count -AutoSize | Out-String -Stream
$p | Group-Object settle_authority | Format-Table Name, Count -AutoSize | Out-String -Stream
""
"recently-settled (last 2d):"
$p | Where-Object { $_.settled_at -and [datetime]::Parse($_.settled_at) -gt $cutoff } |
    Group-Object status | Format-Table Name, Count -AutoSize | Out-String -Stream
""
"=== PnL summary ==="
$summary = (Get-Content (Join-Path $ROOT "artifacts\paper_portfolio.json") -Raw | ConvertFrom-Json).summary
"realized_pnl_usdc      = $($summary.realized_pnl_usdc)"
"win_rate_pct           = $($summary.win_rate_pct)"
"open_positions         = $($summary.open_positions)"
"settled_positions      = $($summary.settled_positions)"
"drawdown_triggered     = $($summary.drawdown_triggered)"
if ($summary.total_settle_correction_usdc) {
    "total_settle_correction_usdc = $($summary.total_settle_correction_usdc)"
}
""

"=== ACTUALS.CSV state ==="
$a = Import-Csv (Join-Path $ROOT "data\actuals.csv")
"total: $($a.Count)"
$a | Group-Object status | Format-Table Name, Count -AutoSize | Out-String -Stream
""
"errors-today (status=error AND target_date >= cutoff_str):"
$a | Where-Object { $_.status -eq "error" -and $_.target_date -ge $cutoff.ToString("yyyy-MM-dd") } |
    Group-Object provider | Format-Table Name, Count -AutoSize | Out-String -Stream
$a | Where-Object { $_.status -eq "error" -and $_.target_date -ge $cutoff.ToString("yyyy-MM-dd") } |
    Select-Object -First 5 slug, provider, target_date, notes | Format-Table -AutoSize -Wrap | Out-String -Stream
""
"metar_history_archive successes (status=ok via fallback):"
$a | Where-Object { $_.provider -eq "metar_history_archive" -and $_.status -eq "ok" } | Measure-Object | Select-Object Count | Format-Table -AutoSize | Out-String -Stream
""

"=== SCHEDULED TASK STATE ==="
Get-ScheduledTask -TaskName Polymarket* | ForEach-Object {
    $i = Get-ScheduledTaskInfo -TaskName $_.TaskName
    "{0,-32}  state={1,-8}  last={2,-22}  result={3}" -f $_.TaskName, $_.State, $i.LastRunTime, $i.LastTaskResult
}
""

"=== DISK USAGE ==="
$root_size_gb = ((Get-ChildItem $ROOT -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum) / 1GB
"$ROOT total: {0:N2} GB" -f $root_size_gb
$drive = Get-PSDrive C
"C: free: {0:N1} GB / {1:N1} GB" -f ($drive.Free / 1GB), (($drive.Used + $drive.Free) / 1GB)
