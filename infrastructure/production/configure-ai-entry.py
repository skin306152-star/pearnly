#!/usr/bin/env python3
"""智能体入口 ai.pearnly.com 的登录配置（ADR-019）：两个账号池各一个公开客户端 pearnly-ai。只增改这一个客户端，
不动账号池设置、主题、其他客户端与账号。

- 公开客户端 + PKCE（S256），只开授权码登录；不开密码直传、隐式、服务账号。
- 回调只允许入口站点的固定地址（--origin 可给多个：正式 ai.pearnly.com，测试 testai.pearnly.com）。
- 登录页样式用 ai 专用主题 pearnly-ai（客户端属性 login_theme），产品站点的登录页不受影响。
可重复执行：已有就按同一份配置更新，最后读回核对。在服务器上以部署操作员身份运行；管理密码只读 runtime.env，不打印。

用法：configure-ai-entry.py --origin https://ai.pearnly.com [--origin https://testai.pearnly.com]
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module  # noqa: E402

REALMS = ("pearnly-business", "pearnly-cowork")
CLIENT_ID = "pearnly-ai"
CALLBACK = "/api/method/pearnly_control.assistant_entry.api.callback"


def representation(origins) -> dict:
    return {
        "clientId": CLIENT_ID, "name": "Pearnly AI", "enabled": True, "protocol": "openid-connect",
        "publicClient": True, "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
        "implicitFlowEnabled": False, "serviceAccountsEnabled": False, "frontchannelLogout": False,
        "redirectUris": [o + CALLBACK for o in origins], "webOrigins": list(origins),
        "baseUrl": origins[0] + "/", "fullScopeAllowed": False,
        "attributes": {"pkce.code.challenge.method": "S256", "login_theme": "pearnly-ai",
                       "post.logout.redirect.uris": "##".join(o + "/" for o in origins)},
    }


def configure(api, origins) -> None:
    clients = f"/admin/realms/{api.realm}/clients"
    wanted = representation(origins)
    found = api.request(clients + f"?clientId={CLIENT_ID}") or []
    if found:
        api.request(clients + f"/{found[0]['id']}", "PUT", {**found[0], **wanted,
                    "attributes": {**(found[0].get("attributes") or {}), **wanted["attributes"]}})
    else:
        api.request(clients, "POST", wanted)
    actual = (api.request(clients + f"?clientId={CLIENT_ID}") or [{}])[0]
    checks = {
        "publicClient": actual.get("publicClient") is True,
        "standardFlowEnabled": actual.get("standardFlowEnabled") is True,
        "directAccessGrantsEnabled": actual.get("directAccessGrantsEnabled") is False,
        "implicitFlowEnabled": actual.get("implicitFlowEnabled") is False,
        "serviceAccountsEnabled": actual.get("serviceAccountsEnabled") is False,
        "redirectUris": sorted(actual.get("redirectUris") or []) == sorted(wanted["redirectUris"]),
        "pkce": (actual.get("attributes") or {}).get("pkce.code.challenge.method") == "S256",
        "login_theme": (actual.get("attributes") or {}).get("login_theme") == "pearnly-ai",
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError(f"{api.realm}: {CLIENT_ID} readback failed: {', '.join(failed)}")


def main():
    origins = [sys.argv[i + 1].rstrip("/") for i, arg in enumerate(sys.argv) if arg == "--origin"]
    if not origins or not all(o.startswith("https://") and o.endswith(".pearnly.com") for o in origins):
        raise SystemExit("Give at least one --origin https://<name>.pearnly.com")
    member = import_module("configure-member-identity")
    env = dict(line.split("=", 1) for line in Path("/opt/pearnly/secrets/runtime.env").read_text().splitlines() if "=" in line)
    for realm in REALMS:
        configure(member.Keycloak("http://127.0.0.1:8180", realm, "pearnly-bootstrap", env["KEYCLOAK_ADMIN_PASSWORD"]), origins)
        print(f"{realm}: {CLIENT_ID} configured and verified ({len(origins)} origin(s))")


if __name__ == "__main__":
    main()
