# run-as-session-user.ps1 <程序> <参数> <等待秒数>：用计划任务以已登录用户（这里是 skin3，Interactive、Limited）身份跑一次，等结束后删任务。不存任何密码：只借用户已登录的交互会话。任务名每个使用方各用各的，别和别人共用（会互相注销）。
param([string]$Exe, [string]$Arguments = "", [int]$Wait = 300)
$name = "PearnlyAccSkin3"
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute $Exe -Argument $Arguments
$principal = New-ScheduledTaskPrincipal -UserId "skin3" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name
$deadline = (Get-Date).AddSeconds($Wait)
Start-Sleep 3
while ((Get-Date) -lt $deadline) {
  $info = Get-ScheduledTask -TaskName $name
  if ($info.State -ne "Running" -and $info.State -ne "Queued") { break }
  Start-Sleep 2
}
$result = (Get-ScheduledTaskInfo -TaskName $name).LastTaskResult
Unregister-ScheduledTask -TaskName $name -Confirm:$false
"task result $result"
