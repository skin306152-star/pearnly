# Build the exact checked-out commit on Windows; publication is a separate Ubuntu job.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\release.ps1 -BuildOnly
#        add -Cloud to build only the cloud package (dist\CC-Cloud.zip + dist\cloud.json); no installer, nothing published
[CmdletBinding()]
param([switch]$BuildOnly, [switch]$Cloud)

if (-not $BuildOnly) {
    throw 'Legacy direct publication is retired. Use -BuildOnly or the release workflow; see docs/RELEASE.md.'
}

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'

# 跑原生 exe(PyInstaller/ISCC/git):PS5.1 在 Stop 下会把原生 stderr 当成终止性错误
# (PyInstaller 以管理员运行时的 deprecation 警告就是 stderr),误杀本来成功的构建。
# 临时降到 Continue 跑,完了恢复 Stop;成败一律只认 $LASTEXITCODE(各步已有显式校验)。
function Invoke-Native {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $args[0] @($args[1..($args.Count - 1)]) } finally { $ErrorActionPreference = $prev }
}

# CI(GitHub Actions windows runner · .github/workflows/release.yml)与本机双跑:
# 环境变量只覆盖本机 Python 和 Inno Setup 路径;构建过程没有发布凭据。
$py64 = if ($env:RELEASE_PY) { $env:RELEASE_PY } else { 'C:\Users\skin3\AppData\Local\Programs\Python\Python311\python.exe' }
$iscc = if ($env:RELEASE_ISCC) { $env:RELEASE_ISCC } else { 'C:\Users\skin3\AppData\Local\Programs\Inno Setup 6\ISCC.exe' }
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$sourceSha = (Invoke-Native git rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceSha -notmatch '^[0-9a-f]{40}$') { throw 'cannot read source commit' }
$dirty = (Invoke-Native git status --porcelain | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'release build requires a clean source checkout' }

$ver = (& $py64 -c "from src.companion.version import VERSION; print(VERSION)").Trim()
if ($LASTEXITCODE -ne 0 -or $ver -notmatch '^\d+(\.\d+){2,3}$') { throw 'cannot read numeric VERSION' }
Write-Host "=== Releasing Pearnly Companion v$ver ==="

# 云端精简包（-Cloud，packaging\companion-cloud.spec）：云端 Express 主机上那份 CC（companion.exe --cloud），不带 PySide6 / 网页内核 /
# Windows 通知，由桥经远程桌面映射盘拷上去。只打这一份：不打正式包、不做安装程序、不写 release.json / latest.json。
if ($Cloud) {
    Write-Host '=== [1/3] build the cloud package dist\companion-cloud ==='
    $cloudDir = Join-Path $root 'dist\companion-cloud'
    Invoke-Native $py64 -m PyInstaller --noconfirm --distpath dist --workpath build\cloud packaging\companion-cloud.spec
    if ($LASTEXITCODE -ne 0) { throw 'cloud package build failed' }
    if (-not (Test-Path (Join-Path $cloudDir 'companion.exe'))) { throw 'cloud package has no companion.exe' }
    if (Get-ChildItem $cloudDir -Recurse -Include 'QtWebEngineProcess.exe', 'Qt6Core.dll', 'shiboken6*' | Select-Object -First 1) {
        throw 'cloud package still contains Qt'
    }

    # 后台 CDX 索引：同正式包放在 companion.exe 旁边（cdx_reindex.runner_path）
    Write-Host '=== [2/3] stage cdx_reindex_runner.exe next to companion.exe ==='
    $cdxSrc = Join-Path $root 'packaging\native\cdx_reindex_runner.exe'
    if (-not (Test-Path $cdxSrc)) { throw "missing prebuilt $cdxSrc (rebuild: see packaging\native\README)" }
    Copy-Item $cdxSrc (Join-Path $cloudDir 'cdx_reindex_runner.exe') -Force

    Write-Host '=== [3/3] zip dist\CC-Cloud.zip ==='
    $zip = Join-Path $root 'dist\CC-Cloud.zip'
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($cloudDir, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
    $zipItem = Get-Item $zip
    $unpacked = (Get-ChildItem $cloudDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
    $cloudManifest = [ordered]@{
        version = $ver
        source_sha = $sourceSha
        sha256 = (Get-FileHash $zipItem.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        size = [int64]$zipItem.Length
        unpacked_size = [int64]$unpacked
    }
    $cloudManifest | ConvertTo-Json | Out-File -FilePath dist\cloud.json -Encoding ascii
    Write-Host ("Cloud package: version=$ver source=$sourceSha zip={0:N1} MB unpacked={1:N1} MB sha256={2}" -f ($zipItem.Length / 1MB), ($unpacked / 1MB), $cloudManifest.sha256)
    Write-Host 'No publication performed.'
    return
}

Write-Host '=== [1/3] build companion.exe (64-bit) ==='
Invoke-Native $py64 -m PyInstaller --noconfirm --distpath dist --workpath build\b64 packaging\companion.spec
if ($LASTEXITCODE -ne 0) { throw 'companion.exe build failed' }

# cdx_reindex_runner.exe 是预编译 Harbour DBFCDX 原生 exe(GTNUL · 源 packaging\native\*.prg),
# 不走 PyInstaller。随仓库提交,发版只需拷进 dist 供 installer 打包。重建见 packaging\native\README。
Write-Host '=== [2/3] stage cdx_reindex_runner.exe (prebuilt Harbour) ==='
$cdxSrc = Join-Path $root 'packaging\native\cdx_reindex_runner.exe'
if (-not (Test-Path $cdxSrc)) { throw "missing prebuilt $cdxSrc (rebuild: see packaging\native\README)" }
Copy-Item $cdxSrc (Join-Path $root 'dist\cdx_reindex_runner.exe') -Force

Write-Host '=== [3/3] build installer ==='
Invoke-Native $iscc "/DAppVersion=$ver" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw 'installer build failed' }

# Inno 的 VersionInfo 由 AppVersion 派生。/DAppVersion 哪天没注入进去,打出来的包会内嵌
# installer.iss 里的回落版本号,而外面一切照常绿。FileVersion 是空的,只能断 ProductVersion。
# 断在这里而不是末尾:产物不匹配时阻断后续发布。
$exeVer = ([string](Get-Item 'dist\CC-Setup.exe').VersionInfo.ProductVersion).Trim()
if ($exeVer -ne $ver) { throw "installer ProductVersion=$exeVer != version.py $ver" }


$installer = Get-Item 'dist\CC-Setup.exe'
$manifest = [ordered]@{
    version = $ver
    product_version = $exeVer
    source_sha = $sourceSha
    sha256 = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    size = [int64]$installer.Length
}
$manifest | ConvertTo-Json | Out-File -FilePath dist\release.json -Encoding ascii
Write-Host "Build verified: version=$ver source=$sourceSha size=$($installer.Length)"
Write-Host 'No publication performed. Upload this run artifact through the Ubuntu publish job.'
