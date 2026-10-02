#!/usr/bin/env bash
# 装 / 更新每日异地备份定时器（宿主机 root）。前提：/opt/pearnly/secrets/backup-writer.json（只写服务账号密钥）与
# backup.key（加密密钥，第一次运行时生成；请另存一份到宿主机以外，否则宿主机丢了就解不开密钥包）。
set -euo pipefail
src=/opt/pearnly/deploy/infrastructure/production
install -m 644 $src/pearnly-offsite-backup.service /etc/systemd/system/pearnly-offsite-backup.service
install -m 644 $src/pearnly-offsite-backup.timer /etc/systemd/system/pearnly-offsite-backup.timer
if [[ ! -s /opt/pearnly/secrets/backup.key ]]; then
  umask 077; openssl rand -base64 48 > /opt/pearnly/secrets/backup.key
  echo "generated /opt/pearnly/secrets/backup.key — copy it somewhere safe off this host"
fi
test -s /opt/pearnly/secrets/backup-writer.json || { echo "missing /opt/pearnly/secrets/backup-writer.json"; exit 1; }
systemctl daemon-reload
systemctl enable --now pearnly-offsite-backup.timer
systemctl list-timers pearnly-offsite-backup.timer --no-pager
