# express-ui.ps1 —— 按动作清单（同目录 xp-actions.json）远程操作 Express 窗口。必须在 Express 所在的交互会话里跑（见 run-as-session-user.ps1）。
# 每次按键 / 点击前核前台窗口是 ExpressI，不是就中止（exit 2），防止把键打进别的程序。launch 只放行白名单里的 Express 程序。
# 动作：launch / activate / keys(SendKeys 写法) / click / dblclick / wait(ms) / paste(剪贴板 + Shift+Insert，用于泰文) / lang / shot(全屏截图) / windows(列顶层窗口)
$dir = 'C:\PearnlyStress\_acc_cc'
$log = "$dir\xp-log.txt"
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(int f, int x, int y, int d, int e);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, int f, int e);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr LoadKeyboardLayout(string id, uint flags);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
}
"@
[W]::SetProcessDPIAware() | Out-Null
function L($m) { "$(Get-Date -Format HH:mm:ss) $m" | Out-File $log -Append -Encoding utf8 }
function Fg() { $h = [W]::GetForegroundWindow(); $p = 0; [W]::GetWindowThreadProcessId($h, [ref]$p) | Out-Null; $sb = New-Object System.Text.StringBuilder 256; [W]::GetWindowText($h, $sb, 256) | Out-Null; $n = (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName; return @{name = $n; title = $sb.ToString()} }
function Guard() { $f = Fg; if ($f.name -ne 'ExpressI') { L "ABORT foreground is $($f.name) '$($f.title)'"; return $false }; return $true }
function Shot($name) { $b = [System.Windows.Forms.SystemInformation]::VirtualScreen; $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size); $bmp.Save("$dir\xp-$name.png", [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose(); L "shot $name fg=$((Fg).name) '$((Fg).title)'" }
$actions = Get-Content "$dir\xp-actions.json" -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($a in $actions) {
  switch ($a.do) {
    'launch' { if (-not (($a.exe -like 'C:\PearnlyExpress\*') -or ($a.exe -eq '\\Accserver\d$\ACCOUNT\70EXP\ExpressI.exe'))) { L "REFUSE launch $($a.exe)"; break }; Start-Process -FilePath $a.exe -WorkingDirectory $a.dir; L "launch $($a.exe)" }
    'activate' { $p = Get-Process ExpressI -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; if ($p) { [W]::ShowWindow($p.MainWindowHandle, 9) | Out-Null; [W]::SetForegroundWindow($p.MainWindowHandle) | Out-Null; L "activate pid=$($p.Id) '$($p.MainWindowTitle)'" } else { L "activate: no ExpressI window" } }
    'keys' { if (Guard) { [System.Windows.Forms.SendKeys]::SendWait($a.text); L "keys $($a.text)" } else { exit 2 } }
    'click' { if (Guard) { [W]::SetCursorPos($a.x, $a.y) | Out-Null; Start-Sleep -Milliseconds 150; [W]::mouse_event(2,0,0,0,0); [W]::mouse_event(4,0,0,0,0); L "click $($a.x),$($a.y)" } else { exit 2 } }
    'dblclick' { if (Guard) { [W]::SetCursorPos($a.x, $a.y) | Out-Null; Start-Sleep -Milliseconds 150; 1..2 | ForEach-Object { [W]::mouse_event(2,0,0,0,0); [W]::mouse_event(4,0,0,0,0); Start-Sleep -Milliseconds 80 }; L "dblclick $($a.x),$($a.y)" } else { exit 2 } }
    'wait' { Start-Sleep -Milliseconds $a.ms }
    'paste' { if (Guard) { [W]::LoadKeyboardLayout('0000041E', 1) | Out-Null; [System.Windows.Forms.Clipboard]::SetText($a.text); Start-Sleep -Milliseconds 200; [System.Windows.Forms.SendKeys]::SendWait('+{INS}'); L "paste ($($a.text.Length) chars)" } else { exit 2 } }
    'lang' { if (Guard) { $hkl = [W]::LoadKeyboardLayout($a.layout, 1); [W]::PostMessage([W]::GetForegroundWindow(), 0x50, [IntPtr]::Zero, $hkl) | Out-Null; L "lang $($a.layout) hkl=$hkl" } else { exit 2 } }
    'shot' { Shot $a.name }
    'windows' { Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { L "win $($_.ProcessName) pid=$($_.Id) '$($_.MainWindowTitle)'" } }
  }
}
L "done"
