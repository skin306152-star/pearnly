# 构建小助手安装产物:64 位主程序 companion.exe(+ 预编译 cdx_reindex_runner.exe)
# 跑法:powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'

$py64 = 'C:\Users\skin3\AppData\Local\Programs\Python\Python311\python.exe'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host '=== [1/3] 64 位主程序 companion.exe (PySide6 托盘+配对+DBF) ==='
& $py64 -m PyInstaller --noconfirm --distpath dist --workpath build\b64 packaging\companion.spec
if ($LASTEXITCODE -ne 0) { throw '64 位打包失败' }

# cdx_reindex_runner.exe 是预编译 Harbour DBFCDX 原生 exe(后台索引重建 · 源 packaging\native\*.prg)。
Write-Host '=== [2/3] 拷入预编译 cdx_reindex_runner.exe (Harbour 后台索引) ==='
$cdxSrc = Join-Path $root 'packaging\native\cdx_reindex_runner.exe'
if (-not (Test-Path $cdxSrc)) { throw "缺预编译 $cdxSrc (重建见 packaging\native\README)" }
Copy-Item $cdxSrc (Join-Path $root 'dist\cdx_reindex_runner.exe') -Force

Write-Host '=== [3/3] 产物 ==='
Get-ChildItem dist\*.exe | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table -AutoSize
Write-Host '提示:companion.exe 首次运行弹配对窗;cdx_reindex_runner.exe 由主程序写盘后自动调用,勿手动跑。'
