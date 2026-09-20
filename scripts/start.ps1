param([int]$Port = 8000, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$localDirectory = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $localDirectory -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe'))) {
    & uv sync --frozen --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency setup failed' }
}
if (-not $SkipBuild) {
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency setup failed' }
        }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    if ($health) { Write-Output "Workbench is already running: http://127.0.0.1:$Port"; exit 0 }
} catch {}
$pythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
$stdoutPath = Join-Path $localDirectory 'server.stdout.log'
$stderrPath = Join-Path $localDirectory 'server.stderr.log'
$serverProcess = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', $Port) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
@{ pid=$serverProcess.Id; port=$Port; root=$projectRoot } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $localDirectory 'server.json') -Encoding utf8
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2; $ready = $true; break } catch {}
}
if (-not $ready) { throw "Server did not become ready. Inspect $stderrPath" }
Write-Output "Workbench ready: http://127.0.0.1:$Port"
Write-Output 'Open the sign-in page to register an account or sign in.'
Write-Output 'Model keys are loaded from the Windows user environment. Lifetime project model budget: CNY 300.'
