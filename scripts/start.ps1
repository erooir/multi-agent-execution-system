param(
    [ValidateRange(1024,65535)][int]$Port = 8000,
    [ValidateRange(1,32)][int]$WorkerConcurrency = $(if ($env:HANGZHI_WORKER_CONCURRENCY) { [int]$env:HANGZHI_WORKER_CONCURRENCY } else { 2 }),
    [switch]$SkipBuild, [string]$DmUrlFile, [switch]$LegacySQLite,
    [switch]$Offline = ($env:HANGZHI_OFFLINE -eq 'true'),
    [switch]$AutoImprove = ($env:HANGZHI_AUTO_IMPROVE -eq 'true'),
    [switch]$QuestionPublicEgress = ($env:HANGZHI_QUESTION_PUBLIC_EGRESS -eq 'true')
)
$ErrorActionPreference = 'Stop'
if ($WorkerConcurrency -lt 1 -or $WorkerConcurrency -gt 32) { throw 'Worker concurrency must be from 1 to 32.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'platform_processes.ps1')
Set-Location -LiteralPath $projectRoot
$localDirectory = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
$pythonExecutable = Join-Path $projectRoot '.venv/Scripts/python.exe'
$pythonPointer = Join-Path $localDirectory 'platform-python.txt'
if (Test-Path -LiteralPath $pythonPointer) {
    $pythonExecutable = (Resolve-Path -LiteralPath ((Get-Content -LiteralPath $pythonPointer -Raw).Trim())).Path
    if ((Split-Path $pythonExecutable -Leaf) -ne 'python.exe' -or -not (Test-Path -LiteralPath (Join-Path (Split-Path (Split-Path $pythonExecutable -Parent) -Parent) 'pyvenv.cfg'))) { throw 'Configured runtime is not a Python virtual environment.' }
}
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw 'Python environment is missing. Provision locked dependencies offline before starting the platform.'
}
$dmPointer = Join-Path $localDirectory 'dm-url-file.txt'
if ($LegacySQLite) {
    if ($DmUrlFile) { throw 'Choose either DM8 or explicit legacy SQLite maintenance.' }
    $env:HANGZHI_REQUIRE_DM = 'false'
    $env:HANGZHI_DATABASE_URL = "sqlite:///" + ((Join-Path $localDirectory 'platform-v2.sqlite3').Replace('\','/'))
} else {
    if (-not $DmUrlFile -and (Test-Path -LiteralPath $dmPointer)) { $DmUrlFile = (Get-Content -LiteralPath $dmPointer -Raw).Trim() }
    if ($DmUrlFile) { $env:HANGZHI_DATABASE_URL = (Get-Content -LiteralPath $DmUrlFile -Raw).Trim() }
    if (-not $env:HANGZHI_DATABASE_URL -or $env:HANGZHI_DATABASE_URL -notmatch '^dm(\+dmPython)?://') { throw 'DM8 connection missing. Supply -DmUrlFile with a private DM URL (see docs/DAMENG.md).' }
    $env:HANGZHI_REQUIRE_DM = 'true'
    & $pythonExecutable -c 'import dmPython, dmSQLAlchemy'
    if ($LASTEXITCODE -ne 0) { throw 'Provision the locked dameng driver extra before starting.' }
}
$env:HANGZHI_OFFLINE = if ($Offline) { 'true' } else { 'false' }
$autoPolicy = Join-Path $localDirectory 'auto-improve.txt'
if (-not $PSBoundParameters.ContainsKey('AutoImprove') -and -not $env:HANGZHI_AUTO_IMPROVE -and (Test-Path -LiteralPath $autoPolicy)) {
    $choice = (Get-Content -LiteralPath $autoPolicy -Raw).Trim()
    if ($choice -notin @('true','false')) { throw 'Invalid local automatic improvement policy; use true or false.' }
    $AutoImprove = $choice -eq 'true'
}
$env:HANGZHI_AUTO_IMPROVE = if ($AutoImprove) { 'true' } else { 'false' }
$questionPolicy = Join-Path $localDirectory 'question-public-egress.txt'
if (-not $PSBoundParameters.ContainsKey('QuestionPublicEgress') -and -not $env:HANGZHI_QUESTION_PUBLIC_EGRESS -and (Test-Path -LiteralPath $questionPolicy)) {
    $choice = (Get-Content -LiteralPath $questionPolicy -Raw).Trim()
    if ($choice -notin @('true','false')) { throw 'Invalid public question egress policy; use true or false.' }
    $QuestionPublicEgress = $choice -eq 'true'
}
$env:HANGZHI_QUESTION_PUBLIC_EGRESS = if ($QuestionPublicEgress -and -not $Offline) { 'true' } else { 'false' }
$profile = $env:HANGZHI_DATABASE_URL + '|' + $env:HANGZHI_OFFLINE + '|' + $env:HANGZHI_AUTO_IMPROVE + '|' + $env:HANGZHI_QUESTION_PUBLIC_EGRESS
$sha = [System.Security.Cryptography.SHA256]::Create()
try { $profileId = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($profile))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
if (-not $SkipBuild) {
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) { throw 'Frontend build dependencies are not prepared. Use the offline build or provision them before startup.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend/dist/index.html'))) { throw 'Build frontend before using -SkipBuild' }
$env:AGNO_TELEMETRY = 'false'
. (Join-Path $PSScriptRoot 'enable_case_media.ps1')
$serverState = Join-Path $localDirectory 'server.json'
$listener = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
$running = $false
if ($listener.Count -gt 0) {
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v2/health" -TimeoutSec 2 }
    catch { throw "Port $Port is occupied by an unrecognized or unhealthy service; no new processes started." }
    if ($health.version -ne '2.0.0' -or $health.service -ne 'hangzhi-agent-platform') { throw "Port $Port is occupied by another service." }
    if (-not (Test-Path -LiteralPath $serverState)) { throw 'Existing API is not recorded by this workspace; stop it explicitly before starting.' }
    $record = Get-Content -LiteralPath $serverState -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($record.profile_id -ne $profileId) { throw 'Existing API uses a different database, model or improvement policy. Stop it before switching.' }
    $process = Get-PlatformProcesses -ProcessId $record.pid
    if ($record.root -ne $projectRoot -or -not $process -or $process.ExecutablePath -ne $pythonExecutable -or $process.CommandLine -notlike '*uvicorn backend.app.main:app*') { throw 'Existing API ownership could not be verified.' }
    $running = $true
}
if (-not $running) {
    $apiProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',$Port) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $localDirectory 'v2-api.stdout.log') -RedirectStandardError (Join-Path $localDirectory 'v2-api.stderr.log') -PassThru
    @{pid=$apiProcess.Id;port=$Port;root=$projectRoot;kind='api';executable=$pythonExecutable;profile_id=$profileId} | ConvertTo-Json | Set-Content -LiteralPath $serverState -Encoding utf8
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
    $record = Get-Content -LiteralPath $workerState -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($record.root -ne $projectRoot) { throw 'Recorded worker workspace mismatch' }
    $process = Get-PlatformProcesses -ProcessId $record.pid
    if ($process) {
        if ($record.profile_id -ne $profileId) { throw 'Existing worker uses a different database, model or improvement policy. Stop it before switching.' }
        $recordedConcurrency = if ($null -eq $record.concurrency) { 2 } else { [int]$record.concurrency }
        if ($recordedConcurrency -ne $WorkerConcurrency) { throw 'Existing worker uses a different concurrency. Stop it before switching.' }
        if ($process.ExecutablePath -ne $pythonExecutable -or $process.CommandLine -notlike '*backend.app.platform.worker*') { throw 'Recorded worker PID belongs to another process; inspect the state file.' }
        $workerRunning = $true
    }
}
if (-not $workerRunning) {
    $workerProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m','backend.app.platform.worker','--concurrency',$WorkerConcurrency) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $localDirectory 'v2-worker.stdout.log') -RedirectStandardError (Join-Path $localDirectory 'v2-worker.stderr.log') -PassThru
    @{pid=$workerProcess.Id;root=$projectRoot;kind='worker';executable=$pythonExecutable;profile_id=$profileId;concurrency=$WorkerConcurrency} | ConvertTo-Json | Set-Content -LiteralPath $workerState -Encoding utf8
    Start-Sleep -Seconds 2
    $workerProcess.Refresh()
    if ($workerProcess.HasExited) { throw 'Worker exited; inspect .local/v2-worker.stderr.log.' }
}
Write-Output "Hangzhi v2: http://127.0.0.1:$Port"
if ($Offline) { Write-Output 'Model access: fully offline.' } else { Write-Output 'Model access: online gateway available for authorized, budgeted tasks.' }
Write-Output "Cross-task automatic improvement: $env:HANGZHI_AUTO_IMPROVE"
Write-Output "Task concurrency per worker: $WorkerConcurrency"
Write-Output 'API is ready; worker process is running. Check the operations page for its heartbeat.'
Write-Output 'Existing identities and lifetime budget are preserved. Fresh installations: read the private bootstrap-admin.json.'
