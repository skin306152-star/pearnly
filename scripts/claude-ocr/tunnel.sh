#!/usr/bin/env bash
# 甲骨文上的 Cloudflare 隧道（ADR-023，幂等）：originals.pearnly.com → 那台本机 127.0.0.1:8444（原件的签名上传 / 预览 / 下载）。
# 隧道 pearnly-originals 用本机 cloudflared 建（`cloudflared tunnel login` 由用户在浏览器授权 pearnly.com；
# `tunnel create` / `tunnel route dns ... originals.pearnly.com` / `tunnel token`）；令牌只存本机
# ~/.config/pearnly-claude-ocr/tunnel.token（600）与那台 /opt/pearnly-claude-ocr/secrets/tunnel.env（600），不进仓库。
# 用法：bash scripts/claude-ocr/tunnel.sh
set -euo pipefail
oracle=(ssh -i "$HOME/.ssh/oracle_free" -o BatchMode=yes -o ConnectTimeout=15 ubuntu@213.35.126.34)
token_file=$HOME/.config/pearnly-claude-ocr/tunnel.token
[[ -s "$token_file" ]] || { echo "missing $token_file" >&2; exit 2; }
printf 'TUNNEL_TOKEN=%s\n' "$(tr -d '[:space:]' < "$token_file")" \
  | "${oracle[@]}" 'sudo sh -c "umask 077; cat > /opt/pearnly-claude-ocr/secrets/tunnel.env"'
"${oracle[@]}" 'sudo docker rm -f pearnly-originals-tunnel >/dev/null 2>&1 || true
sudo docker run -d --name pearnly-originals-tunnel --restart unless-stopped --network host --memory 256m \
  --env-file /opt/pearnly-claude-ocr/secrets/tunnel.env cloudflare/cloudflared:2026.8.3 tunnel --no-autoupdate run --url http://127.0.0.1:8444 >/dev/null
sleep 5; sudo docker logs pearnly-originals-tunnel 2>&1 | grep -E "Registered tunnel connection|ERR" | tail -4'
