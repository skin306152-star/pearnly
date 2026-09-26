#!/usr/bin/env python3
"""本机样板：建 Cowork 自己的账号池 `pearnly-cowork`（幂等，可重复执行）。

用户 2026-09-26 定（方案 A）：Cowork 的账号与 ERP / DMS 分开，Cowork 站点签到自己的 realm。
生产由 `infrastructure/production/bootstrap-auth.py --realm pearnly-cowork` 建；本机样板的 realm 一直是手工建的
（见 sample-keycloak-local.sh 开头），这里把 Cowork 这一个补成脚本：

1. realm 不存在就建，登录相关设置照抄 `pearnly-business`（显示名、邮箱登录、主题、语言、忘记密码……）；
   用户资料里名 / 姓不必填（开户只收用户名与邮箱，同业务 realm）；
2. 建两个 realm 级客户端，与业务 realm 同名同用途：
   `pearnly-admin`（服务账号，建站 / 改密用管理员令牌）、`pearnly-password-check`（只开密码授权，校验当前密码）；
3. 两个客户端密钥写到 `~/.config/pearnly/cowork-realm.env`（0600），不打印。

之后跑 `scripts/sample-keycloak-local.sh`（本机 http、忘记密码、密码策略留空、给 pearnly-admin 授管理角色），
再用 `scripts/sample-ensure-target.sh`（带 PEARNLY_COWORK_* 两个密钥）把这个池配到本地开户目标上。

用法: python3 scripts/sample-cowork-realm.py
"""
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

KC = "http://keycloak.localhost:8105"
SOURCE, REALM = "pearnly-business", "pearnly-cowork"
COPY = ("displayName", "loginWithEmailAllowed", "duplicateEmailsAllowed", "editUsernameAllowed", "rememberMe",
        "resetPasswordAllowed", "registrationAllowed", "loginTheme", "emailTheme", "internationalizationEnabled",
        "supportedLocales", "defaultLocale", "bruteForceProtected", "permanentLockout", "failureFactor",
        "waitIncrementSeconds", "maxFailureWaitSeconds", "passwordPolicy", "sslRequired")
SECRETS = Path.home() / ".config/pearnly/cowork-realm.env"


def request(path, method="GET", data=None, token=None, form=False):
    headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = (urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
    try:
        with urlopen(Request(KC + path, data=body, headers=headers, method=method), timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        raise SystemExit(f"Keycloak {method} {path} failed: HTTP {exc.code}") from None


def client(token, client_id, settings):
    path = f"/admin/realms/{REALM}/clients"
    found = request(path + "?" + urlencode({"clientId": client_id}), token=token)
    if not found:
        request(path, "POST", {"clientId": client_id, "enabled": True, "publicClient": False, **settings}, token)
        found = request(path + "?" + urlencode({"clientId": client_id}), token=token)
    return request(path + f"/{found[0]['id']}/client-secret", token=token)["value"]


def main():
    token = request("/realms/master/protocol/openid-connect/token", "POST", {
        "grant_type": "password", "client_id": "admin-cli",
        "username": "kc-admin-local", "password": "kc-admin-local-only"}, form=True)["access_token"]
    source = request(f"/admin/realms/{SOURCE}", token=token)
    settings = {"realm": REALM, "enabled": True, **{key: source[key] for key in COPY if key in source}}
    if any(row["realm"] == REALM for row in request("/admin/realms", token=token)):
        print(f"[{REALM}] 已存在，按业务 realm 更新登录设置")
        request(f"/admin/realms/{REALM}", "PUT", settings, token)
    else:
        print(f"[{REALM}] 新建")
        request("/admin/realms", "POST", settings, token)
    profile_path = f"/admin/realms/{REALM}/users/profile"
    profile = request(profile_path, token=token)
    for attribute in profile.get("attributes", []):
        if attribute.get("name") in ("firstName", "lastName"):
            attribute.pop("required", None)
    request(profile_path, "PUT", profile, token)
    admin = client(token, "pearnly-admin", {"serviceAccountsEnabled": True, "standardFlowEnabled": False,
                                            "directAccessGrantsEnabled": False})
    check = client(token, "pearnly-password-check", {"serviceAccountsEnabled": False, "standardFlowEnabled": False,
                                                     "directAccessGrantsEnabled": True})
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(SECRETS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(f"PEARNLY_COWORK_ADMIN_SECRET={admin}\nPEARNLY_COWORK_PASSWORD_CHECK_SECRET={check}\n")
    readback = request(f"/admin/realms/{REALM}", token=token)
    assert all(readback.get(key) == settings[key] for key in settings if key != "realm")
    print(f"[{REALM}] 登录设置回读一致；pearnly-admin / pearnly-password-check 就绪，密钥写入 {SECRETS}（未打印）")


if __name__ == "__main__":
    main()
