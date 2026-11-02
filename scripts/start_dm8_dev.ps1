param([string]$Root = 'E:/HangzhiPlatform/dameng-dev')
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath $Root).Path
$exe = (Resolve-Path -LiteralPath (Join-Path $Root 'runtime/source/bin/dmserver.exe')).Path
$ini = (Resolve-Path -LiteralPath (Join-Path $Root 'database/HZDMDEV/dm.ini')).Path
$config = Get-Content -LiteralPath $ini -Raw
foreach ($required in @('LISTEN_IP\s*=\s*127\.0\.0\.1\b','INVITED_IP\s*=\s*127\.0\.0\.1\b','IPV6_LSNR\s*=\s*0\b','PORT_NUM\s*=\s*15286\b')) {
    if ($config -notmatch "(?im)^\s*$required") { throw 'Local DM8 access configuration mismatch. Not starting.' }
}
$rule = Get-NetFirewallRule -Name 'Hangzhi-DM8-15286-BlockNonLoopback' -PolicyStore ActiveStore
$addresses = ($rule | Get-NetFirewallAddressFilter).RemoteAddress
if ($rule.Action -ne 'Block' -or $rule.Direction -ne 'Inbound' -or $rule.Enabled -ne 'True' -or
    ($rule | Get-NetFirewallPortFilter).LocalPort -ne '15286' -or
    ($rule | Get-NetFirewallApplicationFilter).Program -ne $exe -or
    $addresses -notcontains '1.0.0.0-126.255.255.255' -or $addresses -notcontains '128.0.0.0-223.255.255.255' -or
    @(Get-NetFirewallProfile -PolicyStore ActiveStore | Where-Object { -not $_.Enabled }).Count -gt 0) {
    throw 'Missing effective program/port firewall isolation. Not starting.'
}
$recordPath = Join-Path $Root 'private/process.json'
$listener = @(Get-NetTCPConnection -LocalPort 15286 -State Listen -ErrorAction SilentlyContinue)
if ($listener.Count -gt 0) {
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)"
    if (-not $existing -or $existing.ExecutablePath -ne $exe -or $existing.CommandLine -notmatch 'HZDMDEV[/\\]dm\.ini' -or
        @($listener | Where-Object { $_.OwningProcess -ne $record.pid }).Count -gt 0) {
        throw 'Port 15286 is occupied by an unrecognized process.'
    }
    Write-Output 'Verified existing local DM8 listener with restricted access: 127.0.0.1:15286'
    return
}
$bin = Split-Path $exe -Parent
$env:PATH = "$bin;$bin/dependencies;$env:PATH"
$env:TEMP = Join-Path $Root 'tmp'; $env:TMP = $env:TEMP
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$server = Start-Process -FilePath $exe -ArgumentList @($ini,'-noconsole','listen_ipv6=0') -WindowStyle Hidden -WorkingDirectory $bin -PassThru -RedirectStandardOutput (Join-Path $Root "evidence/dmserver-$stamp.out.log") -RedirectStandardError (Join-Path $Root "evidence/dmserver-$stamp.err.log")
@{pid=$server.Id;port=15286;root=$Root} | ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding utf8
for ($attempt=0; $attempt -lt 35; $attempt++) {
    $server.Refresh()
    if ($server.HasExited) { throw 'DM8 startup failed; inspect the private E: diagnostic logs.' }
    if (Get-NetTCPConnection -LocalPort 15286 -OwningProcess $server.Id -State Listen -ErrorAction SilentlyContinue) {
        Write-Output 'Local DM8 listener started with restricted access. Verify SQL readiness using scripts.dm_database check.'
        return
    }
    Start-Sleep -Milliseconds 500
}
throw 'DM8 listener is not ready; inspect the private E: diagnostic logs.'
