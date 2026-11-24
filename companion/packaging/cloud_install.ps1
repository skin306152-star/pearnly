# 云端 Express 主机上安装 CC 云端精简包并做一次设备配对（docs/work/2026-11-16-express-cloud-install-command.md）。
#
# 桥（客户自己的 CC 电脑）把本脚本、CC-Cloud.zip、cloud.json 一起放进隧道盘的 cc\ 目录。用户在云端开始菜单的 PowerShell 里打一行：
#   powershell -NoProfile -ExecutionPolicy Bypass -File \\tsclient\Z\cc\cloud_install.ps1 -Token XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
# （-ExecutionPolicy Bypass 只作用于这一个 powershell 进程；本脚本不调 Set-ExecutionPolicy。）
#
# 做的事：
#   1. 每次都把同目录的 CC-Cloud.zip 先拷到 %LOCALAPPDATA%\Pearnly\CC-Cloud\ 再算 SHA-256，必须等于同目录 cloud.json 的 sha256；
#      不等就打一行结果、以非 0 退出：不解压、不运行、不建快捷方式；
#   2. 解压到 %LOCALAPPDATA%\Pearnly\CC-Cloud\<版本>（同版本目录已有 companion.exe 就不重解压）；
#   3. 启动一次 companion.exe --cloud --pair <令牌>，按退出码打印一行结果；
#   4. 配对成功（退出码 0）才在本用户「启动」文件夹建快捷方式 CC.lnk：companion.exe --cloud（不带令牌），再启动一次
#      companion.exe --cloud；配对失败不留快捷方式（之前安装建的、指向上面目录的 CC.lnk 删掉）。
# 不做的事：不要管理员、不改系统设置与执行策略、不建计划任务、不写注册表、不碰上面两个目录以外的地方；
#   令牌不写进任何文件、快捷方式、日志，不回显；不模拟键鼠、不点系统对话框。
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Token)

$ErrorActionPreference = 'Stop'

# 结果行按云端主机的界面语言（泰 / 中 / 日，其余英文）；中文与原来相同
$messages = @{
    missing   = @{ zh = '缺少安装文件'; th = 'ไม่มีไฟล์ติดตั้ง'; en = 'Installation files missing'; ja = 'インストールファイルがありません' }
    invalid   = @{ zh = '安装文件无效'; th = 'ไฟล์ติดตั้งไม่ถูกต้อง'; en = 'Invalid installation files'; ja = 'インストールファイルが無効です' }
    token     = @{ zh = '令牌有误'; th = 'โทเค็นไม่ถูกต้อง'; en = 'Invalid token'; ja = 'トークンが正しくありません' }
    checksum  = @{ zh = '校验值不符，未安装'; th = 'ค่าตรวจสอบไม่ตรงกัน ไม่ได้ติดตั้ง'; en = 'Checksum mismatch, not installed'; ja = 'チェックサムが一致しないため、インストールしていません' }
    paired    = @{ zh = '配对完成'; th = 'จับคู่เสร็จแล้ว'; en = 'Pairing complete'; ja = 'ペアリングが完了しました' }
    no_drive  = @{ zh = '未找到隧道盘'; th = 'ไม่พบไดรฟ์อุโมงค์'; en = 'Tunnel drive not found'; ja = 'トンネルドライブが見つかりません' }
    no_host   = @{ zh = '缺少主机信息'; th = 'ไม่มีข้อมูลโฮสต์'; en = 'Host information missing'; ja = 'ホスト情報がありません' }
    timed_out = @{ zh = '配对超时'; th = 'จับคู่หมดเวลา'; en = 'Pairing timed out'; ja = 'ペアリングがタイムアウトしました' }
    rejected  = @{ zh = '配对被拒绝'; th = 'การจับคู่ถูกปฏิเสธ'; en = 'Pairing rejected'; ja = 'ペアリングが拒否されました' }
    failed    = @{ zh = '配对失败'; th = 'จับคู่ไม่สำเร็จ'; en = 'Pairing failed'; ja = 'ペアリングに失敗しました' }
}
$lang = (Get-UICulture).TwoLetterISOLanguageName
if (@('zh', 'th', 'ja') -notcontains $lang) { $lang = 'en' }

function Stop-With([string]$Key, [int]$Code) {
    Write-Host $messages[$Key][$lang]
    exit $Code
}

$here = $PSScriptRoot
$zipSource = Join-Path $here 'CC-Cloud.zip'
$manifestPath = Join-Path $here 'cloud.json'
if (-not (Test-Path -LiteralPath $zipSource) -or -not (Test-Path -LiteralPath $manifestPath)) {
    Stop-With 'missing' 10
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$version = [string]$manifest.version
$expected = ([string]$manifest.sha256).ToLowerInvariant()
if ($version -notmatch '^\d+(\.\d+){2,3}$' -or $expected -notmatch '^[0-9a-f]{64}$') {
    Stop-With 'invalid' 10
}

$cleanToken = ($Token -replace '[\s]', '')
if ($cleanToken -notmatch '^[0-9A-Za-z\-]{20,60}$') {
    Stop-With 'token' 5
}

$base = Join-Path $env:LOCALAPPDATA 'Pearnly\CC-Cloud'
$dest = Join-Path $base $version
$exe = Join-Path $dest 'companion.exe'
New-Item -ItemType Directory -Path $base -Force | Out-Null

# 每次都先拷到本机再校验：校验不对立刻退出（不解压、不运行、不建快捷方式）；解压用同一份文件
$localZip = Join-Path $base ('CC-Cloud-' + [guid]::NewGuid().ToString('N') + '.zip')
$staging = Join-Path $base ($version + '.partial-' + [guid]::NewGuid().ToString('N'))
try {
    Copy-Item -LiteralPath $zipSource -Destination $localZip -Force
    $actual = (Get-FileHash -LiteralPath $localZip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Remove-Item -LiteralPath $localZip -Force
        Stop-With 'checksum' 11
    }
    if (-not (Test-Path -LiteralPath $exe)) {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($localZip, $staging)
        if (-not (Test-Path -LiteralPath (Join-Path $staging 'companion.exe'))) {
            Stop-With 'invalid' 10
        }
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
        Move-Item -LiteralPath $staging -Destination $dest
    }
}
finally {
    if (Test-Path -LiteralPath $localZip) { Remove-Item -LiteralPath $localZip -Force }
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}

$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'CC.lnk'

# 配对：令牌只在这个子进程的参数里出现一次；结果看退出码（cloud_link/cloud_side.py 的 EXIT_*）
# 隧道根 = 本脚本所在的 cc\ 的上一级（\\tsclient\<盘>）：直接交给配对，不靠枚举（会话里 \\tsclient 这一级列不出来）
$tunnelRoot = Split-Path -Parent $here
$pairing = Start-Process -FilePath $exe -ArgumentList @('--cloud', '--pair', $cleanToken, '--tunnel', $tunnelRoot) -WorkingDirectory $dest -PassThru
$Token = ''
$cleanToken = ''
if ($pairing.WaitForExit(150000)) {
    $code = $pairing.ExitCode
}
else {
    Stop-Process -Id $pairing.Id -Force
    $code = 6
}
$lines = @{
    0 = 'paired'
    3 = 'no_drive'
    4 = 'no_host'
    5 = 'token'
    6 = 'timed_out'
    7 = 'rejected'
}
$line = if ($lines.ContainsKey($code)) { $lines[$code] } else { 'failed' }
if ($code -ne 0) {
    # 配对失败：不留快捷方式；之前安装建的（指向 %LOCALAPPDATA%\Pearnly\CC-Cloud 下）删掉，别人的不碰
    if (Test-Path -LiteralPath $linkPath) {
        $shell = New-Object -ComObject WScript.Shell
        $old = $shell.CreateShortcut($linkPath)
        $oldTarget = [string]$old.TargetPath
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($old)
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
        if ($oldTarget.StartsWith($base + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $linkPath -Force
        }
    }
    Stop-With $line $code
}

# 配对成功才建开机（登录）自启：只带 --cloud
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $exe
$link.Arguments = '--cloud'
$link.WorkingDirectory = $dest
$link.Save()
[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($link)
[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
Start-Process -FilePath $exe -ArgumentList @('--cloud') -WorkingDirectory $dest | Out-Null
Stop-With $line $code
