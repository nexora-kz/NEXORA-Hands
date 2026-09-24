$ErrorActionPreference = 'Stop'
$Utf8NoBom = [Text.UTF8Encoding]::new($false)
try { [Console]::InputEncoding = $Utf8NoBom } catch {}
try { [Console]::OutputEncoding = $Utf8NoBom } catch {}
$OutputEncoding = $Utf8NoBom
$PSDefaultParameterValues['*:Encoding'] = 'utf8'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { chcp.com 65001 > $null } catch {}
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
            try { & $candidate --version *> $null; if ($LASTEXITCODE -eq 0) { return $candidate } } catch {}
        }
    }
    return $null
}
try {
    $python = Find-Python
    if (-not $python) {
        $version = '3.14.7'
        $installer = Join-Path $env:TEMP "NEXORA-Python-$version-amd64.exe"
        Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$version/python-$version-amd64.exe" -OutFile $installer
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
        @{Url="$rawBase/start.ps1";Path=(Join-Path $runtimeRoot 'start.ps1');Sha256='DAB8C16B9D8FD728085EE0B847668930FBF73976C0431932F4DC23FADDCEE0A5'},
        @{Url="$rawBase/stop.ps1";Path=(Join-Path $runtimeRoot 'stop.ps1');Sha256='64A65E761A41A9DCBBA75706FEA37C4614B944A152B56FF4B6E3F39116733304'},
        @{Url="$rawBase/app/hands.py";Path=(Join-Path $appRoot 'hands.py');Sha256='E7D0B799D5E09061B4E2789207EF49F9F0447C9C211D4D630B345641041C31D5'},
        @{Url="$rawBase/app/supabase_channel.py";Path=(Join-Path $appRoot 'supabase_channel.py');Sha256='53AC5E530E2FBB692DCF1613A95ED69B56F8B605334303CEC0A819BD5AF36354'},
        @{Url="$rawBase/app/hands_supabase_config.json";Path=(Join-Path $appRoot 'hands_supabase_config.json');Sha256='B85E4D6B9605134D0A4566BCC7FB8E0D17FED32AA49E5E9E459FE95962EFFBE9'}
    )
    foreach ($file in $files) {
        $tmp = "$($file.Path).download"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $file.Url -OutFile $tmp
            if ((Get-Item -LiteralPath $tmp).Length -lt 100) { throw 'download too small' }
            $actualHash = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToUpperInvariant()
            if ($actualHash -ne $file.Sha256) { throw 'runtime integrity check failed' }
            Move-Item -LiteralPath $tmp -Destination $file.Path -Force
        } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
    $startPath = Join-Path $runtimeRoot 'start.ps1'
    $bytes = [IO.File]::ReadAllBytes($startPath)
    if (@($bytes | Where-Object { $_ -ge 128 }).Count -ne 0) { throw 'start.ps1 must be ASCII-only' }
    & $startPath -PythonPath $python
    exit $LASTEXITCODE
} catch {
    Write-Host 'NEXORA Hands - connection failed.'
    exit 1
}
