# start-agent.ps1 <pythonw 路径>：以已登录用户 skin3 的交互会话（Interactive、Limited）隐藏起执行代理，只听 127.0.0.1:18765。
# 每次起都换一个新令牌（agent.token），本机执行器经 ssh 读它。任务名只给试点用（PearnlyXpPilotAgent），不和别人的任务共用。
# 不存任何密码：只借用户已登录的会话。停：stop-agent.ps1。
param([Parameter(Mandatory = $true)][string]$Pythonw)
$work = "C:\PearnlyStress\_xp_pilot"
$name = "PearnlyXpPilotAgent"
[guid]::NewGuid().ToString("N") | Set-Content "$work\agent.token" -Encoding ascii
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m pilot.agent_win" -WorkingDirectory $work
$principal = New-ScheduledTaskPrincipal -UserId "skin3" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 10)
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name
Start-Sleep 3
$state = (Get-ScheduledTask -TaskName $name).State
$listening = Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue
"task $state listening=$([bool]$listening)"
