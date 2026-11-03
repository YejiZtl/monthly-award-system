[CmdletBinding()]
param(
    [string]$BindAddress = "0.0.0.0",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot "backend\.venv\Scripts\python.exe"
$runtimeRoot = Join-Path $projectRoot "work\lan-runtime"
$backendPidFile = Join-Path $runtimeRoot "backend.pid"
$workerPidFile = Join-Path $runtimeRoot "worker.pid"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment not found: $python"
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port $Port is already used by process $($listener[0].OwningProcess)."
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$env:PYTHONPATH = Join-Path $projectRoot "backend"
$env:APP_ENV = "production"
$env:INLINE_WORKER = "false"

$backend = Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", $BindAddress, "--port", $Port.ToString()) `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runtimeRoot "backend.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeRoot "backend.stderr.log") `
    -PassThru

$worker = Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "app.worker.main") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runtimeRoot "worker.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeRoot "worker.stderr.log") `
    -PassThru

Set-Content -LiteralPath $backendPidFile -Value $backend.Id -Encoding ascii
Set-Content -LiteralPath $workerPidFile -Value $worker.Id -Encoding ascii

$healthUrl = "http://127.0.0.1:$Port/health"
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($health.status -eq "ok") {
            $ready = $true
            break
        }
    } catch {
        # The server may still be importing dependencies.
    }
}

if (-not $ready) {
    & "$env:SystemRoot\System32\taskkill.exe" /PID $backend.Id /T /F 2>$null | Out-Null
    & "$env:SystemRoot\System32\taskkill.exe" /PID $worker.Id /T /F 2>$null | Out-Null
    throw "Service did not pass its health check within 15 seconds. See logs in $runtimeRoot."
}

Write-Output "backend_pid=$($backend.Id)"
Write-Output "worker_pid=$($worker.Id)"
Write-Output "health=$healthUrl"
Write-Output "preview=http://127.0.0.1:$Port/preview/"
