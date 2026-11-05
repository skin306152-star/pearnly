#!/usr/bin/env python3
"""登录后 e2e 与站外设置对比要用的两样东西，一次性在生产上建好（幂等，可重跑；方案 docs/work/2026-11-05-product-map-next.md §12 第 4 问，
用户 11-05 同意）：

1. 测试租户上的 e2e 账号：testerp 的 `e2eerp`、testcowork 的 `e2ecowork`（产品规定用户名只能是字母和数字）。
   走产品自己的「添加成员」（`team.service.add_member`：先在统一身份里建号、再建站点用户并绑定），不手工改库；
   已有就用 `set_member_password` 把密码对齐到本机文件（本机文件是唯一一份）。权限只给 14 个场景要用的：
   - ERP：功能角色 ERP Purchase（添加成员至少要一个岗位）+ ERP Admin（「添加公司」只有产品管理员能用，`rules.COMPANY_MANAGE_ROLES`；
     添加成员不外发管理员角色，所以单独加，只在测试租户）；
   - Cowork：Cowork Intake（添加公司、代录都按它判）。
   不绑 LINE（脚本核对没有有效 LINE 绑定，有就报出来，不动它）；不碰正式租户、不碰别人的账号。
2. Keycloak 两个账号池的只读客户端 `pearnly-config-reader`：机密客户端、只开服务账号（client_credentials），只给 realm-management 的
   view-realm / view-clients / view-identity-providers。密钥写生产机 /opt/pearnly/secrets/config-reader.env（600，不动 runtime.env）。

    python3 scripts/e2e/provision.py            # 只读：列出要做的每一项与现在的状态（一次 SSH），什么都不写
    python3 scripts/e2e/provision.py --apply    # 照做并回读核对（一次 SSH）

e2e 账号密码只在本机 ~/.config/pearnly-e2e/accounts.env（600）；随机生成，经 SSH 标准输入传过去，不进命令行、不打印。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as e2e  # noqa: E402  凭据文件的位置与键名与执行器同一份

SSH = os.environ.get("PEARNLY_PRODUCTION_SSH", "pearnly")
ACCOUNTS = [
    {"key": "ERP", "product": "erp", "site": "testerp.pearnly.com", "username": "e2eerp", "first_name": "E2E", "last_name": "ERP",
     "roles": ["ERP Purchase"], "extra_roles": ["ERP Admin"], "service": "pearnly_erp_portal.team.service"},
    {"key": "COWORK", "product": "cowork", "site": "testcowork.pearnly.com", "username": "e2ecowork", "first_name": "E2E",
     "last_name": "Cowork", "roles": ["Cowork Intake"], "extra_roles": [], "service": "pearnly_cowork.team.service"},
]
REALMS = ("pearnly-business", "pearnly-cowork")

# 在产品 web 容器里跑（站点目录下）：标准输入是一个账号的 JSON（含密码），只输出状态
SITE_PROGRAM = r'''
import json, sys, importlib
spec = json.loads(sys.stdin.read())
import frappe
frappe.init(site=spec["site"], sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
out = {"site": spec["site"], "username": spec["username"]}
try:
    service = importlib.import_module(spec["service"])
    email = frappe.db.get_value("User", {"username": spec["username"]}, "name")
    out["existed"] = bool(email)
    if spec["apply"]:
        if not email:
            email = service.add_member(username=spec["username"], roles=spec["roles"], password=spec["password"],
                                       first_name=spec["first_name"], last_name=spec["last_name"])["email"]
            out["action"] = "created"
        else:
            service.set_member_password(email, spec["password"])
            out["action"] = "password aligned"
        user = frappe.get_doc("User", email)
        missing = [r for r in spec["roles"] + spec["extra_roles"] if r not in {x.role for x in user.roles}]
        if missing:
            user.add_roles(*missing)
            out["roles_added"] = missing
        frappe.db.commit()
    if email:
        user = frappe.get_doc("User", email)
        out["email"] = email
        out["enabled"] = bool(user.enabled)
        out["roles"] = sorted(r.role for r in user.roles)
        out["identity_bound"] = bool(service._bound_subject(email))
        out["line_bindings"] = frappe.db.count("Pearnly LINE Binding", {"user": email, "status": "active"})
finally:
    frappe.destroy()
print(json.dumps(out, ensure_ascii=False))
'''

# 在生产机上跑：标准输入是 {"apply": bool, "accounts": [...]}；Keycloak 只听本机
HOST_PROGRAM = r'''
import json, os, subprocess, sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

payload = json.loads(sys.stdin.read())
APPLY = payload["apply"]
SITE_PROGRAM = payload["site_program"]
for account in payload["accounts"]:
    web = f"pearnly-production-{account['product']}-web-1"
    bench = "/home/frappe/frappe-bench"
    path = "/tmp/pearnly-e2e-provision.py"
    subprocess.run(["docker", "exec", "-i", web, "sh", "-c", f"cat > {path}"], input=SITE_PROGRAM, text=True, check=True)
    try:
        done = subprocess.run(["docker", "exec", "-i", "-w", bench + "/sites", web, bench + "/env/bin/python", path],
                              input=json.dumps({**account, "apply": APPLY}), capture_output=True, text=True, timeout=180)
    finally:
        subprocess.run(["docker", "exec", web, "rm", "-f", path], check=False)
    line = (done.stdout.strip().splitlines() or [""])[-1]
    if done.returncode != 0 or not line.startswith("{"):
        err = (done.stderr or done.stdout).strip().splitlines()
        print("ACCOUNT", json.dumps({"site": account["site"], "error": (err[-1] if err else f"exit {done.returncode}")[:300]}))
    else:
        print("ACCOUNT", line)

ENV = dict(l.split("=", 1) for l in Path("/opt/pearnly/secrets/runtime.env").read_text().splitlines() if "=" in l)
BASE = "http://127.0.0.1:8180"
SECRETS = Path("/opt/pearnly/secrets/config-reader.env")
CLIENT = "pearnly-config-reader"
ROLES = ("view-realm", "view-clients", "view-identity-providers")


def req(path, method="GET", data=None, token=None, form=False):
    headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = (urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
    with urlopen(Request(BASE + path, data=body, headers=headers, method=method), timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


admin = req("/realms/master/protocol/openid-connect/token", "POST", {"grant_type": "password", "client_id": "admin-cli",
            "username": "pearnly-bootstrap", "password": ENV["KEYCLOAK_ADMIN_PASSWORD"]}, form=True)["access_token"]
stored = {}
if SECRETS.exists():
    stored = dict(l.split("=", 1) for l in SECRETS.read_text().splitlines() if "=" in l)
for realm in payload["realms"]:
    key = "PEARNLY_CONFIG_READER_SECRET_" + realm.upper().replace("-", "_")
    clients = f"/admin/realms/{realm}/clients"
    found = req(clients + f"?clientId={CLIENT}", token=admin) or []
    out = {"realm": realm, "existed": bool(found)}
    if APPLY:
        if not found:
            req(clients, "POST", {"clientId": CLIENT, "name": "Pearnly config reader (read only)", "enabled": True,
                                  "protocol": "openid-connect", "publicClient": False, "serviceAccountsEnabled": True,
                                  "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
                                  "implicitFlowEnabled": False, "fullScopeAllowed": True}, admin)
            found = req(clients + f"?clientId={CLIENT}", token=admin)
            out["action"] = "created"
        cid = found[0]["id"]
        account = req(f"{clients}/{cid}/service-account-user", token=admin)
        management = req(clients + "?clientId=realm-management", token=admin)[0]["id"]
        mapped = f"/admin/realms/{realm}/users/{account['id']}/role-mappings/clients/{management}"
        have = {r["name"] for r in req(mapped, token=admin) or []}
        want = [r for r in req(f"{clients}/{management}/roles", token=admin) if r["name"] in ROLES and r["name"] not in have]
        if want:
            req(mapped, "POST", [{"id": r["id"], "name": r["name"]} for r in want], admin)
            out["roles_added"] = [r["name"] for r in want]
        secret = req(f"{clients}/{cid}/client-secret", token=admin)["value"]
        if stored.get(key) != secret:
            stored[key] = secret
            SECRETS.write_text("".join(f"{k}={v}\n" for k, v in sorted(stored.items())))
            os.chmod(SECRETS, 0o600)
            out["secret_file"] = "written"
    if found:
        cid = found[0]["id"]
        rep = req(f"{clients}/{cid}", token=admin)
        account = req(f"{clients}/{cid}/service-account-user", token=admin)
        management = req(clients + "?clientId=realm-management", token=admin)[0]["id"]
        mapped = req(f"/admin/realms/{realm}/users/{account['id']}/role-mappings/clients/{management}", token=admin) or []
        out.update(public=rep.get("publicClient"), service_account=rep.get("serviceAccountsEnabled"),
                   standard_flow=rep.get("standardFlowEnabled"), direct_grants=rep.get("directAccessGrantsEnabled"),
                   roles=sorted(r["name"] for r in mapped),
                   realm_roles=sorted(r["name"] for r in req(f"/admin/realms/{realm}/users/{account['id']}/role-mappings/realm",
                                                             token=admin) or []))
        if stored.get(key):
            # 回读：用这个客户端自己的令牌能读、不能写
            token = req(f"/realms/{realm}/protocol/openid-connect/token", "POST", {"grant_type": "client_credentials",
                        "client_id": CLIENT, "client_secret": stored[key]}, form=True)["access_token"]
            out["can_read_realm"] = bool(req(f"/admin/realms/{realm}", token=token))
            out["can_read_clients"] = isinstance(req(clients + "?max=1", token=token), list)
            try:
                req(f"/admin/realms/{realm}", "PUT", {"realm": realm}, token)
                out["write_refused"] = False
            except HTTPError as exc:
                out["write_refused"] = exc.code == 403
    print("REALM", json.dumps(out))
print("SECRET_FILE", json.dumps({"exists": SECRETS.exists(), "mode": oct(SECRETS.stat().st_mode & 0o777) if SECRETS.exists() else None,
                                 "keys": sorted(stored)}))
'''


def local_accounts(path: Path, apply: bool) -> dict:
    """本机凭据：有就沿用；--apply 时缺的密码随机生成并先写进 600 文件（远端成功与否都以它为准，重跑会对齐）。"""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    changed = False
    for account in ACCOUNTS:
        if values.get(f"E2E_{account['key']}_USER") != account["username"]:
            values[f"E2E_{account['key']}_USER"] = account["username"]
            changed = True
        if not values.get(f"E2E_{account['key']}_PASSWORD"):
            values[f"E2E_{account['key']}_PASSWORD"] = secrets.token_urlsafe(24)   # 32 个字符
            changed = True
    if apply and changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("# 登录后 e2e 账号（scripts/e2e/provision.py 生成；只在本机，不进仓库、不打印）\n")
            handle.writelines(f"{key}={values[key]}\n" for key in sorted(values))
    os.chmod(path, 0o600) if path.exists() else None
    return values


def plan_lines(apply: bool) -> list[str]:
    verb = "照做" if apply else "只读核对（不写）"
    lines = [f"{verb}，一次 SSH 到 {SSH}："]
    for account in ACCOUNTS:
        lines.append(f"  - {account['site']}：账号 {account['username']}（名 {account['first_name']} 姓 {account['last_name']}），"
                     f"没有就用 {account['service']}.add_member 建（统一身份 + 站点用户），有就把密码对齐本机文件；"
                     f"角色 {'、'.join(account['roles'] + account['extra_roles'])}；核对没有 LINE 绑定")
    for realm in REALMS:
        lines.append(f"  - Keycloak {realm}：只读客户端 pearnly-config-reader（只开服务账号；realm-management 的 view-realm / "
                     "view-clients / view-identity-providers），密钥写 /opt/pearnly/secrets/config-reader.env（600）；"
                     "回读：能读 realm 与客户端、写被拒")
    lines.append(f"  - 本机：e2e 密码在 {e2e.CREDENTIALS}（600），缺的随机生成")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="照做（默认只读核对）")
    args = parser.parse_args(argv)
    print("\n".join(plan_lines(args.apply)))
    values = local_accounts(Path(e2e.CREDENTIALS), args.apply)
    accounts = [{**{k: a[k] for k in ("product", "site", "username", "first_name", "last_name", "roles", "extra_roles", "service")},
                 "password": values[f"E2E_{a['key']}_PASSWORD"] if args.apply else ""} for a in ACCOUNTS]
    payload = json.dumps({"apply": args.apply, "accounts": accounts, "realms": list(REALMS), "site_program": SITE_PROGRAM})
    program = base64.b64encode(HOST_PROGRAM.encode()).decode()
    remote = (f"f=$(mktemp /tmp/pearnly-e2e-host.XXXXXX.py) && echo {program} | base64 -d > \"$f\" && "
              f"python3 \"$f\"; rc=$?; rm -f \"$f\"; exit $rc")
    done = subprocess.run(["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", SSH, remote], input=payload,
                          capture_output=True, text=True, timeout=600)
    for line in done.stdout.splitlines():
        print("  " + line)
    if done.returncode != 0:
        tail = [l for l in done.stderr.splitlines() if l.strip()][-3:]
        print("远端失败：" + " / ".join(tail), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
