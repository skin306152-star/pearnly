#!/usr/bin/env bash
# 构建 website/ 并发布到 pearnly.com：整目录替换 /var/www/pearnly-landing，上一版留在 .prev 供回退。
# 只碰官网目录（和 --nginx 时的 pearnly-landing.conf）；小助手下载目录、业务容器都不动。一次 SSH 完成。
#
#   bash scripts/publish-website.sh            # 只换网页
#   bash scripts/publish-website.sh --nginx    # 同时安装 infrastructure/production/landing.conf 并 reload
#   bash scripts/publish-website.sh --rollback # 换回上一版
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOST=${PEARNLY_WEBSITE_HOST:-pearnly}
SITE=/var/www/pearnly-landing
MODE=${1:-}

if [ "$MODE" = "--rollback" ]; then
  ssh "$HOST" "set -e; test -d $SITE.prev; mv $SITE $SITE.rollback-tmp; mv $SITE.prev $SITE; mv $SITE.rollback-tmp $SITE.prev; echo 'rolled back'"
  exit 0
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
python3 "$ROOT/website/build.py" --out "$WORK/dist"
if [ "$MODE" = "--nginx" ]; then
  cp "$ROOT/infrastructure/production/landing.conf" "$WORK/dist/.landing.conf"
fi

tar -C "$WORK/dist" -czf - . | ssh "$HOST" "set -e
new=$SITE.new
rm -rf \$new && mkdir -p \$new && tar -C \$new -xzf - && chmod -R a+rX \$new
test -s \$new/index.html && test -s \$new/sitemap.xml
conf=
if [ -f \$new/.landing.conf ]; then conf=\$(mktemp) && mv \$new/.landing.conf \$conf; fi
rm -rf $SITE.prev
if [ -d $SITE ]; then mv $SITE $SITE.prev; fi
mv \$new $SITE
if [ -n \"\$conf\" ]; then
  cp /etc/nginx/conf.d/pearnly-landing.conf /etc/nginx/pearnly-landing.conf.prev
  install -m 644 \$conf /etc/nginx/conf.d/pearnly-landing.conf && rm -f \$conf
  if ! /usr/sbin/nginx -t; then
    install -m 644 /etc/nginx/pearnly-landing.conf.prev /etc/nginx/conf.d/pearnly-landing.conf
    echo 'nginx config rejected; previous config restored' >&2
    exit 1
  fi
  /bin/systemctl reload nginx
fi
echo published"
