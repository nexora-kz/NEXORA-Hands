$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Write-Host '============================================================'
Write-Host '                 NEXORA HANDS'
Write-Host '============================================================'
Write-Host '[BOOT] NEXORA Hands remote bootstrap starting...'
function Refresh-Path { $machine=[Environment]::GetEnvironmentVariable('Path','Machine'); $user=[Environment]::GetEnvironmentVariable('Path','User'); $env:Path="$machine;$user" }
function Find-CommandPath([string]$Name) { Refresh-Path; $cmd=Get-Command $Name -ErrorAction SilentlyContinue; if($cmd){return $cmd.Source}; return $null }
$node=Find-CommandPath 'node.exe'
if(-not $node){ Write-Host '[BOOT] Node.js not found. Installing Node.js LTS...'; $winget=Find-CommandPath 'winget.exe'; if(-not $winget){throw 'Windows Package Manager (winget) is required for automatic Node.js installation.'}; & $winget install --id OpenJS.NodeJS.LTS --exact --accept-source-agreements --accept-package-agreements; Refresh-Path; $node=Find-CommandPath 'node.exe' }
if(-not $node){throw 'Node.js LTS installation completed but node.exe was not found.'}
Write-Host "[BOOT] Node.js ready: $(& $node --version)"
$python=Find-CommandPath 'python.exe'
if(-not $python){
  $python=Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
  if(-not (Test-Path $python)){$python=Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'}
}
if(-not (Test-Path $python)){
  $version='3.14.7'
  $installer=Join-Path $env:TEMP "NEXORA-Python-$version-amd64.exe"
  $url="https://www.python.org/ftp/python/$version/python-$version-amd64.exe"
  Write-Host '[BOOT] Python not found. Downloading official Python installer...'
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installer
  Write-Host '[BOOT] Installing Python for the current Windows user...'
  $p=Start-Process -FilePath $installer -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_pip=1','Include_launcher=1','SimpleInstall=1' -Wait -PassThru
  Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
  if($p.ExitCode -ne 0){throw "Python installation failed with exit code $($p.ExitCode)."}
  Refresh-Path
  $python=Find-CommandPath 'python.exe'
  if(-not $python){$python=Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'}
}
if(-not $python -or -not (Test-Path $python)){throw 'Python installation completed but python.exe was not found.'}
Write-Host "[BOOT] Python runtime ready: $(& $python --version)"
$work=Join-Path $env:TEMP ("NEXORA-Hands-" + [guid]::NewGuid().ToString('N'))
$zip=Join-Path $env:TEMP ("NEXORA-Hands-" + [guid]::NewGuid().ToString('N') + '.zip')
try {
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  Write-Host '[BOOT] Downloading NEXORA Hands directly from GitHub...'
  Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/nexora-kz/NEXORA-Hands/archive/refs/heads/main.zip' -OutFile $zip
  Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
  $root=Join-Path $work 'NEXORA-Hands-main'
  if(-not (Test-Path (Join-Path $root 'package.json'))){throw 'Downloaded NEXORA Hands package is incomplete.'}
  Write-Host '[BOOT] Starting NEXORA Hands in this PowerShell console...'
  & (Join-Path $root 'start.ps1')
  exit $LASTEXITCODE
} finally {
  Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
