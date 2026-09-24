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
    # Stop a previously installed runtime so an old start.ps1 cannot keep the host mutex.
    $runtimeNeedle = [IO.Path]::GetFullPath($runtimeRoot).ToLowerInvariant()
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and ($_.Name -match '^(powershell|pwsh|python|pythonw)\.exe$') -and
        $_.CommandLine.ToLowerInvariant().Contains($runtimeNeedle) -and
        ($_.CommandLine -match 'start\.ps1|hands\.py|supabase_channel\.py')
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    $dataRoot = Join-Path $runtimeRoot 'data'
    $appRoot = Join-Path $runtimeRoot 'app'
    New-Item -ItemType Directory -Force -Path $dataRoot,$appRoot | Out-Null
    $rawBase = 'https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/main'
    $files = @(
        @{Url="$rawBase/start.ps1";Path=(Join-Path $runtimeRoot 'start.ps1');Sha256='DAB8C16B9D8FD728085EE0B847668930FBF73976C0431932F4DC23FADDCEE0A5'},
        @{Url="$rawBase/stop.ps1";Path=(Join-Path $runtimeRoot 'stop.ps1');Sha256='6E94D8594B164A3C8D78DF2A32693C2672CCEE7F6DE70C7E637C3781A52BD360'},
        @{Url="$rawBase/app/hands.py";Path=(Join-Path $appRoot 'hands.py');Sha256='8F6E4A2E6DC4AB41510527EA3E4E93EA207487D9F6422974F6CB341DE8075197'},
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
