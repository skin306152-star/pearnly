#!/usr/bin/env bash
# GitHub 工作流（.github/workflows/flows.yml）在一台标准机器上起回归实验室，只建给定的站点：
#   GH_TOKEN=<工作流自带令牌> bash scripts/flows/github-lab.sh <站点…>
# 生产镜像在 GHCR 是私有包（仓库公开后也不改）：用工作流自带的 GITHUB_TOKEN（packages: read）登录、拉完立刻登出，
# 令牌只走标准输入，不进日志、不落盘（docker 登录信息在拉完后随 logout 删掉）。镜像 digest 只认 regression-lab.yml 里那一个。
set -euo pipefail
: "${GH_TOKEN:?GH_TOKEN (the workflow token with packages: read)}"
[ "$#" -gt 0 ] || { echo "usage: $0 <site>..." >&2; exit 2; }
cd "$(dirname "$0")/../.."
LAB=(docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml)
export PEARNLY_REGRESSION_SRC="$PWD"
start=$SECONDS
printf '%s' "$GH_TOKEN" | docker login ghcr.io -u "${GITHUB_ACTOR:-github-actions}" --password-stdin > /dev/null
trap 'docker logout ghcr.io > /dev/null 2>&1 || true' EXIT
"${LAB[@]}" pull --quiet
docker logout ghcr.io > /dev/null
echo "pulled in $((SECONDS - start))s"
"${LAB[@]}" up -d --wait
echo "lab up in $((SECONDS - start))s"
"${LAB[@]}" exec -T bench env/bin/python /workspace/scripts/regression/lab_init.py "$@"
echo "sites ready in $((SECONDS - start))s: $*"
