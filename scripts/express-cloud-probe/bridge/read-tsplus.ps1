# read-tsplus.ps1 —— 以 skin3 跑（T1 第 1 步）：用户双击 TSplus 客户端连云端时，读它拉起的 mstsc 的命令行和临时 .rdp，记下地址 / 端口 / 用户名形式。
# 密码一律不读：.rdp 里含 password / pwd / credential 的行只记「有这一行、多长」；用户名只记域名部分和长度。
param([int]$Seconds = 180, [string]$Root = "C:\PearnlyCloudProbe")
$ErrorActionPreference = "Stop"
$known = @()
$bf = Join-Path $Root "logs\before\mstsc-pids.txt"
if (Test-Path -LiteralPath $bf) { $known = @((Get-Content -LiteralPath $bf -Raw).Trim() -split "," | Where-Object { $_ }) }
foreach ($f in @("hidden-mstsc.pid", "visible-mstsc.pid")) { $pf = Join-Path $Root "logs\$f"; if (Test-Path -LiteralPath $pf) { $known += (Get-Content -LiteralPath $pf -Raw).Trim() } }

function Redact([string]$line) {
  if ($line -match "^(?<k>[^:]+):(?<t>[a-z]):(?<v>.*)$") {
    $k = $Matches.k; $v = $Matches.v
    if ($k -match "password|pwd|credential|secret|token") { return "${k}:$($Matches.t):<redacted, $($v.Length) chars>" }
    if ($k -match "^username$") {
      $dom = $(if ($v -match "^(?<d>[^\\@]+)\\") { $Matches.d + "\" } elseif ($v -match "@(?<d>.+)$") { "@" + $Matches.d } else { "" })
      return "${k}:s:<${dom}… $($v.Length) chars>"
    }
    return $line
  }
  return $line
}

$seen = @{}
$result = [ordered]@{ started = (Get-Date).ToString("s"); processes = @(); rdp_files = @() }
$deadline = (Get-Date).AddSeconds($Seconds)
$since = Get-Date
while ((Get-Date) -lt $deadline) {
  foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='mstsc.exe'" -ErrorAction SilentlyContinue)) {
    $id = "$($p.ProcessId)"
    if ($known -contains $id -or $seen.ContainsKey($id)) { continue }
    $seen[$id] = $true
    $cl = "$($p.CommandLine)"
    $parent = $(try { (Get-Process -Id $p.ParentProcessId -ErrorAction Stop).Path } catch { "" })
    $entry = [ordered]@{ pid = $id; command_line = ($cl -replace "(?i)(/p(?:assword)?:)\S+", '$1<redacted>'); parent = $parent }
    if ($cl -match '"?(?<f>[^"]+\.rdp)"?') {
      $rf = $Matches.f
      $entry.rdp = $rf
      if (Test-Path -LiteralPath $rf) { $entry.rdp_lines = @(Get-Content -LiteralPath $rf | ForEach-Object { Redact $_ }) }
    }
    $result.processes += $entry
  }
  if ($result.processes.Count -gt 0) {
    foreach ($rf in @(Get-ChildItem -LiteralPath $env:TEMP -Filter *.rdp -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -ge $since.AddMinutes(-10) })) {
      if (@($result.rdp_files | Where-Object { $_.path -eq $rf.FullName }).Count) { continue }
      $result.rdp_files += [ordered]@{ path = $rf.FullName; lines = @(Get-Content -LiteralPath $rf.FullName | ForEach-Object { Redact $_ }) }
    }
    if ((Get-Date) -gt $since.AddSeconds(20)) { break }
  }
  Start-Sleep -Milliseconds 500
}
$result | ConvertTo-Json -Depth 5 | Tee-Object -FilePath (Join-Path $Root "logs\t1-tsplus.json")
