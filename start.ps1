$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
$App = Join-Path $Root 'app'
$Log = Join-Path $Data 'logs'
New-Item -ItemType Directory -Force -Path $Log,$App | Out-Null

$RuntimeConfig = Join-Path $Data 'hands_supabase_config.json'
$TemplateConfig = Join-Path $App 'hands_supabase_config.json'
if (-not (Test-Path $RuntimeConfig)) {
    if (-not (Test-Path $TemplateConfig)) { exit 1 }
    Copy-Item $TemplateConfig $RuntimeConfig -Force
}
try {
    $cfg = Get-Content $RuntimeConfig -Raw | ConvertFrom-Json
    if ($null -eq $cfg.worker_id) { $cfg | Add-Member worker_id '' -Force }
    if ($null -eq $cfg.worker_token) { $cfg | Add-Member worker_token '' -Force }
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $RuntimeConfig -Encoding UTF8
} catch { exit 1 }

$RepoBase = 'https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/app'
$Hands = Join-Path $App 'hands.py'
$Channel = Join-Path $App 'supabase_channel.py'
foreach ($item in @(@($Hands,'hands.py'),@($Channel,'supabase_channel.py'))) {
    $target = $item[0]; $name = $item[1]; $tmp = "$target.download"
    try {
        Invoke-WebRequest -Uri "$RepoBase/$name" -OutFile $tmp -UseBasicParsing *> $null
        if ((Get-Item $tmp).Length -lt 1000) { throw 'download failed' }
        Move-Item $tmp $target -Force
    } catch {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $target)) { exit 1 }
    }
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
if (-not (Find-PythonProcess 'hands.py')) {
    Start-Process -FilePath $Python -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -RedirectStandardOutput $HandsOut -RedirectStandardError $HandsErr -WindowStyle Hidden | Out-Null
}
if (-not (Find-PythonProcess 'supabase_channel.py')) {
    Start-Process -FilePath $Python -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden | Out-Null
}

$connected = $false
$statePath = Join-Path $Data 'supabase_channel_state.json'
for ($i = 0; $i -lt 60; $i++) {
    try {
        if (Test-Path $statePath) {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json
            if ($state.heartbeat_ok -eq $true) { $connected = $true; break }
        }
    } catch {}
    Start-Sleep -Seconds 1
}

if ($connected) {
    Write-Host ('NEXORA Hands ' + [char]0x2014 + ' ' + [string]::Concat([char[]](0x043F,0x043E,0x0434,0x043A,0x043B,0x044E,0x0447,0x0435,0x043D,0x0438,0x0435)) + ' ' + [string]::Concat([char[]](0x0443,0x0441,0x0442,0x0430,0x043D,0x043E,0x0432,0x043B,0x0435,0x043D,0x043E)) + '.') -ForegroundColor Green
} else {
    Write-Host ('NEXORA Hands ' + [char]0x2014 + ' ' + [string]::Concat([char[]](0x043F,0x043E,0x0434,0x043A,0x043B,0x044E,0x0447,0x0435,0x043D,0x0438,0x0435)) + ' ' + [string]::Concat([char[]](0x043D,0x0435)) + ' ' + [string]::Concat([char[]](0x0443,0x0441,0x0442,0x0430,0x043D,0x043E,0x0432,0x043B,0x0435,0x043D,0x043E)) + '.') -ForegroundColor Red
}
while ($true) { Start-Sleep -Seconds 5 }
