$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path','Machine')
    $user = [Environment]::GetEnvironmentVariable('Path','User')
    $env:Path = "$machine;$user"
}

function Find-Python {
    Refresh-Path
    $candidates = @(
        (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            try {
                & $candidate --version *> $null
                if ($LASTEXITCODE -eq 0) { return $candidate }
            } catch {}
        }
    }
    return $null
}

try {
    $python = Find-Python
    if (-not $python) {
        $version = '3.14.7'
        $installer = Join-Path $env:TEMP "NEXORA-Python-$version-amd64.exe"
        $url = "https://www.python.org/ftp/python/$version/python-$version-amd64.exe"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer
        $p = Start-Process -FilePath $installer -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_pip=1','Include_launcher=1','SimpleInstall=1' -Wait -PassThru -WindowStyle Hidden
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
        if ($p.ExitCode -ne 0) { throw 'Python installation failed' }
        $python = Find-Python
    }
    if (-not $python) { throw 'Python unavailable' }

    $runtimeRoot = Join-Path $env:LOCALAPPDATA 'NEXORA\Hands'
    $dataRoot = Join-Path $runtimeRoot 'data'
    $appRoot = Join-Path $runtimeRoot 'app'
    New-Item -ItemType Directory -Force -Path $dataRoot,$appRoot | Out-Null

    $rawBase = 'https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main'
    $files = @(
        @{ Url = "$rawBase/start.ps1"; Path = (Join-Path $runtimeRoot 'start.ps1') },
        @{ Url = "$rawBase/stop.ps1"; Path = (Join-Path $runtimeRoot 'stop.ps1') },
        @{ Url = "$rawBase/app/hands.py"; Path = (Join-Path $appRoot 'hands.py') },
        @{ Url = "$rawBase/app/supabase_channel.py"; Path = (Join-Path $appRoot 'supabase_channel.py') },
        @{ Url = "$rawBase/app/hands_supabase_config.json"; Path = (Join-Path $appRoot 'hands_supabase_config.json') }
    )

    foreach ($file in $files) {
        $tmp = "$($file.Path).download"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $file.Url -OutFile $tmp
            if ((Get-Item -LiteralPath $tmp).Length -lt 100) { throw 'download too small' }
            if (Test-Path -LiteralPath $file.Path) { Remove-Item -LiteralPath $file.Path -Force }
            Move-Item -LiteralPath $tmp -Destination $file.Path -Force
        } finally {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
    }

    $startPath = Join-Path $runtimeRoot 'start.ps1'
    $startText = [System.IO.File]::ReadAllText($startPath)
    if ($startText -notmatch 'NEXORA Hands') { throw 'start.ps1 validation failed' }
    if ($startText -match 'вЂ|РїРѕ') { throw 'start.ps1 encoding validation failed' }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startPath -PythonPath $python
    exit $LASTEXITCODE
} catch {
    Write-Host ('NEXORA Hands ' + [char]0x2014 + ' ' + [string]::Concat([char[]](0x043D,0x0435)) + ' ' + [string]::Concat([char[]](0x0443,0x0434,0x0430,0x043B,0x043E,0x0441,0x044C)) + ' ' + [string]::Concat([char[]](0x0443,0x0441,0x0442,0x0430,0x043D,0x043E,0x0432,0x0438,0x0442,0x044C)) + ' ' + [string]::Concat([char[]](0x043F,0x043E,0x0434,0x043A,0x043B,0x044E,0x0447,0x0435,0x043D,0x0438,0x0435)) + '.') -ForegroundColor Red
    exit 1
}
