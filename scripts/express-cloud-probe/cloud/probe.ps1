# probe.ps1 —— 云端 Express 只读实测探针（T2 / T4 / T5 / T6 / T3 第 9 步），在云端会话里运行（Windows Server 2019，PowerShell 5.1）。
#
# 只读：不碰 Express 数据根（只读 secure\SCCOMP.DBF 的公司数由 ccprobe 做），只写两处：
#   ① 隧道 <Tunnel>\out\（结果，桥那边读）；② 云端用户自己的 %LOCALAPPDATA%\PearnlyProbe（拷贝与运行，收到 stop 时删掉）。
# 不写注册表、不写启动文件夹（只看权限）、不装任何东西。
#
# 启动（绕开执行策略，不依赖 $PSScriptRoot）：
#   powershell -nop -w hidden -c "&([scriptblock]::Create([IO.File]::ReadAllText('\\tsclient\X\probe.ps1'))) -Tunnel '\\tsclient\X'"
#
# 控制文件（桥放进 <Tunnel>\ctl\，探针每 5 秒看一次，处理完删掉；stop 除外）：
#   stop           关记事本 / 我们开的 Express / ccprobe，删本地目录，写 out\stopped.json，然后 logoff（注销本会话，不留断开的会话）
#   t6             起子进程跑 T6（拷贝计时 + 正式 CC 启动一次 + CDX 索引程序 + ccprobe 60 分钟）
#   express-open   打开云端 Express 到登录窗口（只到登录框），记窗口标题与截图
#   express-close  关掉上一步打开的 Express
#   shot           立刻截一轮图
param(
  [string]$Tunnel = "",
  [string]$Job = "",            # 空 = 主探针；"t6" = T6 子进程
  [int]$HeartbeatSeconds = 30,
  [int]$ShotSeconds = 300,
  [string]$DataRoot = "D:\CLOUDUSER\ExpressI-Cloud",
  [int]$CcprobeMinutes = 60
)

$ErrorActionPreference = "Stop"
if (-not $Tunnel) { throw "need -Tunnel" }
$Out = Join-Path $Tunnel "out"
$Ctl = Join-Path $Tunnel "ctl"
$Dist = Join-Path $Tunnel "dist"
$Local = Join-Path $env:LOCALAPPDATA "PearnlyProbe"
New-Item -ItemType Directory -Force -Path $Local | Out-Null

# 隧道在会话刚登录时可能还没映射好：最多等 60 秒
for ($i = 0; $i -lt 30 -and -not (Test-Path -LiteralPath $Tunnel); $i++) { Start-Sleep 2 }
foreach ($d in @($Out, $Ctl)) { if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null } }

function Now-Utc { [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ") }

function Write-Json([string]$name, $obj) {
  $text = $obj | ConvertTo-Json -Depth 6 -Compress
  # 本地留一份（隧道断了也能事后看到缺口），再写隧道（先 .part 后改名，桥不会读到半个文件）
  try { Add-Content -LiteralPath (Join-Path $Local "probe.log") -Value "$name $text" -Encoding UTF8 } catch {}
  try {
    $tmp = Join-Path $Out ($name + ".part")
    [IO.File]::WriteAllText($tmp, $text, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination (Join-Path $Out $name) -Force
    return $true
  } catch { return $false }
}

function Log-Error([string]$where, $err) {
  Write-Json ("err-" + (Now-Utc) + "-" + $where + ".json") @{ where = $where; error = "$err" } | Out-Null
}

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Runtime.InteropServices;
public static class PearnlyProbeNative {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO i);
  public static uint IdleMs() {
    var i = new LASTINPUTINFO(); i.cbSize = (uint)Marshal.SizeOf(i);
    if (!GetLastInputInfo(ref i)) return 0;
    return unchecked((uint)Environment.TickCount - i.dwTime);
  }
  // 截一个窗口（PW_RENDERFULLCONTENT=2）；拿不到返回 null
  public static Bitmap Window(IntPtr h) {
    RECT r; if (h == IntPtr.Zero || !GetWindowRect(h, out r)) return null;
    int w = r.R - r.L, ht = r.B - r.T; if (w <= 0 || ht <= 0) return null;
    var bmp = new Bitmap(w, ht);
    using (var g = Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc();
      bool ok = PrintWindow(h, dc, 2);
      g.ReleaseHdc(dc);
      if (!ok) { bmp.Dispose(); return null; }
    }
    return bmp;
  }
  // 整个会话屏幕（会话不在画时这里通常是整片黑）
  public static Bitmap Screen(int w, int h) {
    var bmp = new Bitmap(w, h);
    using (var g = Graphics.FromImage(bmp)) { g.CopyFromScreen(0, 0, 0, 0, new Size(w, h)); }
    return bmp;
  }
  // 抽样 400 个点：全部同色 = 整片一色
  public static bool Uniform(Bitmap b) {
    if (b == null) return true;
    int first = b.GetPixel(0, 0).ToArgb();
    for (int y = 0; y < 20; y++) for (int x = 0; x < 20; x++) {
      if (b.GetPixel(x * (b.Width - 1) / 19, y * (b.Height - 1) / 19).ToArgb() != first) return false;
    }
    return true;
  }
}
"@

$Me = [Security.Principal.WindowsIdentity]::GetCurrent()
$SessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId

function Session-Lines {
  # 只留自己账号的行（多用户主机上别人的用户名不记）
  try {
    $user = $env:USERNAME
    @(& qwinsta 2>$null | Where-Object { $_ -match [regex]::Escape($user) } | ForEach-Object { $_.Trim() })
  } catch { @("qwinsta failed: $_") }
}

function Client-Name {
  try { (Get-ItemProperty -LiteralPath "HKCU:\Volatile Environment\$SessionId" -Name CLIENTNAME -ErrorAction Stop).CLIENTNAME } catch { $env:CLIENTNAME }
}

function Write-Acl-Verdict([string]$path) {
  # 只看 ACL，不真写：deny / allow / none / missing / unreadable
  if (-not $path -or -not (Test-Path -LiteralPath $path)) { return "missing" }
  try { $acl = Get-Acl -LiteralPath $path -ErrorAction Stop } catch { return "unreadable" }
  $sids = @($Me.User.Value) + @($Me.Groups | ForEach-Object { $_.Value })
  $want = [int]([Security.AccessControl.FileSystemRights]::CreateFiles -bor [Security.AccessControl.FileSystemRights]::WriteData)
  $allow = $false; $deny = $false
  foreach ($r in $acl.Access) {
    try { $sid = $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { continue }
    if ($sids -notcontains $sid) { continue }
    if (([int]$r.FileSystemRights -band $want) -eq 0) { continue }
    if ($r.AccessControlType -eq "Deny") { $deny = $true } else { $allow = $true }
  }
  if ($deny) { "deny" } elseif ($allow) { "allow" } else { "none" }
}

function Find-Express {
  $direct = Join-Path $DataRoot "ExpressI.exe"
  if (Test-Path -LiteralPath $direct) { return $direct }
  try {
    $shell = New-Object -ComObject WScript.Shell
    $dirs = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("CommonDesktopDirectory"))
    foreach ($d in $dirs) {
      if (-not $d -or -not (Test-Path -LiteralPath $d)) { continue }
      foreach ($lnk in Get-ChildItem -LiteralPath $d -Filter *.lnk -ErrorAction SilentlyContinue) {
        $t = $shell.CreateShortcut($lnk.FullName).TargetPath
        if ($t -and $t -match "Express[^\\]*\.exe$") { return $t }
      }
    }
  } catch {}
  return ""
}

# ───────────────────────── T6 子进程 ─────────────────────────
if ($Job -eq "t6") {
  $r = [ordered]@{ started = (Now-Utc); local = $Local; free_bytes = $null; copies = @{}; cc = @{}; runner = @(); ccprobe = @{} }
  try { $r.free_bytes = (Get-PSDrive -Name ($Local.Substring(0, 1))).Free } catch {}
  function Timed-Copy([string]$src, [string]$dst) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    if ((Get-Item -LiteralPath $src).PSIsContainer) { Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force }
    else { Copy-Item -LiteralPath $src -Destination $dst -Force }
    $sw.Stop()
    $bytes = (Get-ChildItem -LiteralPath $dst -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    if (-not $bytes) { $bytes = (Get-Item -LiteralPath $dst).Length }
    @{ seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1); bytes = $bytes }
  }
  try {
    # 1) T5 下半：100 MB 拷贝计时（量完就删，省云端空间）
    $r.copies.blob = Timed-Copy (Join-Path $Dist "blob.bin") (Join-Path $Local "blob.bin")
    Remove-Item -LiteralPath (Join-Path $Local "blob.bin") -Force
    Write-Json "t6-progress-blob.json" $r | Out-Null
    # 2) ccprobe（同一套 PyInstaller）与正式 CC 文件夹、CDX 索引程序、样例 DBF
    $r.copies.ccprobe = Timed-Copy (Join-Path $Dist "ccprobe") (Join-Path $Local "ccprobe")
    if (Test-Path -LiteralPath (Join-Path $Dist "cc")) {
      if ($r.free_bytes -and $r.free_bytes -lt 2GB) { $r.copies.cc = @{ skipped = "free space below 2 GB" } }
      else { $r.copies.cc = Timed-Copy (Join-Path $Dist "cc") (Join-Path $Local "cc") }
    }
    $r.copies.runner = Timed-Copy (Join-Path $Dist "cdx_reindex_runner.exe") (Join-Path $Local "cdx_reindex_runner.exe")
    New-Item -ItemType Directory -Force -Path (Join-Path $Local "dbf") | Out-Null
    Copy-Item -LiteralPath (Join-Path $Dist "probe-dbf\PROBE.DBF") -Destination (Join-Path $Local "dbf\PROBE.DBF") -Force
    Write-Json "t6-progress-copies.json" $r | Out-Null

    $hash = @{}
    foreach ($f in @("ccprobe\ccprobe.exe", "cc\companion.exe", "cdx_reindex_runner.exe")) {
      $p = Join-Path $Local $f
      if (Test-Path -LiteralPath $p) { $hash[$f] = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash }
    }
    $r.hash_before = $hash

    # 3) 正式 CC 启动一次：配置、日志全部重定向到探针目录；没配对 → 预期很快以 2 退出、不联网、不碰账套
    $ccExe = Join-Path $Local "cc\companion.exe"
    if (Test-Path -LiteralPath $ccExe) {
      $home2 = Join-Path $Local "cc-home"
      foreach ($d in @("appdata", "localappdata", "tmp")) { New-Item -ItemType Directory -Force -Path (Join-Path $home2 $d) | Out-Null }
      $psi = New-Object Diagnostics.ProcessStartInfo
      $psi.FileName = $ccExe; $psi.Arguments = "--headless"; $psi.UseShellExecute = $false
      $psi.WorkingDirectory = Split-Path $ccExe
      $psi.EnvironmentVariables["APPDATA"] = (Join-Path $home2 "appdata")
      $psi.EnvironmentVariables["LOCALAPPDATA"] = (Join-Path $home2 "localappdata")
      $psi.EnvironmentVariables["TEMP"] = (Join-Path $home2 "tmp"); $psi.EnvironmentVariables["TMP"] = (Join-Path $home2 "tmp")
      $sw = [Diagnostics.Stopwatch]::StartNew()
      $p = [Diagnostics.Process]::Start($psi)
      $done = $p.WaitForExit(180000)
      if (-not $done) { try { $p.Kill() } catch {} }
      $r.cc = @{ exited = $done; exit_code = $(if ($done) { $p.ExitCode } else { $null }); seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) }
      $log = Join-Path $home2 "appdata\Pearnly\logs\companion.log"
      if (Test-Path -LiteralPath $log) { $r.cc.log_tail = @(Get-Content -LiteralPath $log -Tail 20 -Encoding UTF8) }
    }
    Write-Json "t6-progress-cc.json" $r | Out-Null

    # 4) CDX 索引程序：只对探针目录里的样例表 PROBE.DBF（TAGS 只列索引），再对不存在的表跑一次（预期 error=…）
    $runner = Join-Path $Local "cdx_reindex_runner.exe"
    foreach ($t in @("PROBE", "NOPE")) {
      $logp = Join-Path $Local ("dbf\runner-" + $t + ".log")
      $psi = New-Object Diagnostics.ProcessStartInfo
      $psi.FileName = $runner; $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
      $psi.Arguments = "TAGS `"$(Join-Path $Local 'dbf')`" $t `"`" `"`" `"$logp`""
      $p = [Diagnostics.Process]::Start($psi)
      $done = $p.WaitForExit(60000)
      if (-not $done) { try { $p.Kill() } catch {} }
      $text = $(if (Test-Path -LiteralPath $logp) { @(Get-Content -LiteralPath $logp -Encoding UTF8) } else { @() })
      $r.runner += @{ table = $t; exited = $done; exit_code = $(if ($done) { $p.ExitCode } else { $null }); log = $text }
    }
    Write-Json "t6-progress-runner.json" $r | Out-Null

    # 5) ccprobe 常驻 60 分钟（每分钟读一次 SCCOMP 公司数，结果写 out\cc-*.json）
    $cp = Join-Path $Local "ccprobe\ccprobe.exe"
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $cp; $psi.UseShellExecute = $false
    $psi.Arguments = "--root `"$DataRoot`" --out `"$Out`" --local `"$(Join-Path $Local 'ccprobe-out')`" --minutes $CcprobeMinutes"
    $psi.EnvironmentVariables["APPDATA"] = (Join-Path $Local "ccprobe-home")
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = [Diagnostics.Process]::Start($psi)
    $r.ccprobe.pid = $p.Id
    Write-Json "t6-progress-ccprobe-started.json" $r | Out-Null
    $done = $p.WaitForExit(($CcprobeMinutes + 10) * 60000)
    $r.ccprobe.exited = $done
    $r.ccprobe.exit_code = $(if ($done) { $p.ExitCode } else { $null })
    $r.ccprobe.minutes = [math]::Round($sw.Elapsed.TotalMinutes, 1)

    $after = @{}
    foreach ($f in $hash.Keys) {
      $p2 = Join-Path $Local $f
      $after[$f] = $(if (Test-Path -LiteralPath $p2) { (Get-FileHash -LiteralPath $p2 -Algorithm SHA256).Hash } else { "MISSING" })
    }
    $r.hash_after = $after
  } catch { $r.error = "$_" }
  $r.finished = (Now-Utc)
  Write-Json "t6-result.json" $r | Out-Null
  exit 0
}

# ───────────────────────── 主探针 ─────────────────────────
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$state = [ordered]@{ hb = 0; shots = 0; started = (Now-Utc); notepad = $null; express = $null; t6 = $null; last_shot = $null }

# 记事本（第三方程序画面）：开一个探针目录里的文本
$noteFile = Join-Path $Local "probe-note.txt"
Set-Content -LiteralPath $noteFile -Value ("Pearnly cloud probe`r`nstarted " + $state.started + "`r`n" + ("0123456789 ABCDEFGHIJ " * 8)) -Encoding UTF8
try { $state.notepad = Start-Process notepad.exe -ArgumentList "`"$noteFile`"" -PassThru } catch { Log-Error "notepad" $_ }

# 自己的窗口：每秒刷新时间（证明会话里还在画）
$form = New-Object Windows.Forms.Form
$form.Text = "Pearnly probe"; $form.Width = 420; $form.Height = 160; $form.StartPosition = "Manual"; $form.Left = 40; $form.Top = 40
$label = New-Object Windows.Forms.Label
$label.Dock = "Fill"; $label.Font = New-Object Drawing.Font("Consolas", 14); $label.TextAlign = "MiddleCenter"
$form.Controls.Add($label)

function Static-Facts {
  $os = $null; try { $os = (Get-CimInstance Win32_OperatingSystem).Caption } catch {}
  $cs = $null; try { $cs = (Get-Service -Name CSFalconService -ErrorAction Stop).Status.ToString() } catch { $cs = "not found" }
  $drives = @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | ForEach-Object { @{ name = $_.Name; free = $_.Free; used = $_.Used } })
  [ordered]@{
    host = $env:COMPUTERNAME; domain = $env:USERDOMAIN; session = $SessionId; os = $os
    ps = $PSVersionTable.PSVersion.ToString(); language_mode = $ExecutionContext.SessionState.LanguageMode.ToString()
    crowdstrike_service = $cs; express_exe = (Find-Express); data_root_exists = (Test-Path -LiteralPath (Join-Path $DataRoot "secure\SCCOMP.DBF"))
    drives = $drives
    write_acl = [ordered]@{
      localappdata = Write-Acl-Verdict $env:LOCALAPPDATA
      appdata = Write-Acl-Verdict $env:APPDATA
      temp = Write-Acl-Verdict $env:TEMP
      startup = Write-Acl-Verdict ([Environment]::GetFolderPath("Startup"))
      d_root = Write-Acl-Verdict "D:\"
      p_root = Write-Acl-Verdict "P:\"
      userprofile = Write-Acl-Verdict $env:USERPROFILE
    }
    startup_path = [Environment]::GetFolderPath("Startup")
    parent = $(try { $pp = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId; (Get-Process -Id $pp -ErrorAction Stop).ProcessName } catch { "" })
    explorer_running = [bool](Get-Process -Name explorer -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $SessionId })
  }
}
try { Write-Json ("facts-" + (Now-Utc) + ".json") (Static-Facts) | Out-Null } catch { Log-Error "facts" $_ }

function Heartbeat {
  $state.hb++
  $tun = $false; try { $null = Get-ChildItem -LiteralPath $Tunnel -ErrorAction Stop | Select-Object -First 1; $tun = $true } catch { $tun = $false }
  $ccAlive = $null
  if ($state.t6) { try { $ccAlive = -not $state.t6.HasExited } catch {} }
  $hb = [ordered]@{
    n = $state.hb; utc = (Now-Utc); local = (Get-Date).ToString("s"); session = $SessionId
    sessions = (Session-Lines); client = (Client-Name); idle_s = [math]::Round([PearnlyProbeNative]::IdleMs() / 1000)
    tunnel_ok = $tun; notepad_alive = ($state.notepad -and -not $state.notepad.HasExited)
    express_alive = ($state.express -and -not $state.express.HasExited); t6_running = $ccAlive; last_shot = $state.last_shot
  }
  Write-Json ("hb-" + $hb.utc + ".json") $hb | Out-Null
}

function Shots([string]$tag) {
  $state.shots++
  $n = $state.shots
  $res = [ordered]@{ n = $n; utc = (Now-Utc); tag = $tag }
  $targets = @(@{ k = "self"; h = $form.Handle })
  if ($state.notepad -and -not $state.notepad.HasExited) { $state.notepad.Refresh(); $targets += @{ k = "notepad"; h = $state.notepad.MainWindowHandle } }
  if ($state.express -and -not $state.express.HasExited) { $state.express.Refresh(); $targets += @{ k = "express"; h = $state.express.MainWindowHandle } }
  foreach ($t in $targets) {
    try {
      $b = [PearnlyProbeNative]::Window($t.h)
      $res[$t.k + "_uniform"] = [PearnlyProbeNative]::Uniform($b)
      if ($b) { $b.Save((Join-Path $Out ("shot-$n-" + $t.k + ".png")), [Drawing.Imaging.ImageFormat]::Png); $b.Dispose() }
    } catch { $res[$t.k + "_error"] = "$_" }
  }
  try {
    $sz = [Windows.Forms.Screen]::PrimaryScreen.Bounds
    $b = [PearnlyProbeNative]::Screen($sz.Width, $sz.Height)
    $res.screen_uniform = [PearnlyProbeNative]::Uniform($b)
    $b.Save((Join-Path $Out ("shot-$n-screen.png")), [Drawing.Imaging.ImageFormat]::Png); $b.Dispose()
  } catch { $res.screen_error = "$_" }
  $state.last_shot = $res
  Write-Json ("shot-$n.json") $res | Out-Null
}

function Stop-All {
  foreach ($p in @($state.notepad, $state.express)) { try { if ($p -and -not $p.HasExited) { $p.Kill() } } catch {} }
  try { if ($state.t6 -and -not $state.t6.HasExited) { $state.t6.Kill() } } catch {}
  try { Get-Process -Name ccprobe -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $SessionId } | Stop-Process -Force } catch {}
  Start-Sleep 2
  $removed = $true
  try { Remove-Item -LiteralPath $Local -Recurse -Force -ErrorAction Stop } catch { $removed = $false }
  Write-Json "stopped.json" @{ utc = (Now-Utc); hb = $state.hb; local_removed = $removed; local_exists_after = (Test-Path -LiteralPath $Local) } | Out-Null
  try { Remove-Item -LiteralPath (Join-Path $Ctl "stop") -Force } catch {}
  & logoff.exe
}

function Check-Ctl {
  foreach ($c in @("stop", "t6", "express-open", "express-close", "shot")) {
    $f = Join-Path $Ctl $c
    if (-not (Test-Path -LiteralPath $f)) { continue }
    if ($c -ne "stop") { try { Remove-Item -LiteralPath $f -Force } catch {} }
    switch ($c) {
      "stop" { $timer.Stop(); Stop-All; $form.Close(); return }
      "shot" { Shots "ctl" }
      "t6" {
        if (-not $state.t6 -or $state.t6.HasExited) {
          $self = Join-Path $Local "probe.ps1"
          Copy-Item -LiteralPath (Join-Path $Tunnel "probe.ps1") -Destination $self -Force
          $cmd = "&([scriptblock]::Create([IO.File]::ReadAllText('$self'))) -Tunnel '$Tunnel' -Job t6 -DataRoot '$DataRoot' -CcprobeMinutes $CcprobeMinutes"
          $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
          $state.t6 = Start-Process powershell.exe -ArgumentList "-NoProfile -WindowStyle Hidden -EncodedCommand $enc" -PassThru
          Write-Json "t6-launched.json" @{ utc = (Now-Utc); pid = $state.t6.Id } | Out-Null
        }
      }
      "express-open" {
        $exe = Find-Express
        if (-not $exe) { Write-Json "express-open.json" @{ utc = (Now-Utc); error = "express exe not found" } | Out-Null; break }
        $before = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $SessionId } | ForEach-Object { $_.Id })
        $state.express = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -PassThru
        Start-Sleep 15
        $wins = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $SessionId -and $_.MainWindowTitle -and $before -notcontains $_.Id } |
          ForEach-Object { @{ pid = $_.Id; name = $_.ProcessName; title = $_.MainWindowTitle } })
        Write-Json "express-open.json" @{ utc = (Now-Utc); exe = $exe; pid = $state.express.Id; alive = -not $state.express.HasExited; new_windows = $wins } | Out-Null
        Shots "express-open"
      }
      "express-close" {
        $res = @{ utc = (Now-Utc) }
        if ($state.express -and -not $state.express.HasExited) {
          [void]$state.express.CloseMainWindow(); Start-Sleep 10
          if (-not $state.express.HasExited) { $state.express.Kill(); $res.killed = $true }
        }
        $res.alive_after = ($state.express -and -not $state.express.HasExited)
        Write-Json "express-close.json" $res | Out-Null
      }
    }
  }
}

$state.tick = 0
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({
  $state.tick++
  $label.Text = (Get-Date).ToString("HH:mm:ss") + "  hb " + $state.hb
  try {
    if ($state.tick % $HeartbeatSeconds -eq 1) { Heartbeat }
    if ($state.tick % $ShotSeconds -eq 5) { Shots "timer" }
    if ($state.tick % 5 -eq 0) { Check-Ctl }
  } catch { Log-Error "tick" $_ }
})
$timer.Start()
[void]$form.ShowDialog()
