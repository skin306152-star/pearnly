"""流程用例：共用机器通道的代录 AI 转发（`cc_desktop.machine.operator_ai` → `express_operator.ai`，2026-11-03），不花钱。

实验站点没配 Claude 密钥（准备时核实，配了就不调、直接判失败，免得真付费）。在站点里登记两台电脑：一把有效的机器密钥，
一把登记后吊销的。然后从本机按 CC 桌面版的样子（Bearer 机器密钥、JSON 请求体）直接打站点的机器方法：

- 有效密钥、合规请求 → 回 ``{ok, response: {error: not_configured}}``（走到了转发，没配密钥不调）；
- 白名单之外的模型、调用、字段 → 整件拒收（``invalid_*``）；
- 伪造的、已吊销的机器密钥 → 被拒（401 / 403），到不了转发；
- 原有的机器方法（心跳、领取、回执、按需请求、断开、MR.ERP 授权）路径不变，假密钥照旧被拒（不是 404）。

这也是共用机器通道的第一条回归流程（主会话 11-03）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

TITLE = "CC 机器通道的代录 AI 转发：没配密钥回 not_configured、白名单外拒收、假 / 吊销密钥被拒、旧方法不变"
METHOD = "/api/method/pearnly_core.cc_desktop.machine."
OLD = {"heartbeat": "heartbeat", "lease": "lease", "ack": "acknowledge", "request_next": "next_request",
       "request_complete": "complete_request", "leave": "leave", "mrerp_login": "mrerp_login"}
HAIKU = "claude-haiku-5-5"


def prepare(lab, manifest):
    tag = uuid.uuid4().hex  # 电脑键 32–128 位（`cc_desktop.contracts.device_key`）
    return lab.python(manifest["site"], f"""
import os
from pearnly_core.cc_desktop import devices, http_auth

frappe.set_user("admin@erp.regression")
live = devices.register(device_key="flow-ai-live-{tag}", device_name="flow live {tag}")
gone = devices.register(device_key="flow-ai-gone-{tag}", device_name="flow gone {tag}")
devices.leave(devices.authenticate(gone["token"]))  # 电脑自己断开：这把密钥作废
frappe.db.commit()
print(json.dumps({{"site": {manifest["site"]!r}, "live": live["token"], "gone": gone["token"],
                  "key_configured": bool(os.environ.get("PEARNLY_ANTHROPIC_API_KEY", "").strip()),
                  "paths": http_auth.PATHS}}))
""")


def steps(params, camera_code=None):
    return []  # 机器方法没有页面：全在 verify 里按 CC 桌面版的样子直接请求


def judge(params, results, camera_code=None):
    return []


def _post(site, path, token, body):
    from run import PORT

    request = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Host": site, "Content-Type": "application/json",
                                              "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read() or b"{}")
            return response.status, payload.get("message", payload)
    except urllib.error.HTTPError as error:
        return error.code, None


def verify(lab, manifest, params, camera_code=None):
    site, live, gone = params["site"], params["live"], params["gone"]
    if params.get("key_configured"):  # 配了真密钥就不调（不花钱）
        return [{"case": "the lab site has no Claude key", "seen": True, "ok": False}]
    path = METHOD + "operator_ai"
    good = {"batch": "flow-batch", "kind": "check", "request": {"model": HAIKU, "max_tokens": 100, "messages": []}}
    status, reply = _post(site, path, live, good)
    bad = {"model": {**good, "request": {**good["request"], "model": "claude-other"}},
           "kind": {**good, "kind": "other"},
           "field": {**good, "request": {**good["request"], "metadata": {"x": 1}}}}
    refused = {name: _post(site, path, live, body) for name, body in bad.items()}
    forged = _post(site, path, live[:-4] + "zzzz", good)[0]
    revoked = _post(site, path, gone, good)[0]
    old = {op: _post(site, params["paths"][op], live[:-4] + "zzzz", {})[0] for op in OLD}
    return [
        {"case": "a valid machine key reaches the forwarding, which does not call without a key", "seen": [status, reply],
         "ok": status == 200 and (reply or {}).get("ok") is True
         and ((reply or {}).get("response") or {}).get("error") == "not_configured"},
        {"case": "a model, call or field outside the whitelist is refused", "seen": refused,
         "ok": all(code == 200 and ((answer or {}).get("response") or {}).get("error", "").startswith("invalid_")
                   for code, answer in refused.values())},
        {"case": "a forged or revoked machine key is refused", "seen": [forged, revoked],
         "ok": forged in (401, 403) and revoked in (401, 403)},
        {"case": "the old machine methods keep their paths and still refuse a forged key", "seen": old,
         "ok": all(params["paths"][op] == METHOD + method for op, method in OLD.items())
         and all(code in (401, 403) for code in old.values())},
    ]
