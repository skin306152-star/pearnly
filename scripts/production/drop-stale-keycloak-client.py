#!/usr/bin/env python3
"""删掉账号池里不对应任何站点的 Keycloak 客户端（幂等；11-05 用户批：删 pearnly-business 池里 9-26 拆池前留下的 testcowork.pearnly.com）。

先只读确认它确实没在用，再导出备份，最后才删：
- 站点：同名站点的 site_config `pearnly_oidc_issuer` 不指这个池（testcowork 现在指 pearnly-cowork）；
- 会话：这个客户端在这个池里没有在线会话、没有离线会话；
- 登录事件：池里存着的事件里，最近没有这个客户端的登录（池没开事件存储时如实写「没有事件可查」）；
- 备份：客户端完整配置（含密钥）导出到本机 ~/pearnly-devtools/keycloak-backup/<池>-<客户端>-<时间>.json（600），不打印。
任何一项显示还在用就停下，不删。删完回读：这个池里没有了；另一个池里同名客户端照常在。

    python3 scripts/production/drop-stale-keycloak-client.py                 # 只读确认 + 导出备份
    python3 scripts/production/drop-stale-keycloak-client.py --apply         # 确认没在用才删，删后回读

默认就是这一个：--realm pearnly-business --client testcowork.pearnly.com --keep-realm pearnly-cowork。一次 SSH。
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

SSH = os.environ.get("PEARNLY_PRODUCTION_SSH", "pearnly")
BACKUP = Path.home() / "pearnly-devtools" / "keycloak-backup"

HOST_PROGRAM = r'''
import json, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

args = json.loads(sys.stdin.read())
realm, client_id, keep, apply = args["realm"], args["client"], args["keep"], args["apply"]
ENV = dict(l.split("=", 1) for l in Path("/opt/pearnly/secrets/runtime.env").read_text().splitlines() if "=" in l)
BASE = "http://127.0.0.1:8180"


def req(path, method="GET", data=None, token=None, form=False):
    headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = (urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
    with urlopen(Request(BASE + path, data=body, headers=headers, method=method), timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


token = req("/realms/master/protocol/openid-connect/token", "POST", {"grant_type": "password", "client_id": "admin-cli",
            "username": "pearnly-bootstrap", "password": ENV["KEYCLOAK_ADMIN_PASSWORD"]}, form=True)["access_token"]
out = {"realm": realm, "client": client_id}
found = req(f"/admin/realms/{realm}/clients?clientId={client_id}", token=token) or []
out["present"] = bool(found)
in_use = []
if found:
    cid = found[0]["id"]
    rep = req(f"/admin/realms/{realm}/clients/{cid}", token=token)
    rep["__secret"] = (req(f"/admin/realms/{realm}/clients/{cid}/client-secret", token=token) or {}).get("value")
    out["sessions"] = len(req(f"/admin/realms/{realm}/clients/{cid}/user-sessions?max=100", token=token) or [])
    out["offline_sessions"] = len(req(f"/admin/realms/{realm}/clients/{cid}/offline-sessions?max=100", token=token) or [])
    events_on = bool(req(f"/admin/realms/{realm}/events/config", token=token).get("eventsEnabled"))
    out["events_enabled"] = events_on
    events = req(f"/admin/realms/{realm}/events?client={client_id}&max=20", token=token) or []
    out["events"] = len(events)
    out["last_event"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(max(e["time"] for e in events) / 1000)) if events else None
    if out["sessions"] or out["offline_sessions"]:
        in_use.append("有会话")
    if events:
        in_use.append("有登录事件")
# 站点那边：同名站点指的是哪个池
product = "cowork" if client_id.startswith("testcowork") or "cowork" in client_id else "erp"
issuers = {}
for p in ("erp", "cowork", "dms"):
    done = subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", f"pearnly-production-{p}-web-1", "sh", "-c",
                           f"cat {client_id}/site_config.json 2>/dev/null"], capture_output=True, text=True)
    if done.returncode == 0 and done.stdout.strip():
        issuers[p] = json.loads(done.stdout).get("pearnly_oidc_issuer")
out["site_issuers"] = issuers
if any(str(v or "").rstrip("/").endswith("/" + realm) for v in issuers.values()):
    in_use.append(f"站点 {client_id} 指的就是 {realm}")
out["in_use"] = in_use
kept = req(f"/admin/realms/{keep}/clients?clientId={client_id}", token=token) or []
out["keep_present_before"] = bool(kept)
if found and apply and not in_use:
    req(f"/admin/realms/{realm}/clients/{found[0]['id']}", "DELETE", token=token)
    out["action"] = "deleted"
out["present_after"] = bool(req(f"/admin/realms/{realm}/clients?clientId={client_id}", token=token))
kept_after = req(f"/admin/realms/{keep}/clients?clientId={client_id}", token=token) or []
out["keep_present_after"] = bool(kept_after)
out["keep_enabled_after"] = bool(kept_after and kept_after[0].get("enabled"))
print("RESULT", json.dumps(out, ensure_ascii=False))
if found:
    print("BACKUP", json.dumps(rep, ensure_ascii=False))
'''


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--realm", default="pearnly-business")
    parser.add_argument("--client", default="testcowork.pearnly.com")
    parser.add_argument("--keep-realm", default="pearnly-cowork", help="同名客户端应当照常在的另一个池")
    parser.add_argument("--apply", action="store_true", help="确认没在用才删（默认只读确认 + 备份）")
    args = parser.parse_args(argv)
    print(f"{'确认没在用才删' if args.apply else '只读确认 + 备份'}：{args.realm} 池的客户端 {args.client}（{args.keep_realm} 池里的同名客户端不动）")
    program = base64.b64encode(HOST_PROGRAM.encode()).decode()
    remote = (f"f=$(mktemp /tmp/pearnly-kc-drop.XXXXXX.py) && echo {program} | base64 -d > \"$f\" && "
              f"python3 \"$f\"; rc=$?; rm -f \"$f\"; exit $rc")
    payload = json.dumps({"realm": args.realm, "client": args.client, "keep": args.keep_realm, "apply": args.apply})
    done = subprocess.run(["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", SSH, remote], input=payload,
                          capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        tail = [l for l in done.stderr.splitlines() if l.strip()][-3:]
        print("远端失败：" + " / ".join(tail), file=sys.stderr)
        return 1
    result = None
    for line in done.stdout.splitlines():
        tag, _, body = line.partition(" ")
        if tag == "RESULT":
            result = json.loads(body)
            print("  " + body)
        elif tag == "BACKUP":
            BACKUP.mkdir(parents=True, exist_ok=True)
            os.chmod(BACKUP, 0o700)
            stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            target = BACKUP / f"{args.realm}-{args.client}-{stamp}.json"
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(json.loads(body), ensure_ascii=False, indent=1) + "\n")
            print(f"  备份：{target}（600，含客户端密钥，不打印）")
    if result is None:
        print("远端没有给出结果", file=sys.stderr)
        return 1
    if result.get("in_use"):
        print("还在用，没删：" + "、".join(result["in_use"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
