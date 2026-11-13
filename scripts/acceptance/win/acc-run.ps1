# 验收体系：以桌面用户（-User）的一次性计划任务跑 acc-job.ps1，等它结束，删任务并回读。
# 为什么：ssh 进测试机的账号读不到账套服务器（\\accserver），只有已登录桌面的那个用户有凭据（11-13 测试机2 实测）。
# 只打印一行状态；脚本输出在 <Work>\jobs\<Name>.out（UTF-16），由调用方取回。
param([string]$Work,[string]$Name,[string]$User,[string]$Python,[string]$Script,[int]$Wait=1800)
$dir="$Work\jobs"; $out="$dir\$Name.out"; $task="PearnlyAcc-$Name"
Remove-Item "$out","$out.done" -Force -ErrorAction SilentlyContinue
$act=New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Work\acc-job.ps1`" -Python `"$Python`" -Script `"$Script`" -ArgsFile `"$dir\$Name.args`" -Out `"$out`""
$pr=New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$st=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $task -Action $act -Principal $pr -Settings $st -Force | Out-Null
Start-ScheduledTask -TaskName $task
$t=0; while($t -lt $Wait -and -not (Test-Path "$out.done")){Start-Sleep 2;$t+=2}
Unregister-ScheduledTask -TaskName $task -Confirm:$false
$left=[bool](Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue)
$code=if(Test-Path "$out.done"){(Get-Content "$out.done" | Select-Object -First 1)}else{"timeout"}
"ACCJOB $code waited=$t task_left=$left"
