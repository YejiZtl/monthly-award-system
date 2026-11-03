[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $projectRoot "work\lan-runtime"
$pidFiles = @(
    (Join-Path $runtimeRoot "backend.pid"),
    (Join-Path $runtimeRoot "worker.pid")
)

foreach ($pidFile in $pidFiles) {
    if (-not (Test-Path -LiteralPath $pidFile)) {
        continue
    }
    $processId = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
    & "$env:SystemRoot\System32\taskkill.exe" /PID $processId /T /F 2>$null | Out-Null
    Remove-Item -LiteralPath $pidFile -Force
}

Write-Output "LAN services stopped."
