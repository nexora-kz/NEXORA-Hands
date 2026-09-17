$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path','Machine')
    $user = [Environment]::GetEnvironmentVariable('Path','User')
    $env:Path = "$machine;$user"
}
function Find-CommandPath([string]$Name) {
    Refresh-Path
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

try {
    # The PowerShell launcher does not require Node.js. Python is the only
    # runtime needed by the Hands worker and transport processes.
    $python = Find-CommandPath 'python.exe'
    if (-not $python) {
        $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
        if (-not (Test-Path $python)) { $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe' }
    }
    if (-not (Test-Path $python)) {
        $version = '3.14.7'
        $installer = Join-Path $env:TEMP "NEXORA-Python-$version-amd64.exe"
        $url = "https://www.python.org/ftp/python/$version/python-$version-amd64.exe"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer | Out-Null
        $p = Start-Process -FilePath $installer -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_pip=1','Include_launcher=1','SimpleInstall=1' -Wait -PassThru -WindowStyle Hidden
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
        if ($p.ExitCode -ne 0) { throw 'Python installation failed' }
        Refresh-Path
        $python = Find-CommandPath 'python.exe'
        if (-not $python) { $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe' }
    }
    if (-not $python -or -not (Test-Path $python)) { throw 'Python unavailable' }

    # The downloaded repository is only an update source. Runtime data lives
    # under LOCALAPPDATA so worker_id/worker_token survive future launches.
    $runtimeRoot = Join-Path $env:LOCALAPPDATA 'NEXORA\Hands'
    $work = Join-Path $env:TEMP ("NEXORA-Hands-" + [guid]::NewGuid().ToString('N'))
    $zip = Join-Path $env:TEMP ("NEXORA-Hands-" + [guid]::NewGuid().ToString('N') + '.zip')
    try {
        New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $runtimeRoot 'app') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $runtimeRoot 'data') | Out-Null
        New-Item -ItemType Directory -Force -Path $work | Out-Null

        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/nexora-kz/NEXORA-Hands/archive/refs/heads/main.zip' -OutFile $zip | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
        $sourceRoot = Join-Path $work 'NEXORA-Hands-main'
        if (-not (Test-Path (Join-Path $sourceRoot 'start.ps1'))) { throw 'Hands package incomplete' }

        Copy-Item (Join-Path $sourceRoot 'start.ps1') (Join-Path $runtimeRoot 'start.ps1') -Force
        if (Test-Path (Join-Path $sourceRoot 'stop.ps1')) {
            Copy-Item (Join-Path $sourceRoot 'stop.ps1') (Join-Path $runtimeRoot 'stop.ps1') -Force
        }
        if (Test-Path (Join-Path $sourceRoot 'app\hands.py')) {
            Copy-Item (Join-Path $sourceRoot 'app\hands.py') (Join-Path $runtimeRoot 'app\hands.py') -Force
        }
        if (Test-Path (Join-Path $sourceRoot 'app\supabase_channel.py')) {
            Copy-Item (Join-Path $sourceRoot 'app\supabase_channel.py') (Join-Path $runtimeRoot 'app\supabase_channel.py') -Force
        }
        if (Test-Path (Join-Path $sourceRoot 'app\hands_supabase_config.json')) {
            Copy-Item (Join-Path $sourceRoot 'app\hands_supabase_config.json') (Join-Path $runtimeRoot 'app\hands_supabase_config.json') -Force
        }

        & (Join-Path $runtimeRoot 'start.ps1')
        exit $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
} catch {
    Write-Host 'NEXORA Hands — не удалось установить подключение.' -ForegroundColor Red
    exit 1
}
