param(
  [Parameter(Mandatory=$true)][string]$ReleaseSha,
  [Parameter(Mandatory=$true)][string]$PythonPath,
  [switch]$Activate
)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$Data=Join-Path $Root 'data'
$Slots=Join-Path $Root 'slots'
$ActiveFile=Join-Path $Data 'active_slot.txt'
$ManifestFile=Join-Path $Data 'slot_manifest.json'
New-Item -ItemType Directory -Force -Path $Data,$Slots | Out-Null
$active='A'
if(Test-Path $ActiveFile){$v=(Get-Content -Raw $ActiveFile).Trim().ToUpperInvariant();if($v -in @('A','B')){$active=$v}}
$target=if($active -eq 'A'){'B'}else{'A'}
$targetRoot=Join-Path $Slots $target
$stage=Join-Path $Slots ($target+'.stage')
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'app') | Out-Null
$base="https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/$ReleaseSha"
$rels=@('start.ps1','stop.ps1','self-test.ps1','app/hands.py','app/supabase_channel.py','app/hands_supabase_config.json')
$hashes=[ordered]@{}
foreach($rel in $rels){
  $dst=Join-Path $stage ($rel -replace '/','\')
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst)|Out-Null
  Invoke-WebRequest -UseBasicParsing -Uri "$base/$rel" -OutFile $dst
  if((Get-Item $dst).Length -lt 100){throw "download too small: $rel"}
  $hashes[$rel]=(Get-FileHash $dst -Algorithm SHA256).Hash.ToLowerInvariant()
}
& $PythonPath -m py_compile (Join-Path $stage 'app\hands.py') (Join-Path $stage 'app\supabase_channel.py')
if($LASTEXITCODE -ne 0){throw 'slot compile failed'}
$tokens=$null;$errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $stage 'start.ps1'),[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count){throw 'slot start.ps1 parse failed'}
$tokens=$null;$errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $stage 'self-test.ps1'),[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count){throw 'slot self-test.ps1 parse failed'}
Remove-Item $targetRoot -Recurse -Force -ErrorAction SilentlyContinue
Move-Item $stage $targetRoot
$manifest=[ordered]@{
  prepared_at=[DateTimeOffset]::UtcNow.ToString('o')
  release_sha=$ReleaseSha
  active_slot=$active
  prepared_slot=$target
  activated=$false
  hashes=$hashes
}
if($Activate){
  [IO.File]::WriteAllText($ActiveFile,$target,[Text.ASCIIEncoding]::new())
  $manifest.active_slot=$target
  $manifest.activated=$true
}
$manifest|ConvertTo-Json -Depth 10|Set-Content $ManifestFile -Encoding UTF8
$manifest|ConvertTo-Json -Depth 10
