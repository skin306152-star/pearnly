#!/usr/bin/env bash
# 在甲骨文上跑 Claude 识别闸门（ADR-022，scripts/claude-ocr/gate.py）：把这个工作树的 apps 同步过去，在 gate 容器
# （python 3.14 + 生产镜像同版本依赖）里跑；读法经本机 claude-ocr 服务（127.0.0.1:8443，同一份令牌与证书指纹）——
# 与生产同一条路。语料与结果都在那台的 /opt/pearnly-claude-eval（不放这台 Mac）。
# 用法：bash scripts/claude-ocr/run-gate.sh invoices --set test --cap 3 [--workers 6]
#       bash scripts/claude-ocr/run-gate.sh recon --list /corpus/recon/list.tsv --cap 3
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
oracle=(ssh -i "$HOME/.ssh/oracle_free" -o BatchMode=yes -o ConnectTimeout=15 ubuntu@213.35.126.34)

# GATE_SKIP_SYNC=1：几组同时跑时只同步一次（同步会换掉正在跑的容器底下的代码）。
[[ -n "${GATE_SKIP_SYNC:-}" ]] || COPYFILE_DISABLE=1 tar -C "$root" --exclude='__pycache__' --exclude='node_modules' --exclude='public' -czf - \
    apps/pearnly_core/pearnly_core apps/pearnly_cowork/pearnly_cowork apps/pearnly_erp_portal/pearnly_erp_portal \
    scripts/claude-ocr/gate.py \
  | "${oracle[@]}" 'sudo rm -rf /opt/pearnly-claude-gate/src && sudo install -d -m 755 /opt/pearnly-claude-gate/src \
       && sudo tar -C /opt/pearnly-claude-gate/src -xzf -'
args=$(printf ' %q' "$@")
"${oracle[@]}" "sudo bash -s" <<REMOTE
set -euo pipefail
umask 077
env=\$(mktemp)
trap 'rm -f \$env' EXIT
. /opt/pearnly-claude-ocr/secrets/env
pin=\$(openssl x509 -in /opt/pearnly-claude-ocr/secrets/cert.pem -noout -fingerprint -sha256 | cut -d= -f2 | tr -d : | tr A-F a-f)
printf 'PEARNLY_CLAUDE_OCR_URL=https://127.0.0.1:8443\nPEARNLY_CLAUDE_OCR_TOKEN=%s\nPEARNLY_CLAUDE_OCR_CERT_SHA256=%s\n' "\$CLAUDE_OCR_TOKEN" "\$pin" > \$env
install -d -m 755 /opt/pearnly-claude-eval/runs-prod && chown ubuntu /opt/pearnly-claude-eval/runs-prod
docker run --rm --network host --memory 2g --env-file \$env -e PYTHONUNBUFFERED=1 \
  -v /opt/pearnly-claude-gate/src:/src:ro -v /opt/pearnly-claude-eval/corpus:/corpus:ro \
  -v /opt/pearnly-claude-eval/runs-prod:/runs pearnly-claude-gate python /src/scripts/claude-ocr/gate.py$args
REMOTE
