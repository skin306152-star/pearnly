#!/usr/bin/env bash
# 部署 / 更新甲骨文上的 claude-ocr 服务（ADR-022，幂等）。在这台 Mac 上运行。
# 第一次（或带 --secrets）生成：自签证书（生产钉死指纹）、服务令牌；Anthropic 密钥取那台评测目录里用户给的那把。
# 都只在那台的 /opt/pearnly-claude-ocr/secrets（600）；生产 runtime.env 要加的四行写到本机
# ~/.config/pearnly-claude-ocr/runtime.env（600），由 scripts/claude-ocr/production-env.sh 推上去（动生产前先约时间）。
# 入站：甲骨文安全列表 + iptables 只放行生产 107.155.65.74 的 TCP 8443。
# 用法：bash scripts/claude-ocr/deploy.sh [--secrets]
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
oracle=(ssh -i "$HOME/.ssh/oracle_free" -o BatchMode=yes -o ConnectTimeout=15 ubuntu@213.35.126.34)
host=213.35.126.34; port=8443; cfg=$HOME/.config/pearnly-claude-ocr
secrets=0; [[ "${1:-}" == "--secrets" ]] && secrets=1

"${oracle[@]}" 'sudo install -d -m 755 /opt/pearnly-claude-ocr /opt/pearnly-claude-ocr/build && sudo install -d -m 700 /opt/pearnly-claude-ocr/secrets'
COPYFILE_DISABLE=1 tar -C "$root/apps/pearnly_core" --exclude='__pycache__' -czf - pearnly_core/__init__.py pearnly_core/claude_ocr \
  | "${oracle[@]}" 'sudo rm -rf /opt/pearnly-claude-ocr/build/pearnly_core && sudo tar -C /opt/pearnly-claude-ocr/build -xzf - \
       && sudo sh -c ": > /opt/pearnly-claude-ocr/build/pearnly_core/__init__.py"'
cat "$root/infrastructure/claude-ocr/Dockerfile" | "${oracle[@]}" 'sudo tee /opt/pearnly-claude-ocr/build/Dockerfile >/dev/null'

if (( secrets )) || ! "${oracle[@]}" 'sudo test -s /opt/pearnly-claude-ocr/secrets/env'; then
  "${oracle[@]}" "sudo bash -s" <<REMOTE
set -euo pipefail
cd /opt/pearnly-claude-ocr/secrets
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj '/CN=claude-ocr' -keyout key.pem -out cert.pem 2>/dev/null
chmod 644 cert.pem; chgrp 65534 key.pem; chmod 640 key.pem
umask 077
printf 'CLAUDE_OCR_TOKEN=%s\nANTHROPIC_API_KEY=%s\nCLAUDE_OCR_CERT=/secrets/cert.pem\nCLAUDE_OCR_KEY=/secrets/key.pem\nCLAUDE_OCR_PORT=$port\n' \
  "\$(openssl rand -hex 32)" "\$(cat /opt/pearnly-claude-eval/anthropic.key)" > env
REMOTE
fi
"${oracle[@]}" "cd /opt/pearnly-claude-ocr && sudo docker build -q -t pearnly-claude-ocr build >/dev/null \
  && (sudo docker rm -f pearnly-claude-ocr >/dev/null 2>&1 || true) \
  && sudo docker run -d --name pearnly-claude-ocr --restart unless-stopped --memory 1g -p $port:$port \
       --env-file secrets/env -v /opt/pearnly-claude-ocr/secrets/cert.pem:/secrets/cert.pem:ro \
       -v /opt/pearnly-claude-ocr/secrets/key.pem:/secrets/key.pem:ro pearnly-claude-ocr >/dev/null \
  && sleep 2 && sudo docker ps --filter name=pearnly-claude-ocr --format '{{.Names}} {{.Status}}'"

# 生产要的四行（本机 600）：地址、令牌、证书指纹、Anthropic 密钥（甲骨文连不上时生产本机直调）。
install -d -m 700 "$cfg"
"${oracle[@]}" "sudo sh -c '. /opt/pearnly-claude-ocr/secrets/env; printf \"PEARNLY_CLAUDE_OCR_URL=https://$host:$port\nPEARNLY_CLAUDE_OCR_TOKEN=%s\nPEARNLY_ANTHROPIC_API_KEY=%s\n\" \"\$CLAUDE_OCR_TOKEN\" \"\$ANTHROPIC_API_KEY\"; printf \"PEARNLY_CLAUDE_OCR_CERT_SHA256=%s\n\" \$(openssl x509 -in /opt/pearnly-claude-ocr/secrets/cert.pem -noout -fingerprint -sha256 | cut -d= -f2 | tr -d : | tr A-F a-f)'" \
  | (umask 077; cat > "$cfg/runtime.env")
echo "production env lines written to $cfg/runtime.env ($(grep -c = "$cfg/runtime.env") keys)"
