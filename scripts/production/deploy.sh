#!/usr/bin/env bash
# 生产部署一条命令（在本机跑）：构建 → 放镜像 → 服务器激活 → 回读，步骤之间不再等人。
#
#   scripts/production/deploy.sh                 部署远端 main 当前提交（要有部署授权，按「攒批部署」规矩来）
#   scripts/production/deploy.sh --dry-run       演练：不构建、不放镜像、不连生产，用最近一次已放上生产的镜像走到放镜像前一步
#   scripts/production/deploy.sh --digest sha256:<64位> [--skip-stage]
#                                                这个 main 提交已经构建过：跳过构建（--skip-stage 再跳过放镜像）
#   scripts/production/deploy.sh --attach        部署还在服务器上跑而本机断开了：重新接上看进度和结果
#
# 每一步做什么（和 production-deploy-steps 的人工步骤一一对应，检查一项不少）：
#   1. 构建：gh workflow run build-production.yml --ref main，等它跑完；候选检查那一步必须是 success；digest 从构建产物取。
#   2. 放镜像：gh workflow run stage-production.yml -f digest=…，等它跑完（只把镜像拉到生产机，不启动）。
#   3. 激活：一次 SSH 把本提交里的 deploy-run.sh 传上去、nohup 后台启动（脚本内容取自 git 里这个提交，服务器上还会与镜像里那份再比一次）。
#      deploy-run.sh 照旧逐个激活 ERP / Cowork / DMS / 基础层，之间等可用内存 ≥ 8 GB，每 10 秒记内存，最后跑 deploy-readback.sh。
#   4. 看进度：一条 SSH 长连接在服务器那头 tail 日志，部署结束自己断开；中途断线等 120 秒再接，最多 5 次。不在本机循环反复新建 SSH。
# 任何一步失败立刻停下，打印现在停在哪、生产受没受影响、下一步怎么处理；不自动回滚。
# 需要：gh 已登录、~/.config/pearnly-production/operator 密钥、能连生产机。
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
POLL=${PEARNLY_DEPLOY_POLL_SECONDS:-15}
RECONNECT=${PEARNLY_DEPLOY_RECONNECT_SECONDS:-120}
IMAGE_REPO=ghcr.io/skin306152-star/pearnly/frappe
REMOTE_STATE=/opt/pearnly/state

mode=deploy digest= skip_stage=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) mode=dry-run ;;
    --attach) mode=attach ;;
    --digest) digest=${2:-}; shift ;;
    --skip-stage) skip_stage=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) printf '不认识的参数：%s（--help 看用法）\n' "$1" >&2; exit 2 ;;
  esac
  shift
done
if [[ -n "$digest" && ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  printf '--digest 要写成 sha256:<64 位十六进制>\n' >&2; exit 2
fi
if [[ $skip_stage == 1 && -z "$digest" ]]; then
  printf '--skip-stage 要和 --digest 一起用\n' >&2; exit 2
fi

say() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
stop() {  # stop <停在哪> <下一步怎么办…>
  local where=$1; shift
  printf '\n停下：%s\n' "$where" >&2
  local line; for line in "$@"; do printf '  - %s\n' "$line" >&2; done
  exit 1
}
since_iso() { python3 -c 'import datetime;print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))'; }
ssh_production() {
  if [[ -n "${PEARNLY_DEPLOY_SSH:-}" ]]; then
    # shellcheck disable=SC2086  # 测试用：替换成假的 ssh 命令
    $PEARNLY_DEPLOY_SSH "$@"
  else
    ssh -i "$HOME/.config/pearnly-production/operator" -o IdentitiesOnly=yes -o BatchMode=yes \
      -o StrictHostKeyChecking=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 \
      "root@$(python3 "$REPO/scripts/production/target.py")" "$@"
  fi
}

# 找刚触发的那次运行：同一工作流、手动触发、触发时刻之后建的（workflow run 不直接返回编号）
find_run() {  # find_run <workflow> <since> [head sha]
  local workflow=$1 since=$2 head=${3:-} id= i
  for i in $(seq 1 24); do
    id=$(gh run list --workflow "$workflow" --branch main --event workflow_dispatch --limit 10 \
      --json databaseId,headSha,createdAt \
      --jq "[.[] | select(.createdAt >= \"$since\")] | sort_by(.createdAt) | .[0] | select(. != null) | \"\(.databaseId) \(.headSha)\"" || true)
    [[ -n "$id" ]] && break
    sleep 5
  done
  [[ -n "$id" ]] || return 1
  if [[ -n "$head" && "${id#* }" != "$head" ]]; then
    printf '%s\n' "${id% *}"; return 3
  fi
  printf '%s\n' "${id% *}"
}

# 等一次运行结束，步骤变化时打印一行；返回 0 表示 success
watch_run() {  # watch_run <run id>
  local id=$1 state last= errors=0 status conclusion current
  while true; do
    if ! state=$(gh run view "$id" --json status,conclusion,jobs \
        --jq '[.status, (.conclusion // ""), ([.jobs[].steps[]? | select(.status == "in_progress") | .name] | first // "")] | join("|")'); then
      errors=$((errors + 1))
      [[ $errors -lt 5 ]] || return 2
      sleep "$POLL"; continue
    fi
    errors=0
    IFS='|' read -r status conclusion current <<< "$state"
    if [[ "$status" == completed ]]; then
      say "  结束：$conclusion"
      [[ "$conclusion" == success ]]; return
    fi
    if [[ -n "$current" && "$current" != "$last" ]]; then say "  $current"; last=$current; fi
    sleep "$POLL"
  done
}

step_conclusion() {  # step_conclusion <run id> <步骤名前缀>
  gh run view "$1" --json jobs --jq "[.jobs[].steps[] | select(.name | startswith(\"$2\")) | .conclusion] | first // \"\""
}

# ---------- 远端 main ----------
git -C "$REPO" fetch -q origin main
sha=$(git -C "$REPO" rev-parse origin/main)
short=${sha:0:8}
say "远端 main：$short"

if [[ $mode == attach ]]; then
  :
elif [[ $mode == dry-run ]]; then
  t0=$(date +%s)
  say "演练：不触发构建、不放镜像、不连生产"
  stage_run=$(gh run list --workflow stage-production.yml --status success --limit 1 --json databaseId --jq '.[0].databaseId')
  [[ -n "$stage_run" ]] || stop "找不到成功的放镜像运行" "gh auth status 看登录"
  digest=$(gh run view "$stage_run" --log | grep -m1 -oE 'DIGEST: sha256:[0-9a-f]{64}' | cut -d' ' -f2 || true)
  [[ -n "$digest" ]] || stop "从放镜像运行 $stage_run 的日志里取不到 digest"
  stage_head=$(gh run view "$stage_run" --json headSha --jq .headSha)
  build_run=$(gh run list --workflow build-production.yml --status success --limit 20 --json databaseId,headSha \
    --jq "[.[] | select(.headSha == \"$stage_head\")][0].databaseId // empty")
  [[ -n "$build_run" ]] || stop "放镜像 $stage_run 对应提交 ${stage_head:0:8} 没有成功的构建"
  candidate=$(step_conclusion "$build_run" "Verify the candidate")
  [[ "$candidate" == success ]] || stop "构建 $build_run 的候选检查不是 success：$candidate"
  build_digest=$(gh run view "$build_run" --log | grep -m1 -oE 'digest: sha256:[0-9a-f]{64}' | cut -d' ' -f2 || true)
  say "最近一次放上生产：放镜像运行 ${stage_run}，提交 ${stage_head:0:8}，$digest"
  say "对应构建运行 ${build_run}：候选检查 success，构建日志里推送的 digest ${build_digest:-（没取到）}"
  [[ -z "$build_digest" || "$build_digest" == "$digest" ]] || stop "构建推送的 digest 与放镜像用的不一致"
  if runner=$(git -C "$REPO" show "$sha:scripts/production/deploy-run.sh" 2>/dev/null); then
    source_note="远端 main $short"
  else
    runner=$(cat "$REPO/scripts/production/deploy-run.sh"); source_note="本工作树（远端 main 还没有这个脚本）"
  fi
  bash -n <(printf '%s\n' "$runner") || stop "deploy-run.sh 语法不对"
  bash -n "$REPO/scripts/production/deploy-readback.sh" || stop "deploy-readback.sh 语法不对"
  [[ -f "$HOME/.config/pearnly-production/operator" ]] || stop "缺生产 SSH 密钥 ~/.config/pearnly-production/operator"
  host=$(python3 "$REPO/scripts/production/target.py")
  say "服务器端脚本取自${source_note}，语法检查通过；生产机 ${host}，密钥在"
  printf '\n真实部署时接下来会执行（演练到此为止）：\n'
  printf '  gh workflow run stage-production.yml --ref main -f digest=%s\n' "$digest"
  printf '  git show <提交>:scripts/production/deploy-run.sh | ssh root@%s  → nohup bash %s/deploy-run-<短SHA>.sh %s@%s <提交>\n' "$host" "$REMOTE_STATE" "$IMAGE_REPO" "$digest"
  say "演练完成，用时 $(( $(date +%s) - t0 )) 秒"
  exit 0
else
  busy=$(gh run list --workflow build-production.yml --branch main --limit 5 --json status \
    --jq '[.[] | select(.status != "completed")] | length')
  [[ "$busy" == 0 ]] || stop "main 上已有一次构建在跑" "等它结束，或确认那是谁的部署再决定"
  t_start=$(date +%s)
  t_build=0 t_stage=0
  if [[ -z "$digest" ]]; then
    say "1/4 构建镜像（约 7 分钟）"
    since=$(since_iso)
    gh workflow run build-production.yml --ref main
    rc=0; build_run=$(find_run build-production.yml "$since" "$sha") || rc=$?
    [[ $rc -ne 1 ]] || stop "触发后 2 分钟内没找到构建运行" "gh run list --workflow build-production.yml 看看；生产没动"
    [[ $rc -ne 3 ]] || stop "构建 $build_run 跑的不是 ${short}（main 在这期间前进了）" "生产没动；等它结束后重跑本脚本，部署新的 main"
    say "  构建运行 $build_run"
    watch_run "$build_run" || stop "构建 $build_run 没成功" "gh run view $build_run --log-failed 看原因；生产没动" \
      "修好推到 main 后重跑 scripts/production/deploy.sh"
    candidate=$(step_conclusion "$build_run" "Verify the candidate")
    [[ "$candidate" == success ]] || stop "构建 $build_run 的候选检查不是 success：$candidate" "生产没动"
    folder=$(mktemp -d)
    gh run download "$build_run" -n production-image-digest -D "$folder"
    digest=$(grep -oE '@sha256:[0-9a-f]{64}' "$folder/image-digest.txt" | cut -c2- || true)
    rm -rf "$folder"
    [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || stop "构建 $build_run 的产物里取不到 digest" "gh run view $build_run 看摘要里的 Image；生产没动" \
      "取到后：scripts/production/deploy.sh --digest sha256:<64位>"
    t_build=$(( $(date +%s) - t_start ))
    say "  镜像 ${digest}（构建 $((t_build / 60)) 分 $((t_build % 60)) 秒）"
  else
    say "1/4 跳过构建，用 ${digest}（服务器上会核对镜像 revision 是不是 ${short}）"
  fi

  if [[ $skip_stage == 0 ]]; then
    say "2/4 放镜像到生产机（只拉取、不启动）"
    t=$(date +%s); since=$(since_iso)
    gh workflow run stage-production.yml --ref main -f "digest=$digest"
    stage_run=$(find_run stage-production.yml "$since") || stop "触发后 2 分钟内没找到放镜像运行" \
      "gh run list --workflow stage-production.yml 看看；生产没动" "重跑：scripts/production/deploy.sh --digest $digest"
    say "  放镜像运行 $stage_run"
    watch_run "$stage_run" || stop "放镜像 $stage_run 没成功" "gh run view $stage_run --log-failed 看原因；生产服务没动（镜像可能没拉完）" \
      "重跑：scripts/production/deploy.sh --digest $digest"
    t_stage=$(( $(date +%s) - t ))
  else
    say "2/4 跳过放镜像（服务器上会核对镜像已在机器上）"
  fi

  say "3/4 在生产机后台启动激活（一次 SSH）"
  if ! runner=$(git -C "$REPO" show "$sha:scripts/production/deploy-run.sh"); then
    stop "提交 $short 里没有 scripts/production/deploy-run.sh" "按旧的人工步骤部署（production-deploy-steps）"
  fi
  image="$IMAGE_REPO@$digest"
  started=$(printf '%s\n' "$runner" | ssh_production "set -eu
S=$REMOTE_STATE
if [ -f \$S/deploy.lock/pid ] && kill -0 \"\$(cat \$S/deploy.lock/pid)\" 2>/dev/null; then echo \"BUSY \$(cat \$S/deploy.lock/sha 2>/dev/null)\"; exit 0; fi
f=\$S/deploy-run-$short.sh
cat > \$f
rm -f \$S/deploy-$short.status
[ ! -f \$S/deploy-$short.log ] || mv \$S/deploy-$short.log \$S/deploy-$short.log.\$(date +%s)
nohup bash \$f '$image' '$sha' </dev/null >/dev/null 2>&1 &
echo STARTED \$!") || stop "SSH 启动失败" "生产服务没动（激活没开始）；网络恢复后：scripts/production/deploy.sh --digest $digest --skip-stage"
  case "$started" in
    STARTED*) say "  已启动（服务器进程 ${started#STARTED }），日志 $REMOTE_STATE/deploy-$short.log" ;;
    BUSY*) stop "生产机上已有一次部署在跑（${started#BUSY }）" "生产服务没被这次动过；等那次结束再说" ;;
    *) stop "启动结果不明：$started" "只读看一眼 $REMOTE_STATE/deploy-$short.log 再决定" ;;
  esac
  t_server=$(date +%s)
fi

# ---------- 看进度：服务器那头 tail，一条长连接 ----------
say "4/4 激活与回读进度（每个产品 2–4 分钟）"
shown=0 status= attempt=0
while true; do
  attempt=$((attempt + 1))
  rc=
  while IFS= read -r line; do
    case "$line" in
      "__STATUS "*) status=${line#__STATUS } ;;
      "__SSHRC "*) rc=${line#__SSHRC } ;;
      *) printf '  │ %s\n' "$line"; shown=$((shown + 1)) ;;
    esac
  done < <(ssh_production "S=$REMOTE_STATE; L=\$S/deploy-$short.log; T=\$S/deploy-$short.status
for i in \$(seq 1 30); do [ -f \$T ] && break; sleep 1; done
pid=\$(cat \$S/deploy.lock/pid 2>/dev/null || true)
if [ -n \"\$pid\" ] && [ \"\$(cat \$S/deploy.lock/sha 2>/dev/null)\" = '$sha' ] && kill -0 \$pid 2>/dev/null; then
  tail -n +$((shown + 1)) -F --pid=\$pid \$L 2>/dev/null
else
  tail -n +$((shown + 1)) \$L 2>/dev/null
fi
echo \"__STATUS \$(cat \$T 2>/dev/null)\"" </dev/null; echo "__SSHRC $?")
  # 连接正常结束说明服务器那头的部署进程已经退出（或根本没有），按状态文件判定
  [[ "$rc" != 0 ]] || break
  [[ $attempt -lt 5 ]] || stop "看进度的连接断了 5 次" "部署还在服务器上照常跑，不受本机断线影响" \
    "稍后：scripts/production/deploy.sh --attach"
  say "  连接断了（${status:-状态未知}），$RECONNECT 秒后重新接上"
  sleep "$RECONNECT"
done

case "$status" in
  ok)
    say "部署完成：ERP / Cowork / DMS / 基础层已激活，回读通过"
    if [[ $mode == deploy ]]; then
      total=$(( $(date +%s) - t_start )); server=$(( $(date +%s) - t_server ))
      printf '\n用时：构建 %d 分 %d 秒，放镜像 %d 分 %d 秒，激活与回读 %d 分 %d 秒，合计 %d 分 %d 秒\n' \
        $((t_build / 60)) $((t_build % 60)) $((t_stage / 60)) $((t_stage % 60)) \
        $((server / 60)) $((server % 60)) $((total / 60)) $((total % 60))
    fi
    printf '这一批特有的表、字段、补丁回读与线上冒烟按发布记录另做。\n'
    ;;
  "failed readback")
    stop "激活全部完成，回读有问题（见上面 PROBLEM 行）" "不自动回滚；逐项看：维护模式没关、Error Log 有新报错、域名不通还是容器没换" \
      "处理后可单独重跑回读：ssh 到生产机 bash /opt/pearnly/deploy/scripts/production/deploy-readback.sh <镜像> \$(cat $REMOTE_STATE/deploy-$short.since)"
    ;;
  "failed revision"|"failed sync-tooling"|"failed start")
    stop "激活前的核对没过（${status}）" "生产服务没动；看上面日志的 FAILED 行" ;;
  "failed erp"|"failed cowork"|"failed dms"|"failed foundation")
    product=${status#failed }
    stop "激活 $product 失败，停在原地（之后的产品没动）" \
      "不自动回滚；$product 可能还在维护模式，日志 $REMOTE_STATE/activate-$product-$short.log" \
      "迁移前备份在各站点 private/backups/；回退做法见上一次发布记录的「回退」一节" ;;
  running*)
    stop "服务器上的部署进程已经不在，状态停在「${status#running }」（被中断）" \
      "不自动回滚；只读看 $REMOTE_STATE/deploy-$short.log 与对应产品日志，确认该产品是否还在维护模式再决定" ;;
  "")
    stop "服务器上没有 $short 这次部署的状态" "没启动过，或部署的不是远端 main 当前提交；只读看 $REMOTE_STATE/ 下的 deploy-*.status" ;;
  *)
    stop "部署状态：${status}" "只读看 $REMOTE_STATE/deploy-$short.log 再决定" ;;
esac
