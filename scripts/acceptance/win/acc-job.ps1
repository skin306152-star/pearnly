# 验收体系：在桌面用户的会话里跑一次 Python 脚本（账套工具等）。由 acc-run.ps1 注册的一次性计划任务调用，不单独用。
# 参数一行一个放在 -ArgsFile（UTF-8）；脚本的全部输出写 -Out（UTF-16），结束后把退出码写进 <Out>.done。
param([string]$Python,[string]$Script,[string]$ArgsFile,[string]$Out)
$a = @(Get-Content -LiteralPath $ArgsFile -Encoding UTF8)
& $Python $Script @a *> $Out
"exit $LASTEXITCODE" | Out-File -LiteralPath "$Out.done" -Encoding ascii
