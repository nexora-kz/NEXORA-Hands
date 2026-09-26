param([string]$PythonPath = '')
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$Data=Join-Path $Root 'data'
$App=Join-Path $Root 'app'
$Previous=Join-Path $Root 'previous'
$Manifest=Join-Path $Data 'release_manifest.json'
$Report=Join-Path $Data 'self_test.json'
function Hash([string]$p){ if(Test-Path -LiteralPath $p){(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()}else{''}}
$checks=[ordered]@{}
try{
  $checks.python=($PythonPath -and (Test-Path -LiteralPath $PythonPath))
  $checks.hands=(Test-Path -LiteralPath (Join-Path $App 'hands.py'))
  $checks.channel=(Test-Path -LiteralPath (Join-Path $App 'supabase_channel.py'))
  $checks.config=(Test-Path -LiteralPath (Join-Path $App 'hands_supabase_config.json'))
  if($checks.python){ & $PythonPath -m py_compile (Join-Path $App 'hands.py') (Join-Path $App 'supabase_channel.py'); $checks.compile=($LASTEXITCODE -eq 0) } else {$checks.compile=$false}
  $statePath=Join-Path $Data 'supabase_channel_state.json'
  $deadline=(Get-Date).AddSeconds(90); $state=$null
  while((Get-Date)-lt $deadline){
    if(Test-Path $statePath){try{$state=Get-Content -Raw $statePath|ConvertFrom-Json}catch{}}
    if($state -and $state.heartbeat_ok -eq $true -and $state.executor_online -eq $true -and $state.queue_stalled -eq $false){break}
    Start-Sleep -Seconds 1
  }
  $checks.heartbeat=($state -and $state.heartbeat_ok -eq $true)
  $checks.executor=($state -and $state.executor_online -eq $true)
  $checks.queue=($state -and $state.queue_stalled -eq $false)
  $checks.result_submit=($state -and @($state.result_submit_errors.PSObject.Properties).Count -eq 0)
  $ok=(@($checks.Values|?{$_ -ne $true}).Count -eq 0)
  [ordered]@{ok=$ok;checked_at=[DateTimeOffset]::UtcNow.ToString('o');checks=$checks;manifest=(if(Test-Path $Manifest){Get-Content -Raw $Manifest|ConvertFrom-Json}else{$null})}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $Report -Encoding UTF8
  if(-not $ok){exit 1}; exit 0
}catch{
  [ordered]@{ok=$false;checked_at=[DateTimeOffset]::UtcNow.ToString('o');checks=$checks;error=$_.Exception.Message}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $Report -Encoding UTF8
  exit 1
}
