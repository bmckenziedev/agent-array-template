Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:AA_PYTHON) {
    & $env:AA_PYTHON "$PSScriptRoot/aa.py" @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 "$PSScriptRoot/aa.py" @args
} else {
    & python "$PSScriptRoot/aa.py" @args
}
exit $LASTEXITCODE
