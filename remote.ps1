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
