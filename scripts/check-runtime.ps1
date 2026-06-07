param(
    [switch]$SkipHealth,
    [int]$HealthTimeoutSeconds = 20
)

$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$llmShard1 = "E:\program\models\llm\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
$llmShard2 = "E:\program\models\llm\qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf"
$asrModel = "E:\program\models\asr\faster-whisper-medium.en"

function Write-Check($label, $ok, $detail = "") {
    $status = if ($ok) { "OK" } else { "FAIL" }
    $line = "{0,-28} {1}" -f $label, $status
    if ($detail) {
        $line = "$line - $detail"
    }
    if ($ok) {
        Write-Host $line -ForegroundColor Green
    } else {
        Write-Host $line -ForegroundColor Red
    }
}

Push-Location $root
try {
    Write-Host "Runtime check: $root"
    Write-Host ""

    Write-Check "Project Python" (Test-Path $python) $python
    if (Test-Path $python) {
        & $python -c "import sys; print('Python ' + sys.version.split()[0])"
    }

    Write-Check "Node dependencies" (Test-Path (Join-Path $root "node_modules")) "node_modules"
    Write-Check "LLM shard 1" (Test-Path $llmShard1) $llmShard1
    Write-Check "LLM shard 2" (Test-Path $llmShard2) $llmShard2
    Write-Check "ASR medium.en" (Test-Path (Join-Path $asrModel "model.bin")) $asrModel

    if (Test-Path $python) {
        Write-Host ""
        Write-Host "Python packages:"
        & $python -c "import importlib.util as u; mods=['fastapi','uvicorn','faster_whisper','chromadb','llama_cpp','pyttsx3','httpx']; [print(f'{m}: ' + ('OK' if u.find_spec(m) else 'MISSING')) for m in mods]"
    }

    Write-Host ""
    foreach ($port in 8765, 5173) {
        $owners = @(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        if ($owners.Count -eq 0) {
            Write-Check "Port $port" $true "free"
            continue
        }

        $details = foreach ($owner in $owners) {
            try {
                $process = Get-CimInstance Win32_Process -Filter "ProcessId = $owner" -ErrorAction Stop
                "$owner $($process.Name)"
            } catch {
                "$owner unknown"
            }
        }
        Write-Check "Port $port" $false ($details -join ", ")
    }

    if (-not $SkipHealth) {
        Write-Host ""
        Write-Host "Health probe:"
        $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
        $lastError = $null
        do {
            try {
                $payload = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 2
                $payload.status | ConvertTo-Json -Depth 5
                $lastError = $null
                break
            } catch {
                $lastError = $_.Exception.Message
                Start-Sleep -Milliseconds 500
            }
        } while ((Get-Date) -lt $deadline)

        if ($lastError) {
            Write-Check "Backend health" $false $lastError
        }
    }
} finally {
    Pop-Location
}
