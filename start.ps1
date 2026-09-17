$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
$App = Join-Path $Root 'app'
$Log = Join-Path $Data 'logs'
New-Item -ItemType Directory -Force -Path $Log | Out-Null
New-Item -ItemType Directory -Force -Path $App | Out-Null

# Refresh PATH so a Python installation made by the launcher is visible.
$machinePath = [Environment]::GetEnvironmentVariable('Path','Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path','User')
$env:Path = "$machinePath;$userPath"

# First-run runtime identity: each PC gets its own persistent worker_id.
$RuntimeConfig = Join-Path $Data 'hands_supabase_config.json'
$TemplateConfig = Join-Path $App 'hands_supabase_config.json'
if (-not (Test-Path $RuntimeConfig)) {
    if (-not (Test-Path $TemplateConfig)) {
        Write-Host '[ERROR] Missing Hands runtime template config.' -ForegroundColor Red
        exit 1
    }
    Copy-Item $TemplateConfig $RuntimeConfig -Force
}
try {
    $cfg = Get-Content $RuntimeConfig -Raw | ConvertFrom-Json
    if ($null -eq $cfg.worker_id) { $cfg | Add-Member -NotePropertyName worker_id -NotePropertyValue '' -Force }
    if ($null -eq $cfg.worker_token) { $cfg | Add-Member -NotePropertyName worker_token -NotePropertyValue '' -Force }
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $RuntimeConfig -Encoding UTF8
} catch {
    Write-Host "[ERROR] Invalid Hands runtime config: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '                 NEXORA HANDS' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host '[BOOT] Starting local execution agent...' -ForegroundColor Gray

# Always refresh the two executable app files from the public repository.
# This prevents a stale local worker from surviving future launcher runs.
$RepoBase = 'https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/app'
$Hands = Join-Path $App 'hands.py'
$Channel = Join-Path $App 'supabase_channel.py'
$HandsTmp = "$Hands.download"
$ChannelTmp = "$Channel.download"
try {
    Write-Host '[UPDATE] Checking current Hands worker...' -ForegroundColor Yellow
    Invoke-WebRequest -Uri "$RepoBase/hands.py" -OutFile $HandsTmp -UseBasicParsing
    if ((Get-Item $HandsTmp).Length -lt 1000) { throw 'Downloaded hands.py is unexpectedly small.' }
    Move-Item $HandsTmp $Hands -Force
    Write-Host '[UPDATE] hands.py refreshed.' -ForegroundColor Green

    Write-Host '[UPDATE] Checking current Supabase channel...' -ForegroundColor Yellow
    Invoke-WebRequest -Uri "$RepoBase/supabase_channel.py" -OutFile $ChannelTmp -UseBasicParsing
    if ((Get-Item $ChannelTmp).Length -lt 1000) { throw 'Downloaded supabase_channel.py is unexpectedly small.' }
    Move-Item $ChannelTmp $Channel -Force
    Write-Host '[UPDATE] supabase_channel.py refreshed.' -ForegroundColor Green
} catch {
    Remove-Item $HandsTmp,$ChannelTmp -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $Hands) -or -not (Test-Path $Channel)) {
        Write-Host "[ERROR] Cannot obtain Hands runtime files: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    Write-Host "[UPDATE] Refresh failed; keeping existing runtime files: $($_.Exception.Message)" -ForegroundColor DarkYellow
}

$Python = $null
$PythonArgs = @()
try { & python --version *> $null; if ($LASTEXITCODE -eq 0) { $Python='python' } } catch {}
if (-not $Python) {
    try { & py --version *> $null; if ($LASTEXITCODE -eq 0) { $Python='py'; $PythonArgs=@() } } catch {}
}
if (-not $Python) {
    Write-Host '[ERROR] Python is not available after automatic installation.' -ForegroundColor Red
    exit 1
}

Write-Host "[BOOT] Python: $Python" -ForegroundColor DarkGray
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
if ($handsProc) {
    Write-Host "[HANDS] Already running PID $($handsProc.ProcessId)" -ForegroundColor Green
} else {
    Write-Host '[HANDS] Starting hands.py (hidden)...' -ForegroundColor Yellow
    if ($Python -eq 'py') { $handsProc = Start-Process py -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -RedirectStandardOutput $HandsOut -RedirectStandardError $HandsErr -WindowStyle Hidden -PassThru }
    else { $handsProc = Start-Process $Python -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -RedirectStandardOutput $HandsOut -RedirectStandardError $HandsErr -WindowStyle Hidden -PassThru }
    Write-Host "[HANDS] PID $($handsProc.Id)" -ForegroundColor Green
}
if ($channelProc) {
    Write-Host "[CHANNEL] Already running PID $($channelProc.ProcessId)" -ForegroundColor Green
} else {
    Write-Host '[CHANNEL] Starting Supabase transport (hidden)...' -ForegroundColor Yellow
    if ($Python -eq 'py') { $channelProc = Start-Process py -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden -PassThru }
    else { $channelProc = Start-Process $Python -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden -PassThru }
    Write-Host "[CHANNEL] PID $($channelProc.Id)" -ForegroundColor Green
}

Write-Host ''
Write-Host '[READY] NEXORA Hands is running.' -ForegroundColor Green
Write-Host '[READY] This window is the live local runtime console.' -ForegroundColor Green
Write-Host '[READY] Press Ctrl+C to close this monitor; use stop.ps1 to stop the agent.' -ForegroundColor DarkGray
Write-Host ''
$lastHandsStatus = ''
$lastChannelStatus = ''
$lastTask = ''
$seen = @{}
while ($true) {
    try {
        $statePath = Join-Path $Data 'state.json'
        $channelStatePath = Join-Path $Data 'supabase_channel_state.json'
        if (Test-Path $statePath) {
            $s = Get-Content $statePath -Raw | ConvertFrom-Json
            $line = "[HANDS] $($s.status) PID=$($s.pid)"
            if ($s.status -ne $lastHandsStatus -or $s.task_id -ne $lastTask) { Write-Host $line -ForegroundColor DarkCyan; $lastHandsStatus=$s.status; $lastTask=$s.task_id }
        }
        if (Test-Path $channelStatePath) {
            $c = Get-Content $channelStatePath -Raw | ConvertFrom-Json
            $line = "[CHANNEL] $($c.status) worker=$($c.worker_id)"
            if ($c.status -ne $lastChannelStatus) { Write-Host $line -ForegroundColor DarkMagenta; $lastChannelStatus=$c.status }
            if ($c.error) { Write-Host "[CHANNEL][ERROR] $($c.error)" -ForegroundColor Red }
        }
        Get-ChildItem (Join-Path $Data 'outbox\*.json') -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object {
                if (-not $seen.ContainsKey($_.FullName)) {
                    try {
                        $r = Get-Content $_.FullName -Raw | ConvertFrom-Json
                        Write-Host "[RESULT] task=$($r.task_id) status=$($r.status) file=$($_.Name)" -ForegroundColor White
                        $seen[$_.FullName] = $true
                    } catch {}
                }
            }
    } catch { Write-Host "[MONITOR][ERROR] $($_.Exception.Message)" -ForegroundColor Red }
    Start-Sleep -Seconds 1
}
