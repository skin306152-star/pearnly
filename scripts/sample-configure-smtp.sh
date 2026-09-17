#!/usr/bin/env bash
# 给身份服务（Keycloak）的两个 realm 配上发件邮箱。
#
# 为什么需要它：开通租户后要发**邀请邮件**（用户点链接自己设密码），
# 登录页的**忘记密码**也要发邮件。这两条都走 Keycloak 的发信通道，
# 而 Keycloak 的邮件服务器设置**默认是空的**——不配就一封都发不出去。
#
# 凭据从 .env.local 读（已被 .gitignore 忽略），不写进本文件、不进 Git。
#
# 用法: scripts/sample-configure-smtp.sh [--test]
set -euo pipefail

KC=http://keycloak.localhost:8105
REALMS=(pearnly-platform pearnly-business)

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$repo_root/.env.local"
if [ ! -f "$env_file" ]; then
  echo "缺少 $env_file（里面放 SMTP 凭据，见文件头注释）" >&2
  exit 2
fi
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

: "${PEARNLY_SMTP_HOST:?}" "${PEARNLY_SMTP_PORT:?}" "${PEARNLY_SMTP_USER:?}"
: "${PEARNLY_SMTP_PASSWORD:?}" "${PEARNLY_SMTP_FROM:?}"

admin_token() {
  curl -sS -m 20 -X POST "$KC/realms/master/protocol/openid-connect/token" \
    -d 'grant_type=password' -d 'client_id=admin-cli' \
    --data-urlencode "username=${KC_BOOTSTRAP_ADMIN_USERNAME:-kc-admin-local}" \
    --data-urlencode "password=${KC_BOOTSTRAP_ADMIN_PASSWORD:-kc-admin-local-only}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))'
}

TOKEN="$(admin_token)"
if [ -z "$TOKEN" ]; then
  echo "取身份服务管理员令牌失败" >&2
  exit 1
fi
echo "已取到管理员令牌"

# smtpServer 是 realm 级下的**嵌套**配置。踩过的坑：把扁平字段直接当 realm 表示发，
# 会得到 400 `Unrecognized field "host"` —— 必须包在 {"smtpServer": {...}} 里。
# 另外 Keycloak 的 realm PUT 是**合并**语义（未出现的字段不动），所以只发这一块
# 不会冲掉注册开关、令牌寿命等其它设置（已实测：客户端与各开关均保持原样）。
smtp_inner=$(python3 - "$PEARNLY_SMTP_HOST" "$PEARNLY_SMTP_PORT" "$PEARNLY_SMTP_FROM" \
                       "$PEARNLY_SMTP_FROM_NAME" "$PEARNLY_SMTP_USER" "$PEARNLY_SMTP_PASSWORD" <<'PY'
import json, sys
host, port, frm, name, user, pwd = sys.argv[1:7]
print(json.dumps({
    "host": host,
    "port": str(port),
    "from": frm,
    "fromDisplayName": name,
    "replyTo": frm,
    "replyToDisplayName": name,
    "envelopeFrom": frm,
    "ssl": "false",       # 587 用 STARTTLS，不用隐式 SSL
    "starttls": "true",
    "auth": "true",
    "user": user,
    "password": pwd,
}, ensure_ascii=False))
PY
)
smtp_json=$(python3 -c 'import json,sys; print(json.dumps({"smtpServer": json.loads(sys.argv[1])}, ensure_ascii=False))' "$smtp_inner")

for realm in "${REALMS[@]}"; do
  echo "[$realm] 写入发件邮箱 ${PEARNLY_SMTP_FROM}（经 ${PEARNLY_SMTP_HOST}:${PEARNLY_SMTP_PORT}）"
  code=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X PUT \
    "$KC/admin/realms/$realm" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data-binary "$smtp_json")
  if [ "$code" != "204" ]; then
    echo "  [错误] 写入失败，HTTP $code" >&2
    exit 1
  fi

  # 忘记密码要能用，这个开关必须开（本项目的产品登录页就靠它）
  curl -sS -m 20 -o /dev/null -X PUT "$KC/admin/realms/$realm" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data-binary '{"resetPasswordAllowed":true}'

  got=$(curl -sS -m 20 -H "Authorization: Bearer $TOKEN" "$KC/admin/realms/$realm" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); s=d.get("smtpServer") or {}; print(s.get("host"), s.get("port"), s.get("user"), "| resetPasswordAllowed=", d.get("resetPasswordAllowed"), "| 密码已设=", bool(s.get("password")))')
  echo "  回读: $got"
done

if [ "${1:-}" = "--test" ]; then
  for realm in "${REALMS[@]}"; do
    echo "[$realm] 测试发信通道"
    body=$(python3 - "$smtp_json" <<'PY'
import json, sys
print(json.dumps({"config": json.loads(sys.argv[1])["smtpServer"]}, ensure_ascii=False))
PY
)
    out=$(curl -sS -m 60 -w '\nHTTP=%{http_code}' -X POST \
      "$KC/admin/realms/$realm/test-smtp-connection" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data-binary "$body" 2>&1 || true)
    echo "  $out"
  done
else
  echo "提示：加 --test 可让身份服务实际连一次 SMTP 验证凭据"
fi

echo "[done] 发件邮箱配置完成"
