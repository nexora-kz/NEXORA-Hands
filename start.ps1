param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$Utf8NoBom = [Text.UTF8Encoding]::new($false)
try { [Console]::InputEncoding = $Utf8NoBom } catch {}
try { [Console]::OutputEncoding = $Utf8NoBom } catch {}
$OutputEncoding = $Utf8NoBom
$PSDefaultParameterValues['*:Encoding'] = 'utf8'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { chcp.com 65001 > $null } catch {}
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
$App = Join-Path $Root 'app'
$Log = Join-Path $Data 'logs'
New-Item -ItemType Directory -Force -Path $Log,$App | Out-Null
$RuntimeConfig = Join-Path $Data 'hands_supabase_config.json'
$TemplateConfig = Join-Path $App 'hands_supabase_config.json'
if (-not (Test-Path -LiteralPath $RuntimeConfig)) { Copy-Item -LiteralPath $TemplateConfig -Destination $RuntimeConfig -Force }
try {
    $cfg = Get-Content -LiteralPath $RuntimeConfig -Raw | ConvertFrom-Json
    if ($null -eq $cfg.worker_id) { $cfg | Add-Member worker_id '' -Force }
    if ($null -eq $cfg.worker_token) { $cfg | Add-Member worker_token '' -Force }
    $cfg | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $RuntimeConfig -Encoding UTF8
} catch { exit 1 }
if (-not (Test-Path -LiteralPath (Join-Path $App 'hands.py'))) { exit 1 }
if (-not (Test-Path -LiteralPath (Join-Path $App 'supabase_channel.py'))) { exit 1 }
if (-not $PythonPath) {
    $PythonPath = (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    if (-not $PythonPath) { $PythonPath = 'C:\Python314\python.exe' }
}
if (-not (Test-Path -LiteralPath $PythonPath)) { exit 1 }
$Hands = [IO.Path]::GetFullPath((Join-Path $App 'hands.py'))
$Channel = [IO.Path]::GetFullPath((Join-Path $App 'supabase_channel.py'))
$HandsOut = Join-Path $Log 'hands.stdout.log'
$HandsErr = Join-Path $Log 'hands.stderr.log'
$ChannelOut = Join-Path $Log 'channel.stdout.log'
$ChannelErr = Join-Path $Log 'channel.stderr.log'
$HostMutex = New-Object System.Threading.Mutex($false, 'Local\NEXORA.Hands.Host')
$OwnsHostMutex = $false
try {
    $OwnsHostMutex = $HostMutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
    $OwnsHostMutex = $true
}
if (-not $OwnsHostMutex) {
    Write-Host 'NEXORA Hands - already connected.'
    exit 0
}
function Stop-ExactPythonScript([string]$Path) {
    $needle = [IO.Path]::GetFullPath($Path).ToLowerInvariant()
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($needle)
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
Stop-ExactPythonScript $Hands
Stop-ExactPythonScript $Channel
Start-Sleep -Milliseconds 300
$handsProc = $null
$channelProc = $null
try {
    $handsProc = Start-Process -FilePath $PythonPath -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -NoNewWindow -PassThru
    $channelProc = Start-Process -FilePath $PythonPath -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden -PassThru
    $statePath = Join-Path $Data 'supabase_channel_state.json'
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    $connected = $false
    for ($i=0; $i -lt 90; $i++) {
        try {
            if (Test-Path -LiteralPath $statePath) {
                $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
                if ($state.heartbeat_ok -eq $true) { $connected = $true; break }
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (-not $connected) { Write-Host 'NEXORA Hands - connection failed.'; exit 1 }
    Write-Host 'NEXORA Hands - connected.'
    while ($true) { Start-Sleep -Seconds 5 }
} finally {
    if ($handsProc) { Stop-Process -Id $handsProc.Id -Force -ErrorAction SilentlyContinue }
    if ($channelProc) { Stop-Process -Id $channelProc.Id -Force -ErrorAction SilentlyContinue }
    if ($OwnsHostMutex) { try { $HostMutex.ReleaseMutex() } catch {} }
    if ($HostMutex) { $HostMutex.Dispose() }
}
