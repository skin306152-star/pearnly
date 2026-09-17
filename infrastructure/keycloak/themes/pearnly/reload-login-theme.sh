#!/usr/bin/env bash
# 改完登录主题后，用它让改动真正生效。
#
# 为什么需要单独一个脚本（2026-09-18 实际踩到的坑）：
#   Keycloak 会把主题资源压缩成 gzip 存在容器里的
#   /opt/keycloak/data/tmp/kc-gzip-cache/<版本号>/ 下面，**重启容器不会清**。
#   于是浏览器（会发 Accept-Encoding: gzip）一直拿到旧文件，而 curl 不带压缩时
#   拿到的是新文件 —— 两边看到的不是同一份内容，极难排查。
#   所以：清缓存 + 重启，两件事必须一起做。
#
# 用法: ./reload-login-theme.sh [容器名] [Colima context]
set -euo pipefail

CONTAINER="${1:-pearnly-sample-keycloak-1}"
CONTEXT="${2:-colima-pearnly}"

echo "1/3 清理 gzip 缓存 ..."
docker --context "$CONTEXT" exec "$CONTAINER" \
  sh -c 'rm -rf /opt/keycloak/data/tmp/kc-gzip-cache && echo "   已清空 kc-gzip-cache"'

echo "2/3 重启容器 ..."
docker --context "$CONTEXT" restart "$CONTAINER" >/dev/null

echo "3/3 等待就绪 ..."
for _ in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 \
    http://keycloak.localhost:8105/realms/pearnly-platform/.well-known/openid-configuration || true)
  if [ "$code" = "200" ]; then echo "   就绪"; exit 0; fi
  sleep 1
done
echo "   超时：身份服务 40 秒内没起来，去看容器日志" >&2
exit 1
