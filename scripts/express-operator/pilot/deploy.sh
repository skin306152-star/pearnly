#!/bin/bash
# deploy.sh：把试点代码推到试点电脑的 <WORK>\pilot（只放这一个目录，不碰别的）。
# 环境变量：LAB_HOST（默认 rec-laptop，10-26 起专给试点用的录制笔记本）、LAB_WORK（默认 C:/XpPilot）。远程外壳是 cmd 也能用。
# 之后起代理：ssh rec-laptop "powershell -NoProfile -ExecutionPolicy Bypass -File C:\XpPilot\pilot\start-agent.ps1 -Pythonw C:\Python313\pythonw.exe"
set -e
HOST=${LAB_HOST:-rec-laptop}
WORK=${LAB_WORK:-C:/XpPilot}
HERE=$(cd "$(dirname "$0")" && pwd)
ssh "$HOST" "powershell -NoProfile -Command \"New-Item -ItemType Directory -Force '$WORK/pilot','$WORK/copies' | Out-Null\"" </dev/null
scp -q "$HERE"/*.py "$HERE"/*.json "$HERE"/*.ps1 "$HOST:$WORK/pilot/"
echo "已推到 $HOST:$WORK/pilot"
