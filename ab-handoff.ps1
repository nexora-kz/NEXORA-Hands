param(
  [Parameter(Mandatory=$true)][ValidateSet('A','B')][string]$TargetSlot,
  [Parameter(Mandatory=$true)][string]$PythonPath,
  [int]$HealthTimeoutSeconds=90
)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$Data=Join-Path $Root 'data'
$Pending=Join-Path $Data 'pending_switch.json'
$Runner=Join-Path $Root 'ab-switch.ps1'
if(-not(Test-Path $Runner)){throw 'ab-switch.ps1 missing'}
$payload=[ordered]@{target_slot=$TargetSlot;requested_at=[DateTimeOffset]::UtcNow.ToString('o');status='pending'}
$payload|ConvertTo-Json|Set-Content $Pending -Encoding UTF8
$args=@('-NoProfile','-ExecutionPolicy','RemoteSigned','-File',$Runner,'-TargetSlot',$TargetSlot,'-PythonPath',$PythonPath,'-HealthTimeoutSeconds',"$HealthTimeoutSeconds")
$p=Start-Process -FilePath 'powershell.exe' -ArgumentList $args -WindowStyle Hidden -PassThru
[ordered]@{ok=$true;handoff_pid=$p.Id;target_slot=$TargetSlot;pending_file=$Pending}|ConvertTo-Json
exit 0
