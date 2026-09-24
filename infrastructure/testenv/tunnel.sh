#!/usr/bin/env bash
# 在测试机（WSL）内：给某个测试租户站点开一个公网 https 入口。
#
# 站点名保持与线上一致（例如 testdms.pearnly.com）。隧道主机名是随机域名，
# Frappe 按 Host 找站点，所以在中间放一个 nginx，把 Host 改写成站点名。
#
# 用法：bash tunnel.sh <站点名> <本地端口>
#   例：bash tunnel.sh testdms.pearnly.com 8183
#
# 说明：这是 Cloudflare 的"快速隧道"，地址每次重启都会变，LINE 后台的 webhook
# 地址要跟着改。要固定地址需要用自己的域名建 named tunnel（需要域名与账号）。
set -euo pipefail

[[ $# -ge 2 ]] || { echo 'usage: tunnel.sh <site> <port>'; exit 2; }
site=$1; port=$2
[[ "$site" =~ ^[a-z0-9.-]+$ && "$port" =~ ^[0-9]+$ ]] || { echo 'bad arguments'; exit 2; }

conf=/etc/nginx/sites-available/pearnly-testenv
cat > "$conf" <<EOF
# 测试环境公网入口：隧道进来后按站点名转给对应前端。
server {
  listen 127.0.0.1:8080;
  client_max_body_size 64m;
  location / {
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host $site;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
  }
}
EOF
ln -sf "$conf" /etc/nginx/sites-enabled/pearnly-testenv
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# 交给 systemd 托管：直接在 ssh 会话里后台起进程会被会话结束时的信号杀掉。
unit=/etc/systemd/system/pearnly-tunnel.service
cat > "$unit" <<EOF
[Unit]
Description=Pearnly testenv public entry (Cloudflare quick tunnel)
After=network-online.target nginx.service
Wants=network-online.target

[Service]
ExecStart=$(command -v cloudflared) tunnel --url http://127.0.0.1:8080 --no-autoupdate
Restart=always
RestartSec=5
StandardOutput=append:/var/log/pearnly-tunnel.log
StandardError=append:/var/log/pearnly-tunnel.log

[Install]
WantedBy=multi-user.target
EOF
: > /var/log/pearnly-tunnel.log
systemctl daemon-reload
systemctl enable pearnly-tunnel >/dev/null 2>&1
systemctl restart pearnly-tunnel

for _ in $(seq 1 40); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /var/log/pearnly-tunnel.log | head -1 || true)
  if [[ -n "$url" ]] && systemctl is-active --quiet pearnly-tunnel; then
    echo "PUBLIC_URL=$url"
    echo "site=$site port=$port"
    echo "service=systemctl status pearnly-tunnel"
    exit 0
  fi
  sleep 2
done
echo 'tunnel url not found or service not active; log tail:'
tail -20 /var/log/pearnly-tunnel.log
exit 1
