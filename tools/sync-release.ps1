param([string]$ReleaseSha)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo
if(-not $ReleaseSha){$ReleaseSha=(git rev-parse HEAD).Trim()}
if($ReleaseSha -notmatch '^[0-9a-fA-F]{40}$'){throw 'ReleaseSha must be a full Git SHA'}
$ReleaseSha=$ReleaseSha.ToLowerInvariant()
$payload=@('start.ps1','stop.ps1','self-test.ps1','app/hands.py','app/supabase_channel.py','app/hands_supabase_config.json','app/agent_control.py','app/agent_updater.py')
$worktrees=@('D:\NEXORA Hands\github_work','D:\NEXORA Hands\clean_public_test')
$origin=(git rev-parse origin/main).Trim().ToLowerInvariant()
if($origin -ne $ReleaseSha){throw "origin/main $origin != release $ReleaseSha"}
$dirty=git status --porcelain
if($dirty){throw 'canonical repository is dirty'}
foreach($w in $worktrees){if(Test-Path (Join-Path $w '.git')){git -C $w fetch origin main --quiet; git -C $w reset --hard origin/main | Out-Null; if((git -C $w rev-parse HEAD).Trim().ToLowerInvariant() -ne $ReleaseSha){throw "worktree sync failed: $w"}; if(git -C $w status --porcelain){throw "worktree dirty after sync: $w"}}}
$remote=Join-Path $repo 'remote.ps1'; $s=[IO.File]::ReadAllText($remote)
$s=[regex]::Replace($s,"(?m)(raw.githubusercontent.com/nexora-kz/NEXORA-Hands/)[0-9a-fA-F]{40}",'${1}'+$ReleaseSha)
$s=[regex]::Replace($s,"(?m)(release_sha=')[0-9a-fA-F]{40}(')",'${1}'+$ReleaseSha+'$2')
foreach($rel in $payload){$hash=(Get-FileHash -LiteralPath (Join-Path $repo ($rel -replace '/','\')) -Algorithm SHA256).Hash.ToUpperInvariant(); $escaped=[regex]::Escape($rel); $pattern="(?m)(@\{Rel='$escaped';Sha256=')[0-9A-Fa-f]{64}('})"; $s=[regex]::Replace($s,$pattern,'${1}'+$hash+'$2')}
[IO.File]::WriteAllText($remote,$s,(New-Object Text.UTF8Encoding($false)))
$tokens=$null;$errors=$null;[Management.Automation.Language.Parser]::ParseFile($remote,[ref]$tokens,[ref]$errors)|Out-Null;if($errors.Count){throw ($errors|ForEach-Object Message|Out-String)}
if(git diff --quiet -- remote.ps1){Write-Output "SYNC_OK release=$ReleaseSha bootstrap=unchanged";exit 0}
git add remote.ps1
git commit -m "Pin bootstrap to release $($ReleaseSha.Substring(0,7))" | Out-Null
git push origin main | Out-Null
$syncHead=(git rev-parse HEAD).Trim().ToLowerInvariant()
foreach($w in $worktrees){if(Test-Path (Join-Path $w '.git')){git -C $w fetch origin main --quiet; git -C $w reset --hard origin/main | Out-Null}}
Write-Output "SYNC_OK payload=$ReleaseSha repository=$syncHead"
