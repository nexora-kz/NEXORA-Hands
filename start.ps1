$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
$App = Join-Path $Root 'app'
$Log = Join-Path $Data 'logs'
New-Item -ItemType Directory -Force -Path $Log | Out-Null
New-Item -ItemType Directory -Force -Path $App | Out-Null

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
try { & python.exe --version *> $null; if ($LASTEXITCODE -eq 0) { $Python = (Get-Command python.exe).Source } } catch {}
if (-not $Python) {
    try { & py.exe --version *> $null; if ($LASTEXITCODE -eq 0) { $Python = (Get-Command py.exe).Source } } catch {}
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
    Start-Process -FilePath $Python -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -RedirectStandardOutput $HandsOut -RedirectStandardError $HandsErr -WindowStyle Hidden | Out-Null
}
if (-not $channelProc) {
    Start-Process -FilePath $Python -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden | Out-Null
}

$connected = $false
$statePath = Join-Path $Data 'supabase_channel_state.json'
for ($i = 0; $i -lt 60; $i++) {
    try {
        if (Test-Path $statePath) {
            $c = Get-Content $statePath -Raw | ConvertFrom-Json
            if ($c.heartbeat_ok -eq $true) { $connected = $true; break }
        }
    } catch {}
    Start-Sleep -Seconds 1
}

if ($connected) {
    $msg = [Text.Encoding]::UTF8.GetString([byte[]](0x4E,0x45,0x58,0x4F,0x52,0x41,0x20,0x48,0x61,0x6E,0x64,0x73,0x20,0xE2,0x80,0x94,0x20,0xD0,0xBF,0xD0,0xBE,0xD0,0xB4,0xD0,0xBA,0xD0,0xBB,0xD1,0x8E,0xD1,0x87,0xD0,0xB5,0xD0,0xBD,0xD0,0xB8,0xD0,0xB5,0x20,0xD1,0x83,0xD1,0x81,0xD1,0x82,0xD0,0xB0,0xD0,0xBD,0xD0,0xBE,0xD0,0xB2,0xD0,0xBB,0xD0,0xB5,0xD0,0xBD,0xD0,0xBE,0xD0,0x.))
    Write-Host $msg -ForegroundColor Green
} else {
    $msg = [Text.Encoding]::UTF8.GetString([byte[]](0x4E,0x45,0x58,0x4F,0x52,0x41,0x20,0x48,0x61,0x6E,0x64,0x73,0x20,0xE2,0x80,0x94,0x20,0xD0,0xBF,0xD0,0xBE,0xD0,0xB4,0xD0,0xBA,0xD0,0xBB,0xD1,0x8E,0xD1,0x87,0xD0,0xB5,0xD0,0xBD,0xD0,0xB8,0xD0,0xB5,0x20,0xD0,0xBD,0xD0,0xB5,0x20,0xD1,0x83,0xD1,0x81,0xD1,0x82,0xD0,0xB0,0xD0,0xBD,0xD0,0xBE,0xD0,0xB2,0xD0,0xBB,0xD0,0xB5,0xD0,0xBD,0xD0,0xBE,0x.))
    Write-Host $msg -ForegroundColor Red
}

while ($true) { Start-Sleep -Seconds 5 }
