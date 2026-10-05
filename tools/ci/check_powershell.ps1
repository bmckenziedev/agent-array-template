Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$failed = $false
Get-ChildItem -LiteralPath . -Filter '*.ps1' -Recurse -File | ForEach-Object {
    if ($_.FullName -notmatch '[\\/](rendered|\.git|\.ci-venvs|node_modules)[\\/]') {
        $tokens = $null
        $parseErrors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $_.FullName, [ref]$tokens, [ref]$parseErrors)
        foreach ($parseError in $parseErrors) {
            Write-Output "FAIL $($_.FullName):$($parseError.Extent.StartLineNumber) $($parseError.Message)"
            $failed = $true
        }
    }
}
if ($failed) { exit 1 }
Write-Output 'PowerShell syntax: PASS'
