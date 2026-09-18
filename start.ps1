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
$HandsState = Join-Path $Data 'state.json'
$ChannelState = Join-Path $Data 'supabase_channel_state.json'
$HandsOut = Join-Path $Log 'hands.stdout.log'
$HandsErr = Join-Path $Log 'hands.stderr.log'
$ChannelOut = Join-Path $Log 'channel.stdout.log'
$ChannelErr = Join-Path $Log 'channel.stderr.log'

$HostMutex = New-Object System.Threading.Mutex($false, 'Local\NEXORA.Hands.Host')
$OwnsHostMutex = $false
try { $OwnsHostMutex = $HostMutex.WaitOne(0) }
catch [System.Threading.AbandonedMutexException] { $OwnsHostMutex = $true }
if (-not $OwnsHostMutex) {
    Write-Host 'NEXORA Hands - already connected.'
    exit 0
}

function Stop-ExactPythonScript([string]$Path) {
    $needle = [IO.Path]::GetFullPath($Path).ToLowerInvariant()
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($needle)
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
function Start-HandsProcess {
    Start-Process -FilePath $PythonPath -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -NoNewWindow -PassThru
}
function Start-ChannelProcess {
    Start-Process -FilePath $PythonPath -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden -PassThru
}
function State-AgeSeconds([string]$Path,[string]$Property) {
    try {
        if (-not (Test-Path -LiteralPath $Path)) { return [double]::PositiveInfinity }
        $s = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $v = [double]($s.$Property)
        if ($v -le 0) { return [double]::PositiveInfinity }
        return [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0 - $v
    } catch { return [double]::PositiveInfinity }
}

Stop-ExactPythonScript $Hands
Stop-ExactPythonScript $Channel
Start-Sleep -Milliseconds 300
$handsProc = $null
$channelProc = $null

try {
    $handsProc = Start-HandsProcess
    $channelProc = Start-ChannelProcess
    Remove-Item -LiteralPath $ChannelState -Force -ErrorAction SilentlyContinue

    $connected = $false
    for ($i=0; $i -lt 90; $i++) {
        try {
            if (Test-Path -LiteralPath $ChannelState) {
                $state = Get-Content -LiteralPath $ChannelState -Raw | ConvertFrom-Json
                if ($state.heartbeat_ok -eq $true -and $state.executor_online -eq $true) { $connected = $true; break }
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (-not $connected) { Write-Host 'NEXORA Hands - connection failed.'; exit 1 }
    Write-Host 'NEXORA Hands - connected.'

    while ($true) {
        Start-Sleep -Seconds 2

        $restartHands = $false
        $restartChannel = $false

        if ($null -eq $handsProc -or $handsProc.HasExited) { $restartHands = $true }
        elseif ((State-AgeSeconds $HandsState 'updated_at') -gt 20) { $restartHands = $true }

        if ($null -eq $channelProc -or $channelProc.HasExited) { $restartChannel = $true }
        elseif ((State-AgeSeconds $ChannelState 'heartbeat_at') -gt 45) { $restartChannel = $true }

        if ($restartHands) {
            if ($handsProc -and -not $handsProc.HasExited) { Stop-Process -Id $handsProc.Id -Force -ErrorAction SilentlyContinue }
            Stop-ExactPythonScript $Hands
            Start-Sleep -Milliseconds 500
            $handsProc = Start-HandsProcess
            Write-Host '[WATCHDOG] Executor restarted.'
        }

        if ($restartChannel) {
            if ($channelProc -and -not $channelProc.HasExited) { Stop-Process -Id $channelProc.Id -Force -ErrorAction SilentlyContinue }
            Stop-ExactPythonScript $Channel
            Start-Sleep -Milliseconds 500
            $channelProc = Start-ChannelProcess
            Write-Host '[WATCHDOG] Transport restarted.'
        }
    }
}
finally {
    if ($handsProc -and -not $handsProc.HasExited) { Stop-Process -Id $handsProc.Id -Force -ErrorAction SilentlyContinue }
    if ($channelProc -and -not $channelProc.HasExited) { Stop-Process -Id $channelProc.Id -Force -ErrorAction SilentlyContinue }
    if ($OwnsHostMutex) { try { $HostMutex.ReleaseMutex() } catch {} }
    if ($HostMutex) { $HostMutex.Dispose() }
}
