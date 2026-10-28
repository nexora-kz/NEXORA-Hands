param(
 [ValidateSet('Check','Apply')][string]$Mode='Check',
 [string]$ReleaseSha='',
 [string]$PreviousSha='',
 [string]$ProjectRef='igeddbknrctsdufieosg',
 [switch]$SkipLocalAgent
)
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$report=[ordered]@{started=(Get-Date).ToUniversalTime().ToString('o');mode=$Mode;steps=@();success=$false}
function Step($name,$state,$detail='') { $report.steps+=@{name=$name;status=$state;detail=$detail}; Write-Host "$name : $state $detail" }
function RunGit([string[]]$ArgsList) {
 $output= & git -C $repo @ArgsList 2>&1
 if($LASTEXITCODE -ne 0){throw "Git $($ArgsList -join ' '): $output"}
 return ($output -join "`n").Trim()
}
try {
 $head=RunGit @('rev-parse','HEAD')
 $origin=RunGit @('ls-remote','origin','refs/heads/main')
 $remote=($origin -split '\s+')[0]
 $dirty=RunGit @('status','--porcelain','--untracked-files=no')
 if($dirty){throw 'Tracked source files changed; commit and review before release'}
 if(-not $ReleaseSha){$ReleaseSha=$head}
 if($ReleaseSha -notmatch '^[a-f0-9]{40}$'){throw 'Full release SHA required'}
 $localRelease=RunGit @('cat-file','-t',$ReleaseSha)
 if($localRelease -ne 'commit'){throw 'Release SHA not commit'}
 Step 'git' 'PASS' "HEAD=$head remote=$remote runtime_sha=$ReleaseSha"
 $bootstrap=Get-Content (Join-Path $repo 'remote.ps1') -Raw
 if($bootstrap -notmatch [regex]::Escape($ReleaseSha)){throw 'Bootstrap does not pin requested runtime SHA'}
 $payload=@('start.ps1','stop.ps1','self-test.ps1','app/hands.py','app/supabase_channel.py','app/hands_supabase_config.json','app/agent_control.py','app/agent_updater.py')
 foreach($rel in $payload){
   $expected=(Get-FileHash (Join-Path $repo $rel) -Algorithm SHA256).Hash.ToUpperInvariant()
   $match=[regex]::Match($bootstrap,"(?m)Rel='"+[regex]::Escape($rel)+"';Sha256='([0-9A-Fa-f]{64})'")
   if(-not $match.Success -or $match.Groups[1].Value.ToUpperInvariant() -ne $expected){throw "Bootstrap hash mismatch: $rel"}
 }
 $page=Get-Content (Join-Path $repo 'launch.html') -Raw
 if($page -notmatch 'NEXORA-Hands-remote.ps1' -or $page -notmatch 'main/remote.ps1'){throw 'launch.html bootstrap URL missing'}
 Step 'bootstrap' 'PASS' '8 hashes match and launch.html references remote.ps1'
 $functionNames=@('nexora-hands-mcp','nexora-hands-transport','nexora-hands-control','nexora-hands-control-v2')
 foreach($f in $functionNames){if(-not(Test-Path (Join-Path $repo "supabase/functions/$f/index.ts"))){throw "Missing function: $f"}}
 Step 'supabase_source' 'PASS' ($functionNames -join ',')
 if($PreviousSha) {
  $changed=RunGit @('diff','--name-only',$PreviousSha,$ReleaseSha,'--','supabase/functions')
  $changedFunctions=@($changed -split "`n" | Where-Object { $_ -match '^supabase/functions/([^/]+)/' } | ForEach-Object {($_ -split '/')[2]} | Select-Object -Unique)
 } else { $changedFunctions=@() }
 Step 'supabase_diff' 'PASS' ("changed_functions="+($changedFunctions -join ','))
 if($Mode -eq 'Check'){
   Step 'deployment' 'SKIPPED' 'check-only: nothing changed remotely'
   $report.success=$true
 } else {
   if($remote -ne $head){throw 'origin/main differs from local HEAD; push reviewed commit first'}
   if(-not $PreviousSha){throw 'Apply requires PreviousSha for reliable server-change detection'}
   if($changedFunctions.Count -gt 0){
     if(-not(Get-Command npx -ErrorAction SilentlyContinue)){throw 'npx unavailable: no server changes applied'}
     Push-Location $repo
     try {
       foreach($fn in $changedFunctions){
         & npx --yes supabase functions deploy $fn --project-ref $ProjectRef
         if($LASTEXITCODE -ne 0){throw "Supabase deploy failed: $fn"}
         Step "supabase:$fn" 'PASS' 'deploy returned 0'
       }
     } finally {Pop-Location}
   } else {Step 'supabase_deploy' 'SKIPPED' 'no changed server functions'}
   if(-not $SkipLocalAgent) {
     $agent=Join-Path $env:LOCALAPPDATA 'NEXORA/Hands'
     $manifest=Join-Path $agent 'data/release_manifest.json'
     if(-not(Test-Path $manifest)){throw 'Hands runtime missing'}
     $old=(Get-Content $manifest -Raw|ConvertFrom-Json).release_sha
     if($old -ne $ReleaseSha){
       $ctl=Join-Path $agent 'app/agent_control.py'
       $py=(Get-Command python.exe -ErrorAction Stop).Source
       $arg="import sys;sys.path.insert(0,sys.argv[1]);import agent_control;print(agent_control.start_agent_update(sys.argv[2],'update'))"
       & $py -c $arg (Join-Path $agent 'app') $ReleaseSha
       if($LASTEXITCODE -ne 0){throw 'Agent update not accepted'}
       Step 'agent_update' 'ACCEPTED' 'waiting for detached updater result'
       $deadline=(Get-Date).AddSeconds(120)
       $confirmed=$false
       do {
         Start-Sleep -Seconds 2
         $current=(Get-Content $manifest -Raw|ConvertFrom-Json).release_sha
         $resultPath=Join-Path $agent 'data/agent_update_report.json'
         if($current -eq $ReleaseSha -and (Test-Path $resultPath)){
           $result=Get-Content $resultPath -Raw|ConvertFrom-Json
           if($result.release_sha -eq $ReleaseSha -and $result.ok -eq $true){$confirmed=$true;break}
         }
       } while ((Get-Date) -lt $deadline)
       if(-not $confirmed){throw 'Agent update not confirmed within 120 seconds'}
       Step 'agent_verify' 'PASS' 'release manifest and update report agree'
     } else {Step 'agent_update' 'SKIPPED' 'installed release already matches'}
   }
   $report.success=$true
 }
} catch { Step 'pipeline' 'FAIL' $_.Exception.Message; $report.success=$false }
finally {
 $report.finished=(Get-Date).ToUniversalTime().ToString('o')
 $target=Join-Path $env:LOCALAPPDATA 'NEXORA/Hands/data/unified_release_report.json'
 $dir=Split-Path -Parent $target
 if(-not(Test-Path $dir)){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
 ($report|ConvertTo-Json -Depth 7)|Set-Content -LiteralPath $target -Encoding utf8
 Write-Host "REPORT $target"
}
if(-not $report.success){exit 1}
