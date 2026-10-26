# stop-agent.ps1：停执行代理、删计划任务和令牌。不动 Express、不动 CC。
$work = "C:\PearnlyStress\_xp_pilot"
$name = "PearnlyXpPilotAgent"
Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item "$work\agent.token" -ErrorAction SilentlyContinue
$left = Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue
"stopped listening=$([bool]$left)"
