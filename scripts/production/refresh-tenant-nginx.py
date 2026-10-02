#!/usr/bin/env python3
"""按 provision-agent 的模板重写已开通租户站的宿主机 nginx 配置（幂等；宿主机 root 运行）。

开户时 provision-agent 写 /etc/nginx/conf.d/pearnly-tenant-<site>.conf，之后模板再改（2026-10-02：真实客户端 IP，
X-Real-IP / X-Forwarded-For 改为本机看到的来源）已开通的站点不会自动跟上。部署时跑一次：只改内容不同的文件，
先 `nginx -t`，不通过就把改过的文件全部还原、不 reload。站点与产品组以 /opt/pearnly/state/sites/<site>.json 为准，
已删除（deleted）或还没有证书的站点不动。
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("provision_agent", HERE / "infrastructure/production/provision-agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
CONF = Path("/etc/nginx/conf.d")
LETSENCRYPT = Path("/etc/letsencrypt/live")


def plan(state=agent.STATE, conf=CONF, certs=LETSENCRYPT):
    """[(配置文件, 新内容)]：只列内容会变的。"""
    changes = []
    for manifest in sorted(state.glob("*.json")):
        owner = json.loads(manifest.read_text())
        site, group = owner.get("site"), owner.get("group")
        if owner.get("deleted") or group not in agent.PORTS or not (certs / str(site) / "fullchain.pem").exists():
            continue
        path = conf / f"pearnly-tenant-{site}.conf"
        if not path.exists():
            continue
        text = agent.nginx(site, group, True)
        if path.read_text() != text:
            changes.append((path, text))
    return changes


def main():
    agent.ensure_timing_format()  # 租户配置引用的耗时日志格式（provision-agent.TIMING_FORMAT）
    changes = plan()
    backups = {path: path.read_text() for path, _ in changes}
    for path, text in changes:
        path.write_text(text)
    if changes:
        test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
        if test.returncode:
            for path, text in backups.items():
                path.write_text(text)
            print(json.dumps({"status": "restored", "error": test.stderr[-500:]}))
            return 1
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
    print(json.dumps({"status": "ok", "rewritten": [path.name for path, _ in changes]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
