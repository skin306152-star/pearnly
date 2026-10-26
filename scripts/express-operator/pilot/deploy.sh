#!/bin/bash
# deploy.sh：把试点代码推到测试机 C:\PearnlyStress\_xp_pilot\pilot（只放这一个目录，不碰别的）。
# 环境变量：LAB_HOST（默认 lenovo-office-lan）。之后在测试机上起代理：
#   ssh lenovo-office-lan "powershell -ExecutionPolicy Bypass -File C:\PearnlyStress\_xp_pilot\pilot\start-agent.ps1 -Pythonw <pythonw 路径>"
set -e
HOST=${LAB_HOST:-lenovo-office-lan}
HERE=$(cd "$(dirname "$0")" && pwd)
ssh "$HOST" "New-Item -ItemType Directory -Force C:/PearnlyStress/_xp_pilot/pilot | Out-Null" </dev/null
scp -q "$HERE"/*.py "$HERE"/*.json "$HERE"/*.ps1 "$HOST:C:/PearnlyStress/_xp_pilot/pilot/"
echo "已推到 $HOST:C:/PearnlyStress/_xp_pilot/pilot"
