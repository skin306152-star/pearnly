# 测试机 hosts 加一行「<Mac 局域网地址> erp.maclab.localhost cowork.maclab.localhost」（管理员 ssh 会话里跑，不走桌面用户通道）。
# 本机 scripts/acceptance/maclab_pc.py prepare 先读回 hosts 判定要不要加（已有同一行不动、同名字不同地址停下），这里再核一遍：
# 有任何未注释的行已带这两个名字就退出码 3、不改。改前整份备份到 -BackupDir\hosts-backup-<时间>；只追加这一行，别的行不动。
# 末行 HOSTS_OK，前面打印 hosts 里带 maclab.localhost 的行与备份路径。
param([Parameter(Mandatory=$true)][string]$Address, [string]$BackupDir = "C:\PearnlyAcceptance\maclab")
$ErrorActionPreference = "Stop"
if ($Address -notmatch '^\d{1,3}(\.\d{1,3}){3}$') { "BAD_ADDRESS"; exit 2 }
$names = @("erp.maclab.localhost", "cowork.maclab.localhost")
$hosts = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$lines = @(Get-Content -LiteralPath $hosts)
foreach ($line in $lines) {
  $body = ($line -split '#', 2)[0]
  $tokens = @($body -split '\s+' | Where-Object { $_ })
  foreach ($name in $names) { if ($tokens.Count -gt 1 -and ($tokens[1..($tokens.Count - 1)] -contains $name)) { "ALREADY: $line"; exit 3 } }
}
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$backup = Join-Path $BackupDir ("hosts-backup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
Copy-Item -LiteralPath $hosts -Destination $backup
$text = [IO.File]::ReadAllText($hosts)
$lead = if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { "`r`n" } else { "" }
[IO.File]::AppendAllText($hosts, $lead + "$Address " + ($names -join " ") + "`r`n", [Text.Encoding]::ASCII)
"BACKUP $backup"
Get-Content -LiteralPath $hosts | Where-Object { $_ -match 'maclab\.localhost' } | ForEach-Object { "LINE $_" }
"HOSTS_OK"
