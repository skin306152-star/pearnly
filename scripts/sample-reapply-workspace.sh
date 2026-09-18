#!/usr/bin/env bash
# 把**当前**的功能目录重打到已经开通的站点上：工作区 + 角色权限。
#
# 为什么需要单独一个脚本：开通步骤的 payload 是**建单时**就存进库的字符串
# （`Pearnly Provisioning Step.payload`），目录后来改了它也不会跟着动。
# 实测过：老单子的 `set_oidc` payload 里就没有后来新增的凭据字段。所以
# "目录改了、已经开出来的站点也要跟上"只能按现在这份目录**重算**一份再打过去。
#
# 新开的站点不需要它 —— 开通流程本来就用当前目录算。
#
# 用法: scripts/sample-reapply-workspace.sh <部署名...>
#   例: scripts/sample-reapply-workspace.sh DEP-2026-0043
#
# 幂等：被调的两个函数都是"有就更新、没有就建"。
set -euo pipefail

CTX=colima-pearnly
COMPOSE=infrastructure/compose/01-bench.yml
CONTROL=http://127.0.0.1:8104
CONTROL_HOST=control.localhost

USER_NAME="${PEARNLY_OPS_USER:-earn}"
USER_PASS="${PEARNLY_OPS_PASS:-earn}"

JAR=$(mktemp)
RAW=$(mktemp)
trap 'rm -f "$JAR" "$RAW"' EXIT

d()  { docker --context "$CTX" "$@"; }
dc() { d compose -f "$COMPOSE" "$@"; }
py() { python3 "$@"; }

# bench execute 对 --kwargs 做的是 Python eval，嵌套 dict 会踩到它自己的解析问题
# （而且真实异常会被换成一句无关的 NameError）。所以 spec 一律以**字符串**传（踩过）。
WRAP_ROLES='import json, sys; print(json.dumps({"spec_json": sys.argv[1], "admin_user": sys.argv[2]}))'
WRAP_SPEC='import json, sys; print(json.dumps({"spec_json": sys.argv[1]}))'

# 从控制面那份原始返回里取一个字段（原始返回存文件，避免在 shell 里嵌大段 Python）。
# 字符串取**原值**（json.dumps 会带上引号，当站点名用就错了），dict/list 才转 JSON。
pick() { py -c '
import json, sys
v = json.load(open(sys.argv[1]))["message"][sys.argv[2]]
print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
' "$RAW" "$1"; }
pick1() { py -c 'import json,sys; m=json.load(open(sys.argv[1]))["message"]; print(m["roles"][sys.argv[2]] if sys.argv[2] in m["roles"] else "")' "$RAW" "$1"; }

[ $# -ge 1 ] || { echo "用法: $0 <部署名...>" >&2; exit 2; }

echo "[登录] 控制面"
curl -sS -c "$JAR" -o /dev/null -X POST "$CONTROL/api/method/login" \
  -H "Host: $CONTROL_HOST" -H 'Content-Type: application/json' \
  --data-binary "$(py -c 'import json,sys; print(json.dumps({"usr":sys.argv[1],"pwd":sys.argv[2]}))' "$USER_NAME" "$USER_PASS")"

for dep in "$@"; do
  echo "[重算] $dep"
  curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" \
    "$CONTROL/api/method/pearnly_control.provisioning.api_reapply_payloads?deployment=$dep" > "$RAW"

  site=$(pick site)
  group=$(pick group)
  admin_user=$(pick1 admin_user)
  roles=$(pick roles)
  workspace=$(pick workspace)

  # 缺一个就停：带着空值往下跑只会把站点改坏，而且看不出来
  for pair in "站点:$site" "运行组:$group" "管理员:$admin_user"; do
    [ -n "${pair#*:}" ] || { echo "  [错误] 控制面返回缺 ${pair%%:*}：$(head -c 200 "$RAW")" >&2; exit 1; }
  done
  echo "  站点=${site} 组=${group} 管理员=${admin_user}"

  echo "  角色与权限"
  dc exec -T "$group-bench" bench --site "$site" execute \
    pearnly_core.provisioning.ensure_feature_roles \
    --kwargs "$(py -c "$WRAP_ROLES" "$roles" "$admin_user")" 2>&1 | tail -2

  echo "  工作区"
  dc exec -T "$group-bench" bench --site "$site" execute \
    pearnly_core.provisioning.ensure_product_workspace \
    --kwargs "$(py -c "$WRAP_SPEC" "$workspace")" 2>&1 | tail -2

  # 必须重启：`ensure_product_workspace` 会把"买到了哪些功能"写进 site_config，
  # 而 gunicorn 是 --preload（配置在进程启动时读一次）—— 不重启的话菜单还是旧的，
  # 表现为"后台数据都改了，页面纹丝不动"（踩过）。
  d compose -f "$COMPOSE" restart "$group-web" >/dev/null 2>&1 || true
  echo "  已重启 ${group}-web（让站点读到新的功能清单）"
done

echo "[done] 重打完成"
