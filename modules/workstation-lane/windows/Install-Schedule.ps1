param([switch]$Yes, [Parameter(Mandatory)][string]$Config, [Parameter(Mandatory)][string]$Schedule)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/LaneCommon.ps1"
$policy = Get-Content -LiteralPath $Schedule -Raw | ConvertFrom-Json
$windowsZone = ConvertTo-WindowsTimeZoneId $policy.timezone
if (-not $Yes) { Write-Output "Preview: interactive volunteer lane in $windowsZone"; return }
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$script = Join-Path $PSScriptRoot 'Run-Lane.ps1'
$arguments = '-NoProfile -File "{0}" -Yes -Config "{1}" -Schedule "{2}"' -f $script, $Config, $Schedule
$action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'VolunteerFactoryLane' -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
