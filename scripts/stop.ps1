$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.local\server.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'No recorded server.'; exit 0 }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.pid)" -ErrorAction SilentlyContinue
$expectedExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
if ($serverProcess -and $serverProcess.ExecutablePath -eq $expectedExecutable -and $serverProcess.CommandLine -like '*uvicorn backend.app.main:app*') {
    Stop-Process -Id $state.pid
    Write-Output 'Workbench stopped.'
} else { Write-Output 'Recorded process is absent or does not match; no process stopped.' }
