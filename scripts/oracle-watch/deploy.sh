#!/usr/bin/env bash
# 把 infrastructure/oracle-watch/ 装到甲骨文那台（幂等）：守望每分钟一次、恢复演练每月 1 日。在这台 Mac 上运行。
# 密钥只在第一次（或带 --secrets）时写，直接经管道写到那台的 /opt/pearnly-watch/secrets/（600、root），本机与终端都不落地：
#   backup-reader.json  备份桶只读服务账号的新密钥（gcloud 现建）
#   smtp.json           官方发信邮箱 hello@pearnly.com（scripts/production/smtp-config.py --control，读本机控制面）
#   backup.key          每日备份密钥包的解密钥匙（从生产服务器取；顺带在主服务器之外另存了一份）
#   watch.env           ALERT_TO=收告警的邮箱
# 用法：bash scripts/oracle-watch/deploy.sh [--secrets] [--alert-to 邮箱] [--check-binlog]
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
oracle=(ssh -i "$HOME/.ssh/oracle_free" -o BatchMode=yes -o ConnectTimeout=15 ubuntu@213.35.126.34)
alert_to=skinzihao@gmail.com; secrets=0; check_binlog=0
while (( $# )); do
  case "$1" in
    --secrets) secrets=1 ;;
    --alert-to) alert_to=$2; shift ;;
    --check-binlog) check_binlog=1 ;;   # 生产开始每 5 分钟同步改动记录之后再带
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

"${oracle[@]}" 'sudo install -d -m 755 /opt/pearnly-watch /var/lib/pearnly-watch && sudo install -d -m 700 /opt/pearnly-watch/secrets'
tar -C "$root/infrastructure/oracle-watch" -czf - . | "${oracle[@]}" 'sudo tar -C /opt/pearnly-watch -xzf - && sudo chown -R root:root /opt/pearnly-watch && sudo chmod 755 /opt/pearnly-watch/drill.sh'

put() { "${oracle[@]}" "sudo sh -c 'umask 077; cat > /opt/pearnly-watch/secrets/$1'"; }
missing() { ! "${oracle[@]}" "sudo test -s /opt/pearnly-watch/secrets/$1"; }
if (( secrets )) || missing backup-reader.json; then
  gcloud iam service-accounts keys create /dev/stdout --quiet \
    --iam-account=pearnly-backup-reader@pearnly.iam.gserviceaccount.com 2>/dev/null | put backup-reader.json
fi
if (( secrets )) || missing smtp.json; then
  PEARNLY_CONTROL_CONTAINER=${PEARNLY_CONTROL_CONTAINER:-pearnly-sample-control-web-1} \
    python3 "$root/scripts/production/smtp-config.py" --control | put smtp.json
fi
if (( secrets )) || missing backup.key; then
  ssh -o BatchMode=yes -o ConnectTimeout=15 pearnly 'cat /opt/pearnly/secrets/backup.key' | put backup.key
fi
printf 'ALERT_TO=%s\nCHECK_BINLOG=%s\n' "$alert_to" "$check_binlog" | put watch.env

"${oracle[@]}" 'cd /opt/pearnly-watch && sudo docker build -q -t pearnly-watch . >/dev/null \
  && for unit in pearnly-watch.service pearnly-watch.timer pearnly-drill.service pearnly-drill.timer; do
       sudo install -m 644 $unit /etc/systemd/system/$unit; done \
  && sudo systemctl daemon-reload && sudo systemctl enable --now pearnly-watch.timer pearnly-drill.timer >/dev/null \
  && sudo ls -l /opt/pearnly-watch/secrets | awk "NR>1 {print \$1, \$5, \$9}" \
  && systemctl list-timers pearnly-watch.timer pearnly-drill.timer --no-pager | head -3'
