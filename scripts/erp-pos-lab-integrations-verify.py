"""Verify the ERP integration domain (LINE binding) on the dedicated laboratory.

Real Site path, synthetic data, **fake LINE transport**: the official-account lookup
and every reply are replaced in-process, so nothing leaves the host and no real LINE
channel or message is involved. Covers: the code is refused while the channel is not
configured, one live code per account, one-time consumption, bind/unbind from the
chat side, job-based access, and tenant isolation.
"""
import json
import os
from pathlib import Path
import sys

import frappe

CONTROL_APP = "/home/frappe/frappe-bench/apps/pearnly_control"
SITE = os.environ.get("PEARNLY_ERP_POS_LAB_SITE", "erp-pos-a.localhost")
OTHER_SITE = "erp-pos-b.localhost" if SITE != "erp-pos-b.localhost" else "erp-pos-a.localhost"
ALLOWED_SITES = {"erp-pos-a.localhost", "erp-pos-b.localhost"}
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")

OWNER = "erp-pos-owner@example.com"
OUTSIDER = "erp-pos-outsider@example.com"
LINE_A = "Ulablineaaaaaaaaaaaaaaaaaaaaaaaaa1"
LINE_B = "Ulablineaaaaaaaaaaaaaaaaaaaaaaaaa2"


def connect(site=SITE):
    if os.environ.get("PEARNLY_ERP_POS_LAB") != "1":
        raise SystemExit("Dedicated ERP/POS laboratory required")
    if site not in ALLOWED_SITES or not (SITE_ROOT / ".erp-pos-lab").is_file():
        raise SystemExit("Dedicated lab Site marker required")
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get("pearnly_erp_pos_lab") or frappe.conf.get("db_host") != "db":
        raise SystemExit("Refusing a non-lab Site")
    frappe.flags.mute_emails = True
    return site


def as_user(user, func, *args, **kwargs):
    frappe.set_user(user)
    try:
        return True, func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exception type is the evidence
        return False, f"{type(exc).__name__}: {exc}"[:300]


def drop_user(email):
    if frappe.db.exists("User", email):
        frappe.delete_doc("User", email, force=True, ignore_permissions=True)
        frappe.db.commit()


def ensure_member(email, roles):
    drop_user(email)
    frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                    "send_welcome_email": 0,
                    "roles": [{"role": "Desk User"}, *({"role": r} for r in roles)]
                    }).insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=email)


class FakeLine:
    """Fake LINE transport: the bot identity call and every outgoing message."""

    def __init__(self):
        self.replies = []
        self.profiles = {}

    def install(self, line_module):
        self._line = line_module
        self._reply = line_module.reply
        self._get_profile = line_module.get_profile
        self._requests_get = line_module.requests.get

        def reply(reply_token, messages):
            self.replies.append({"reply_token": reply_token, "messages": messages})
            return True

        def get_profile(user_id):
            return self.profiles.get(user_id, {"displayName": "LINE " + user_id[-4:]})

        def bot_info(url, headers=None, timeout=None):
            assert url == "https://api.line.me/v2/bot/info", url
            assert "Bearer lab-token" in (headers or {}).get("Authorization", ""), headers
            return _FakeResponse({"basicId": "@labbot", "displayName": "Lab ERP Bot"})

        line_module.reply = reply
        line_module.get_profile = get_profile
        line_module.requests.get = bot_info

    def restore(self):
        self._line.reply = self._reply
        self._line.get_profile = self._get_profile
        self._line.requests.get = self._requests_get

    def last_text(self):
        if not self.replies:
            return None
        message = (self.replies[-1]["messages"] or [{}])[0]
        return message.get("text")


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def send(line_module, line_user_id, text=None, *, message_type="text", event_type="message"):
    """Feed one LINE event through the real transport dispatch (which reads hooks)."""
    event = {"type": event_type, "source": {"userId": line_user_id},
             "replyToken": "lab-reply-" + line_user_id[-4:]}
    if event_type == "message":
        event["message"] = {"type": message_type, "text": text}
    return line_module._dispatch(event)


def main():
    site = connect()
    frappe.set_user("Administrator")
    from pearnly_core import line, line_binding
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.integrations import binding

    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    if not frappe.db.exists("User", OWNER):
        ensure_member(OWNER, [])
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)
    ensure_member(OUTSIDER, [])

    results = {}
    for name in frappe.get_all("Pearnly LINE Binding", filters={"user": OWNER}, pluck="name"):
        frappe.delete_doc("Pearnly LINE Binding", name, force=True, ignore_permissions=True)
    frappe.db.delete("Pearnly LINE Bind Code", {"user": OWNER})
    frappe.db.commit()

    # 渠道没配好 → 不发码。发得出码、扫不进号比没有更糟。
    saved = {key: frappe.conf.get(f"pearnly_line_{key}")
             for key in ("channel_id", "channel_secret", "channel_access_token")}
    for key in saved:
        frappe.conf[f"pearnly_line_{key}"] = ""
    ok, err = as_user(OWNER, binding.start)
    results["code_refused_without_a_channel"] = ok
    results["code_refused_error"] = None if ok else err
    ok, err = as_user(OWNER, binding.account)
    results["account_refused_without_a_channel"] = ok

    # 配上（进程内，不写站点配置）+ 假 LINE 通道 → 发码、机器人身份一起回来。
    frappe.conf["pearnly_line_channel_id"] = "lab-channel"
    frappe.conf["pearnly_line_channel_secret"] = "lab-secret"
    frappe.conf["pearnly_line_channel_access_token"] = "lab-token"
    fake = FakeLine()
    fake.install(line)
    try:
        ok, first = as_user(OWNER, binding.start)
        results["code_issued"] = ok
        results["code_issued_error"] = None if ok else first
        code = first.get("code") if ok else None
        results["code_shape"] = bool(code) and len(code) == 6 and code.isdigit()
        results["code_ttl_seconds"] = first.get("expires_in") if ok else None
        results["bot"] = {k: first.get(k) for k in ("bot_basic_id", "bot_friend_url", "bot_name")} \
            if ok else None

        # 再发一次：旧码立刻作废（一位员工同时只有一个有效码）。
        ok, second = as_user(OWNER, binding.start)
        results["second_code_issued"] = ok
        stale = as_user(OWNER, lambda: line_binding.consume_bind_code(code, line_user_id=LINE_A))
        results["stale_code_refused"] = stale[1] is None
        live_code = second.get("code") if ok else None

        # 通道侧：把码发给机器人就完成绑定。
        frappe.set_user("Administrator")
        results["dispatch_handled"] = send(line, LINE_A, live_code)
        row = frappe.db.get_value("Pearnly LINE Binding", {"line_user_id": LINE_A},
                                  ["user", "status", "display_name"], as_dict=True) or {}
        results["bound_user"] = row.get("user")
        results["bound_status"] = row.get("status")
        results["bind_reply"] = fake.last_text()

        # 同一个码不能再用一次（一次性消费）。
        send(line, LINE_B, live_code)
        results["code_reused"] = bool(frappe.db.exists(
            "Pearnly LINE Binding", {"line_user_id": LINE_B}))

        # 网页侧状态与解绑。
        ok, status = as_user(OWNER, binding.status)
        results["status_bound"] = bool(status.get("bound")) if ok else status
        ok, off = as_user(OWNER, binding.unbind)
        results["unbind"] = ok
        ok, status = as_user(OWNER, binding.status)
        results["status_after_unbind"] = bool(status.get("bound")) if ok else status

        # 解绑也可以从聊天里做（用户手打的词）。
        ok, third = as_user(OWNER, binding.start)
        send(line, LINE_A, third.get("code") if ok else "")
        send(line, LINE_A, "ยกเลิกการเชื่อมต่อ")
        results["chat_unbind_status"] = frappe.db.get_value(
            "Pearnly LINE Binding", {"line_user_id": LINE_A}, "status")
        results["chat_unbind_reply"] = fake.last_text()

        # 绑定之后发图片：录单还没迁，回一句"还没开放"，**不假装收下**。
        ok, fourth = as_user(OWNER, binding.start)
        send(line, LINE_A, fourth.get("code") if ok else "")
        send(line, LINE_A, message_type="image")
        results["unhandled_event_reply"] = fake.replies[-1]["messages"][0].get("text") \
            if fake.replies else None

        # 岗位：没岗位的账号既看不到状态也不能发码。
        ok, err = as_user(OUTSIDER, binding.status)
        results["outsider_can_read"] = ok
        ok, err = as_user(OUTSIDER, binding.start)
        results["outsider_can_bind"] = ok
        ok, err = as_user(OUTSIDER, binding.account)
        results["outsider_can_read_account"] = ok

        # 别的站点看不到这条绑定（每租户一个站点 = 天然隔离）。
        results["site_a_bindings"] = frappe.db.count("Pearnly LINE Binding")
    finally:
        fake.restore()
        for key, value in saved.items():
            frappe.conf[f"pearnly_line_{key}"] = value or ""

    frappe.set_user("Administrator")
    frappe.destroy()
    connect(OTHER_SITE)
    results["other_site_bindings"] = frappe.db.count("Pearnly LINE Binding")

    expected = {
        "code_refused_without_a_channel": False,
        "account_refused_without_a_channel": False,
        "dispatch_handled": True,
        "code_issued": True,
        "code_shape": True,
        "code_ttl_seconds": 600,
        "second_code_issued": True,
        "stale_code_refused": True,
        "bound_user": OWNER,
        "bound_status": "active",
        "code_reused": False,
        "status_bound": True,
        "unbind": True,
        "status_after_unbind": False,
        "chat_unbind_status": "unbound",
        "outsider_can_read": False,
        "outsider_can_bind": False,
        "outsider_can_read_account": False,
        "other_site_bindings": 0,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if results.get("bot", {}).get("bot_basic_id") != "@labbot":
        problems["bot_identity"] = results.get("bot")
    if not results.get("bot", {}).get("bot_friend_url", "").endswith("%40labbot"):
        problems["bot_friend_url"] = results.get("bot")
    if results.get("chat_unbind_reply") is None:
        problems["chat_unbind_reply"] = results.get("replies")
    if not results.get("unhandled_event_reply"):
        problems["unhandled_event_reply"] = results.get("unhandled_event_reply")
    refusal = str(results.get("code_refused_error") or "")
    if not refusal.startswith("ValidationError") or "LINE channel" not in refusal:
        problems["code_refused_message"] = results.get("code_refused_error")

    print(json.dumps({"site": site, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
