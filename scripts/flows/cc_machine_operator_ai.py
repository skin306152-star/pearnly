"""流程用例：CC 两种电脑通道（ERP 的共用机器通道 `cc_desktop.machine.operator_ai`、Cowork 的 `companion.operator_api.operator_ai`）的代录 AI 转发（`express_operator.ai`，2026-11-03），不花钱。

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
HAIKU = "claude-haiku-5-5"
# 两种通道原有的机器方法（不含新加的 operator_ai）：路径一字不变
CORE = "pearnly_core.cc_desktop.machine."
COWORK = "pearnly_cowork.companion."
OLD = {
    "core": {"heartbeat": CORE + "heartbeat", "lease": CORE + "lease", "ack": CORE + "acknowledge",
             "request_next": CORE + "next_request", "request_complete": CORE + "complete_request", "leave": CORE + "leave",
             "mrerp_login": CORE + "mrerp_login"},
    "cowork": {"heartbeat": COWORK + "heartbeat.report", "lease": COWORK + "queue_api.lease",
               "ack": COWORK + "queue_api.acknowledge", "request_next": COWORK + "request_api.next_request",
               "request_complete": COWORK + "request_api.complete_request", "device_hello": COWORK + "device_api.hello",
               "device_leave": COWORK + "device_api.leave", "mrerp_login": COWORK + "credential_api.mrerp_login"},
}
NEW = {"core": CORE + "operator_ai", "cowork": COWORK + "operator_api.operator_ai"}


def prepare(lab, manifest):
    tag = uuid.uuid4().hex  # 电脑键 32–128 位（`cc_desktop.contracts.device_key`）
    if "cowork" in manifest["site"]:
        # Cowork 通道（``exp_`` 密钥）：新版 CC 桌面版带电脑键登记；「电脑」列表移除那台 = 吊销它的密钥
        return lab.python(manifest["site"], f"""
import os
from pathlib import Path
from pearnly_cowork.companion import credentials, http_auth

# 电脑通道总闸（站点配置）实验站点默认关着：临时打开，跑完由 verify 还原原值（同 cowork-companion-credentials-lab-verify）
config_path = Path(frappe.get_site_path("site_config.json"))
config = json.loads(config_path.read_text())
was = config.get("pearnly_cowork_erp_push_enabled")
config["pearnly_cowork_erp_push_enabled"] = True
config_path.write_text(json.dumps(config, indent=1))
frappe.conf.pearnly_cowork_erp_push_enabled = True
frappe.set_user("admin@cowork.regression")
live = credentials.register("flow-ai-live-{tag}", express=True, device_key="live{tag}", device_name="flow live")
gone = credentials.register("flow-ai-gone-{tag}", express=True, device_key="gone{tag}", device_name="flow gone")
credentials.remove_device(gone["device"])
frappe.db.commit()
print(json.dumps({{"site": {manifest["site"]!r}, "channel": "cowork", "switch_was": was, "live": live["token"], "gone": gone["token"],
                  "key_configured": bool(os.environ.get("PEARNLY_ANTHROPIC_API_KEY", "").strip()),
                  "paths": http_auth.MACHINE_PATHS}}))
""")
    return lab.python(manifest["site"], f"""
import os
from pearnly_core.cc_desktop import devices, http_auth

frappe.set_user("admin@erp.regression")
live = devices.register(device_key="flow-ai-live-{tag}", device_name="flow live")
gone = devices.register(device_key="flow-ai-gone-{tag}", device_name="flow gone")
devices.leave(devices.authenticate(gone["token"]))  # 电脑自己断开：这把密钥作废
frappe.db.commit()
print(json.dumps({{"site": {manifest["site"]!r}, "channel": "core", "live": live["token"], "gone": gone["token"],
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
    try:
        return _verify(params)
    finally:
        if params.get("channel") == "cowork":  # 还原电脑通道总闸
            lab.python(manifest["site"], f"""
from pathlib import Path
config_path = Path(frappe.get_site_path("site_config.json"))
config = json.loads(config_path.read_text())
was = {params.get("switch_was")!r}
if was is None:
    config.pop("pearnly_cowork_erp_push_enabled", None)
else:
    config["pearnly_cowork_erp_push_enabled"] = was
config_path.write_text(json.dumps(config, indent=1))
print(json.dumps({{"restored": True}}))
""")


def _verify(params):
    site, live, gone = params["site"], params["live"], params["gone"]
    if params.get("key_configured"):  # 配了真密钥就不调（不花钱）
        return [{"case": "the lab site has no Claude key", "seen": True, "ok": False}]
    channel = params["channel"]
    path = "/api/method/" + NEW[channel]
    good = {"batch": "flow-batch", "kind": "check", "request": {"model": HAIKU, "max_tokens": 100, "messages": []}}
    status, reply = _post(site, path, live, good)
    bad = {"model": {**good, "request": {**good["request"], "model": "claude-other"}},
           "kind": {**good, "kind": "other"},
           "field": {**good, "request": {**good["request"], "metadata": {"x": 1}}}}
    refused = {name: _post(site, path, live, body) for name, body in bad.items()}
    forged = _post(site, path, live[:-4] + "zzzz", good)[0]
    revoked = _post(site, path, gone, good)[0]
    old = {op: _post(site, params["paths"][op], live[:-4] + "zzzz", {})[0] for op in OLD[channel]}
    return [
        {"case": f"{channel}: the new method is where CC Desktop calls it", "seen": params["paths"].get("operator_ai"),
         "ok": params["paths"].get("operator_ai") == path},
        {"case": "a valid machine key reaches the forwarding, which does not call without a key", "seen": [status, reply],
         "ok": status == 200 and (reply or {}).get("ok") is True
         and ((reply or {}).get("response") or {}).get("error") == "not_configured"},
        {"case": "a model, call or field outside the whitelist is refused", "seen": refused,
         "ok": all(code == 200 and ((answer or {}).get("response") or {}).get("error", "").startswith("invalid_")
                   for code, answer in refused.values())},
        {"case": "a forged or revoked machine key is refused", "seen": [forged, revoked],
         "ok": forged in (401, 403) and revoked in (401, 403)},
        {"case": "the old machine methods keep their paths and still refuse a forged key", "seen": old,
         "ok": all(params["paths"][op] == "/api/method/" + method for op, method in OLD[channel].items())
         and all(code in (400, 401, 403) for code in old.values())},  # Cowork 回执先核请求体（空的回 400），都进不去
    ]
