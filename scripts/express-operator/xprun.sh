#!/bin/bash
# xprun.sh <actions.json> <截图名…>：把动作清单交给测试机，以会话用户身份跑 express-ui.ps1，打印日志并拉回截图到 ./xp/。
# 环境变量：LAB_HOST（默认 lenovo-office-lan）、LAB_DIR（测试机上脚本目录，默认 C:/PearnlyStress/_acc_cc）。
set -e
HOST=${LAB_HOST:-lenovo-office-lan}; DIR=${LAB_DIR:-C:/PearnlyStress/_acc_cc}; WDIR=${DIR//\//\\}
A=$1; shift; mkdir -p xp
scp -q "$A" "$HOST:$DIR/xp-actions.json"
ssh "$HOST" "Remove-Item $WDIR\\xp-log.txt -ErrorAction SilentlyContinue; powershell -ExecutionPolicy Bypass -File $WDIR\\run-as-session-user.ps1 -Exe C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -Arguments '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $WDIR\\express-ui.ps1' -Wait 120 | Out-Null; Get-Content $WDIR\\xp-log.txt -Encoding UTF8" 2>&1
for n in "$@"; do scp -q "$HOST:$DIR/xp-$n.png" "xp/$n.png" && echo "got $n"; done
