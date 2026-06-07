$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw "Project Python not found: $venvPython"
}

Push-Location $root
try {
    & $venvPython scripts\smoke_memory.py
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
