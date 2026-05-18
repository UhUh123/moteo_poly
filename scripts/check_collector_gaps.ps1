$root = "C:\poly\detect-temperature\data\history"
foreach ($day in @("2026-05-12","2026-05-13","2026-05-14","2026-05-15","2026-05-16","2026-05-17","2026-05-18")) {
    $dpath = Join-Path $root $day
    if (-not (Test-Path $dpath)) { continue }
    $snaps = Get-ChildItem $dpath -Directory | Where-Object { $_.Name -like "*-regular" } | Sort-Object Name
    $maxgap = 0
    $gaps = @()
    $prev = $null
    foreach ($s in $snaps) {
        $hh = [int]$s.Name.Substring(0,2)
        $mm = [int]$s.Name.Substring(2,2)
        $ss = [int]$s.Name.Substring(4,2)
        $cur = $hh*3600 + $mm*60 + $ss
        if ($prev -ne $null) {
            $delta = $cur - $prev
            if ($delta -gt $maxgap) { $maxgap = $delta }
            if ($delta -gt 360) { $gaps += @{name=$s.Name; delta=$delta} }
        }
        $prev = $cur
    }
    "{0}  count={1,3}  maxgap={2,5}s  gaps_over_6min={3}" -f $day, $snaps.Count, $maxgap, $gaps.Count
    foreach ($g in $gaps) { "    gap at {0}: delta={1}s" -f $g.name, $g.delta }
}
