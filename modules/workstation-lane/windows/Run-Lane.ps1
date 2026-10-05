param([switch]$Yes, [Parameter(Mandatory)][string]$Config, [Parameter(Mandatory)][string]$Schedule)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/LaneCommon.ps1"
$policy = Get-Content -LiteralPath $Schedule -Raw | ConvertFrom-Json
$windowsZone = ConvertTo-WindowsTimeZoneId $policy.timezone
[TimeZoneInfo]::FindSystemTimeZoneById($windowsZone) | Out-Null
$mode = '--check'
if ($Yes) { $mode = '--yes' }
& python "$PSScriptRoot/../lane.py" --config $Config --schedule $Schedule --parent-pid $PID $mode
if ($LASTEXITCODE -ne 0) { throw 'Lane supervisor failed' }
