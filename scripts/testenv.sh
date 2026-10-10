#!/usr/bin/env bash
# Mac 侧驱动：把栈定义同步到 Windows 测试机，并在那边执行 compose。
# 真正干活的是 infrastructure/testenv/run.sh（在 WSL 里以 root 执行），这里只做
# 打包、投递、环境文件和端口转发，避免多层引号把命令打散。
#
# **一个 app 一套栈**：DMS / ERP / Cowork 各有自己的项目名、MariaDB、Redis、站点卷与
# 网络，互不影响；同时只跑一个。app 用第一个附加参数或 PEARNLY_TESTENV_APP 指定，
# 默认 dms。
#
# **工具与被测代码分开**：驱动与测试辅助脚本取**本分支**，产品代码与 compose 取
# PEARNLY_TESTENV_SRC（默认就是当前工作树）。所以「改完代码要上测试机」时，把源指向
# 被测分支即可，不必先把工具改动合进被测分支。
#
# 用法：
#   PEARNLY_TESTENV_IMAGE=ghcr.io/...@sha256:... bash scripts/testenv.sh up dms
#   PEARNLY_TESTENV_SRC=../dms-five-minute-snapshots/pearnly bash scripts/testenv.sh lab-start dms
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
src=${PEARNLY_TESTENV_SRC:-$root}
host=${PEARNLY_TESTENV_HOST:-lenovo-office-lan}
distro=${PEARNLY_TESTENV_DISTRO:-Ubuntu-24.04}
remote_deploy=/opt/pearnly-testenv/deploy
win_temp=/mnt/c/Users/codexops/AppData/Local/Temp
archive=pearnly-testenv-deploy.tgz

wsl() { ssh -o BatchMode=yes "$host" "wsl -d $distro -u root -- $*"; }
wsl_bash() { ssh -o BatchMode=yes "$host" "wsl -d $distro -u root -- bash $*"; }
bash_remote() { wsl_bash "$remote_deploy/infrastructure/testenv/run.sh" "$@"; }
# 带 app 的远端调用：把 app 用 env 传进 WSL，不混进子命令参数里（`logs <服务>` 这类
# 子命令自己还有参数，混在一起会歧义）。
bash_remote_app() {
  local target_app=$1; shift
  ssh -o BatchMode=yes "$host" \
    "wsl -d $distro -u root -- env TESTENV_APP=$target_app bash $remote_deploy/infrastructure/testenv/run.sh $*"
}

resolve_app() {
  case "${1:-${PEARNLY_TESTENV_APP:-dms}}" in
    dms|erp|cowork) echo "${1:-${PEARNLY_TESTENV_APP:-dms}}" ;;
    *) echo "unknown app: ${1:-} (expected dms|erp|cowork)" >&2; exit 2 ;;
  esac
}

sync() {
  # 工具取本分支，产品代码与 compose 取被测树（$src）。
  # COPYFILE_DISABLE：macOS 自带的 tar 默认把扩展属性写成 `._文件名` 一起打包，
  # 解到测试机就是一堆同名的二进制 `._*.py`（2026-09-27 实测混进了实时源码目录）。
  COPYFILE_DISABLE=1 tar -czf "/tmp/$archive" \
    -C "$root" infrastructure/testenv \
    -C "$src" infrastructure/production infrastructure/keycloak
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
  # 口令只认一份：本机凭据目录（与 Cloudflare 凭据同处，600）。工作树根目录那份是旧位置，
  # 仍兼容；两处都没有才生成，并直接写进凭据目录——换工作树不会再"重新生成口令"。
  local state=$HOME/.config/pearnly-testenv/testenv-secrets
  [[ -s "$state" ]] || [[ ! -s "$root/.testenv-secrets" ]] || state=$root/.testenv-secrets
  if [[ ! -s "$state" ]]; then
    mkdir -p "$(dirname "$state")"
    umask 077
    {
      printf 'DB_ROOT_PASSWORD=%s\n' "$(openssl rand -hex 24)"
      printf 'KEYCLOAK_DB_PASSWORD=%s\n' "$(openssl rand -hex 24)"
      printf 'KEYCLOAK_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 24)"
    } > "$state"
  fi
  # Cowork 实验室站点管理员的本地密码（线上没有 Cowork 租户，见 infrastructure/testenv/cowork-lab.py）。
  grep -q '^COWORK_LAB_ADMIN_PASSWORD=' "$state" \
    || (umask 077; printf 'COWORK_LAB_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 12)" >> "$state")
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
  rm -rf "$stage"; mkdir -p "$stage"
  local db pub priv
  db=$(ls -1t "$src"/*"$slug"*database.sql.gz 2>/dev/null | head -1)
  pub=$(ls -1t "$src"/*"$slug"*files.tar 2>/dev/null | grep -v private-files | head -1)
  priv=$(ls -1t "$src"/*"$slug"*private-files.tar 2>/dev/null | head -1)
  [[ -n "$db" && -n "$pub" && -n "$priv" && -f "$src/$site-site_config.json" ]] || {
    echo "missing export for $site under $src (need db/public/private/site_config)" >&2; exit 2; }
  cp "$db" "$pub" "$priv" "$src/$site-site_config.json" "$stage/"
  # 只传本站点这一组（以前传整个 /tmp/pearnly-import，别的站点暂存过的文件也跟着走）。
  wsl mkdir -p "$win_temp/pearnly-import" "/opt/pearnly-testenv/import/$site"
  wsl rm -rf "$win_temp/pearnly-import/$site"
  scp -q -o BatchMode=yes -r "$stage" "$host:C:/Users/codexops/AppData/Local/Temp/pearnly-import/"
  wsl cp -r "$win_temp/pearnly-import/$site/." "/opt/pearnly-testenv/import/$site/"
  echo "published $(ls -1 "$stage" | wc -l | tr -d ' ') files for $site"
}

# WSL 保活（2026-09-28）：测试机重启后没有常驻的 wsl 会话，WSL 空闲一会儿就整体关掉、测试栈跟着停。
# 用 WMI 起一个独立的 `wsl … sleep infinity`（不随 SSH 会话结束），lab-stop 时结束它。已在跑就不重复起。
keepalive() {
  local action=$1 script
  if [[ "$action" == start ]]; then
    script="if (-not (Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'pearnly-keepalive' })) { \$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='powershell.exe -NoProfile -WindowStyle Hidden -c \"wsl -d $distro -u root -- sleep infinity # pearnly-keepalive\"'}; 'keepalive started ' + \$r.ProcessId } else { 'keepalive already running' }"
  else
    script="Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'pearnly-keepalive|-u root -- sleep infinity' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }; 'keepalive stopped'"
  fi
  ssh -o BatchMode=yes "$host" "powershell -NoProfile -EncodedCommand $(printf '%s' "$script" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\n')"
}

lab_start() {
  # 一键开始测试（一个产品一套栈，一次只跑一个）。**默认复用**：栈、站点卷与导入过的租户
  # 数据都留在测试机上，再跑只做「必要时拉镜像 → 起栈（自动升级库、清缓存）→ 切域名」。
  # 只有测试机上还没有这个租户，或显式 PEARNLY_TESTENV_REFRESH=1，才从线上重新导出导入。
  # `lab-start erp` 只动 ERP：ERP 域名切测试机，DMS 域名与线上都不碰。
  local apps; apps=$(lab_apps "$@" | paste -sd' ' -)
  local count; count=$(lab_apps "$@" | wc -l | tr -d ' ')
  (( count == 1 )) || {
    echo '一次只跑一个产品：每个 app 一套独立栈（数据库/Redis/卷都分开），' >&2
    echo '同时起两套会争用端口，也失去了「测一个不影响另一个」的意义。' >&2
    echo "请分开跑，例如：lab-start dms" >&2
    exit 2
  }
  local first; first=$(lab_apps "$@" | head -1)
  local spec; spec=$(printf '%s\n' "$first" | tenant_spec)
  local site=${spec#*:}
  # 账本导入 / 升级跟着本产品的栈走，不能落回默认的 dms。
  export PEARNLY_TESTENV_APP=$first
  echo "本次涉及产品：$apps（站点 $site）"

  echo "== 1/4 镜像：测试机上没有这个 digest 才拉"
  if has_image; then echo "· 已在测试机：$(image_from_env)"; else bash "$0" pull | tail -2; fi

  keepalive start
  echo "== 2/4 起本栈（先停别的测试栈；自动升级站点库、清缓存、写运行配置）"
  bash "$0" up "$first" | tail -8

  if [[ "${PEARNLY_TESTENV_REFRESH:-0}" == 1 ]] || ! tenant_present "$first" "$site"; then
    echo "== 3/4 刷新租户数据：线上导出 → 只取本次这一组 → 导入 → 账本 → 再收尾"
    lab_refresh "$first" "$spec"
  else
    echo "== 3/4 复用测试机上已导入的 $site（要最新线上数据：PEARNLY_TESTENV_REFRESH=1）"
  fi

  # 测试环境跑被测工作树的实时代码（用户 2026-09-27）：之后改代码只要 `testenv.sh live`，不必再构建镜像。
  echo "== 实时源码：挂上 $src 的代码"
  live "$first" | tail -6

  echo "== 4/4 域名切到测试环境；线上那份测试租户的定时任务暂停（lab-stop 恢复）"
  bash "$root/scripts/testenv-cloudflare.sh" switch-lab $apps
  bash "$root/scripts/testenv-cloudflare.sh" status $apps
  prod_scheduler pause "$first"
}

live() {
  # 实时源码：把被测工作树（$src）当前的产品代码（含未提交的改动）同步到测试机，挂进本产品容器并重启。
  # 表结构 / 补丁 / hooks 没变时只清缓存（十几秒）；变了才迁移。镜像不换，仍是 PEARNLY_TESTENV_IMAGE 那份。
  local target_app; target_app=$(resolve_app "${1:-}")
  local apps=(pearnly_core pearnly_cowork pearnly_dms pearnly_erp_portal pearnly_control)
  local tarball=/tmp/pearnly-testenv-live.tgz list migrate_hash
  list=$(cd "$src/apps" && git ls-files -co --exclude-standard -- "${apps[@]}" \
    | while IFS= read -r file; do [[ -f "$file" ]] && printf '%s\n' "$file"; done)
  [[ -n "$list" ]] || { echo "no product code under $src/apps" >&2; exit 2; }
  # 前端 SPA 是构建产物（.gitignore），默认用镜像里那份。调前端时设 PEARNLY_TESTENV_LOCAL_SPA=1：
  # 带上工作树里本地构建好的那份（先在 frontend/ 里 vite build），省掉每轮换镜像。
  if [[ "${PEARNLY_TESTENV_LOCAL_SPA:-}" == 1 ]]; then
    local spa=pearnly_core/pearnly_core/public/pearnly-app
    [[ -f "$src/apps/$spa/index.html" ]] || {
      echo "PEARNLY_TESTENV_LOCAL_SPA=1 but $src/apps/$spa is not built (run vite build in frontend/)" >&2; exit 2; }
    list+=$'\n'$(cd "$src/apps" && find "$spa" -type f | LC_ALL=C sort)
    # 智能体对话页（ADR-019，frontend/assistant）本地构建过就一起带上；没构建就用镜像里那份。
    local assistant=pearnly_core/pearnly_core/public/assistant
    [[ -f "$src/apps/$assistant/index.html" ]] && list+=$'\n'$(cd "$src/apps" && find "$assistant" -type f | LC_ALL=C sort)
  fi
  (cd "$src/apps" && printf '%s\n' "$list" | COPYFILE_DISABLE=1 tar -czf "$tarball" -T -)
  migrate_hash=$(cd "$src/apps" && printf '%s\n' "$list" \
    | grep -E '/doctype/.*\.json$|/patches\.txt$|/patches/.*\.py$|/hooks\.py$|/fixtures/' \
    | LC_ALL=C sort | while IFS= read -r file; do printf '%s\n' "$file"; cat "$file"; done | shasum -a 256 | cut -c1-16)
  sync
  scp -q -o BatchMode=yes "$tarball" "$host:C:/Users/codexops/AppData/Local/Temp/pearnly-testenv-live.tgz"
  rm -f "$tarball"
  echo "源码：$(git -C "$src" rev-parse --short HEAD)$(git -C "$src" diff --quiet HEAD -- apps || echo ' + 未提交改动')（$src）"
  # 测试机上 `wsl --shutdown` 过（例如改网络模式）会带走保活进程，WSL 空闲关掉后隧道报 530（2026-10-07）：
  # 每次同步顺带补起，已在跑就什么都不做。
  keepalive start
  bash_remote_app "$target_app" live "$win_temp/pearnly-testenv-live.tgz" "$migrate_hash"
}

lab_refresh() {
  local first=$1 spec=$2
  local prod_host=${PEARNLY_TESTENV_PROD_HOST:-107.155.65.74}
  local prod_key=${PEARNLY_TESTENV_PROD_KEY:-$HOME/.config/pearnly-production/operator}
  [[ -s "$prod_key" ]] || { echo "missing production key $prod_key" >&2; exit 2; }
  # 每次一个新目录：线上 /root/te-export 下以前的导出（别的产品、旧批次）一律不取。
  local run; run=$(date -u +%Y%m%dT%H%M%SZ)
  local prod_dir=/root/te-export/$run
  local local_dir; local_dir=$(mktemp -d "${TMPDIR:-/tmp}/te-export.XXXXXX")
  ssh -i "$prod_key" -o BatchMode=yes "root@$prod_host" 'bash -s' "$prod_dir" \
    "$spec" billing:billing.internal \
    < "$root/infrastructure/testenv/export-test-tenant.sh" | tail -3
  scp -q -i "$prod_key" -o BatchMode=yes "root@$prod_host:$prod_dir/*" "$local_dir/"
  echo "· 取回 $(ls -1 "$local_dir" | wc -l | tr -d ' ') 个文件（$(du -sh "$local_dir" | cut -f1)）"
  export PEARNLY_TESTENV_IMPORT_SRC=$local_dir
  bash "$0" import "${spec%%:*}" "${spec#*:}" | tail -2
  # 账本（控制面）必须在本栈自己的 billing-web 里，否则产品的付费路径会卡在计费那一步无限重试。
  bash "$0" import billing billing.internal | tail -2
  bash "$0" migrate billing billing.internal | tail -2
  # 导入是全新的库，起栈时那次升级没有覆盖到，必须再来一次。
  bash "$0" finish "$first" | tail -10
  rm -rf "$local_dir"
}

has_image() {
  local image; image=$(image_from_env)
  [[ "$image" == *@sha256:* ]] && wsl docker image inspect "$image" >/dev/null 2>&1
}

tenant_present() {
  local app=$1 site=$2
  wsl test -s "/var/lib/docker/volumes/pearnly-testenv-${app}_sites-${app}/_data/$site/site_config.json"
}

# 线上那份测试租户的定时任务：切到测试机期间暂停，切回线上时恢复（用户 2026-09-27 定）。
# 为什么：切域名只把 LINE / 网页流量引到测试机，线上那份站点的定时任务照跑 —— 它按线上旧配置
# 每 10 分钟把 LINE 图文菜单挂回去、用同一个第三方账号登录做回读与补救，测出来的菜单、同步与
# 并发结果就不是被测代码的。只动测试租户（`tenant_spec` 里那一个站点），正式租户不碰。
prod_scheduler() {
  local action=$1 app=$2 spec site group
  spec=$(printf '%s\n' "$app" | tenant_spec); group=${spec%%:*}; site=${spec#*:}
  local prod_host=${PEARNLY_TESTENV_PROD_HOST:-107.155.65.74}
  local prod_key=${PEARNLY_TESTENV_PROD_KEY:-$HOME/.config/pearnly-production/operator}
  [[ -s "$prod_key" ]] || { echo "missing production key $prod_key" >&2; return 2; }
  ssh -i "$prod_key" -o BatchMode=yes "root@$prod_host" \
    "docker exec -w /home/frappe/frappe-bench pearnly-production-$group-web-1 bench --site $site scheduler $action \
     && docker exec -w /home/frappe/frappe-bench pearnly-production-$group-web-1 bench --site $site scheduler status"
}

lab_stop() {
  # 一键结束测试：域名切回线上 → 停本栈（站点与数据库卷保留，下次 up 直接回来）。
  local apps; apps=$(lab_apps "$@" | paste -sd' ' -)
  bash "$root/scripts/testenv-cloudflare.sh" switch-prod $apps
  bash "$root/scripts/testenv-cloudflare.sh" status $apps
  for one in $apps; do prod_scheduler resume "$one"; done
  for one in $apps; do bash "$0" down "$one"; done
  keepalive stop || true
  echo '本栈已停，数据卷保留；重新开始测试用 lab-start。'
}

# 有公网测试租户的产品：app → <组>:<站点>；不带参数=全部。
lab_apps() {
  if (( $# == 0 )); then printf '%s\n' dms erp cowork; else printf '%s\n' "$@"; fi
}

tenant_spec() {
  while read -r app; do
    case "$app" in
      dms) echo 'dms:testdms.pearnly.com' ;;
      erp) echo 'erp:testerp.pearnly.com' ;;
      cowork) echo 'cowork:testcowork.pearnly.com' ;;
      *) echo "unknown app: $app (expected dms|erp|cowork)" >&2; exit 2 ;;
    esac
  done
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
  config|ps|check|smoke|down|reset|base)
    app=$(resolve_app "${2:-}"); sync; bash_remote_app "$app" "$1" ;;
  tunnel) shift; sync; bash_remote tunnel "$@" ;;
  tunnel-named)
    cf=$HOME/.config/pearnly-testenv/tunnel.token
    [[ -s "$cf" ]] || { echo "missing $cf (run scripts/testenv-cloudflare.sh tunnel-create)" >&2; exit 2; }
    sync
    scp -q -o BatchMode=yes "$cf" "$host:C:/Users/codexops/AppData/Local/Temp/pearnly-tunnel.token"
    wsl install -m 600 "$win_temp/pearnly-tunnel.token" /opt/pearnly-testenv/cloudflare-tunnel.token
    wsl_bash "$remote_deploy/infrastructure/testenv/tunnel-named.sh"
    ;;
  up) app=$(resolve_app "${2:-}"); sync; write_env; bash_remote_app "$app" up ;;
  finish) app=$(resolve_app "${2:-}"); sync; bash_remote_app "$app" finish ;;
  # logs / sh 后面还有自己的参数，app 只能从环境变量选：
  #   PEARNLY_TESTENV_APP=erp bash scripts/testenv.sh logs dms-web
  logs) shift; sync; bash_remote_app "$(resolve_app)" logs "$@" ;;
  sites) app=$(resolve_app "${2:-}"); sync; bash_remote_app "$app" sites ;;
  lab-init) app=$(resolve_app "${2:-cowork}"); sync; bash_remote_app "$app" lab-init ;;
  import)
    group=${2:?group}; site=${3:?site}
    # 账本不是产品：它住在当前那套栈的 billing-web 里（app 用 PEARNLY_TESTENV_APP 选）。
    target=$group; [[ "$group" == billing ]] && target=$(resolve_app)
    sync; publish_import "$group" "$site"; bash_remote_app "$target" import "$group" "$site"
    ;;
  migrate)
    group=${2:?group}; site=${3:?site}
    target=$group; [[ "$group" == billing ]] && target=$(resolve_app)
    sync; bash_remote_app "$target" migrate "$group" "$site"
    ;;
  ai-entry) sync; bash_remote_app "$(resolve_app)" ai-entry "${2:-testai.pearnly.com}" ;;
  sh) shift; bash_remote_app "$(resolve_app)" sh "$@" ;;
  urls) urls ;;
  lab-start) shift; lab_start "$@" ;;
  live) live "${2:-}" ;;
  live-off) app=$(resolve_app "${2:-}"); sync; bash_remote_app "$app" live-off ;;
  lab-stop) shift; lab_stop "$@" ;;
  *)
    cat <<'EOF'
usage: testenv.sh {setup|sync|env|pull|config|up|finish|ps|logs|urls|down|reset|sites|sh|lab-start [产品]|lab-stop [产品]}

  setup            在那台 Windows 的 WSL 里装好与正式机同源的宿主运行环境
  env              同步栈定义并写运行环境文件（需要 PEARNLY_TESTENV_IMAGE）
  up [产品]        拉起该产品的独立栈（默认 dms）：先停掉别的测试栈，再起基础层与本产品；
                   起完自动升级站点库、清 Frappe 缓存、补 OCR 容量与队列配置
  finish [产品]    只跑上面那三个收尾动作（导入数据后、换镜像后单独用）
  sites [产品]     建/迁移本栈的空白测试站点
  lab-init [cowork] Cowork 空白站点：建本栈账本、开通功能、建实验室管理员（线上没有 Cowork 租户可导入）
  urls             打印 Mac 侧端口转发与可直接打开的地址
  logs <服务>      看某个服务日志（产品用 PEARNLY_TESTENV_APP 选，默认 dms）
  reset            停栈并删除本栈的数据卷（只删本产品项目名下的卷）
  lab-start [产品] 一键开始测试：起本栈 → 线上导出该租户 → 取回 → 导入 → 收尾 → 域名切过来
  lab-stop [产品]  一键结束测试：域名切回线上 → 停本栈（站点与数据库卷保留）

产品（app）为 dms|erp|cowork，**一次只跑一个**：每个产品一套独立栈（自己的数据库、
Redis、站点卷与网络），测一个不会碰到另一个。被测代码用 PEARNLY_TESTENV_SRC 指向对应
工作树；驱动与测试辅助脚本始终取本分支。
EOF
    ;;
esac
