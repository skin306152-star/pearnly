#!/usr/bin/env python3
"""智能体入口 fatcc.com 的登录配置（ADR-019）：两个账号池各一个公开客户端 pearnly-ai。只增改这一个客户端，
不动账号池设置、主题、其他客户端与账号。

- 公开客户端 + PKCE（S256），只开授权码登录；不开密码直传、隐式、服务账号。
- 回调只允许入口站点的固定地址（--origin 可给多个：正式 fatcc.com（2026-10-10 起；原计划 ai.pearnly.com），测试 testai.pearnly.com）。
- 登录页样式用 ai 专用主题 pearnly-ai（客户端属性 login_theme），产品站点的登录页不受影响。
可重复执行：已有就按同一份配置更新，最后读回核对。在服务器上以部署操作员身份运行；管理密码只读 runtime.env，不打印。

用法：configure-ai-entry.py --origin https://fatcc.com [--origin https://testai.pearnly.com] [--tenant-logout]

--tenant-logout：另给每个租户站点客户端的退出跳转追加智能体入口（从对话页退出回到 fatcc.com，ADR-019；
用户 2026-10-10 同意改现有配置）。只追加不删，读回核对。
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


SITE_CLIENT = __import__("re").compile(r"^[a-z0-9-]+\.pearnly\.com$")


def logout_targets(client_id: str) -> list[str]:
    """租户站点客户端要追加的退出跳转：正式入口；test 开头的测试租户再加测试入口。"""
    targets = ["https://fatcc.com/*"]
    if client_id.startswith("test"):
        targets.append("https://testai.pearnly.com/*")
    return targets


def allow_logout_back(api) -> int:
    """给每个租户站点客户端的 post.logout.redirect.uris 追加智能体入口（只追加，不删原有地址），读回核对。"""
    clients = f"/admin/realms/{api.realm}/clients"
    changed = 0
    for row in api.request(clients + "?max=1000") or []:
        client_id = row.get("clientId") or ""
        attributes = row.get("attributes") or {}
        if not SITE_CLIENT.match(client_id) or "backchannel.logout.url" not in attributes:
            continue  # 只动开户建的站点客户端（有后台登出地址的那些）
        existing = [u for u in (attributes.get("post.logout.redirect.uris") or "").split("##") if u]
        wanted = existing + [u for u in logout_targets(client_id) if u not in existing]
        if wanted == existing:
            continue
        api.request(clients + f"/{row['id']}", "PUT", {**row, "attributes": {**attributes,
                    "post.logout.redirect.uris": "##".join(wanted)}})
        back = api.request(clients + f"/{row['id']}")
        if (back.get("attributes") or {}).get("post.logout.redirect.uris") != "##".join(wanted):
            raise RuntimeError(f"{api.realm}: {client_id} logout redirect readback failed")
        changed += 1
    return changed


def main():
    origins = [sys.argv[i + 1].rstrip("/") for i, arg in enumerate(sys.argv) if arg == "--origin"]
    if not origins or not all(o in ("https://fatcc.com", "https://www.fatcc.com") or (o.startswith("https://") and o.endswith(".pearnly.com"))
                              for o in origins):
        raise SystemExit("Give at least one --origin https://fatcc.com or https://<name>.pearnly.com")
    member = import_module("configure-member-identity")
    env = dict(line.split("=", 1) for line in Path("/opt/pearnly/secrets/runtime.env").read_text().splitlines() if "=" in line)
    for realm in REALMS:
        api = member.Keycloak("http://127.0.0.1:8180", realm, "pearnly-bootstrap", env["KEYCLOAK_ADMIN_PASSWORD"])
        configure(api, origins)
        print(f"{realm}: {CLIENT_ID} configured and verified ({len(origins)} origin(s))")
        if "--tenant-logout" in sys.argv:
            print(f"{realm}: logout back to the ai entry allowed on {allow_logout_back(api)} more tenant client(s)")


if __name__ == "__main__":
    main()
