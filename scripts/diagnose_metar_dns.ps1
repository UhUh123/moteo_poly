$ROOT = "C:\poly\detect-temperature"
$h = Get-Content (Join-Path $ROOT "status\health.json") -Raw | ConvertFrom-Json
$m = $h.tasks.collector_metar
$errShort = if ($m.error) { $m.error.Substring(0, [Math]::Min(120, $m.error.Length)) } else { "" }
"=== current metar task state ==="
"code={0} last={1} appended={2}" -f $m.code, $m.last_run, $m.rows_appended
"err={0}" -f $errShort
""
"=== last 8 metar log lines ==="
Get-Content (Join-Path $ROOT "logs\collector.log") -Tail 400 |
    Select-String "mode=metar|metar fetch failed|metar ok|metar skip" |
    Select-Object -Last 8 | ForEach-Object { $_.Line }
""
"=== DNS resolution now ==="
$dns = Resolve-DnsName aviationweather.gov -Type A -ErrorAction SilentlyContinue
if ($dns) { $dns | Select-Object Name, IPAddress | Format-Table -AutoSize | Out-String -Stream }
else { "DNS still failing" }
""
"=== TCP 443 reachable now? ==="
$tnc = Test-NetConnection aviationweather.gov -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue
"reachable={0}" -f $tnc
""
"=== other gov resolutions ==="
foreach ($h in @("clob.polymarket.com", "polymarket.com", "api.weather.com", "api.open-meteo.com")) {
    $r = Resolve-DnsName $h -Type A -ErrorAction SilentlyContinue
    if ($r) { "{0,-30} -> {1}" -f $h, ($r[0].IPAddress) }
    else    { "{0,-30} -> FAIL" -f $h }
}
""
"=== how many consecutive metar failures today? ==="
$logTail = Get-Content (Join-Path $ROOT "logs\collector.log") -Tail 600
$today = (Get-Date -Format "yyyy-MM-dd")
$todayMetar = $logTail | Select-String "^$today.*mode=metar"
$todayFails = $logTail | Select-String "^$today.*metar fetch failed"
"runs_today={0}  fails_today={1}" -f $todayMetar.Count, $todayFails.Count
