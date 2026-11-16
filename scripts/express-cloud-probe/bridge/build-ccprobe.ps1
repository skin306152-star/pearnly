# build-ccprobe.ps1 —— macadmin 在测试机上跑（不用 skin3）：用 CC 桌面版同一版本的 PyInstaller 把 ccprobe 打成没签名的 onedir，放进 tunnel\dist\ccprobe。
# 依赖版本照 companion/packaging/requirements-build.txt（--no-deps 逐个钉死）；venv 建在 C:\PearnlyCloudProbe\build，收尾整个目录删掉。
# 打好后在本机先跑一遍（读测试机自己的样例 DBF，不碰任何账套），再跑一遍 CDX 索引程序对样例表，确认两样在桥上本身是好的。
param([string]$Root = "C:\PearnlyCloudProbe")
$ErrorActionPreference = "Stop"
$build = Join-Path $Root "build"
$src = Join-Path $build "src"          # Mac 推过来的：ccprobe.py + src\companion\{__init__,config,logger,express_locate}.py
$dist = Join-Path $Root "tunnel\dist"
$py = $null
foreach ($c in @("C:\PearnlyPython311\python.exe") + @(Get-ChildItem "C:\actions-runner\_work\_tool\Python\3.11.*\x64\python.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })) {
  if (Test-Path -LiteralPath $c) { $py = $c; break }
}
if (-not $py) { throw "no Python 3.11 on this machine" }
$venv = Join-Path $build "venv"
if (-not (Test-Path -LiteralPath (Join-Path $venv "Scripts\python.exe"))) { & $py -m venv $venv; if ($LASTEXITCODE) { throw "venv failed" } }
$vp = Join-Path $venv "Scripts\python.exe"
& $vp -m pip install --quiet --disable-pip-version-check --no-deps `
  pyinstaller==6.21.0 pyinstaller-hooks-contrib==2026.6 altgraph==0.17.5 pefile==2024.8.26 pywin32-ctypes==0.2.3 `
  packaging==26.0 setuptools==80.9.0 dbf==0.99.11 aenum==3.1.17
if ($LASTEXITCODE) { throw "pip failed" }
Push-Location $src
try {
  & $vp -m PyInstaller --noconfirm --clean --onedir --windowed --name ccprobe `
    --distpath $dist --workpath (Join-Path $build "work") --specpath $build `
    --paths $src --hidden-import dbf --hidden-import aenum --hidden-import src.companion.express_locate (Join-Path $src "ccprobe.py")
  if ($LASTEXITCODE) { throw "PyInstaller failed" }
} finally { Pop-Location }

# 桥上自检：ccprobe 读样例表 3 次（--no-locate，不扫测试机的盘），CDX 索引程序对样例表 TAGS 一次、对不存在的表一次
$self = Join-Path $build "selftest"
Remove-Item -LiteralPath $self -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $self "root\secure"), (Join-Path $self "dbf") | Out-Null
Copy-Item -LiteralPath (Join-Path $dist "probe-dbf\SCCOMP.DBF") -Destination (Join-Path $self "root\secure\SCCOMP.DBF")
Copy-Item -LiteralPath (Join-Path $dist "probe-dbf\PROBE.DBF") -Destination (Join-Path $self "dbf\PROBE.DBF")
$env:APPDATA = Join-Path $self "home"
$p = Start-Process -FilePath (Join-Path $dist "ccprobe\ccprobe.exe") -ArgumentList "--root `"$(Join-Path $self 'root')`" --out `"$(Join-Path $self 'out')`" --minutes 0.1 --interval 2 --no-locate" -PassThru -Wait
"ccprobe selftest exit=$($p.ExitCode)"
Get-ChildItem -LiteralPath (Join-Path $self "out") | ForEach-Object { "  $($_.Name): $(Get-Content -LiteralPath $_.FullName -Raw)" }
foreach ($t in @("PROBE", "NOPE")) {
  $log = Join-Path $self "dbf\runner-$t.log"
  $r = Start-Process -FilePath (Join-Path $dist "cdx_reindex_runner.exe") -ArgumentList "TAGS `"$(Join-Path $self 'dbf')`" $t `"`" `"`" `"$log`"" -PassThru -Wait -WindowStyle Hidden
  "runner $t exit=$($r.ExitCode): $(if (Test-Path $log) { (Get-Content -LiteralPath $log) -join ' / ' } else { 'no log' })"
}
"sha256:"
foreach ($f in @("ccprobe\ccprobe.exe", "cdx_reindex_runner.exe", "probe-dbf\PROBE.DBF")) { "  $f $((Get-FileHash -LiteralPath (Join-Path $dist $f)).Hash)" }
if (Test-Path -LiteralPath (Join-Path $dist "cc\companion.exe")) { "  cc\companion.exe $((Get-FileHash -LiteralPath (Join-Path $dist 'cc\companion.exe')).Hash) $((Get-Item (Join-Path $dist 'cc\companion.exe')).VersionInfo.ProductVersion)" }
"dist size MB: " + [math]::Round(((Get-ChildItem -LiteralPath $dist -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
