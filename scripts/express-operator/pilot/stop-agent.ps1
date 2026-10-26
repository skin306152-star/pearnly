# stop-agent.ps1 [-Work C:\XpPilot]：停执行代理、删计划任务和令牌。不动 Express、不动 CC。
param([string]$Work = "C:\XpPilot")
$name = "PearnlyXpPilotAgent"
Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item "$Work\agent.token" -ErrorAction SilentlyContinue
Start-Sleep 2
$left = Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue
"stopped listening=$([bool]$left)"
