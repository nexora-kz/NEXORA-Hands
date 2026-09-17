$ErrorActionPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host 'NEXORA Hands — stopping...' -ForegroundColor Yellow
$targets = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and ($_.CommandLine -like '*NEXORA Hands*hands.py*' -or $_.CommandLine -like '*NEXORA Hands*supabase_channel.py*') }
foreach ($p in $targets) {
    Write-Host "Stopping PID $($p.ProcessId)" -ForegroundColor Gray
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host 'NEXORA Hands stopped.' -ForegroundColor Green
