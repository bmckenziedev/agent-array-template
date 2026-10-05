Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function ConvertTo-WindowsTimeZoneId([string]$Iana) {
    # Modern .NET exposes the CLDR mapping; older Windows PowerShell fails closed.
    $method = [TimeZoneInfo].GetMethods() | Where-Object Name -eq 'TryConvertIanaIdToWindowsId'
    if (-not $method) { throw 'PowerShell 7 with IANA conversion support is required' }
    $windowsId = ''
    if (-not [TimeZoneInfo]::TryConvertIanaIdToWindowsId($Iana, [ref]$windowsId)) {
        throw 'Unknown IANA time zone'
    }
    return $windowsId
}
