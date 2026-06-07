$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root ".assistant_data\runtime\web-processes.json"
$desktopPidFile = Join-Path $root ".assistant_data\runtime\desktop-wrapper.pid"
$rootPattern = [Regex]::Escape($root)

function Stop-PortProcess($port) {
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($connection in $connections) {
        try {
            Stop-Process -Id $connection.OwningProcess -Force -ErrorAction Stop
            Write-Host "Stopped process on port $port (PID $($connection.OwningProcess))"
        } catch {
        }
    }

    for ($i = 0; $i -lt 10; $i++) {
        $remaining = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        if (-not $remaining) {
            return
        }
        Start-Sleep -Milliseconds 300
    }

    $remaining = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($remaining) {
        Write-Warning "Port $port is still in use."
    }
}

function Stop-WrapperTree($targetPid) {
    $children = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ParentProcessId -eq $targetPid }
    foreach ($child in $children) {
        Stop-WrapperTree $child.ProcessId
    }

    try {
        Stop-Process -Id $targetPid -Force -ErrorAction Stop
        Write-Host "Stopped wrapper PowerShell PID $targetPid"
    } catch {
    }
}

function Stop-ProjectDesktopProcesses() {
    $processes = Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and (
                ($_.CommandLine -match $rootPattern -and $_.CommandLine -match "start-assistant\.ps1|cargo.+run|npm(\.cmd)? run dev|vite|tauri") -or
                ($_.ExecutablePath -like (Join-Path $root "src-tauri\target\debug\local-voice-memory-assistant.exe")) -or
                ($_.CommandLine -match "webview-exe-name=local-voice-memory-assistant\.exe")
            )
        }

    foreach ($process in $processes) {
        Stop-WrapperTree $process.ProcessId
    }
}

if (Test-Path $desktopPidFile) {
    try {
        $desktopPid = [int](Get-Content $desktopPidFile -Raw)
        Stop-WrapperTree $desktopPid
    } catch {
    }
    Remove-Item $desktopPidFile -Force -ErrorAction SilentlyContinue
}

if (Test-Path $pidFile) {
    try {
        $payload = Get-Content $pidFile -Raw | ConvertFrom-Json
        foreach ($wrapperPid in @($payload.backend_wrapper_pid, $payload.frontend_wrapper_pid)) {
            if (-not $wrapperPid) {
                continue
            }
            Stop-WrapperTree $wrapperPid
        }
    } catch {
    }

    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

$wrapperProcesses = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match $rootPattern -and (
            $_.CommandLine -match "backend\.app" -or
            $_.CommandLine -match "npm(\.cmd)? run dev -- --host 127\.0\.0\.1 --port 5173"
        )
    }

foreach ($process in $wrapperProcesses) {
    Stop-WrapperTree $process.ProcessId
}

Stop-ProjectDesktopProcesses
Stop-PortProcess 8765
Stop-PortProcess 5173
