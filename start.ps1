$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
$App = Join-Path $Root 'app'
$Log = Join-Path $Data 'logs'
New-Item -ItemType Directory -Force -Path $Log | Out-Null
New-Item -ItemType Directory -Force -Path $App | Out-Null

$machinePath = [Environment]::GetEnvironmentVariable('Path','Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path','User')
$env:Path = "$machinePath;$userPath"

$RuntimeConfig = Join-Path $Data 'hands_supabase_config.json'
$TemplateConfig = Join-Path $App 'hands_supabase_config.json'
if (-not (Test-Path $RuntimeConfig)) {
    if (-not (Test-Path $TemplateConfig)) { exit 1 }
    Copy-Item $TemplateConfig $RuntimeConfig -Force
}
try {
    $cfg = Get-Content $RuntimeConfig -Raw | ConvertFrom-Json
    if ($null -eq $cfg.worker_id) { $cfg | Add-Member -NotePropertyName worker_id -NotePropertyValue '' -Force }
    if ($null -eq $cfg.worker_token) { $cfg | Add-Member -NotePropertyName worker_token -NotePropertyValue '' -Force }
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $RuntimeConfig -Encoding UTF8
} catch { exit 1 }

$RepoBase = 'https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/app'
$Hands = Join-Path $App 'hands.py'
$Channel = Join-Path $App 'supabase_channel.py'
$HandsTmp = "$Hands.download"
$ChannelTmp = "$Channel.download"
try {
    Invoke-WebRequest -Uri "$RepoBase/hands.py" -OutFile $HandsTmp -UseBasicParsing *> $null
    if ((Get-Item $HandsTmp).Length -lt 1000) { throw 'hands.py download failed' }
    Move-Item $HandsTmp $Hands -Force

    Invoke-WebRequest -Uri "$RepoBase/supabase_channel.py" -OutFile $ChannelTmp -UseBasicParsing *> $null
    if ((Get-Item $ChannelTmp).Length -lt 1000) { throw 'supabase_channel.py download failed' }
    Move-Item $ChannelTmp $Channel -Force
} catch {
    Remove-Item $HandsTmp,$ChannelTmp -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $Hands) -or -not (Test-Path $Channel)) { exit 1 }
}

$Python = $null
try { & python --version *> $null; if ($LASTEXITCODE -eq 0) { $Python='python' } } catch {}
if (-not $Python) {
    try { & py --version *> $null; if ($LASTEXITCODE -eq 0) { $Python='py' } } catch {}
}
if (-not $Python) { exit 1 }

$HandsOut = Join-Path $Log 'hands.stdout.log'
$HandsErr = Join-Path $Log 'hands.stderr.log'
$ChannelOut = Join-Path $Log 'channel.stdout.log'
$ChannelErr = Join-Path $Log 'channel.stderr.log'
function Find-PythonProcess([string]$script) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$script*" } |
        Select-Object -First 1
}

$handsProc = Find-PythonProcess 'hands.py'
$channelProc = Find-PythonProcess 'supabase_channel.py'
if (-not $handsProc) {
    if ($Python -eq 'py') { $handsProc = Start-Process py -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -RedirectStandardOutput $HandsOut -RedirectStandardError $HandsErr -WindowStyle Hidden -PassThru }
    else { $handsProc = Start-Process $Python -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -RedirectStandardOutput $HandsOut -RedirectStandardError $HandsErr -WindowStyle Hidden -PassThru }
}
if (-not $channelProc) {
    if ($Python -eq 'py') { $channelProc = Start-Process py -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden -PassThru }
    else { $channelProc = Start-Process $Python -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden -PassThru }
}

# User-facing console contains status only. Technical diagnostics remain in logs.
$connected = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $channelStatePath = Join-Path $Data 'supabase_channel_state.json'
        if (Test-Path $channelStatePath) {
            $c = Get-Content $channelStatePath -Raw | ConvertFrom-Json
            if ($c.heartbeat_ok -eq $true) { $connected = $true; break }
        }
    } catch {}
    Start-Sleep -Seconds 1
}
if ($connected) {
    Write-Host 'NEXORA Hands — подключение установлено.' -ForegroundColor Green
} else {
    Write-Host 'NEXORA Hands — подключение не установлено.' -ForegroundColor Red
}

while ($true) {
    Start-Sleep -Seconds 5
}
