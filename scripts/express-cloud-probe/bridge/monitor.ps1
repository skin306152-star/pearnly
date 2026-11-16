# monitor.ps1 —— 以 skin3 跑（T4 / T2 计数）：每分钟记一次我们起的 mstsc 的 CPU 时间、内存，以及隧道里心跳文件的数量和最新一条。
# 结果追加到 logs\monitor.csv；跑满 -Minutes 自己退出；看到 tunnel\out\stopped.json 也退出。
param([int]$Minutes = 150, [string]$Root = "C:\PearnlyCloudProbe")
$csv = Join-Path $Root "logs\monitor.csv"
if (-not (Test-Path -LiteralPath $csv)) { "time,mstsc_pid,cpu_s,ws_mb,private_mb,hb_count,hb_latest,cc_count,shot_count,idle_s" | Out-File -LiteralPath $csv -Encoding UTF8 }
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class PearnlyIdle { [StructLayout(LayoutKind.Sequential)] struct L { public uint cb; public uint t; }
  [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref L l);
  public static uint Seconds() { var l = new L(); l.cb = (uint)Marshal.SizeOf(l); GetLastInputInfo(ref l); return unchecked((uint)Environment.TickCount - l.t) / 1000; } }
"@
$out = Join-Path $Root "tunnel\out"
$end = (Get-Date).AddMinutes($Minutes)
while ((Get-Date) -lt $end) {
  $hp = $(try { (Get-Content -LiteralPath (Join-Path $Root "logs\hidden-mstsc.pid") -Raw).Trim() } catch { "" })
  $p = $(if ($hp) { Get-Process -Id $hp -ErrorAction SilentlyContinue } else { $null })
  $hb = @(Get-ChildItem -LiteralPath $out -Filter "hb-*.json" -ErrorAction SilentlyContinue | Sort-Object Name)
  $cc = @(Get-ChildItem -LiteralPath $out -Filter "cc-0*.json" -ErrorAction SilentlyContinue).Count
  $sh = @(Get-ChildItem -LiteralPath $out -Filter "shot-*.json" -ErrorAction SilentlyContinue).Count
  $row = @(
    (Get-Date).ToString("s"), $hp,
    $(if ($p) { [math]::Round($p.TotalProcessorTime.TotalSeconds, 2) } else { "" }),
    $(if ($p) { [math]::Round($p.WorkingSet64 / 1MB, 1) } else { "" }),
    $(if ($p) { [math]::Round($p.PrivateMemorySize64 / 1MB, 1) } else { "" }),
    $hb.Count, $(if ($hb.Count) { $hb[-1].Name } else { "" }), $cc, $sh, [PearnlyIdle]::Seconds()
  ) -join ","
  Add-Content -LiteralPath $csv -Value $row -Encoding UTF8
  if (Test-Path -LiteralPath (Join-Path $out "stopped.json")) { break }
  Start-Sleep -Seconds 60
}
