param([string]$ExpectedRepoSha)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path; Set-Location $repo
if(-not $ExpectedRepoSha){$ExpectedRepoSha=(git rev-parse origin/main).Trim()}
$repos=@($repo,'D:\NEXORA Hands\github_work','D:\NEXORA Hands\clean_public_test')
foreach($r in $repos){if(-not(Test-Path (Join-Path $r '.git'))){throw "missing repo $r"};$h=(git -C $r rev-parse HEAD).Trim();if($h -ne $ExpectedRepoSha){throw "HEAD mismatch $r $h"};if(git -C $r status --porcelain){throw "dirty repo $r"}}
foreach($f in @('remote.cmd','launch.html')){$txt=[IO.File]::ReadAllText((Join-Path $repo $f));if($txt -notmatch 'https://raw\.githubusercontent\.com/nexora-kz/NEXORA-Hands/main/remote\.ps1'){throw "$f bootstrap URL mismatch"}}
$bootstrap=[IO.File]::ReadAllText((Join-Path $repo 'remote.ps1'));if($bootstrap -notmatch "raw\.githubusercontent\.com/nexora-kz/NEXORA-Hands/[0-9a-f]{40}"){throw 'bootstrap immutable payload URL missing'}
$manifest=Get-Content (Join-Path $repo 'release-manifest.json') -Raw|ConvertFrom-Json;if($manifest.supabase_project -ne 'igeddbknrctsdufieosg'){throw 'Supabase project mismatch'}
Write-Output "VERIFY_OK repository=$ExpectedRepoSha entrypoints=remote.cmd,launch.html bootstrap=remote.ps1 supabase=$($manifest.supabase_project)"
