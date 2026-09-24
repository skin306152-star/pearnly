#!/usr/bin/env bash
# 在测试机（WSL）内：把固定域名的 Cloudflare 隧道装成 systemd 服务。
#
# 与 tunnel.sh 的区别：tunnel.sh 是随机域名的"快速隧道"（地址每次变）；
# 这个是命名隧道，入口按隧道配置里的固定主机名走（testdms/testerp.pearnly.com），
# 因此不需要本地 nginx 改写 Host。
#
# 运行令牌由 Mac 侧投递到 /opt/pearnly-testenv/cloudflare-tunnel.token（600），
# 服务通过 EnvironmentFile 读取，不写进 unit 文件。
set -euo pipefail

secret=/opt/pearnly-testenv/cloudflare-tunnel.token
env_file=/opt/pearnly-testenv/cloudflare-tunnel.env
[[ -s "$secret" ]] || { echo "missing $secret"; exit 2; }

umask 077
printf 'TUNNEL_TOKEN=%s\n' "$(tr -d '\n' < "$secret")" > "$env_file"
chmod 600 "$env_file"

cat > /etc/systemd/system/pearnly-tunnel.service <<EOF
[Unit]
Description=Pearnly testenv public entry (Cloudflare named tunnel)
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=$env_file
ExecStart=$(command -v cloudflared) tunnel --no-autoupdate run
Restart=always
RestartSec=5
StandardOutput=append:/var/log/pearnly-tunnel.log
StandardError=append:/var/log/pearnly-tunnel.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable pearnly-tunnel >/dev/null 2>&1
: > /var/log/pearnly-tunnel.log
systemctl restart pearnly-tunnel
sleep 8
echo "active=$(systemctl is-active pearnly-tunnel)"
tail -6 /var/log/pearnly-tunnel.log
