#!/bin/bash
# probe.sh —— Mac 这边驱动云端 Express 只读实测（T1–T6）。测试机1（lenovo-office-lan）当桥，SSH 用户 macadmin，桌面用户 skin3。
# 用前：占用表登记、主会话同意。全部步骤见 docs/work/2026-11-11-express-cloud-probe-checklist.md。
#
#   probe.sh dist              本机造发放物（假表、CDX 索引程序、ccprobe 源码）到 $WORK/dist，打印 sha256
#   probe.sh push              推脚本与发放物到测试机 C:\PearnlyCloudProbe；顺手把测试机上已装 CC 的程序文件夹只读复制进 tunnel\dist\cc
#   probe.sh parse             在测试机上只做 PowerShell 语法解析（不运行任何脚本）
#   probe.sh build             测试机上打 ccprobe 并自检（macadmin）
#   probe.sh as <脚本> [参数…]  以 skin3 跑 bridge\<脚本>.ps1（prep / idle / read-tsplus / hidden-desk / send-run / restore）
#   probe.sh bg <脚本> [参数…]  同上但不等（monitor 用）
#   probe.sh ctl <名字>         往 tunnel\ctl 放控制文件（stop / t6 / express-open / express-close / shot）
#   probe.sh status            心跳数、最新心跳、T6 进度、cc-*.json 数
#   probe.sh pull              拉回 tunnel\out 与 logs 到 $WORK/evidence（只拉结果，不拉 dist）
#   probe.sh cleanup           删 PearnlyCloudProbe-* 计划任务与 C:\PearnlyCloudProbe，读回确认（先 restore、先 pull）
set -euo pipefail
HOST=${LAB_HOST:-lenovo-office-lan}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
HERE="$REPO/scripts/express-cloud-probe"
WORK=${PROBE_WORK:-$HOME/pearnly-devtools/express-cloud-probe-1115}
PY=${PROBE_PY:-$HOME/pearnly-devtools/cc-venv/bin/python}
R='C:\PearnlyCloudProbe'

ps() { ssh "$HOST" "$1"; }   # 测试机 SSH 默认外壳就是 PowerShell

cmd=${1:-}; shift || true
case "$cmd" in
  dist)
    rm -rf "$WORK/dist" && mkdir -p "$WORK/dist/probe-dbf" "$WORK/dist/src/src/companion"
    "$PY" "$HERE/make_dbf.py" "$WORK/dist/probe-dbf"
    cp "$REPO/companion/packaging/native/cdx_reindex_runner.exe" "$WORK/dist/"
    cp "$HERE/ccprobe/ccprobe.py" "$WORK/dist/src/"
    : > "$WORK/dist/src/src/__init__.py"
    for f in __init__ config logger express_locate; do cp "$REPO/companion/src/companion/$f.py" "$WORK/dist/src/src/companion/"; done
    (cd "$WORK/dist" && find . -type f | sort | xargs shasum -a 256)
    echo "source sha: $(git -C "$REPO" rev-parse HEAD)"
    ;;
  push)
    ssh "$HOST" "New-Item -ItemType Directory -Force -Path $R\\bridge,$R\\logs,$R\\build\\src,$R\\tunnel\\dist\\probe-dbf,$R\\tunnel\\out,$R\\tunnel\\ctl | Out-Null"
    scp -q "$HERE"/bridge/*.ps1 "$HOST:C:/PearnlyCloudProbe/bridge/"
    scp -q "$HERE/cloud/probe.ps1" "$HOST:C:/PearnlyCloudProbe/tunnel/probe.ps1"
    scp -q "$WORK/dist/cdx_reindex_runner.exe" "$HOST:C:/PearnlyCloudProbe/tunnel/dist/"
    scp -q "$WORK"/dist/probe-dbf/*.DBF "$HOST:C:/PearnlyCloudProbe/tunnel/dist/probe-dbf/"
    scp -q -r "$WORK/dist/src/." "$HOST:C:/PearnlyCloudProbe/build/src/"
    # 已装 CC 的程序文件夹（只读复制，不含卸载文件）；T6 用它看正式版程序本身在云端能不能起来
    ps "\$s=@('C:\\Users\\skin3\\AppData\\Local\\Programs\\CC','C:\\Users\\skin3\\AppData\\Local\\Programs\\Pearnly Companion') | Where-Object { Test-Path (Join-Path \$_ 'companion.exe') } | Select-Object -First 1; if (\$s) { robocopy \$s $R\\tunnel\\dist\\cc /E /XF unins*.* /NFL /NDL /NJH /NJS /R:1 /W:1 | Out-Null; 'cc copied: ' + (Get-Item $R\\tunnel\\dist\\cc\\companion.exe).VersionInfo.ProductVersion } else { 'no installed CC under skin3 Programs' }"
    ps "Get-ChildItem -Recurse -File $R\\bridge,$R\\tunnel\\probe.ps1 | ForEach-Object { \$_.Name + ' ' + (Get-FileHash \$_.FullName).Hash.Substring(0,12) }"
    ;;
  parse)
    ps "\$bad=0; foreach (\$f in @(Get-ChildItem $R\\bridge\\*.ps1) + @(Get-Item $R\\tunnel\\probe.ps1)) { \$e=\$null; [void][System.Management.Automation.Language.Parser]::ParseFile(\$f.FullName,[ref]\$null,[ref]\$e); if (\$e) { \$bad++; \$f.Name + ': ' + (\$e | ForEach-Object { \$_.Message + ' @' + \$_.Extent.StartLineNumber }) -join '; ' } else { \$f.Name + ': ok' } }; 'errors: ' + \$bad"
    ;;
  build)
    ssh "$HOST" "powershell -NoProfile -ExecutionPolicy Bypass -File $R\\bridge\\build-ccprobe.ps1"
    ;;
  as|bg)
    s=$1; shift
    wait=600; [ "$cmd" = bg ] && wait=0
    argstr=""; [ $# -gt 0 ] && argstr="-Arguments \"$*\""
    ssh "$HOST" "powershell -NoProfile -ExecutionPolicy Bypass -File $R\\bridge\\run-as-skin3.ps1 -Script $R\\bridge\\$s.ps1 $argstr -Wait $wait"
    ;;
  ctl)
    ps "New-Item -ItemType File -Force -Path $R\\tunnel\\ctl\\$1 | Out-Null; 'ctl $1 placed ' + (Get-Date).ToString('s')"
    ;;
  status)
    ps "\$o='$R\\tunnel\\out'; \$hb=@(Get-ChildItem \$o -Filter hb-*.json | Sort-Object Name); 'hb=' + \$hb.Count + ' latest=' + \$(if(\$hb){\$hb[-1].Name}); 'cc ticks=' + @(Get-ChildItem \$o -Filter cc-0*.json).Count; 'shots=' + @(Get-ChildItem \$o -Filter shot-*.json).Count; Get-ChildItem \$o -Filter t6-*.json | ForEach-Object Name; Get-ChildItem \$o -Filter err-*.json | ForEach-Object Name; if (Test-Path \$o\\stopped.json) { 'stopped: ' + (Get-Content \$o\\stopped.json -Raw) }; if(\$hb){ Get-Content \$hb[-1].FullName -Raw }"
    ;;
  pull)
    mkdir -p "$WORK/evidence"
    scp -q -r "$HOST:C:/PearnlyCloudProbe/tunnel/out" "$WORK/evidence/"
    scp -q -r "$HOST:C:/PearnlyCloudProbe/logs" "$WORK/evidence/"
    find "$WORK/evidence" -type f | wc -l
    ;;
  cleanup)
    ps "Get-ScheduledTask -TaskName 'PearnlyCloudProbe-*' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:\$false; Remove-Item -Recurse -Force $R -ErrorAction SilentlyContinue; 'tasks left: ' + @(Get-ScheduledTask -TaskName 'PearnlyCloudProbe-*' -ErrorAction SilentlyContinue).Count; 'dir exists: ' + (Test-Path $R)"
    ;;
  *) sed -n 2,17p "$0"; exit 1 ;;
esac
