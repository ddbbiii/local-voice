param()

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$buildDir = Join-Path $root "build\pyinstaller-backend"
$specDir = Join-Path $root "build\pyinstaller-spec"
$resourceDir = Join-Path $root "src-tauri\resources\backend-runtime"
$entryScript = Join-Path $root "backend_desktop_entry.py"

Get-CimInstance Win32_Process -Filter "Name = 'assistant-backend.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -like (Join-Path $resourceDir "assistant-backend*") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 500

if (-not (Test-Path $python)) {
    throw "Python runtime not found: $python"
}

if (-not (Test-Path $entryScript)) {
    throw "Backend entry script not found: $entryScript"
}

New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
New-Item -ItemType Directory -Force -Path $specDir | Out-Null

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $resourceDir
New-Item -ItemType Directory -Force -Path $resourceDir | Out-Null
Set-Content -Path (Join-Path $resourceDir "placeholder.txt") -Value "backend runtime placeholder" -Encoding UTF8

& $python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)" 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install pyinstaller
}

$pyInstallerArgs = @(
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--noconsole",
    "--name",
    "assistant-backend",
    "--distpath",
    $resourceDir,
    "--workpath",
    $buildDir,
    "--specpath",
    $specDir,
    "--collect-all",
    "chromadb",
    "--collect-all",
    "faster_whisper",
    "--collect-all",
    "ctranslate2",
    "--collect-all",
    "tokenizers",
    "--collect-all",
    "uvicorn",
    "--collect-all",
    "httpx",
    "--collect-all",
    "numpy",
    "--collect-all",
    "nvidia.cublas",
    "--collect-all",
    "nvidia.cuda_runtime",
    "--collect-all",
    "nvidia.cuda_nvrtc",
    "--collect-all",
    "nvidia.cudnn",
    "--hidden-import",
    "uvicorn.logging",
    "--hidden-import",
    "uvicorn.loops.auto",
    "--hidden-import",
    "uvicorn.protocols.http.auto",
    "--hidden-import",
    "uvicorn.protocols.websockets.auto",
    "--hidden-import",
    "uvicorn.lifespan.on",
    $entryScript
)

Push-Location $root
try {
    & $python @pyInstallerArgs
} finally {
    Pop-Location
}

$backendExe = Join-Path $resourceDir "assistant-backend\assistant-backend.exe"
if (-not (Test-Path $backendExe)) {
    throw "PyInstaller completed but backend exe was not produced: $backendExe"
}

Write-Host "Built backend runtime:"
Write-Host $backendExe
