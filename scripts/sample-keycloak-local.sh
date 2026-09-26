#!/usr/bin/env bash
# 本机样板的身份服务前置条件（幂等，可重复执行）。
#
# 为什么需要这个脚本 —— 三件事都是"不设就不通"，而且都不在应用代码里：
#
# 1. `sslRequired=none`（**仅本机**）：本机跑在 http 上，而 realm 默认是
#    `external`，Keycloak 会给 AUTH_SESSION_ID / KC_RESTART 这些登录 cookie
#    加上 `Secure`。http 下浏览器与 curl 按规范**都不会回传**这类 cookie，
#    结果就是提交账号密码时被拒："Restart login cookie not found"。
#    ⚠️ 生产必须保持 `external`（有 TLS）。这条只对本机样板成立。
#
# 2. 开户凭据要能**管理客户端**：开通时每个部署都要建自己的 OIDC 客户端
#    （回调地址按站点域名算，共用客户端第二个租户就登不进来）。控制面用的
#    `pearnly-admin` 原本只有 view/manage-users，建客户端会 403。
#
# 3. 忘记密码开关：登录页要有"忘记密码"，前提是 realm 开着 resetPasswordAllowed
#    （发信通道另见 sample-configure-smtp.sh）。
#
# 4. **密码策略留空**：用户 2026-09-18 明确要求"密码不要卡得太严格，纯字母、
#    纯数字都可以，**包括首次使用**"。首次使用 = 用户点邀请邮件里的链接、在
#    身份服务自己的设密页设密码，那一步的强度规则完全由 realm 的
#    `passwordPolicy` 决定，跟我们自己的校验无关。所以这里显式写成空，
#    保证"邀请注册"和"产品内改密"两条路的规则一致。
#
# 用法: scripts/sample-keycloak-local.sh
set -euo pipefail

KC=http://keycloak.localhost:8105
REALMS=(pearnly-business pearnly-cowork pearnly-platform)
ADMIN_CLIENT=pearnly-admin

TOKEN=$(curl -sS -m 20 -X POST "$KC/realms/master/protocol/openid-connect/token" \
  -d 'grant_type=password' -d 'client_id=admin-cli' \
  --data-urlencode 'username=kc-admin-local' \
  --data-urlencode 'password=kc-admin-local-only' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')
[ -n "$TOKEN" ] || { echo "取管理员令牌失败" >&2; exit 1; }
echo "已取到管理员令牌"

api() { curl -sS -m 20 -H "Authorization: Bearer $TOKEN" "$@"; }

for realm in "${REALMS[@]}"; do
  echo "[$realm] 本机 http：sslRequired=none + 打开忘记密码 + 密码策略留空"
  code=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X PUT "$KC/admin/realms/$realm" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data-binary '{"sslRequired":"none","resetPasswordAllowed":true,"passwordPolicy":""}')
  [ "$code" = "204" ] || { echo "  写入失败 HTTP $code" >&2; exit 1; }

  cid=$(api "$KC/admin/realms/$realm/clients?clientId=$ADMIN_CLIENT" \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["id"] if d else "")')
  if [ -z "$cid" ]; then
    echo "  没有 $ADMIN_CLIENT 客户端，跳过角色授予"
  else
    sa=$(api "$KC/admin/realms/$realm/clients/$cid/service-account-user" \
         | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))')
    rm_id=$(api "$KC/admin/realms/$realm/clients?clientId=realm-management" \
            | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["id"] if d else "")')
    if [ -n "$sa" ] && [ -n "$rm_id" ]; then
      have=$(api "$KC/admin/realms/$realm/users/$sa/role-mappings/clients/$rm_id" \
             | python3 -c 'import sys,json; print(" ".join(r["name"] for r in json.load(sys.stdin)))')
      for role in view-users manage-users view-clients manage-clients; do
        case " $have " in
          *" $role "*) echo "  $role 已有" ;;
          *)
            body=$(api "$KC/admin/realms/$realm/clients/$rm_id/roles/$role")
            curl -sS -m 20 -o /dev/null -w "  授予 $role -> HTTP %{http_code}\n" \
              -X POST "$KC/admin/realms/$realm/users/$sa/role-mappings/clients/$rm_id" \
              -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
              --data-binary "[$body]"
            ;;
        esac
      done
    fi
  fi

  api "$KC/admin/realms/$realm" | python3 -c "
import sys,json
d=json.load(sys.stdin)
policy = d.get('passwordPolicy') or ''
print('  回读: sslRequired=%s resetPasswordAllowed=%s passwordPolicy=%r' % (
    d.get('sslRequired'), d.get('resetPasswordAllowed'), policy))
if policy:
    sys.exit('  [错误] 密码策略非空，首次使用会被卡住：' + policy)
"
done

echo "[done] 本机身份服务前置条件就绪（生产不要照抄 sslRequired=none）"
