# start-agent.ps1 -Pythonw <路径> [-User acct] [-Work C:\XpPilot]：以桌面已登录用户的交互会话（Interactive、Limited）隐藏起执行代理，只听 127.0.0.1:18765。
# 每次起都换一个新令牌（<Work>\agent.token），本机执行器经 ssh 读它。任务名只给试点用（PearnlyXpPilotAgent）。
# 不存任何密码：只借用户已登录的会话。停：stop-agent.ps1。
param([Parameter(Mandatory = $true)][string]$Pythonw, [string]$User = "acct", [string]$Work = "C:\XpPilot")
$name = "PearnlyXpPilotAgent"
[guid]::NewGuid().ToString("N") | Set-Content "$Work\agent.token" -Encoding ascii
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m pilot.agent_win" -WorkingDirectory $Work
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 10)
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name
Start-Sleep 3
$state = (Get-ScheduledTask -TaskName $name).State
$listening = Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue
"task $state listening=$([bool]$listening)"
