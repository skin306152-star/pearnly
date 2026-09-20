#!/usr/bin/env bash
# New Ubuntu host only. Installs the host runtime; no DNS, tenant or database writes.
set -euo pipefail
[[ "$EUID" == 0 ]] || exit 2
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 && "$(uname -m)" == x86_64 ]] || exit 2
[[ -s /root/.ssh/authorized_keys ]] || { echo 'An operator public key is required'; exit 2; }
export DEBIAN_FRONTEND=noninteractive
install -d -m 700 /opt/pearnly/secrets /opt/pearnly/state
install -d -m 755 /opt/pearnly/deploy /var/www/letsencrypt
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg nginx certbot ufw unattended-upgrades
install -d -m 755 /etc/apt/keyrings
curl --fail --silent --show-error https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
gpg --batch --show-keys --with-colons /etc/apt/keyrings/docker.asc | grep -q '9DC858229FC7DD38854AE2D88D81803C0EBFCD88'
chmod 644 /etc/apt/keyrings/docker.asc
printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable\n' > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
timedatectl set-timezone UTC
install -d -m 755 /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=256M\n' > /etc/systemd/journald.conf.d/pearnly.conf
systemctl restart systemd-journald
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
systemctl enable --now docker nginx
docker version --format '{{.Server.Version}}'
docker compose version
ufw status
