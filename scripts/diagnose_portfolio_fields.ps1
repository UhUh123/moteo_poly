$ROOT = "C:\poly\detect-temperature"
$p = Import-Csv (Join-Path $ROOT "artifacts\paper_portfolio.csv")
"total rows: $($p.Count)"
""
"=== columns present ==="
($p | Get-Member -MemberType NoteProperty).Name -join ", "
""
"=== field-blank counts ==="
$cols = @("target_date","station_id","target_extreme","interval_unit","city",
          "event_slug","market_slug","side","status","interval_lower","interval_upper")
foreach ($c in $cols) {
    $blank = ($p | Where-Object { -not $_.PSObject.Properties[$c] -or -not $_.$c }).Count
    "{0,-22} blank={1,3} / total={2}" -f $c, $blank, $p.Count
}
""
"=== status x target_date present ==="
$p | Group-Object {
    $d = if ($_.PSObject.Properties['target_date']) { $_.target_date } else { "" }
    $has = if ($d) { "with_date" } else { "no_date" }
    "{0}|{1}" -f $_.status, $has
} | Sort-Object Count -Descending | Format-Table Count, Name -AutoSize
""
"=== sample stuck-old row ==="
$p | Where-Object { $_.event_slug -eq "highest-temperature-in-shanghai-on-may-12-2026" } |
    Select-Object event_slug, status, settle_authority, target_date, station_id, target_extreme, interval_unit, side, interval_lower, interval_upper | Format-List
""
"=== sample fresh row (May 17) ==="
$p | Where-Object { $_.event_slug -like "*may-17-2026" } | Select-Object -First 1 |
    Select-Object event_slug, status, settle_authority, target_date, station_id, target_extreme, interval_unit, side, interval_lower, interval_upper | Format-List
