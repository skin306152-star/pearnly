# lab_task.ps1 -Python <python.exe> -Out <输出目录> [-User aksar] [-Args "..."]：在桌面已登录用户的交互会话里跑一次 lab_run.py（隐藏窗口），
# 跑完任务自删。只借用户已登录的会话，不存任何密码；Express 要先由用户本人打开并进试点公司。
param([Parameter(Mandatory = $true)][string]$Python, [Parameter(Mandatory = $true)][string]$Out,
      [string]$User = "aksar", [string]$Work = "C:\XpPilot", [string]$Extra = "")
$name = "PearnlyOperatorLab"
New-Item -ItemType Directory -Force $Out | Out-Null
if (-not $Extra -and (Test-Path "$Work\lab\extra.txt")) { $Extra = (Get-Content "$Work\lab\extra.txt" -Raw).Trim() }  # 经 SSH 传带空格的参数会被拆开：放进文件
$argsLine = "/c `"`"$Python`" `"$Work\lab\lab_run.py`" --src `"$Work\companion_src`" --drafts `"$Work\lab\drafts.jsonl`" --out `"$Out`" $Extra > `"$Out\stderr.txt`" 2>&1`""
Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argsLine -WorkingDirectory "$Work\lab"
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $name
"started $name -> $Out"
