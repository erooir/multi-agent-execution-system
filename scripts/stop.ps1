$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.local\server.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'No recorded server.'; exit 0 }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.pid)" -ErrorAction SilentlyContinue
$expectedExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
if ($serverProcess -and $serverProcess.ExecutablePath -eq $expectedExecutable -and $serverProcess.CommandLine -like '*uvicorn backend.app.main:app*') {
    # Windows virtual-environment Python may launch a base-interpreter child.
    # Stop only matching descendants of this recorded launcher, before the launcher.
    $allProcesses = @(Get-CimInstance Win32_Process)
    $ownedIds = [System.Collections.Generic.List[int]]::new()
    $ownedIds.Add([int]$state.pid)
    for ($index = 0; $index -lt $ownedIds.Count; $index++) {
        foreach ($child in $allProcesses) {
            if ($child.ParentProcessId -eq $ownedIds[$index] -and
                $child.Name -eq 'python.exe' -and
                $child.CommandLine -like '*uvicorn backend.app.main:app*' -and
                $child.CommandLine -match "--port\s+$($state.port)(\s|$)") {
                $ownedIds.Add([int]$child.ProcessId)
            }
        }
    }
    for ($index = $ownedIds.Count - 1; $index -ge 0; $index--) {
        Stop-Process -Id $ownedIds[$index] -ErrorAction SilentlyContinue
    }
    Write-Output 'Workbench stopped.'
} else { Write-Output 'Recorded process is absent or does not match; no process stopped.' }
