param(
    [ValidateSet("desktop", "web")]
    [string]$Mode = "desktop"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$npmCmd = "npm.cmd"
$runtimeDir = Join-Path $root ".assistant_data\runtime"
$pidFile = Join-Path $runtimeDir "web-processes.json"
$desktopPidFile = Join-Path $runtimeDir "desktop-wrapper.pid"
$llmApiBase = [Environment]::GetEnvironmentVariable("ASSISTANT_LLM_API_BASE", "User")
$llmApiModel = [Environment]::GetEnvironmentVariable("ASSISTANT_LLM_API_MODEL", "User")
$llmApiKey = [Environment]::GetEnvironmentVariable("ASSISTANT_LLM_API_KEY", "User")

function Assert-Path($path, $label) {
    if (-not (Test-Path $path)) {
        throw "$label not found: $path"
    }
}

function Get-PortOwners($port) {
    @(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Stop-OwnedPortProcess($port) {
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        try {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)" -ErrorAction Stop
            if ($process.CommandLine -and $process.CommandLine -like "*$root*") {
                Stop-Process -Id $connection.OwningProcess -Force -ErrorAction Stop
            }
        } catch {
        }
    }
}

function Assert-PortAvailable($port) {
    $owners = Get-PortOwners $port
    if (-not $owners -or $owners.Count -eq 0) {
        return
    }

    $details = foreach ($owner in $owners) {
        try {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction Stop
            "$owner $($process.Name) $($process.CommandLine)"
        } catch {
            "$owner <unknown>"
        }
    }
    throw "Port $port is already in use by a non-project process:`n$($details -join "`n")"
}

function Stop-WrapperProcesses() {
    if (-not (Test-Path $pidFile)) {
        return
    }

    try {
        $payload = Get-Content $pidFile -Raw | ConvertFrom-Json
    } catch {
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        return
    }

    foreach ($wrapperPid in @($payload.backend_wrapper_pid, $payload.frontend_wrapper_pid)) {
        if (-not $wrapperPid) {
            continue
        }
        try {
            Stop-Process -Id $wrapperPid -Force -ErrorAction Stop
        } catch {
        }
    }

    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

function Stop-ProjectDesktopProcesses() {
    $rootPattern = [Regex]::Escape($root)
    $processes = Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and (
                ($_.CommandLine -match $rootPattern -and $_.CommandLine -match "cargo.+run|npm(\.cmd)? run dev|vite|tauri") -or
                ($_.ExecutablePath -like (Join-Path $root "src-tauri\target\debug\local-voice-memory-assistant.exe")) -or
                ($_.CommandLine -match "webview-exe-name=local-voice-memory-assistant\.exe")
            )
        }

    foreach ($process in $processes) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        } catch {
        }
    }
}

Assert-Path $venvPython "Project Python"
Assert-Path (Join-Path $root "node_modules") "node_modules"
Assert-Path (Join-Path $root "package.json") "package.json"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

if ($Mode -eq "desktop") {
    Stop-WrapperProcesses
    Stop-ProjectDesktopProcesses
    Stop-OwnedPortProcess 8765
    Stop-OwnedPortProcess 5173
    Assert-PortAvailable 8765
    Assert-PortAvailable 5173
    $PID | Set-Content -Path $desktopPidFile -Encoding ASCII
    Push-Location $root
    try {
        & $npmCmd run tauri:dev
    } finally {
        Pop-Location
    }
    exit $LASTEXITCODE
}

$backendArgs = @(
    "-NoExit",
    "-Command",
    "`$env:ASSISTANT_ASR_DEVICE='cuda'; if ('$llmApiBase') { `$env:ASSISTANT_LLM_API_BASE='$llmApiBase' }; if ('$llmApiModel') { `$env:ASSISTANT_LLM_API_MODEL='$llmApiModel' }; if ('$llmApiKey') { `$env:ASSISTANT_LLM_API_KEY='$llmApiKey' }; `$env:PATH = '$root\.venv\Lib\site-packages\nvidia\cublas\bin;$root\.venv\Lib\site-packages\nvidia\cuda_runtime\bin;$root\.venv\Lib\site-packages\nvidia\cudnn\bin;' + `$env:PATH; Set-Location '$root'; & '$venvPython' -m backend.app"
)

$frontendArgs = @(
    "-NoExit",
    "-Command",
    "Set-Location '$root'; & '$npmCmd' run dev -- --host 127.0.0.1 --port 5173"
)

Stop-WrapperProcesses
Stop-OwnedPortProcess 8765
Stop-OwnedPortProcess 5173
Assert-PortAvailable 8765
Assert-PortAvailable 5173

$backendProcess = Start-Process powershell -ArgumentList $backendArgs -WindowStyle Normal -PassThru
Start-Sleep -Seconds 2
$frontendProcess = Start-Process powershell -ArgumentList $frontendArgs -WindowStyle Normal -PassThru

@{
    backend_wrapper_pid = $backendProcess.Id
    frontend_wrapper_pid = $frontendProcess.Id
} | ConvertTo-Json | Set-Content -Path $pidFile -Encoding UTF8

Write-Host "Started web mode."
Write-Host "Backend: http://127.0.0.1:8765"
Write-Host "Frontend: http://127.0.0.1:5173"
