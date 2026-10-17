$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedExecutable = Join-Path $projectRoot '.venv/Scripts/python.exe'
foreach ($kind in @('worker','server')) {
    $statePath = Join-Path $projectRoot ".local/$kind.json"
    if (-not (Test-Path -LiteralPath $statePath)) { continue }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.root -ne $projectRoot) { throw 'Recorded workspace mismatch' }
    $pattern = if ($kind -eq 'worker') { '*backend.app.platform.worker*' } else { '*uvicorn backend.app.main:app*' }
    $recorded = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.pid)" -ErrorAction SilentlyContinue
    if (-not $recorded) { Write-Output "$kind recorded process no longer exists; no process was stopped."; continue }
    if ($recorded.ExecutablePath -ne $expectedExecutable -or $recorded.CommandLine -notlike $pattern) { throw "Recorded $kind PID belongs to another process; refusing to stop it." }
    $allProcesses = @(Get-CimInstance Win32_Process)
    $ownedIds = [System.Collections.Generic.List[int]]::new()
    $ownedIds.Add([int]$state.pid)
    for ($index=0; $index -lt $ownedIds.Count; $index++) {
        foreach ($child in $allProcesses) {
            if ($child.ParentProcessId -eq $ownedIds[$index] -and $child.Name -eq 'python.exe' -and $child.CommandLine -like $pattern) {
                $ownedIds.Add([int]$child.ProcessId)
            }
        }
    }
    for ($index=$ownedIds.Count-1; $index -ge 0; $index--) {
        if (Get-Process -Id $ownedIds[$index] -ErrorAction SilentlyContinue) {
            try { Stop-Process -Id $ownedIds[$index] -ErrorAction Stop }
            catch { if (Get-Process -Id $ownedIds[$index] -ErrorAction SilentlyContinue) { throw } }
        }
    }
    $remaining = @()
    for ($attempt=0; $attempt -lt 20; $attempt++) {
        $remaining = @($ownedIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 100
    }
    if ($remaining.Count -gt 0) { throw "$kind failed to stop completely; do not run maintenance backups." }
    Write-Output "$kind stopped. Leases and in-flight uncertainty remain persisted for recovery."
}
