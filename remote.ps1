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
    $stageRoot = Join-Path $runtimeRoot 'update-stage'
    $previousRoot = Join-Path $runtimeRoot 'previous'
    New-Item -ItemType Directory -Force -Path $dataRoot,$appRoot | Out-Null
    Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $stageRoot 'app') | Out-Null
    $rawBase = 'https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/4b490a7606b5c9b05dbb84ece92857c0b13399c8'
    $files = @(
        @{Rel='start.ps1';Sha256='0C149BDA954B0AC70777EB3B80B44A2F4434A944C8915D7FCD69F8CB8A3FCF76'},
        @{Rel='stop.ps1';Sha256='64A65E761A41A9DCBBA75706FEA37C4614B944A152B56FF4B6E3F39116733304'},
        @{Rel='self-test.ps1';Sha256='ED43524BDF5CB1BCB7676260B91B9D318CE49A8825E2D253A021D3CB8AC7A742'},
        @{Rel='app/hands.py';Sha256='738B3A90B3280796E0FAF9DAB310B03CCB2D2796CFE160DF1BBF04EA17090330'},
        @{Rel='app/supabase_channel.py';Sha256='924B31D1CD891EFC3F2FF46B440E6B7FFF590DEAE9D3247C49A036732D3A7AA3'},
        @{Rel='app/hands_supabase_config.json';Sha256='435844EAF35BFE270FD41AB9C1706B462F9097A19CAC09DDCC3BFA118001CAEA'},
        @{Rel='app/agent_control.py';Sha256='6E600117CD47E63D8DF808A5062C740B04A943FACFFBBF2C4F9CEF1572A0ECD1'},
        @{Rel='app/agent_updater.py';Sha256='4F62B292B6B110D577E2D92F4DD75AA54FDA5266F902D2869CF54A459EFD92E1'}
    )
    foreach ($file in $files) {
        $stagePath=Join-Path $stageRoot ($file.Rel -replace '/','\')
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $stagePath) | Out-Null
        Invoke-WebRequest -UseBasicParsing -Uri "$rawBase/$($file.Rel)" -OutFile $stagePath
        if ((Get-Item -LiteralPath $stagePath).Length -lt 100) { throw 'download too small' }
        $actualHash=(Get-FileHash -LiteralPath $stagePath -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($actualHash -ne $file.Sha256) { throw "runtime integrity check failed: $($file.Rel)" }
    }
    & $python -m py_compile (Join-Path $stageRoot 'app\hands.py') (Join-Path $stageRoot 'app\supabase_channel.py') (Join-Path $stageRoot 'app\agent_control.py') (Join-Path $stageRoot 'app\agent_updater.py')
    if($LASTEXITCODE -ne 0){throw 'staged Python compile failed'}
    Remove-Item -LiteralPath $previousRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $previousRoot 'app') | Out-Null
    foreach($rel in @('start.ps1','stop.ps1','self-test.ps1','app\hands.py','app\supabase_channel.py','app\hands_supabase_config.json','app\agent_control.py','app\agent_updater.py')){
        $current=Join-Path $runtimeRoot $rel
        if(Test-Path -LiteralPath $current){$backup=Join-Path $previousRoot $rel; New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup)|Out-Null; Copy-Item -LiteralPath $current -Destination $backup -Force}
    }
    try{
        foreach($file in $files){$rel=$file.Rel -replace '/','\'; $src=Join-Path $stageRoot $rel; $dst=Join-Path $runtimeRoot $rel; New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst)|Out-Null; Copy-Item -LiteralPath $src -Destination $dst -Force}
        @{release_sha='4b490a7606b5c9b05dbb84ece92857c0b13399c8';installed_at=[DateTimeOffset]::UtcNow.ToString('o')}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $dataRoot 'release_manifest.json') -Encoding UTF8
    }catch{
        foreach($rel in @('start.ps1','stop.ps1','self-test.ps1','app\hands.py','app\supabase_channel.py','app\hands_supabase_config.json','app\agent_control.py','app\agent_updater.py')){$backup=Join-Path $previousRoot $rel;if(Test-Path -LiteralPath $backup){$dst=Join-Path $runtimeRoot $rel;Copy-Item -LiteralPath $backup -Destination $dst -Force}}
        throw
    }
    Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
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
