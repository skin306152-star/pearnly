#!/usr/bin/env bash
# 配置统一登录页的语言与主题（幂等，可重复执行）。
#
# 为什么要有这个脚本：这套设置当初是直接敲 API 设的，仓库里没留下任何记录 ——
# 换台机器、或者哪天被谁改回去，就只能靠猜。语言这种"每个租户都会看到"的东西
# 不该是口口相传的状态。
#
# 默认语言的取法（用户 2026-09-18 定："新租户默认泰语"）：
#   pearnly-business  租户登录口 —— **泰语**（租户在泰国）
#   pearnly-platform  控制面登录口 —— 中文（只有老板自己用，见 ADR-004）
# 登录页上仍然可以自己切语言，切换结果记在账号上（Keycloak 的用户 locale）。
#
# 用法: scripts/sample-configure-login-theme.sh
set -euo pipefail

KC="${PEARNLY_KEYCLOAK_URL:-http://keycloak.localhost:8105}"
THEME="${PEARNLY_LOGIN_THEME:-pearnly}"
# 顺序就是登录页上四个标签的顺序（旧设计：中 / TH / EN / 日）
SUPPORTED='["zh-CN","th","en","ja"]'

TOKEN=$(curl -sS -m 20 -X POST "$KC/realms/master/protocol/openid-connect/token" \
  -d 'grant_type=password' -d 'client_id=admin-cli' \
  --data-urlencode 'username=kc-admin-local' \
  --data-urlencode 'password=kc-admin-local-only' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')
[ -n "$TOKEN" ] || { echo "取管理员令牌失败（身份服务起来了吗？）" >&2; exit 1; }

# realm:期望的默认语言
REALMS=("pearnly-business:th" "pearnly-cowork:th" "pearnly-platform:zh-CN")

for entry in "${REALMS[@]}"; do
  realm="${entry%%:*}"
  default_locale="${entry##*:}"
  echo "[$realm] 登录主题=$THEME 默认语言=$default_locale"

  body=$(printf '{"loginTheme":"%s","internationalizationEnabled":true,"supportedLocales":%s,"defaultLocale":"%s","rememberMe":true,"resetPasswordAllowed":true}' \
    "$THEME" "$SUPPORTED" "$default_locale")

  code=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X PUT "$KC/admin/realms/$realm" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data-binary "$body")
  [ "$code" = "204" ] || { echo "  写入失败 HTTP $code" >&2; exit 1; }

  # 回读：只信服务端返回的，不信我们自己发出去的
  curl -sS -m 20 -H "Authorization: Bearer $TOKEN" "$KC/admin/realms/$realm" \
    | PEARNLY_WANT_THEME="$THEME" PEARNLY_WANT_LOCALE="$default_locale" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
theme = os.environ["PEARNLY_WANT_THEME"]
locale = os.environ["PEARNLY_WANT_LOCALE"]
ok = (d.get("loginTheme") == theme
      and d.get("internationalizationEnabled") is True
      and d.get("defaultLocale") == locale)
print("  回读: theme=%s intl=%s default=%s supported=%s reset=%s %s" % (
    d.get("loginTheme"), d.get("internationalizationEnabled"), d.get("defaultLocale"),
    d.get("supportedLocales"), d.get("resetPasswordAllowed"), "OK" if ok else "不一致!"))
sys.exit(0 if ok else 1)
'
done

echo "[done] 登录页语言配置就绪"
