# prep.ps1 —— 以 skin3 跑（run-as-skin3.ps1）：记下测试机现状（收尾按它原样还原），把隧道文件夹 subst 成盘，写 .rdp，备好 100 MB 测速文件。
# 只动 C:\PearnlyCloudProbe 和 subst 盘；不碰 CC、不碰 C 盘 / S 盘账套、不改远程桌面设置（只导出）。
param([string]$CloudHost = "198.51.100.7", [int]$Port = 3390, [string]$CloudUser = "CLOUDUSER-01", [string]$CloudDomain = "example", [string]$Root = "C:\PearnlyCloudProbe")
$ErrorActionPreference = "Stop"
$tunnel = Join-Path $Root "tunnel"
$before = Join-Path $Root "logs\before"
foreach ($d in @("tunnel\out", "tunnel\ctl", "tunnel\dist", "logs\before", "rdp")) { New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null }

# 1) 现状快照（已有快照就不覆盖：重跑 prep 不能把我们自己改过的状态当成「原状」）
if (-not (Test-Path -LiteralPath (Join-Path $before "taken.txt"))) {
  cmdkey /list | Out-File -LiteralPath (Join-Path $before "cmdkey.txt") -Encoding UTF8
  $tsc = "HKCU\Software\Microsoft\Terminal Server Client"
  $null = reg query $tsc 2>$null
  if ($LASTEXITCODE -eq 0) { reg export $tsc (Join-Path $before "tsc.reg") /y | Out-Null; "1" | Out-File (Join-Path $before "tsc-existed.txt") }
  else { "0" | Out-File (Join-Path $before "tsc-existed.txt") }
  subst | Out-File -LiteralPath (Join-Path $before "subst.txt") -Encoding UTF8
  $def = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Default.rdp"
  if (Test-Path -LiteralPath $def) { Copy-Item -LiteralPath $def -Destination (Join-Path $before "Default.rdp") -Force }
  @(Get-Process -Name mstsc -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }) -join "," | Out-File (Join-Path $before "mstsc-pids.txt")
  (Get-Date).ToString("s") | Out-File (Join-Path $before "taken.txt")
}

# 2) 隧道盘：优先 X，被占就往前找空闲盘符
$letterFile = Join-Path $Root "logs\drive.txt"
$letter = $(if (Test-Path -LiteralPath $letterFile) { (Get-Content -LiteralPath $letterFile -Raw).Trim() } else { "" })
if (-not $letter) {
  $used = @([IO.DriveInfo]::GetDrives() | ForEach-Object { $_.Name.Substring(0, 1).ToUpper() })
  foreach ($c in @("X", "W", "V", "U", "T", "R", "Q", "O", "N", "M", "L", "K")) { if ($used -notcontains $c) { $letter = $c; break } }
  if (-not $letter) { throw "no free drive letter" }
  $letter | Out-File -LiteralPath $letterFile -Encoding ascii
}
if (-not (Test-Path -LiteralPath "${letter}:\")) { subst "${letter}:" $tunnel; if ($LASTEXITCODE -ne 0) { throw "subst failed" } }

# 3) .rdp：vis（T1 可见、用户本人输密码）、hidden（隐藏桌面用，同内容）、alt（登录即跑探针）、c（只在 subst 盘映射不过时备用）
function Rdp([string]$file, [string]$drives, [string]$tunnelUnc, [bool]$alt) {
  $lines = @(
    "full address:s:$CloudHost", "server port:i:$Port", "username:s:$CloudUser", "domain:s:$CloudDomain", "drivestoredirect:s:$drives", "redirectclipboard:i:0", "redirectprinters:i:0",
    "redirectcomports:i:0", "redirectsmartcards:i:0", "redirectwebauthn:i:0", "devicestoredirect:s:", "camerastoredirect:s:",
    "audiomode:i:2", "audiocapturemode:i:0", "keyboardhook:i:1", "screen mode id:i:1", "desktopwidth:i:1280", "desktopheight:i:800",
    "session bpp:i:16", "smart sizing:i:0", "autoreconnection enabled:i:1", "prompt for credentials:i:0", "promptcredentialonce:i:1",
    "authentication level:i:0", "enablecredsspsupport:i:1", "displayconnectionbar:i:1"
  )
  if ($alt) {
    $wait = "for(`$i=0;`$i -lt 30 -and -not(Test-Path '$tunnelUnc\probe.ps1');`$i++){sleep 2};"
    $run = "&([scriptblock]::Create([IO.File]::ReadAllText('$tunnelUnc\probe.ps1'))) -Tunnel '$tunnelUnc'"
    $lines += "alternate shell:s:powershell.exe -NoProfile -WindowStyle Hidden -Command `"$wait $run`""
    $lines += "shell working directory:s:"
  }
  # mstsc 认 UTF-16LE（带 BOM）的 .rdp
  [IO.File]::WriteAllLines((Join-Path $Root "rdp\$file"), $lines, (New-Object Text.UnicodeEncoding($false, $true)))
}
$unc = "\\tsclient\$letter"
Rdp "probe-vis.rdp" "${letter}:\;" $unc $false
Rdp "probe-hidden.rdp" "${letter}:\;" $unc $false
Rdp "probe-alt.rdp" "${letter}:\;" $unc $true
Rdp "probe-c.rdp" "C:\;" "\\tsclient\C\PearnlyCloudProbe\tunnel" $false
Rdp "probe-c-alt.rdp" "C:\;" "\\tsclient\C\PearnlyCloudProbe\tunnel" $true

# 4) 100 MB 随机数（T5 拷贝测速）
$blob = Join-Path $tunnel "dist\blob.bin"
if (-not (Test-Path -LiteralPath $blob) -or (Get-Item -LiteralPath $blob).Length -ne 100MB) {
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $buf = New-Object byte[] (1MB)
  $fs = [IO.File]::Create($blob); try { for ($i = 0; $i -lt 100; $i++) { $rng.GetBytes($buf); $fs.Write($buf, 0, $buf.Length) } } finally { $fs.Close() }
}

# 5) 用户的 TSplus 客户端在哪（只列，不动）
$tsplus = @()
foreach ($d in @([Environment]::GetFolderPath("Desktop"), (Join-Path $env:USERPROFILE "Downloads"), [Environment]::GetFolderPath("CommonDesktopDirectory"))) {
  if ($d -and (Test-Path -LiteralPath $d)) {
    $tsplus += @(Get-ChildItem -LiteralPath $d -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "W10E|Host25|tsplus|Express.*Cloud" } | ForEach-Object { $_.FullName })
  }
}

[ordered]@{
  drive = $letter; tunnel_unc = $unc; tunnel_visible = (Test-Path -LiteralPath "${letter}:\probe.ps1")
  blob_bytes = (Get-Item -LiteralPath $blob).Length; tsplus_client = $tsplus
  before_taken = (Get-Content -LiteralPath (Join-Path $before "taken.txt") -Raw).Trim()
  cmdkey_had_target = [bool](Select-String -LiteralPath (Join-Path $before "cmdkey.txt") -Pattern ([regex]::Escape("TERMSRV/$CloudHost")) -Quiet)
} | ConvertTo-Json
