#!/usr/bin/env python3
"""只读：两个账号池（业务池 pearnly-business、Cowork 池 pearnly-cowork）的现状，换账号池前后核对用。

在生产主机上以部署操作员身份运行（管理密码只读 runtime.env，不打印）；只发 GET，不改任何设置：

    python3 scripts/production/inspect-identity-pools.py --site testcowork.pearnly.com \\
        --email pearnlyai@gmail.com --email 123@123.com

每个池输出：池级登录设置（邮箱登录、忘记密码、发信是否配好、登录主题）、站点客户端在不在、外部登录入口
（只列别名）、LINE 登录的池级设置能否通过 `verify_realm`、给的邮箱在这个池里有没有号（只答有 / 没有，不输出身份编号）。
"""
import argparse
import json
import os
from pathlib import Path
import runpy
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
REALMS = ("pearnly-business", "pearnly-cowork")


def inspect(api, sites, emails, verify_realm):
    prefix = f"/admin/realms/{api.realm}"
    if not api.exists(prefix):
        return {"realm": api.realm, "exists": False}
    realm = api.request(prefix) or {}
    clients = {c.get("clientId") for c in api.request(prefix + "/clients?max=1000") or []}
    report = {
        "realm": api.realm,
        "exists": True,
        "enabled": realm.get("enabled"),
        "login_with_email": realm.get("loginWithEmailAllowed"),
        "reset_password_allowed": realm.get("resetPasswordAllowed"),
        "registration_allowed": realm.get("registrationAllowed"),
        "duplicate_emails_allowed": realm.get("duplicateEmailsAllowed"),
        "smtp_configured": bool((realm.get("smtpServer") or {}).get("host")),
        "login_theme": realm.get("loginTheme"),
        "default_locale": realm.get("defaultLocale"),
        "platform_clients": sorted(c for c in clients if c in (
            "pearnly-control", "pearnly-admin", "pearnly-password-check", "pearnly-ai", "pearnly-member-identity")),
        "site_clients": {site: site in clients for site in sites},
        "tenant_client_count": sum(1 for c in clients if str(c).endswith(".pearnly.com")),
        "identity_providers": sorted(i["alias"] for i in api.request(prefix + "/identity-provider/instances") or []),
        "users_by_email": {},
    }
    try:
        verify_realm(api)
        report["line_realm_settings"] = "ok"
    except RuntimeError as error:
        report["line_realm_settings"] = str(error)
    for email in emails:
        rows = api.request(prefix + f"/users?email={quote(email)}&exact=true&briefRepresentation=true") or []
        report["users_by_email"][email] = len(rows)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", action="append", default=[], help="tenant site (= its OIDC client id)")
    parser.add_argument("--email", action="append", default=[], help="email to look up in each pool")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run on the production host as the deployment operator")
    env = dict(line.split("=", 1) for line in
               Path("/opt/pearnly/secrets/runtime.env").read_text().splitlines() if "=" in line)
    line_login = runpy.run_path(str(HERE / "configure-line-login.py"))
    for realm in REALMS:
        api = line_login["Keycloak"]("http://127.0.0.1:8180", realm, "pearnly-bootstrap", env["KEYCLOAK_ADMIN_PASSWORD"])
        print(json.dumps(inspect(api, args.site, args.email, line_login["verify_realm"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
