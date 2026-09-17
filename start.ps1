param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
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
$RepoBase = 'https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main/app'
$files = @(
    @{Name='hands.py'; Path=(Join-Path $App 'hands.py')},
    @{Name='supabase_channel.py'; Path=(Join-Path $App 'supabase_channel.py')}
)
foreach ($f in $files) {
    $tmp = "$($f.Path).download"
    try {
        Invoke-WebRequest -Uri "$RepoBase/$($f.Name)" -OutFile $tmp -UseBasicParsing
        if ((Get-Item -LiteralPath $tmp).Length -lt 1000) { throw 'download failed' }
        Move-Item -LiteralPath $tmp -Destination $f.Path -Force
    } catch {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $f.Path)) { exit 1 }
    }
}
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
function Test-ExactPythonScript([string]$Path) {
    $needle = [IO.Path]::GetFullPath($Path).ToLowerInvariant()
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($needle)
    }).Count -gt 0
}
if (-not (Test-ExactPythonScript $Hands)) {
    Start-Process -FilePath $PythonPath -ArgumentList "`"$Hands`"" -WorkingDirectory $Root -RedirectStandardOutput $HandsOut -RedirectStandardError $HandsErr -WindowStyle Hidden | Out-Null
}
if (-not (Test-ExactPythonScript $Channel)) {
    Start-Process -FilePath $PythonPath -ArgumentList "`"$Channel`"" -WorkingDirectory $Root -RedirectStandardOutput $ChannelOut -RedirectStandardError $ChannelErr -WindowStyle Hidden | Out-Null
}
$statePath = Join-Path $Data 'supabase_channel_state.json'
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
if ($connected) { Write-Host 'NEXORA Hands - connected.' } else { Write-Host 'NEXORA Hands - connection failed.'; exit 1 }
while ($true) { Start-Sleep -Seconds 5 }
