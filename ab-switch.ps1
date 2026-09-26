param(
  [Parameter(Mandatory=$true)][ValidateSet('A','B')][string]$TargetSlot,
  [Parameter(Mandatory=$true)][string]$PythonPath,
  [int]$HealthTimeoutSeconds=90
)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$Data=Join-Path $Root 'data'
$Slots=Join-Path $Root 'slots'
$ActiveFile=Join-Path $Data 'active_slot.txt'
$ReportFile=Join-Path $Data 'ab_switch_report.json'
$TargetRoot=Join-Path $Slots $TargetSlot
if(-not(Test-Path (Join-Path $TargetRoot 'app\hands.py'))){throw "target slot missing: $TargetSlot"}
$old='LEGACY'
if(Test-Path $ActiveFile){$v=(Get-Content -Raw $ActiveFile).Trim().ToUpperInvariant();if($v -in @('A','B')){$old=$v}}
$oldRoot=if($old -in @('A','B')){Join-Path $Slots $old}else{$Root}
function Stop-Hands {
  Get-CimInstance Win32_Process|Where-Object {$_.CommandLine -and ($_.CommandLine -like '*NEXORA\Hands*hands.py*' -or $_.CommandLine -like '*NEXORA\Hands*supabase_channel.py*')}|ForEach-Object {Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}
  Start-Sleep -Milliseconds 800
}
function Start-Slot([string]$slotRoot){
  $start=Join-Path $slotRoot 'start.ps1'
  if(-not(Test-Path $start)){throw "start.ps1 missing: $slotRoot"}
  & $start -PythonPath $PythonPath
  if($LASTEXITCODE -ne 0){throw "start failed: $slotRoot"}
}
function Wait-Healthy {
  $statePath=Join-Path $Data 'supabase_channel_state.json'
  $deadline=(Get-Date).AddSeconds($HealthTimeoutSeconds)
  do{
    Start-Sleep -Seconds 1
    if(Test-Path $statePath){
      try{
        $s=Get-Content -Raw $statePath|ConvertFrom-Json
        $fresh=$false
        if($s.updated_at){try{$fresh=([DateTimeOffset]::UtcNow-[DateTimeOffset]::Parse([string]$s.updated_at)).TotalSeconds -lt 30}catch{}}
        if($s.heartbeat_ok -eq $true -and $s.executor_online -eq $true -and $s.queue_stalled -eq $false -and @($s.result_submit_errors.PSObject.Properties).Count -eq 0){return $true}
      }catch{}
    }
  }while((Get-Date)-lt $deadline)
  return $false
}
$report=[ordered]@{started_at=[DateTimeOffset]::UtcNow.ToString('o');from=$old;to=$TargetSlot;ok=$false;rolled_back=$false}
try{
  Stop-Hands
  [IO.File]::WriteAllText($ActiveFile,$TargetSlot,[Text.ASCIIEncoding]::new())
  Start-Slot $TargetRoot
  if(-not(Wait-Healthy)){throw 'target slot health timeout'}
  $self=Join-Path $TargetRoot 'self-test.ps1'
  if(Test-Path $self){& $self -PythonPath $PythonPath;if($LASTEXITCODE -ne 0){throw 'target slot self-test failed'}}
  $report.ok=$true
  $report.completed_at=[DateTimeOffset]::UtcNow.ToString('o')
}catch{
  $report.error=$_.Exception.Message
  $report.rolled_back=$true
  try{
    Stop-Hands
    if($old -in @('A','B')){[IO.File]::WriteAllText($ActiveFile,$old,[Text.ASCIIEncoding]::new())}else{Remove-Item $ActiveFile -Force -ErrorAction SilentlyContinue}
    Start-Slot $oldRoot
    $report.rollback_healthy=(Wait-Healthy)
  }catch{$report.rollback_error=$_.Exception.Message}
  $report.completed_at=[DateTimeOffset]::UtcNow.ToString('o')
  $report|ConvertTo-Json -Depth 8|Set-Content $ReportFile -Encoding UTF8
  exit 1
}
$report|ConvertTo-Json -Depth 8|Set-Content $ReportFile -Encoding UTF8
exit 0
