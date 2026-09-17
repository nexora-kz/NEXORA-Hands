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
    $node = Find-CommandPath 'node.exe'
    if (-not $node) {
        $winget = Find-CommandPath 'winget.exe'
        if (-not $winget) { throw 'winget unavailable' }
        & $winget install --id OpenJS.NodeJS.LTS --exact --accept-source-agreements --accept-package-agreements *> $null
        Refresh-Path
        $node = Find-CommandPath 'node.exe'
    }
    if (-not $node) { throw 'Node.js unavailable' }

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

    $work = Join-Path $env:TEMP ("NEXORA-Hands-" + [guid]::NewGuid().ToString('N'))
    $zip = Join-Path $env:TEMP ("NEXORA-Hands-" + [guid]::NewGuid().ToString('N') + '.zip')
    try {
        New-Item -ItemType Directory -Force -Path $work | Out-Null
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/nexora-kz/NEXORA-Hands/archive/refs/heads/main.zip' -OutFile $zip | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
        $root = Join-Path $work 'NEXORA-Hands-main'
        if (-not (Test-Path (Join-Path $root 'package.json'))) { throw 'Hands package incomplete' }
        & (Join-Path $root 'start.ps1')
        exit $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
} catch {
    Write-Host 'NEXORA Hands — не удалось установить подключение.' -ForegroundColor Red
    exit 1
}
