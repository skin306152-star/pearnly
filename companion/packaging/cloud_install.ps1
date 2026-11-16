# 云端 Express 主机上安装 CC 云端精简包并做一次设备配对（docs/work/2026-11-16-express-cloud-install-command.md）。
#
# 桥（客户自己的 CC 电脑）把本脚本、CC-Cloud.zip、cloud.json 一起放进隧道盘的 cc\ 目录。用户在云端开始菜单的 PowerShell 里打一行：
#   powershell -NoProfile -ExecutionPolicy Bypass -File \\tsclient\Z\cc\cloud_install.ps1 -Token XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
# （-ExecutionPolicy Bypass 只作用于这一个 powershell 进程；本脚本不调 Set-ExecutionPolicy。）
#
# 做的事：
#   1. 把同目录的 CC-Cloud.zip 先拷到 %LOCALAPPDATA%\Pearnly\CC-Cloud\ 再算 SHA-256，必须等于同目录 cloud.json 的 sha256，不等就停；
#   2. 解压到 %LOCALAPPDATA%\Pearnly\CC-Cloud\<版本>（同版本目录已有 companion.exe 就不重解压）；
#   3. 在本用户「启动」文件夹建快捷方式 CC.lnk：companion.exe --cloud（不带令牌）；
#   4. 启动一次 companion.exe --cloud --pair <令牌>，按退出码打印一行结果；配对成功再启动一次 companion.exe --cloud（不带令牌）。
# 不做的事：不要管理员、不改系统设置与执行策略、不建计划任务、不写注册表、不碰上面两个目录以外的地方；
#   令牌不写进任何文件、快捷方式、日志，不回显；不模拟键鼠、不点系统对话框。
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Token)

$ErrorActionPreference = 'Stop'

function Stop-With([string]$Line, [int]$Code) {
    Write-Host $Line
    exit $Code
}

$here = $PSScriptRoot
$zipSource = Join-Path $here 'CC-Cloud.zip'
$manifestPath = Join-Path $here 'cloud.json'
if (-not (Test-Path -LiteralPath $zipSource) -or -not (Test-Path -LiteralPath $manifestPath)) {
    Stop-With '缺少安装文件' 10
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$version = [string]$manifest.version
$expected = ([string]$manifest.sha256).ToLowerInvariant()
if ($version -notmatch '^\d+(\.\d+){2,3}$' -or $expected -notmatch '^[0-9a-f]{64}$') {
    Stop-With '安装文件无效' 10
}

$cleanToken = ($Token -replace '[\s]', '')
if ($cleanToken -notmatch '^[0-9A-Za-z\-]{20,60}$') {
    Stop-With '令牌有误' 5
}

$base = Join-Path $env:LOCALAPPDATA 'Pearnly\CC-Cloud'
$dest = Join-Path $base $version
$exe = Join-Path $dest 'companion.exe'
New-Item -ItemType Directory -Path $base -Force | Out-Null

if (-not (Test-Path -LiteralPath $exe)) {
    # 先拷到本机再校验、再从这份解压：校验与解压用同一份文件
    $localZip = Join-Path $base ('CC-Cloud-' + [guid]::NewGuid().ToString('N') + '.zip')
    $staging = Join-Path $base ($version + '.partial-' + [guid]::NewGuid().ToString('N'))
    try {
        Copy-Item -LiteralPath $zipSource -Destination $localZip -Force
        $actual = (Get-FileHash -LiteralPath $localZip -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            Stop-With '校验值不符，未安装' 11
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($localZip, $staging)
        if (-not (Test-Path -LiteralPath (Join-Path $staging 'companion.exe'))) {
            Stop-With '安装文件无效' 10
        }
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
        Move-Item -LiteralPath $staging -Destination $dest
    }
    finally {
        if (Test-Path -LiteralPath $localZip) { Remove-Item -LiteralPath $localZip -Force }
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
}

# 开机（登录）自启：只带 --cloud
$startup = [Environment]::GetFolderPath('Startup')
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $startup 'CC.lnk'))
$link.TargetPath = $exe
$link.Arguments = '--cloud'
$link.WorkingDirectory = $dest
$link.Save()
[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($link)
[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)

# 配对：令牌只在这个子进程的参数里出现一次；结果看退出码（cloud_link/cloud_side.py 的 EXIT_*）
$pairing = Start-Process -FilePath $exe -ArgumentList @('--cloud', '--pair', $cleanToken) -WorkingDirectory $dest -PassThru
$Token = ''
$cleanToken = ''
if (-not $pairing.WaitForExit(150000)) {
    Stop-Process -Id $pairing.Id -Force
    Stop-With '配对超时' 6
}
$code = $pairing.ExitCode
$lines = @{
    0 = '配对完成'
    3 = '未找到隧道盘'
    4 = '缺少主机信息'
    5 = '令牌有误'
    6 = '配对超时'
    7 = '配对被拒绝'
}
$line = if ($lines.ContainsKey($code)) { $lines[$code] } else { '配对失败' }
if ($code -eq 0) {
    Start-Process -FilePath $exe -ArgumentList @('--cloud') -WorkingDirectory $dest | Out-Null
}
Stop-With $line $code
