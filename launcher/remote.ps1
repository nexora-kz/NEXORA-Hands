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
$npx=Find-CommandPath 'npx.cmd'
if(-not $npx){throw 'npx.cmd was not found after Node.js installation.'}
Write-Host '[BOOT] Starting NEXORA Hands from GitHub...' -ForegroundColor Cyan
& $npx --yes github:nexora-kz/NEXORA-Hands remote
exit $LASTEXITCODE
