# idle.ps1 —— 以 skin3 跑：skin3 桌面多少秒没有键鼠输入、有没有锁屏、现在开着哪些 mstsc / Express / CC 进程（开工前核「测试机没人在用」）。
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class PearnlyIdle2 { [StructLayout(LayoutKind.Sequential)] struct L { public uint cb; public uint t; }
  [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref L l);
  public static uint Seconds() { var l = new L(); l.cb = (uint)Marshal.SizeOf(l); GetLastInputInfo(ref l); return unchecked((uint)Environment.TickCount - l.t) / 1000; } }
"@
[ordered]@{
  idle_s = [PearnlyIdle2]::Seconds()
  locked = [bool](Get-Process -Name LogonUI -ErrorAction SilentlyContinue)
  mstsc = @(Get-Process -Name mstsc -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Id) $($_.MainWindowTitle)" })
  express = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match "^Express" } | ForEach-Object { "$($_.Id) $($_.ProcessName)" })
  cc = @(Get-Process -Name companion -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Id)" })
} | ConvertTo-Json
