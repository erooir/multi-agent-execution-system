param([ValidateRange(1024,65535)][int]$Port = 8000, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$localDirectory = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
$pythonExecutable = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    & uv sync --frozen --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed' }
}
if (-not $SkipBuild) {
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) { & npm.cmd ci; if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' } }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend/dist/index.html'))) { throw 'Build frontend before using -SkipBuild' }
$env:AGNO_TELEMETRY = 'false'
$serverState = Join-Path $localDirectory 'server.json'
$listener = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
$running = $false
if ($listener.Count -gt 0) {
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v2/health" -TimeoutSec 2 }
    catch { throw "Port $Port is occupied by an unrecognized or unhealthy service; no new processes started." }
    if ($health.version -ne '2.0.0' -or $health.service -ne 'hangzhi-agent-platform') { throw "Port $Port is occupied by another service." }
    if (-not (Test-Path -LiteralPath $serverState)) { throw 'Existing API is not recorded by this workspace; stop it explicitly before starting.' }
    $record = Get-Content -LiteralPath $serverState -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)" -ErrorAction SilentlyContinue
    if ($record.root -ne $projectRoot -or -not $process -or $process.ExecutablePath -ne $pythonExecutable -or $process.CommandLine -notlike '*uvicorn backend.app.main:app*') { throw 'Existing API ownership could not be verified.' }
    $running = $true
}
if (-not $running) {
    $apiProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',$Port) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $localDirectory 'v2-api.stdout.log') -RedirectStandardError (Join-Path $localDirectory 'v2-api.stderr.log') -PassThru
    @{pid=$apiProcess.Id;port=$Port;root=$projectRoot;kind='api'} | ConvertTo-Json | Set-Content -LiteralPath $serverState -Encoding utf8
}
$ready = $false
for ($attempt=0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v2/health" -TimeoutSec 2
        if ($health.version -eq '2.0.0' -and $health.service -eq 'hangzhi-agent-platform') { $ready = $true; break }
    } catch {}
    if ($apiProcess) { $apiProcess.Refresh(); if ($apiProcess.HasExited) { break } }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'API did not become ready; worker was not started. Inspect .local/v2-api.stderr.log.' }
$workerState = Join-Path $localDirectory 'worker.json'
$workerRunning = $false
if (Test-Path -LiteralPath $workerState) {
    $record = Get-Content -LiteralPath $workerState -Raw | ConvertFrom-Json
    if ($record.root -ne $projectRoot) { throw 'Recorded worker workspace mismatch' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)" -ErrorAction SilentlyContinue
    if ($process) {
        if ($process.ExecutablePath -ne $pythonExecutable -or $process.CommandLine -notlike '*backend.app.platform.worker*') { throw 'Recorded worker PID belongs to another process; inspect the state file.' }
        $workerRunning = $true
    }
}
if (-not $workerRunning) {
    $workerProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m','backend.app.platform.worker') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $localDirectory 'v2-worker.stdout.log') -RedirectStandardError (Join-Path $localDirectory 'v2-worker.stderr.log') -PassThru
    @{pid=$workerProcess.Id;root=$projectRoot;kind='worker'} | ConvertTo-Json | Set-Content -LiteralPath $workerState -Encoding utf8
    Start-Sleep -Seconds 2
    $workerProcess.Refresh()
    if ($workerProcess.HasExited) { throw 'Worker exited; inspect .local/v2-worker.stderr.log.' }
}
Write-Output "Hangzhi v2: http://127.0.0.1:$Port"
Write-Output 'API is ready; worker process is running. Check the operations page for its heartbeat.'
Write-Output 'Existing identities and lifetime budget are preserved. Fresh installations: read the private bootstrap-admin.json.'
