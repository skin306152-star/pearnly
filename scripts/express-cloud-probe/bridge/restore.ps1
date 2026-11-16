# restore.ps1 —— 以 skin3 跑（收尾）：把测试机还原成 prep.ps1 快照时的样子。
# 结束我们起的 mstsc（只认 pid 文件和命令行里带 PearnlyCloudProbe 的，用户自己的 TSplus 会话不动）；删我们加的远程桌面凭据；
# 远程桌面客户端注册表整棵换回快照；Default.rdp 换回；撤掉 subst 盘。删 C:\PearnlyCloudProbe 与计划任务由 macadmin 那边做（先把日志拉回 Mac）。
param([string]$CloudHost = "198.51.100.7", [string]$Root = "C:\PearnlyCloudProbe")
$before = Join-Path $Root "logs\before"
if (-not (Test-Path -LiteralPath (Join-Path $before "taken.txt"))) { throw "no snapshot in $before; nothing to restore against" }
$report = [ordered]@{}

# 1) 我们起的 mstsc
$known = @((Get-Content -LiteralPath (Join-Path $before "mstsc-pids.txt") -Raw).Trim() -split "," | Where-Object { $_ })
$killed = @()
foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='mstsc.exe'" -ErrorAction SilentlyContinue)) {
  $id = "$($p.ProcessId)"
  if ($known -contains $id) { continue }
  $ours = ("$($p.CommandLine)" -match "PearnlyCloudProbe")
  foreach ($f in @("hidden-mstsc.pid", "visible-mstsc.pid")) {
    $pf = Join-Path $Root "logs\$f"
    if ((Test-Path -LiteralPath $pf) -and (Get-Content -LiteralPath $pf -Raw).Trim() -eq $id) { $ours = $true }
  }
  if ($ours) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue; $killed += $id }
}
$report.mstsc_killed = $killed
$report.mstsc_left = @(Get-CimInstance Win32_Process -Filter "Name='mstsc.exe'" -ErrorAction SilentlyContinue | ForEach-Object { "$($_.ProcessId)" })

# 2) 凭据：快照里没有这条才删
$had = [bool](Select-String -LiteralPath (Join-Path $before "cmdkey.txt") -Pattern ([regex]::Escape("TERMSRV/$CloudHost")) -Quiet)
if (-not $had) { cmdkey /delete:"TERMSRV/$CloudHost" | Out-Null }
$report.cred_present_after = [bool]((cmdkey /list) -match [regex]::Escape("TERMSRV/$CloudHost"))
$report.cred_present_before = $had

# 3) 远程桌面客户端注册表：整棵换回快照（含证书记录、SuppressWhenMinimized）
$tsc = "HKCU\Software\Microsoft\Terminal Server Client"
$existed = ((Get-Content -LiteralPath (Join-Path $before "tsc-existed.txt") -Raw).Trim() -eq "1")
reg delete $tsc /f 2>$null | Out-Null
if ($existed) { reg import (Join-Path $before "tsc.reg") 2>$null | Out-Null }
$now = Join-Path $Root "logs\tsc-after.reg"
$null = reg query $tsc 2>$null
if ($LASTEXITCODE -eq 0) { reg export $tsc $now /y | Out-Null; $report.tsc_same = ((Get-FileHash $now).Hash -eq (Get-FileHash (Join-Path $before "tsc.reg")).Hash) }
else { $report.tsc_same = (-not $existed) }

# 4) Default.rdp
$def = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Default.rdp"
$bdef = Join-Path $before "Default.rdp"
if (Test-Path -LiteralPath $bdef) { Copy-Item -LiteralPath $bdef -Destination $def -Force; $report.default_rdp = "restored" }
elseif (Test-Path -LiteralPath $def) { Remove-Item -LiteralPath $def -Force; $report.default_rdp = "removed (did not exist before)" }
else { $report.default_rdp = "absent before and after" }

# 5) subst 盘
$lf = Join-Path $Root "logs\drive.txt"
if (Test-Path -LiteralPath $lf) {
  $l = (Get-Content -LiteralPath $lf -Raw).Trim()
  if ($l -and -not (Select-String -LiteralPath (Join-Path $before "subst.txt") -Pattern "^${l}:" -Quiet)) { subst "${l}:" /d | Out-Null }
  $report.subst_after = @(subst)
}
$report | ConvertTo-Json -Depth 4
