"=== ipconfig DNS servers ==="
Get-DnsClientServerAddress -AddressFamily IPv4 |
    Where-Object { $_.ServerAddresses } |
    Select-Object InterfaceAlias, ServerAddresses |
    Format-Table -AutoSize | Out-String -Stream
""
"=== resolve aviationweather.gov via configured DNS ==="
$default = Resolve-DnsName aviationweather.gov -Type A -ErrorAction SilentlyContinue
if ($default) { $default | Select-Object Name,IPAddress | Format-Table -AutoSize | Out-String -Stream }
else { "FAIL via system DNS" }
""
"=== resolve aviationweather.gov via 8.8.8.8 ==="
$google = Resolve-DnsName aviationweather.gov -Type A -Server 8.8.8.8 -ErrorAction SilentlyContinue
if ($google) { $google | Select-Object Name,IPAddress | Format-Table -AutoSize | Out-String -Stream }
else { "FAIL via 8.8.8.8" }
""
"=== resolve aviationweather.gov via 1.1.1.1 ==="
$cf = Resolve-DnsName aviationweather.gov -Type A -Server 1.1.1.1 -ErrorAction SilentlyContinue
if ($cf) { $cf | Select-Object Name,IPAddress | Format-Table -AutoSize | Out-String -Stream }
else { "FAIL via 1.1.1.1" }
""
"=== local hosts file overrides ==="
$hostsPath = "$env:windir\System32\drivers\etc\hosts"
if (Test-Path $hostsPath) {
    $matches = Get-Content $hostsPath | Select-String -Pattern "aviationweather|weather\.com|polymarket"
    if ($matches) { $matches | ForEach-Object { $_.Line } } else { "(no overrides)" }
}
""
"=== flush dns cache and retry ==="
ipconfig /flushdns | Out-Null
Start-Sleep -Seconds 2
$retry = Resolve-DnsName aviationweather.gov -Type A -ErrorAction SilentlyContinue
if ($retry) { "RECOVERED after flushdns: " + ($retry[0].IPAddress) }
else { "still FAIL after flushdns" }
""
"=== Tailscale interface (often hijacks DNS) ==="
Get-NetIPInterface -AddressFamily IPv4 |
    Where-Object { $_.InterfaceAlias -like "*Tailscale*" -or $_.InterfaceAlias -like "*VPN*" } |
    Select-Object InterfaceAlias, ConnectionState, NlMtu | Format-Table -AutoSize | Out-String -Stream
