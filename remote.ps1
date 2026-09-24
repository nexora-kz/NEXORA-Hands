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
    $rawBase = 'https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/041272e80c8237abaae699afd18f9212806af6ae'
    $files = @(
        @{Url="$rawBase/start.ps1";Path=(Join-Path $runtimeRoot 'start.ps1');Sha256='0C149BDA954B0AC70777EB3B80B44A2F4434A944C8915D7FCD69F8CB8A3FCF76'},
        @{Url="$rawBase/stop.ps1";Path=(Join-Path $runtimeRoot 'stop.ps1');Sha256='64A65E761A41A9DCBBA75706FEA37C4614B944A152B56FF4B6E3F39116733304'},
        @{Url="$rawBase/app/hands.py";Path=(Join-Path $appRoot 'hands.py');Sha256='E7D0B799D5E09061B4E2789207EF49F9F0447C9C211D4D630B345641041C31D5'},
        @{Url="$rawBase/app/supabase_channel.py";Path=(Join-Path $appRoot 'supabase_channel.py');Sha256='6C1F8789F85F0E66A0365C0198EAC8F36B8412F332FA6F0B91B9DC9B4E0D4A10'},
        @{Url="$rawBase/app/hands_supabase_config.json";Path=(Join-Path $appRoot 'hands_supabase_config.json');Sha256='435844EAF35BFE270FD41AB9C1706B462F9097A19CAC09DDCC3BFA118001CAEA'}
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
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        $errLog = Join-Path $dataRoot 'logs\channel.stderr.log'
        $detail = if (Test-Path $errLog) { (Get-Content $errLog -Tail 8 -ErrorAction SilentlyContinue) -join ' | ' } else { 'channel log unavailable' }
        Write-Host ('NEXORA Hands - runtime failed: ' + $detail)
    }
    exit $rc
} catch {
    Write-Host ('NEXORA Hands - connection failed: ' + $_.Exception.Message)
    exit 1
}
