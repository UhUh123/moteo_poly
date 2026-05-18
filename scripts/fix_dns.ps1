# Try to set persistent DNS = 8.8.8.8, 1.1.1.1 on the active adapters.
# If we lack admin, this fails noisily — that's fine, we want to know.

$ErrorActionPreference = "Continue"

"=== current DNS servers ==="
Get-DnsClientServerAddress -AddressFamily IPv4 |
    Where-Object { $_.ServerAddresses } |
    Select-Object InterfaceAlias, ServerAddresses |
    Format-Table -AutoSize | Out-String -Stream

# Pick adapters that are 'Up' and not loopback / VPN.
$adapters = Get-NetAdapter |
    Where-Object { $_.Status -eq "Up" -and $_.InterfaceDescription -notlike "*Loopback*" -and $_.InterfaceAlias -notlike "*Tailscale*" -and $_.InterfaceAlias -notlike "*Radmin*" }

"=== will attempt to set DNS on these adapters ==="
$adapters | Select-Object Name, InterfaceAlias, Status | Format-Table -AutoSize | Out-String -Stream

foreach ($a in $adapters) {
    try {
        Set-DnsClientServerAddress -InterfaceIndex $a.InterfaceIndex -ServerAddresses ("8.8.8.8", "1.1.1.1") -ErrorAction Stop
        "set DNS on '$($a.InterfaceAlias)' OK"
    } catch {
        "set DNS on '$($a.InterfaceAlias)' FAILED: $_"
    }
}

ipconfig /flushdns | Out-Null
Start-Sleep -Seconds 1

"=== DNS servers AFTER change ==="
Get-DnsClientServerAddress -AddressFamily IPv4 |
    Where-Object { $_.ServerAddresses } |
    Select-Object InterfaceAlias, ServerAddresses |
    Format-Table -AutoSize | Out-String -Stream

"=== resolve aviationweather.gov now ==="
$r = Resolve-DnsName aviationweather.gov -Type A -ErrorAction SilentlyContinue
if ($r) { $r | Select-Object Name, IPAddress | Format-Table -AutoSize | Out-String -Stream }
else { "still FAIL" }
