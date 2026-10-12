# Build the exact checked-out commit on Windows; publication is a separate Ubuntu job.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\release.ps1 -BuildOnly
[CmdletBinding()]
param([switch]$BuildOnly)

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
