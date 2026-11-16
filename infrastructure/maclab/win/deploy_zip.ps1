# 把本机打好的 zip 展开到测试机上的一个目录（整目录换新）：maclab 的第三方包目录、CC 源码副本 ccsrc\companion。
# 本机 scripts/acceptance/maclab_pc.py prepare 经 ssh 调用（避开 ssh → cmd → PowerShell 的多层引号）。只认 C:\PearnlyAcceptance 下的目标。
# 末行 DEPLOY_OK <文件数>；zip 用完删掉。
param([Parameter(Mandatory=$true)][string]$Zip, [Parameter(Mandatory=$true)][string]$Dest)
$ErrorActionPreference = "Stop"
$full = [IO.Path]::GetFullPath($Dest)
if (-not $full.StartsWith("C:\PearnlyAcceptance\", [StringComparison]::OrdinalIgnoreCase)) { "REFUSED $full"; exit 2 }
if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force }
New-Item -ItemType Directory -Force -Path $full | Out-Null
Expand-Archive -LiteralPath $Zip -DestinationPath $full -Force
Remove-Item -LiteralPath $Zip -Force
"DEPLOY_OK " + @(Get-ChildItem -LiteralPath $full -Recurse -File).Count
