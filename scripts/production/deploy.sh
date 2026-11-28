#!/usr/bin/env bash
# 生产部署一条命令（在本机跑）：构建 → 放镜像 → 服务器激活 → 回读，步骤之间不再等人。
#
#   scripts/production/deploy.sh                 从远端 main 当前提交打发布标记 release-<UTC 年月日-时分> 并推上去，部署这个标记
#                                                （要有部署授权，按「攒批部署」规矩来）
#   scripts/production/deploy.sh --tag <标记>    部署已有的发布标记（重跑、或部署 main 上更早的提交）；标记所指提交必须在 main 上
#   scripts/production/deploy.sh --dry-run [--tag <标记>] [--digest sha256:<64位>]
#                                                演练：不打标记、不构建、不放镜像，用最近一次已放上生产的镜像走到放镜像前一步；
#                                                只读连生产一次，做部署前租户配置对比
#   scripts/production/deploy.sh --tag <标记> --digest sha256:<64位> [--skip-stage]
#                                                这个标记已经构建过：跳过构建（--skip-stage 再跳过放镜像）
#   scripts/production/deploy.sh --attach [--tag <标记>]
#                                                部署还在服务器上跑而本机断开了：重新接上看进度和结果（不给标记就接最近一个发布标记）
#
# 按发布标记部署（10-31）：流程闸、构建、放镜像、服务器核对镜像 revision 都针对标记所指的那个提交，不再要求部署的是 main 最新；
# main 照常往前合，不为部署冻结。
#
# 每一步做什么（和 production-deploy-steps 的人工步骤一一对应，检查一项不少）：
#   0'. 部署前租户配置对比（演练和正式部署都做，正式部署在构建前）：用这个提交的产品地图导出，经一次 SSH 在生产机
#      各产品 web 容器里读站点配置（check-tenant-config.py，只读，不进数据库容器）；「应一致」的键正式租户与测试租户不同、
#      或「形状规则」不满足就停下，没有绕过开关。服务器激活前会用镜像里那份再核一次（deploy-run.sh 的 config 步骤）。
#      接着在本机核每个配了 LIFF 的站点的落地地址（check-liff-endpoints.py，只读 HTTP），不对也停下（11-03 事故待补项 A）。
#      再核站外设置（11-05，check-external-config.py）：同一次 SSH 里 external-config-collect.py 只读 Keycloak 两个池、各站点 LINE 频道、
#      fatcc.com 租户站点登记，本机再读 Cloudflare 解析、证书与 LINE 登录回调；按产品地图 [ext] 分类，应一致 / 形状不对、缺入口登记就停下。
#   0. 流程用例闸：这个提交的 GitHub 状态 pearnly/flows 必须是全套通过（本机回归实验室 scripts/flows/run.py --all --post-status
#      回写）；没过就停下。没有全套时沿用首父链上最近一次全套，前提是之后只改了非产品路径（flows_inherit.py）；
#      不能沿用就停下、打印补跑命令，不构建、不动生产。
#   1. 构建：gh workflow run build-production.yml --ref <标记>，等它跑完；候选检查那一步必须是 success；digest 从构建产物取。
#   2. 放镜像：gh workflow run stage-production.yml --ref <标记> -f digest=…，等它跑完（只把镜像拉到生产机，不启动）。
#   3. 激活：一次 SSH 把本提交里的 deploy-run.sh 传上去、nohup 后台启动（脚本内容取自 git 里这个提交，服务器上还会与镜像里那份再比一次）。
#      deploy-run.sh 照旧逐个激活 ERP / Cowork / DMS / 基础层，之间等可用内存 ≥ 8 GB，每 10 秒记内存，最后跑 deploy-readback.sh。
#      激活前用新镜像起一次性只读容器预览站外 LINE 表面（PLAN 逐套写进日志，没跑成即失败、线上没动）；三个产品激活后、
#      基础层前同步（富菜单：有漂移才发布、发布后重挂并回读，不一致即失败；没凭据的站点跳过并列出）。
#      演练（--dry-run）也经一次 SSH 跑同一份预览，用最近一次放上生产的镜像，或 --digest 指定已放上去的那份。
#   4. 看进度：一条 SSH 长连接在服务器那头 tail 日志，部署结束自己断开；中途断线等 120 秒再接，最多 5 次。不在本机循环反复新建 SSH。
#   5. 回读通过后跑登录后端到端（e2e_gate：测试租户、本机无头浏览器）；不通过停下，没测到只打印。
# 任何一步失败立刻停下，打印现在停在哪、生产受没受影响、下一步怎么处理；不自动回滚。
# 需要：gh 已登录、~/.config/pearnly-production/operator 密钥、能连生产机。
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
POLL=${PEARNLY_DEPLOY_POLL_SECONDS:-15}
RECONNECT=${PEARNLY_DEPLOY_RECONNECT_SECONDS:-120}
IMAGE_REPO=ghcr.io/skin306152-star/pearnly/frappe
REMOTE_STATE=/opt/pearnly/state
# 站外设置对比要从发布标记里取的文件：生产机段、本机比较，以及它导入「应有」的开户 / 配置脚本
EXTERNAL_FILES="scripts/production/external-config-collect.py scripts/production/check-external-config.py \
scripts/production/configure-line-login.py infrastructure/production/bootstrap-auth.py infrastructure/production/configure-ai-entry.py \
infrastructure/production/site-step.py infrastructure/production/target.json apps/pearnly_core/pearnly_core/account/line_login_contract.py \
scripts/e2e/run.py scripts/e2e/scenarios.py"

mode=deploy digest= skip_stage=0 tag=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) mode=dry-run ;;
    --attach) mode=attach ;;
    --tag) tag=${2:-}; shift ;;
    --digest) digest=${2:-}; shift ;;
    --skip-stage) skip_stage=1 ;;
    -h|--help) sed -n '2,35p' "$0"; exit 0 ;;
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
if [[ -n "$digest" && -z "$tag" && $mode != dry-run ]]; then
  printf '--digest 要和 --tag 一起用（跳过的是那个标记的构建）\n' >&2; exit 2
fi
# 演练带 --digest：只用来做站外 LINE 表面预览（那份镜像要已放上生产机），其余照旧用最近一次放上生产的镜像
preview_digest=
if [[ $mode == dry-run ]]; then preview_digest=$digest; digest=; fi
if [[ -n "$tag" && ! "$tag" =~ ^release-[0-9]{8}-[0-9]{4}$ ]]; then
  printf '--tag 要写成 release-<年月日>-<时分>（例如 release-20261031-1405）\n' >&2; exit 2
fi

say() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
# 测试站指向先打出来（主会话 11-23）：任一测试站在测试环境时第一行醒目标出
python3 "$(dirname "$0")/../testenv_pointer.py" || true
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
find_run() {  # find_run <workflow> <since> [head sha]：按发布标记找（标记触发的运行，headBranch 就是标记名）
  local workflow=$1 since=$2 head=${3:-} id= i
  for i in $(seq 1 24); do
    id=$(gh run list --workflow "$workflow" --branch "$tag" --event workflow_dispatch --limit 10 \
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

# 流程用例闸：这个提交的 GitHub 状态 pearnly/flows 要是 success，且是全套（说明以 all flows 开头）。
# 状态由 GitHub 的 Flows 工作流（.github/workflows/flows.yml，11-16 起）或本机回归实验室（scripts/flows/run.py）回写，同名同格式；
# 没有状态照样拦；没有绕过开关。
flows_gate() {
  local line rerun
  rerun="在 GitHub 上跑全套并回写（约半小时，不占本机）：gh workflow run flows.yml --ref <发布标记> -f scope=all，跑完重跑本脚本；或在挂本提交源码的回归实验室跑全套并回写：git worktree add <目录> $sha && cd <目录> && PEARNLY_REGRESSION_SRC=\$PWD docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml up -d --wait && python3 scripts/flows/run.py --all --post-status（详见 tests/flows/README.md），通过后重跑本脚本。跑过全套但没带 --post-status 的不算：结果没登记到这个提交；集成头还会变时别跑全套，只跑受影响的 run.py --changed --base <上一个头>，头冻结后再跑这一条"
  line=$(cd "$REPO" && gh api "repos/{owner}/{repo}/commits/$sha/status" \
    --jq '[.statuses[] | select(.context == "pearnly/flows")][0] | select(. != null) | "\(.state) \(.description)"') || line=
  if [[ -n "$line" && "${line%% *}" != success ]]; then
    stop "提交 $short 的流程用例没通过：${line#* }" "生产没动" "看 run.py 的结果修好；$rerun"
  fi
  if [[ -n "$line" && "${line#* }" == "all flows"* ]]; then
    say "流程用例：${line#* }"
  else
    # 沿用（10-30）：本提交没有全套时，沿首父链找最近一次全套；之后只改了非产品路径（测试、文档、检查脚本……）就沿用，否则拦
    local inherited
    inherited=$(python3 "$REPO/scripts/production/flows_inherit.py" "$sha" 2>&1) ||
      stop "提交 $short ${line:+只跑过受影响的流程（${line#* }），}不能沿用之前的全套：$inherited" "生产没动" "$rerun"
    say "流程用例：$inherited"
  fi
  # 存量提醒（不拦）：上次放上生产以来改到的、还没有流程兜底的入口背后模块（scripts/flows/coverage.py 的存量清单）
  local last note
  last=$(cd "$REPO" && gh run list --workflow stage-production.yml --status success --limit 1 --json headSha --jq '.[0].headSha // ""' 2>/dev/null) || last=
  if [[ "$last" =~ ^[0-9a-f]{40}$ ]]; then
    note=$(cd "$REPO" && python3 scripts/flows/coverage.py --deploy-note --base "$last" --head "$sha" 2>/dev/null) || note=
    [[ -z "$note" ]] || printf '%s\n' "$note" | sed 's/^/  ⚠ /'
  fi
}

# 站外 LINE 表面预览（11-28）：用要上线的镜像（surfaces-plan.sh，生产机上一次性只读容器）逐站对照，打印每个站点
# 「无变化 / 将发布 N 套及每套改了什么」。演练经一次 SSH 跑它；正式部署在 deploy-run.sh 激活前的 plan 步骤里跑，没跑成即部署失败。
surfaces_note() {
  python3 - "$1" <<'PY'
import json, sys
for row in sys.argv[1].splitlines():
    if row.startswith("PLAN-FAILED "):
        print("  没跑成：" + row[len("PLAN-FAILED "):])
        continue
    if row.startswith("PLAN-SKIP "):
        print("  没有可比的：" + row[len("PLAN-SKIP "):] + "（这份镜像早于菜单随部署同步；要看这一批，先放镜像再带 --digest 演练）")
        continue
    if not row.startswith("SUMMARY "):
        continue
    s = json.loads(row[len("SUMMARY "):])
    drift = s["drift"]
    print(f"  {s['product']}: 一致 {len(s['ok'])} 项；部署时会发布 {len(drift)} 项")
    for label in sorted(s["ok"]):
        print(f"    无变化 {label}")
    for label in sorted(drift):
        changes = (s.get("changes") or {}).get(label) or {}
        images = sorted({r.get("image", "unknown") for r in changes.values()})
        print(f"    将发布 {label}：{len(changes)} 套（图：{'、'.join(images) or '未知'}）")
        for key, r in sorted(changes.items()):
            print(f"      {key}  图 {r.get('image')}；" + ("；".join(r.get("text") or []) or "文字无变化"))
    for label, why in sorted(s["skipped"].items()):
        print(f"    跳过 {label}：{why}")
    for label, why in sorted(s["failed"].items()):
        print(f"    读不出 {label}：{why}")
PY
}

surfaces_preview() {  # surfaces_preview <镜像>：演练用，一次 SSH，生产机上跑本提交里的 surfaces-plan.sh
  local image=$1 plan out rc=0
  plan=$(git -C "$REPO" show "$sha:scripts/production/surfaces-plan.sh" 2>/dev/null) ||
    { say "站外 LINE 表面预览：提交 $short 还没有 surfaces-plan.sh，跳过"; return 0; }
  out=$(printf '%s\n' "$plan" | ssh_production "bash -s '$image'" 2>&1) || rc=$?
  surfaces_note "$out"
  [[ $rc == 0 ]] || stop "站外 LINE 表面预览没跑成（上面「没跑成」行）" "生产没动" \
    "镜像不在生产机上就先放镜像（演练可带 --digest 指定已放上去的那份）；其余看 PLAN-FAILED 行"
  say "站外 LINE 表面预览完（${image}）"
}

# 部署前租户配置对比：这个提交的产品地图导出交给生产机上的 check-tenant-config.py（一次 SSH、只读）
config_gate() {
  local tmp out rc=0 b64
  tmp=$(mktemp -d)
  if ! git -C "$REPO" archive "$sha" scripts/impact scripts/check-product-map.py scripts/production/check-tenant-config.py \
      scripts/production/check-liff-endpoints.py 2>/dev/null \
      | tar -x -C "$tmp" 2>/dev/null || [[ ! -f "$tmp/scripts/production/check-tenant-config.py" ]]; then
    rm -rf "$tmp"
    say "租户配置对比：提交 $short 还没有产品地图，跳过"
    return 0
  fi
  python3 "$tmp/scripts/check-product-map.py" --export "$tmp/product-map.json" ||
    { rm -rf "$tmp"; stop "提交 $short 的产品地图导不出来" "生产没动" "python3 scripts/check-product-map.py 看地图错在哪"; }
  b64=$(base64 < "$tmp/product-map.json" | tr -d '\n')
  # 站外设置对比（11-05）：这个提交有那几个脚本才跑；单独打包，免得更早的标记连租户配置对比都跳过
  local collect=
  if git -C "$REPO" archive "$sha" $EXTERNAL_FILES 2>/dev/null | tar -x -C "$tmp" 2>/dev/null &&
      [[ -f "$tmp/scripts/production/external-config-collect.py" ]]; then
    collect="python3 -c 'import base64,sys,zlib; exec(compile(zlib.decompress(base64.b64decode(sys.argv[1])), \"external-config-collect\", \"exec\"))' $(python3 -c 'import base64,sys,zlib; print(base64.b64encode(zlib.compress(open(sys.argv[1],"rb").read(), 9)).decode())' "$tmp/scripts/production/external-config-collect.py") || echo __EXT_FAILED"
  fi
  # 读到的站点配置（已去敏）经 stderr 回到本机，接着用来核 LIFF 落地地址；生产机上不落文件
  # 同一次 SSH 顺带列出上次回读通过之后被重启 / 重建过的生产容器（手工动过生产的痕迹，只报告；workflow.md「手工动过生产」）
  out=$(ssh_production "python3 - --map-b64 $b64 --dump /dev/stderr; rc=\$?
last=\$(cat $REMOTE_STATE/last-readback-ok 2>/dev/null || echo)
if [ -z \"\$last\" ]; then echo '__MANUAL unknown'; else
for c in \$(docker ps --format '{{.Names}}' | grep '^pearnly-production-'); do
  at=\$(docker inspect -f '{{.State.StartedAt}}' \$c); [ \"\$(date -d \"\$at\" +%s)\" -gt \"\$last\" ] && echo \"__MANUAL \$c \$at\"
done; fi
$collect
exit \$rc" < "$tmp/scripts/production/check-tenant-config.py" 2> "$tmp/sites.json") || rc=$?
  printf '%s\n' "$out" | sed -n 's/^__EXT //p' > "$tmp/external.json"
  printf '%s\n' "$out" | grep -v '^__MANUAL\|^__EXT' | sed 's/^/  │ /'
  manual=$(printf '%s\n' "$out" | sed -n 's/^__MANUAL //p')
  if [[ "$manual" == unknown ]]; then
    say "  ⚠ 生产机上还没有回读通过的时间记录（这一版之后的第一次部署会记下）"
  elif [[ -n "$manual" ]]; then
    printf '  ⚠ 上次回读通过之后被重启 / 重建过的生产容器（手工动过生产就应当场跑过回读）：\n'
    printf '%s\n' "$manual" | sed 's/^/      /'
  fi
  case $rc in
    0) say "租户配置对比通过" ;;
    1) rm -rf "$tmp"; stop "正式租户的配置与测试租户对不上（上面 BLOCK 行）" "生产没动" \
         "逐项核对：改租户配置，或在 scripts/impact/product-map/config.toml 改分类（走评审）后重跑；没有绕过开关" ;;
    *) rm -rf "$tmp"; stop "租户配置对比没跑成（退出码 $rc）" "生产没动" "网络或生产机问题；恢复后重跑" ;;
  esac
  liff_gate "$tmp"
  external_gate "$tmp"
  rm -rf "$tmp"
}

# 站外设置对比（11-05，docs/work/2026-11-05-product-map-next.md §3）：Keycloak、LINE、fatcc.com 登记、解析、证书。
# 生产机那段已在 config_gate 同一次 SSH 里读完（只交回推导事实，密钥不出生产机）；这里在本机比较并读本机能读的。没有绕过开关。
external_gate() {
  local tmp=$1 rc=0 out
  if [[ ! -f "$tmp/scripts/production/check-external-config.py" ]]; then say "站外设置对比：提交 $short 还没有检查脚本，跳过"; return 0; fi
  [[ -s "$tmp/external.json" ]] || stop "读不到生产机回传的站外设置" "生产没动" "重跑一次；还不行先只读看生产机上 /opt/pearnly/secrets/config-reader.env 与网络"
  out=$(python3 "$tmp/scripts/production/check-external-config.py" --facts "$tmp/external.json" --map "$tmp/product-map.json" 2>&1) || rc=$?
  printf '%s\n' "$out" | sed 's/^/  │ /'
  case $rc in
    0) say "站外设置对比通过" ;;
    1) stop "站外设置不对（上面 BLOCK 行）" "生产没动" \
         "改 Keycloak / LINE 后台 / fatcc.com 登记 / 解析后重跑，或在 scripts/impact/product-map/config.toml 改分类（走评审）；没有绕过开关" ;;
    *) stop "站外设置对比没跑成（退出码 $rc）" "生产没动" "本机缺文件或输入读不出；看上面输出" ;;
  esac
}

# 部署前也核 LIFF 落地地址（11-03 事故的待补项 A）：每个配了 LIFF 的站点，落地地址必须是规定的那一个、从 LINE 进网页不出 404。
# 本机只读 HTTP，不要密钥；不对就拦部署，没有绕过开关（LIFF 设置改在控制面 / LINE 那边，改好再部署）。
liff_gate() {
  local tmp=$1 rows rc=0 out
  if [[ ! -f "$tmp/scripts/production/check-liff-endpoints.py" ]]; then say "LIFF 落地地址：提交 $short 还没有检查脚本，跳过"; return 0; fi
  rows=$(python3 -c '
import json, sys
dumps = json.load(open(sys.argv[1]))
for product, sites in sorted(dumps.items()):
    for row in sites if isinstance(sites, list) else []:
        liff = (row.get("keys") or {}).get("pearnly_line_liff_id")
        if liff:
            print(product, row["site"], liff)' "$tmp/sites.json" 2>/dev/null) ||
    stop "读不到生产机回传的站点配置，LIFF 落地地址没法核" "生产没动" "重跑一次；还不行先看 $REMOTE_STATE 与网络"
  [[ -n "$rows" ]] || stop "没有一个站点配了 LIFF，LIFF 落地地址没法核" "生产没动" "核对生产站点配置里的 pearnly_line_liff_id"
  out=$(printf '%s\n' "$rows" | python3 "$tmp/scripts/production/check-liff-endpoints.py" --stdin 2>&1) || rc=$?
  printf '%s\n' "$out" | sed 's/^/  │ /'
  [[ $rc == 0 ]] || stop "LIFF 落地地址或从 LINE 进网页的入口不对（上面 PROBLEM 行）" "生产没动" \
    "ERP / Cowork 的 LIFF 落地地址必须是站点根、DMS 是 /liff/dms-booking：在控制面改好（pearnly_control.onboarding.line_liff）再部署；没有绕过开关"
  say "LIFF 落地地址通过"
}

# 本批真机单：上次放上生产到这次改到的地图入口里，上线必验的与只能真机的（只打印，不拦）
batch_note() {
  local last files
  last=$(cd "$REPO" && gh run list --workflow stage-production.yml --status success --limit 1 --json headSha --jq '.[0].headSha // ""' 2>/dev/null) || last=
  [[ "$last" =~ ^[0-9a-f]{40}$ && "$last" != "$sha" ]] || return 0
  files=$(git -c core.quotepath=off -C "$REPO" diff --name-only "$last" "$sha" 2>/dev/null) || return 0
  [[ -n "$files" ]] || return 0
  printf '\n本批（%s..%s）真机单与上线必验：\n' "${last:0:8}" "$short"
  # shellcheck disable=SC2086  # 文件名逐个作参数
  (cd "$REPO" && python3 scripts/check-product-map.py --batch --files $files 2>/dev/null) | sed 's/^/  /' || true
}

# 登录后端到端（11-05，docs/work/2026-11-05-product-map-next.md）：回读通过后，用专用 e2e 账号经真实的 fatcc.com 与 Keycloak
# 登录生产上的测试租户（testerp / testcowork），走登录、切换站点、添加公司、退出、LINE 进网页、对话一句、代录新建任务（scripts/e2e/run.py --all，
# 约 6 分钟，每次部署对话场景约 1–2 泰铢模型费）。正式租户一个都不登录。不通过就停下（不自动回滚）；没测到（测试租户切在测试机、
# 本机没有 e2e 账号或 Playwright 版本不对）只打印、不拦。本机 Python：PEARNLY_E2E_PYTHON，默认 ~/pearnly-devtools/e2e-venv/bin/python。
e2e_gate() {
  local py=${PEARNLY_E2E_PYTHON:-$HOME/pearnly-devtools/e2e-venv/bin/python} rc=0 out
  if [[ ! -x "$py" ]]; then
    printf '\n登录后 e2e：没测到（本机没有 %s；建法见 scripts/e2e/run.py 文件头）\n' "$py"
    return 0
  fi
  say "登录后 e2e（测试租户，约 6 分钟）"
  out=$(cd "$REPO" && "$py" scripts/e2e/run.py --all 2>&1) || rc=$?
  printf '%s\n' "$out" | sed 's/^/  /'
  case $rc in
    0) say "登录后 e2e 全部通过" ;;
    3) printf '\n登录后 e2e 有没测到的格（见上面「没测到」行），不算通过也不算不通过，不拦\n' ;;
    1) stop "生产已激活、回读通过，但测试租户登录后流程不通过（见上面「不通过」行与截图）" \
         "不自动回滚；先只读看截图与上面原因，判断是这批代码、站外设置还是测试租户数据" \
         "回退做法见上一次发布记录的「回退」一节；修好后单独重跑：$py scripts/e2e/run.py --all" ;;
    *) printf '\n登录后 e2e 没跑起来（退出码 %s），不拦；本机单独重跑：%s scripts/e2e/run.py --all\n' "$rc" "$py" ;;
  esac
}

# ---------- 发布标记 ----------
git -C "$REPO" fetch -q --tags origin main
main_sha=$(git -C "$REPO" rev-parse origin/main)
if [[ -z "$tag" && $mode == attach ]]; then
  tag=$(git -C "$REPO" tag -l 'release-*' --sort=-creatordate | head -1)
  [[ -n "$tag" ]] || stop "没有发布标记可接" "用 --tag <标记> 指定"
fi
if [[ -z "$tag" && $mode == deploy ]]; then
  tag="release-$(date -u +%Y%m%d-%H%M)"
  if git -C "$REPO" rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    stop "标记 $tag 已经有了（同一分钟打过）" "等一分钟再跑，或用 --tag $tag 部署它"
  fi
  git -C "$REPO" tag -a "$tag" "$main_sha" -m "production release $tag" || stop "打标记 $tag 失败" "生产没动"
  git -C "$REPO" push -q origin "refs/tags/$tag" ||
    stop "推标记 $tag 失败" "生产没动；本地标记还在：git push origin refs/tags/$tag 后用 --tag $tag 重跑"
  say "打了发布标记 ${tag}（远端 main ${main_sha:0:8}）"
fi
if [[ -n "$tag" ]]; then
  sha=$(git -C "$REPO" rev-parse -q --verify "refs/tags/$tag^{commit}") || stop "没有发布标记 $tag" "git fetch --tags 看看名字；生产没动"
  git -C "$REPO" merge-base --is-ancestor "$sha" "$main_sha" || stop "标记 $tag 所指的提交不在 main 上" "发布标记只从 main 打；生产没动"
else
  sha=$main_sha  # 演练没给标记：拿远端 main 当前提交走一遍，不打标记
fi
short=${sha:0:8}
say "部署${tag:+发布标记 $tag → }提交 $short"

if [[ $mode == attach ]]; then
  :
elif [[ $mode == dry-run ]]; then
  t0=$(date +%s)
  say "演练：不触发构建、不放镜像；只读连生产一次做租户配置对比"
  flows_gate
  config_gate
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
  plan_digest=${preview_digest:-$digest}
  if [[ -n "$preview_digest" ]]; then plan_note="--digest 指定"; else plan_note="最近一次放上生产的镜像；要看这一批的预览，先构建并放镜像，再带 --digest 演练"; fi
  say "站外 LINE 表面预览：用 ${plan_digest}（${plan_note}）"
  surfaces_preview "$IMAGE_REPO@$plan_digest"
  if runner=$(git -C "$REPO" show "$sha:scripts/production/deploy-run.sh" 2>/dev/null); then
    source_note="提交 $short"
  else
    runner=$(cat "$REPO/scripts/production/deploy-run.sh"); source_note="本工作树（提交 $short 还没有这个脚本）"
  fi
  bash -n <(printf '%s\n' "$runner") || stop "deploy-run.sh 语法不对"
  bash -n "$REPO/scripts/production/deploy-readback.sh" || stop "deploy-readback.sh 语法不对"
  [[ -f "$HOME/.config/pearnly-production/operator" ]] || stop "缺生产 SSH 密钥 ~/.config/pearnly-production/operator"
  host=$(python3 "$REPO/scripts/production/target.py")
  say "服务器端脚本取自${source_note}，语法检查通过；生产机 ${host}，密钥在"
  batch_note
  printf '\n真实部署时接下来会执行（演练到此为止）：\n'
  printf '  gh workflow run stage-production.yml --ref %s -f digest=%s\n' "${tag:-<新打的发布标记>}" "$digest"
  printf '  git show <提交>:scripts/production/deploy-run.sh | ssh root@%s  → nohup bash %s/deploy-run-<短SHA>.sh %s@%s <提交>\n' "$host" "$REMOTE_STATE" "$IMAGE_REPO" "$digest"
  say "演练完成，用时 $(( $(date +%s) - t0 )) 秒"
  exit 0
else
  busy=$(gh run list --workflow build-production.yml --limit 5 --json status \
    --jq '[.[] | select(.status != "completed")] | length')
  [[ "$busy" == 0 ]] || stop "已有一次生产镜像构建在跑" "等它结束，或确认那是谁的部署再决定"
  flows_gate
  config_gate
  t_start=$(date +%s)
  t_build=0 t_stage=0
  if [[ -z "$digest" ]]; then
    say "1/4 构建镜像（约 7 分钟）"
    since=$(since_iso)
    gh workflow run build-production.yml --ref "$tag"
    rc=0; build_run=$(find_run build-production.yml "$since" "$sha") || rc=$?
    [[ $rc -ne 1 ]] || stop "触发后 2 分钟内没找到构建运行" "gh run list --workflow build-production.yml 看看；生产没动"
    [[ $rc -ne 3 ]] || stop "构建 $build_run 跑的不是 ${short}（标记 $tag 被改指了？）" "生产没动；查标记后用 --tag 重跑"
    say "  构建运行 $build_run"
    watch_run "$build_run" || stop "构建 $build_run 没成功" "gh run view $build_run --log-failed 看原因；生产没动" \
      "修好合进 main 后重跑 scripts/production/deploy.sh（会打新的发布标记）"
    candidate=$(step_conclusion "$build_run" "Verify the candidate")
    [[ "$candidate" == success ]] || stop "构建 $build_run 的候选检查不是 success：$candidate" "生产没动"
    folder=$(mktemp -d)
    gh run download "$build_run" -n production-image-digest -D "$folder"
    digest=$(grep -oE '@sha256:[0-9a-f]{64}' "$folder/image-digest.txt" | cut -c2- || true)
    rm -rf "$folder"
    [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || stop "构建 $build_run 的产物里取不到 digest" "gh run view $build_run 看摘要里的 Image；生产没动" \
      "取到后：scripts/production/deploy.sh --tag $tag --digest sha256:<64位>"
    t_build=$(( $(date +%s) - t_start ))
    say "  镜像 ${digest}（构建 $((t_build / 60)) 分 $((t_build % 60)) 秒）"
  else
    say "1/4 跳过构建，用 ${digest}（服务器上会核对镜像 revision 是不是 ${short}）"
  fi

  if [[ $skip_stage == 0 ]]; then
    say "2/4 放镜像到生产机（只拉取、不启动）"
    t=$(date +%s); since=$(since_iso)
    gh workflow run stage-production.yml --ref "$tag" -f "digest=$digest"
    stage_run=$(find_run stage-production.yml "$since") || stop "触发后 2 分钟内没找到放镜像运行" \
      "gh run list --workflow stage-production.yml 看看；生产没动" "重跑：scripts/production/deploy.sh --tag $tag --digest $digest"
    say "  放镜像运行 $stage_run"
    watch_run "$stage_run" || stop "放镜像 $stage_run 没成功" "gh run view $stage_run --log-failed 看原因；生产服务没动（镜像可能没拉完）" \
      "重跑：scripts/production/deploy.sh --tag $tag --digest $digest"
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
echo STARTED \$!") || stop "SSH 启动失败" "生产服务没动（激活没开始）；网络恢复后：scripts/production/deploy.sh --tag $tag --digest $digest --skip-stage"
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
    "稍后：scripts/production/deploy.sh --attach --tag $tag"
  say "  连接断了（${status:-状态未知}），$RECONNECT 秒后重新接上"
  sleep "$RECONNECT"
done

[[ $mode != deploy ]] || batch_note

case "$status" in
  ok)
    say "部署完成：ERP / Cowork / DMS / 基础层已激活，回读通过"
    if [[ $mode == deploy ]]; then
      total=$(( $(date +%s) - t_start )); server=$(( $(date +%s) - t_server ))
      printf '\n用时：构建 %d 分 %d 秒，放镜像 %d 分 %d 秒，激活与回读 %d 分 %d 秒，合计 %d 分 %d 秒\n' \
        $((t_build / 60)) $((t_build % 60)) $((t_stage / 60)) $((t_stage % 60)) \
        $((server / 60)) $((server % 60)) $((total / 60)) $((total % 60))
    fi
    e2e_gate
    printf '这一批特有的表、字段、补丁回读与线上冒烟按发布记录另做。\n'
    ;;
  "failed readback")
    stop "激活全部完成，回读有问题（见上面 PROBLEM 行）" "不自动回滚；逐项看：维护模式没关、Error Log 有新报错、域名不通还是容器没换" \
      "处理后可单独重跑回读：ssh 到生产机 bash /opt/pearnly/deploy/scripts/production/deploy-readback.sh <镜像> \$(cat $REMOTE_STATE/deploy-$short.since)"
    ;;
  "failed config")
    stop "激活前的租户配置对比没过（上面 BLOCK 行）" "生产服务没动（镜像已放上生产机）" \
      "改租户配置或改地图分类后：scripts/production/deploy.sh --tag $tag --digest $digest --skip-stage" ;;
  "failed surfaces")
    stop "三个产品已激活，站外 LINE 表面同步或回读没过（上面 SUMMARY / FAILED 行写了哪一站）" \
      "不自动回滚；没发布的站点菜单仍是旧的，已发布的站点在日志里有 BEFORE 行" \
      "看 $REMOTE_STATE/surfaces-<产品>-$short.log；要回退某站用 FAILED 行里的 restore 命令；修好后 deploy.sh --tag $tag --digest $digest --skip-stage 重跑" ;;
  "failed plan")
    stop "激活前用新镜像做的站外 LINE 表面预览没跑成（上面 PLAN-FAILED 行）" "生产服务没动（镜像已放上生产机）" \
      "看 PLAN-FAILED 行；修好后 scripts/production/deploy.sh --tag $tag --digest $digest --skip-stage" ;;
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
    stop "服务器上没有 $short 这次部署的状态" "没启动过，或部署的不是标记 $tag 所指的提交；只读看 $REMOTE_STATE/ 下的 deploy-*.status" ;;
  *)
    stop "部署状态：${status}" "只读看 $REMOTE_STATE/deploy-$short.log 再决定" ;;
esac
