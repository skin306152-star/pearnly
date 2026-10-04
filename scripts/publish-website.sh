#!/usr/bin/env bash
# 构建 website/ 并发布到 pearnly.com：整目录替换 /var/www/pearnly-landing，上一版留在 .prev 供回退。
# 只碰官网目录、总览视频目录（--media）和 pearnly-landing.conf（--nginx）；小助手下载目录、业务容器都不动。一次 SSH 完成。
#
#   bash scripts/publish-website.sh                    # 只换网页
#   bash scripts/publish-website.sh --nginx --media    # 同时安装 landing.conf、上传 website/media 里的总览视频
#   bash scripts/publish-website.sh --rollback         # 网页换回上一版
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOST=${PEARNLY_WEBSITE_HOST:-pearnly}
SITE=/var/www/pearnly-landing
MEDIA=/var/www/pearnly-media
WITH_NGINX=0 WITH_MEDIA=0

for arg in "$@"; do
  case "$arg" in
    --rollback)
      ssh "$HOST" "set -e; test -d $SITE.prev; mv $SITE $SITE.rollback-tmp; mv $SITE.prev $SITE; mv $SITE.rollback-tmp $SITE.prev; echo 'rolled back'"
      exit 0 ;;
    --nginx) WITH_NGINX=1 ;;
    --media) WITH_MEDIA=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
python3 "$ROOT/website/build.py" --out "$WORK/dist"
mkdir -p "$WORK/bundle"
mv "$WORK/dist" "$WORK/bundle/site"
if [ "$WITH_NGINX" = 1 ]; then
  cp "$ROOT/infrastructure/production/landing.conf" "$WORK/bundle/landing.conf"
fi
if [ "$WITH_MEDIA" = 1 ]; then
  mkdir -p "$WORK/bundle/media"
  for name in $(python3 -c "import sys; sys.path.insert(0, '$ROOT/website'); import content; print(' '.join(v['file'] for v in content.OVERVIEW.values()))"); do
    test -s "$ROOT/website/media/$name" || { echo "missing website/media/$name" >&2; exit 1; }
    cp "$ROOT/website/media/$name" "$WORK/bundle/media/"
  done
fi

tar -C "$WORK/bundle" -cf - . | ssh "$HOST" "set -e
in=\$(mktemp -d)
trap 'rm -rf \$in' EXIT
tar -C \$in -xf -
test -s \$in/site/index.html && test -s \$in/site/sitemap.xml
chmod -R a+rX \$in
if [ -d \$in/media ]; then
  rm -rf $MEDIA.new && mv \$in/media $MEDIA.new
  rm -rf $MEDIA.prev; if [ -d $MEDIA ]; then mv $MEDIA $MEDIA.prev; fi
  mv $MEDIA.new $MEDIA && rm -rf $MEDIA.prev
fi
rm -rf $SITE.new && mv \$in/site $SITE.new
rm -rf $SITE.prev; if [ -d $SITE ]; then mv $SITE $SITE.prev; fi
mv $SITE.new $SITE
if [ -f \$in/landing.conf ]; then
  cp /etc/nginx/conf.d/pearnly-landing.conf /etc/nginx/pearnly-landing.conf.prev
  install -m 644 \$in/landing.conf /etc/nginx/conf.d/pearnly-landing.conf
  if ! /usr/sbin/nginx -t; then
    install -m 644 /etc/nginx/pearnly-landing.conf.prev /etc/nginx/conf.d/pearnly-landing.conf
    echo 'nginx config rejected; previous config restored' >&2
    exit 1
  fi
  /bin/systemctl reload nginx
fi
echo published"
