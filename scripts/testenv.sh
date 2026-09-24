#!/usr/bin/env bash
# Mac 侧驱动：把本工作树的栈定义同步到 Windows 测试机，并在那边执行 compose。
# 真正干活的是 infrastructure/testenv/run.sh（在 WSL 里以 root 执行），这里只做
# 打包、投递、环境文件和端口转发，避免多层引号把命令打散。
#
# 用法：
#   PEARNLY_TESTENV_IMAGE=ghcr.io/...@sha256:... bash scripts/testenv.sh up
#   bash scripts/testenv.sh sites dms
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
host=${PEARNLY_TESTENV_HOST:-lenovo-office-lan}
distro=${PEARNLY_TESTENV_DISTRO:-Ubuntu-24.04}
remote_deploy=/opt/pearnly-testenv/deploy
win_temp=/mnt/c/Users/codexops/AppData/Local/Temp
archive=pearnly-testenv-deploy.tgz

wsl() { ssh -o BatchMode=yes "$host" "wsl -d $distro -u root -- $*"; }
wsl_bash() { ssh -o BatchMode=yes "$host" "wsl -d $distro -u root -- bash $*"; }
bash_remote() { wsl_bash "$remote_deploy/infrastructure/testenv/run.sh" "$@"; }

sync() {
  tar -czf "/tmp/$archive" -C "$root" \
    infrastructure/production infrastructure/keycloak infrastructure/testenv
  scp -q -o BatchMode=yes "/tmp/$archive" "$host:C:/Users/codexops/AppData/Local/Temp/$archive"
  wsl mkdir -p "$remote_deploy"
  wsl tar xzf "$win_temp/$archive" -C /opt/pearnly-testenv/deploy
  echo "synced $(tar tzf /tmp/$archive | wc -l | tr -d ' ') entries to $remote_deploy"
  rm -f "/tmp/$archive"
}

write_env() {
  local image=${PEARNLY_TESTENV_IMAGE:-}
  if [[ -z "$image" ]]; then
    echo 'PEARNLY_TESTENV_IMAGE must be the digest the production host is running' >&2
    exit 2
  fi
  local state=$root/.testenv-secrets
  if [[ ! -s "$state" ]]; then
    umask 077
    {
      printf 'DB_ROOT_PASSWORD=%s\n' "$(openssl rand -hex 24)"
      printf 'KEYCLOAK_DB_PASSWORD=%s\n' "$(openssl rand -hex 24)"
      printf 'KEYCLOAK_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 24)"
    } > "$state"
  fi
  umask 077
  {
    printf 'PEARNLY_IMAGE=%s\n' "$image"
    cat "$state"
    printf 'AUTH_HOST=%s\n' "${PEARNLY_TESTENV_AUTH_HOST:-auth.test.localhost}"
  } > /tmp/pearnly-testenv.env
  scp -q -o BatchMode=yes /tmp/pearnly-testenv.env "$host:C:/Users/codexops/AppData/Local/Temp/pearnly-testenv.env"
  wsl install -m 600 "$win_temp/pearnly-testenv.env" /opt/pearnly-testenv/env
  rm -f /tmp/pearnly-testenv.env
  echo 'runtime env written to the test host (600, not in the repo)'
}

urls() {
  cat <<EOF
Mac 侧先开转发（另开一个窗口，保持不关）：
  ssh -N -L 8181:127.0.0.1:8181 -L 8182:127.0.0.1:8182 -L 8183:127.0.0.1:8183 -L 8184:127.0.0.1:8184 -L 8180:127.0.0.1:8180 $host

然后浏览器直接打开（*.localhost 由浏览器解析到本机回环，不需要改 hosts）：
  cowork  http://cowork.${PEARNLY_TESTENV_SITE_SUFFIX:-test.localhost}:8181
  erp     http://erp.${PEARNLY_TESTENV_SITE_SUFFIX:-test.localhost}:8182
  dms     http://dms.${PEARNLY_TESTENV_SITE_SUFFIX:-test.localhost}:8183
  billing http://billing.internal:8184
  keycloak http://auth.${PEARNLY_TESTENV_SITE_SUFFIX:-test.localhost}:8180
EOF
}

image_from_env() {
  if [[ -n "${PEARNLY_TESTENV_IMAGE:-}" ]]; then
    printf '%s\n' "$PEARNLY_TESTENV_IMAGE"
  else
    wsl grep -E '^PEARNLY_IMAGE=' /opt/pearnly-testenv/env | cut -d= -f2-
  fi
}

pull() {
  # 用与 CI 相同的强制命令脚本拉镜像：凭据走 stdin，只在临时 DOCKER_CONFIG 里用一次，
  # 测试机上不留长期凭据。
  local image; image=$(image_from_env)
  [[ "$image" == *@sha256:* ]] || { echo "need a pinned digest, got: $image" >&2; exit 2; }
  umask 077
  { printf '%s\n' "$image"; gh auth token; } > /tmp/pearnly-testenv-pull.txt
  ssh -o BatchMode=yes "$host" \
    "wsl -d $distro -u root -- python3 $remote_deploy/infrastructure/production/receive-image.py" \
    < /tmp/pearnly-testenv-pull.txt
  rm -f /tmp/pearnly-testenv-pull.txt
  echo "pulled $image"
}

publish_import() {
  # 把 Mac 上导出的测试租户备份放到测试机的 import 目录（不进仓库、不长期留存）。
  local group=$1 site=$2 src=${PEARNLY_TESTENV_IMPORT_SRC:-/tmp/te-export}
  local slug; slug=$(printf '%s' "$site" | tr '.' '_')
  local stage=/tmp/pearnly-import/$site
  mkdir -p "$stage"
  local db pub priv
  db=$(ls -1t "$src"/*"$slug"*database.sql.gz 2>/dev/null | head -1)
  pub=$(ls -1t "$src"/*"$slug"*files.tar 2>/dev/null | grep -v private-files | head -1)
  priv=$(ls -1t "$src"/*"$slug"*private-files.tar 2>/dev/null | head -1)
  [[ -n "$db" && -n "$pub" && -n "$priv" && -f "$src/$site-site_config.json" ]] || {
    echo "missing export for $site under $src (need db/public/private/site_config)" >&2; exit 2; }
  cp "$db" "$pub" "$priv" "$src/$site-site_config.json" "$stage/"
  # 传父目录，远端结构与 win_temp/pearnly-import/<site>/ 一致
  scp -q -o BatchMode=yes -r /tmp/pearnly-import "$host:C:/Users/codexops/AppData/Local/Temp/"
  wsl mkdir -p "/opt/pearnly-testenv/import/$site"
  wsl cp -r "$win_temp/pearnly-import/$site/." "/opt/pearnly-testenv/import/$site/"
  echo "published $(ls -1 "$stage" | wc -l | tr -d ' ') files for $site"
}

lab_start() {
  # 一键开始测试：从线上导出测试租户 → 取回 → 导入测试环境 → 把域名切过来。
  local prod_host=${PEARNLY_TESTENV_PROD_HOST:-107.155.65.74}
  local prod_key=${PEARNLY_TESTENV_PROD_KEY:-$HOME/.config/pearnly-production/operator}
  local prod_dir=/root/te-export
  [[ -s "$prod_key" ]] || { echo "missing production key $prod_key" >&2; exit 2; }

  echo "== 1/4 线上导出测试租户"
  ssh -i "$prod_key" -o BatchMode=yes "root@$prod_host" 'bash -s' "$prod_dir" \
    dms:testdms.pearnly.com erp:testerp.pearnly.com \
    < "$root/infrastructure/testenv/export-test-tenant.sh" | tail -3

  echo "== 2/4 取回本机"
  mkdir -p /tmp/te-export
  scp -q -i "$prod_key" -o BatchMode=yes "root@$prod_host:$prod_dir/*" /tmp/te-export/

  echo "== 3/4 导入测试环境"
  bash "$0" import dms testdms.pearnly.com | tail -2
  bash "$0" import erp testerp.pearnly.com | tail -2

  echo "== 4/4 把域名切到测试环境"
  bash "$root/scripts/testenv-cloudflare.sh" switch-lab
  bash "$root/scripts/testenv-cloudflare.sh" status
}

lab_stop() {
  # 一键结束测试：域名切回线上。
  bash "$root/scripts/testenv-cloudflare.sh" switch-prod
  bash "$root/scripts/testenv-cloudflare.sh" status
}

case "${1:-}" in
  setup)
    sync
    status=0
    wsl_bash "$remote_deploy/infrastructure/testenv/wsl-setup.sh" || status=$?
    if [[ "$status" == 3 ]]; then
      echo 'restarting WSL to pick up systemd, then re-running setup'
      ssh -o BatchMode=yes "$host" "wsl --shutdown"
      sleep 8
      wsl_bash "$remote_deploy/infrastructure/testenv/wsl-setup.sh"
    elif [[ "$status" != 0 ]]; then
      exit "$status"
    fi
    ;;
  sync) sync ;;
  env) sync; write_env ;;
  pull) sync; pull ;;
  config|ps|check|smoke|down|reset|base) sync; bash_remote "$@" ;;
  tunnel) shift; sync; bash_remote tunnel "$@" ;;
  tunnel-named)
    cf=$HOME/.config/pearnly-testenv/tunnel.token
    [[ -s "$cf" ]] || { echo "missing $cf (run scripts/testenv-cloudflare.sh tunnel-create)" >&2; exit 2; }
    sync
    scp -q -o BatchMode=yes "$cf" "$host:C:/Users/codexops/AppData/Local/Temp/pearnly-tunnel.token"
    wsl install -m 600 "$win_temp/pearnly-tunnel.token" /opt/pearnly-testenv/cloudflare-tunnel.token
    wsl_bash "$remote_deploy/infrastructure/testenv/tunnel-named.sh"
    ;;
  up) sync; write_env; bash_remote up ;;
  logs) shift; sync; bash_remote logs "$@" ;;
  sites) shift; sync; bash_remote sites "$@" ;;
  import)
    group=${2:?group}; site=${3:?site}
    sync; publish_import "$group" "$site"; bash_remote import "$group" "$site"
    ;;
  migrate)
    group=${2:?group}; site=${3:?site}
    sync; bash_remote migrate "$group" "$site"
    ;;
  sh) shift; bash_remote sh "$@" ;;
  urls) urls ;;
  lab-start) lab_start ;;
  lab-stop) lab_stop ;;
  *)
    cat <<'EOF'
usage: testenv.sh {setup|sync|env|pull|config|up|ps|logs|urls|down|reset|sites|sh}

  setup            在那台 Windows 的 WSL 里装好与正式机同源的宿主运行环境
  env              同步栈定义并写运行环境文件（需要 PEARNLY_TESTENV_IMAGE）
  up               拉镜像并起来（先基础层，再三个产品组）
  sites [组...]    建/迁移测试站点，默认 dms；组为 dms/erp/cowork
  urls             打印 Mac 侧端口转发与可直接打开的地址
  logs <服务>      看某个服务日志
  reset            停栈并删除测试数据卷（只删 pearnly-testenv_ 开头的卷）
EOF
    ;;
esac
