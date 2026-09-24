#!/usr/bin/env bash
# 在那台 Windows 的 WSL2 Ubuntu 24.04 里准备宿主运行环境。
# 与正式服务器的 scripts/production/bootstrap-host.sh 同源同包，只少了 ufw/nginx/certbot
# （测试机不接公网入口，TLS 由隧道终结）。
set -euo pipefail

[[ "$EUID" == 0 ]] || { echo 'Run as root inside WSL'; exit 2; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == "24.04" && "$(uname -m)" == "x86_64" ]] || {
  echo 'Expected Ubuntu 24.04 x86_64'; exit 2; }

# systemd 必须在 WSL 里打开，docker/nginx 才能像正式机那样由 systemd 托管。
if [[ ! -d /run/systemd/system ]]; then
  printf '[boot]\nsystemd=true\n' > /etc/wsl.conf
  echo 'Enabled systemd in /etc/wsl.conf; restart WSL and re-run this script.'
  exit 3
fi

export DEBIAN_FRONTEND=noninteractive
install -d -m 700 /opt/pearnly/secrets /opt/pearnly/state
install -d -m 755 /opt/pearnly-testenv/deploy
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg
install -d -m 755 /etc/apt/keyrings
curl --fail --silent --show-error https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
gpg --batch --show-keys --with-colons /etc/apt/keyrings/docker.asc \
  | grep -q '9DC858229FC7DD38854AE2D88D81803C0EBFCD88'
chmod 644 /etc/apt/keyrings/docker.asc
printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n' \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# 公网入口用：nginx 做 Host 改写，cloudflared 提供 https 隧道。
apt-get install -y -qq nginx
curl -fsSL -o /tmp/cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
dpkg -i /tmp/cloudflared.deb >/dev/null
timedatectl set-timezone UTC
systemctl enable --now docker nginx

# 测试环境的 OCR 是占位空凭据：正式 compose 会把它挂进产品容器，测试站点不启用 OCR，
# 因此不会产生付费调用。
[[ -s /opt/pearnly/secrets/ocr.json ]] || printf '{}\n' > /opt/pearnly/secrets/ocr.json
chmod 600 /opt/pearnly/secrets/ocr.json

docker version --format 'docker server {{.Server.Version}}'
docker compose version
echo WSL_SETUP_OK
