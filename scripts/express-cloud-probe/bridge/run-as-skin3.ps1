# run-as-skin3.ps1 —— 由 macadmin（SSH）调用：用一次性计划任务以 skin3（Interactive、Limited、允许电池）跑一个桥脚本，等它结束后删任务，打印它的输出。
# 不存任何密码，只借 skin3 已登录的交互会话。任务名一律 PearnlyCloudProbe-*，收尾按前缀清点。
#   powershell -File run-as-skin3.ps1 -Script C:\PearnlyCloudProbe\bridge\prep.ps1 -Arguments "-Port 3390" -Wait 120
#   -Wait 0：只启动不等（长跑的 monitor 用），任务留着、收尾删。
param([Parameter(Mandatory = $true)][string]$Script, [string]$Arguments = "", [int]$Wait = 120, [string]$Name = "")
$ErrorActionPreference = "Stop"
$root = "C:\PearnlyCloudProbe"
if (-not $Name) { $Name = [IO.Path]::GetFileNameWithoutExtension($Script) }
$task = "PearnlyCloudProbe-$Name"
$outFile = Join-Path $root "logs\$Name.out"
Remove-Item -LiteralPath $outFile -ErrorAction SilentlyContinue
$cmd = "& '$Script' $Arguments *> '$outFile'"
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand $enc"
$principal = New-ScheduledTaskPrincipal -UserId "skin3" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $task
if ($Wait -le 0) { "started $task (left registered; cleanup removes PearnlyCloudProbe-*)"; return }
$deadline = (Get-Date).AddSeconds($Wait)
Start-Sleep 2
while ((Get-Date) -lt $deadline) {
  $st = (Get-ScheduledTask -TaskName $task).State
  if ($st -ne "Running" -and $st -ne "Queued") { break }
  Start-Sleep 1
}
$st = (Get-ScheduledTask -TaskName $task).State
$res = (Get-ScheduledTaskInfo -TaskName $task).LastTaskResult
Unregister-ScheduledTask -TaskName $task -Confirm:$false
"task $task state=$st result=$res"
if (Test-Path -LiteralPath $outFile) { [Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-Content -LiteralPath $outFile }
