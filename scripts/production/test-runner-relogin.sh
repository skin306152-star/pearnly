#!/usr/bin/env bash
# 盯梢循环的"会话失效自愈"检查：不联网，只验函数行为。
#
# 为什么值得有一条：控制面跑一次 migrate / 清缓存会把所有会话作废，而盯梢循环只
# 在启动时登录一次。2026-09-19 实测后果是执行器**永远**看不见待办 —— 用户在界面上
# 排了删除，控制面有单子，本机执行器一动不动，日志里只有一串 json 解析报错。
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
RUNNER="$HERE/scripts/sample-provision.sh"
py() { python3 "$@"; }
CONTROL=http://127.0.0.1:9
CONTROL_HOST=control.localhost
JAR=/dev/null

extract() { sed -n "/^$1()/,/^}/p" "$RUNNER"; }

# 计数器落在文件上：`out=$(api_get_json …)` 是子 shell，函数里改的变量传不出来。
TALLY=$(mktemp)
count() { echo "$1" >> "$TALLY"; }
tally_of() { grep -c "^$1\$" "$TALLY" || true; }

# 第一次返回空（会话失效的样子），重登之后返回正常 JSON。
api_get() {
  count api_get
  if [ "$(tally_of api_get)" -eq 1 ]; then echo ""; else echo '{"message":[]}'; fi
}
login() { count login; }

eval "$(extract api_get_json)"
out=$(api_get_json "pearnly_control.provisioning.api_next_steps?limit=1")

[ "$(tally_of login)" = 1 ] || { echo "FAIL: 会话失效后没有重登" >&2; exit 1; }
[ "$(tally_of api_get)" = 2 ] || { echo "FAIL: 没有重试请求" >&2; exit 1; }
[ "$out" = '{"message":[]}' ] || { echo "FAIL: 重登后没取到响应：$out" >&2; exit 1; }

# 正常响应不该触发重登。
: > "$TALLY"
api_get() { count api_get; echo '{"message":[]}'; }
eval "$(extract api_get_json)"
api_get_json "pearnly_control.provisioning.api_next_steps?limit=1" >/dev/null
[ "$(tally_of login)" = 0 ] || { echo "FAIL: 正常响应也重登了" >&2; exit 1; }

echo "PASS 会话失效自动重登，正常响应不重登"
