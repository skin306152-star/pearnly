"""MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7 条，评估 §14.4，2026-11-02）：共用层与两条机器通道的离线检查。

- 一次性票（`pearnly_core.cc_desktop.login_tickets`）：格式、只存摘要、绑住站点 / 通道 / 连接 / 人 / 电脑、用过即作废；
  报文只收 {ticket, username, password}，报错只回码、不带原文；
- 共用通道（ERP 用，`cc_desktop.api.mrerp_login_ticket` + `machine.mrerp_login`）：产品没接回 unsupported；接了以后只有
  产品说能改这条连接的人拿得到票、只有发票时那台电脑能交、交来的密码进产品的存储钩子、回给电脑的不含密码；
  这台电脑已授权的连接记在电脑行上（只有连接号）；
- 机器方法只认精确 POST + 机器令牌：两条通道的路径表与 CC 桌面版 `channel.py` 一致，会话调用、别的 HTTP 方法拒绝；
- 心跳 ``mrerp_logins`` 的存法（只收连接号；旧版不报不动；报空照存）。
"""
import ast
import pathlib
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()

from pearnly_core.cc_desktop import api, contracts, devices, http_auth, login_tickets, machine  # noqa: E402
from pearnly_core.cc_desktop.wire import route_contracts  # noqa: E402
from pearnly_core.execution import state  # noqa: E402

SITE = "testerp.localhost"
KEY = "k" * 43
PASSWORD = "S3cret-pass!"
COMPANION = ROOT / "companion/src/companion"
COWORK_AUTH = ROOT / "apps/pearnly_cowork/pearnly_cowork/companion/http_auth.py"


class FakeStore:
    """不淘汰的状态存储的替身：只实现票用到的 set / eval（取出并删）/ make_key。"""

    def __init__(self):
        self.data = {}

    def make_key(self, key):
        return "site|" + key

    def set(self, key, value, ex=None):
        self.data[key] = value

    def eval(self, script, count, key):
        return self.data.pop(key, None)


class Tickets(unittest.TestCase):
    def test_ticket_shape_and_key_is_a_digest(self):
        ticket = login_tickets.new_ticket()
        self.assertTrue(login_tickets.valid_ticket(ticket))
        self.assertNotIn(ticket, login_tickets.key_of(ticket))
        for bad in ("", "mrl_short", "exp_" + "a" * 40, None, 7, "mrl_" + "a" * 40 + " "):
            self.assertFalse(login_tickets.valid_ticket(bad))

    def test_claim_is_bound_and_matches_only_the_same_site_channel_and_computer(self):
        bound = login_tickets.claim(site=SITE, channel="core", connection="ERPC-1", user="a@x", device="PCC-1")
        self.assertEqual(bound["scope"], "site")
        with self.assertRaises(login_tickets.TicketError):
            login_tickets.claim(site=SITE, channel="core", connection="X", user="a", device="d", scope="all")
        self.assertEqual(login_tickets.matches(bound, site=SITE, channel="core", device="PCC-1"), bound)
        for kwargs in ({"site": "other"}, {"channel": "cowork"}, {"device": "PCC-2"}, {"device": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(login_tickets.TicketError):
                login_tickets.matches(bound, **{"site": SITE, "channel": "core", "device": "PCC-1", **kwargs})
        with self.assertRaises(login_tickets.TicketError):
            login_tickets.matches(None, site=SITE, channel="core", device="PCC-1")
        with self.assertRaises(login_tickets.TicketError):
            login_tickets.claim(site=SITE, channel="email", connection="X", user="a", device="d")

    def test_issue_stores_no_secret_and_redeem_works_once(self):
        store = FakeStore()
        with patch.object(state, "store", lambda: store):
            ticket = login_tickets.issue(site=SITE, channel="core", connection="ERPC-1", user="a@x", device="PCC-1")
            self.assertNotIn(ticket, repr(store.data))
            self.assertEqual(login_tickets.redeem(ticket)["connection"], "ERPC-1")
            self.assertIsNone(login_tickets.redeem(ticket))
            self.assertIsNone(login_tickets.redeem("not-a-ticket"))

    def test_login_request_takes_exactly_three_keys_and_never_echoes_them(self):
        ticket = login_tickets.new_ticket()
        self.assertEqual(login_tickets.login_request({"ticket": ticket, "username": "u", "password": PASSWORD}),
                         (ticket, "u", PASSWORD))
        bad = [{"ticket": ticket, "username": "u"}, {"ticket": ticket, "username": "u", "password": PASSWORD, "x": 1},
               {"ticket": ticket, "username": " u", "password": PASSWORD},
               {"ticket": ticket, "username": "u", "password": "*****"},
               {"ticket": ticket, "username": "u", "password": ""},
               {"ticket": ticket, "username": "u", "password": "a\nb"},
               {"ticket": "bad", "username": "u", "password": PASSWORD}, ["ticket"]]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(login_tickets.TicketError) as caught:
                login_tickets.login_request(body)
            self.assertEqual(caught.exception.code, "login_invalid")
            self.assertNotIn(PASSWORD, str(caught.exception))


class CoreChannel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fake.reset(self.tmp.name)
        fake.STATE.users = {"boss@x": {"enabled": 1}, "staff@x": {"enabled": 1}, "other@x": {"enabled": 1}}
        # 管理员写站点（site），使用成员只存本机（local），别人都不行（主会话 11-02 定）
        self.allowed, self.editors, self.members, self.stored = {"boss@x", "staff@x", "other@x"}, {"boss@x"}, {"staff@x"}, []
        fake.STATE.attrs.update({"t.allowed": lambda user: user in self.allowed,
                                 "t.login_scope": lambda user, connection: "site" if user in self.editors
                                 else "local" if user in self.members else "",
                                 "t.login_store": self.store_hook})
        self.hooks(login=True)
        self.store = FakeStore()
        self.enterContext(patch.object(state, "store", lambda: self.store))
        frappe.session.user = "boss@x"
        self.device = devices.register(request_key="r1", device_key=KEY, device_name="ACC-PC")["device"]
        self.context = devices.Context(SITE, self.device, "boss@x")

    def tearDown(self):
        self.tmp.cleanup()

    def hooks(self, *, login):
        values = {"allowed": ["t.allowed"]}
        if login:
            values.update({"mrerp_login_scope": ["t.login_scope"], "mrerp_login_store": ["t.login_store"]})
        fake.STATE.hooks["pearnly_cc_desktop"] = values

    def store_hook(self, connection, username, password, *, user, device_label, scope):
        self.stored.append((connection, username, password, user, device_label, scope))
        return {"connection": connection, "system_url": "https://www.mrerp4sme.com", "username": username,
                "device": device_label, "at": "2026-11-02T13:00:00+00:00", "written": scope == "site"}

    def machine_call(self, body, context=None):
        frappe.local.form_dict = dict(body)
        with patch.object(http_auth, "current_request", lambda operation: (context or self.context, dict(body))):
            return machine._login("mrerp_login")

    def ticket(self, user="boss@x", key=KEY, connection="ERPC-1"):
        frappe.session.user = user
        return api.mrerp_login_ticket(connection=connection, device_key=key)

    def test_without_the_product_hook_both_halves_answer_unsupported(self):
        self.hooks(login=False)
        self.assertEqual(self.ticket(), {"ok": False, "code": "unsupported"})
        body = {"ticket": login_tickets.new_ticket(), "username": "u", "password": PASSWORD}
        self.assertEqual(self.machine_call(body), {"ok": False, "code": "unsupported"})
        self.assertEqual(self.stored, [])

    def test_only_people_the_product_lets_change_the_connection_get_a_ticket(self):
        with self.assertRaises(frappe.PermissionError):
            self.ticket(user="other@x")
        self.assertEqual(self.ticket(user="staff@x")["scope"], "local")
        self.assertEqual(self.ticket(key="z" * 43), {"ok": False, "code": "device_unknown"})
        self.assertEqual(self.ticket(key="short")["code"], "login_invalid")
        issued = self.ticket()
        self.assertTrue(issued["ok"])
        self.assertTrue(login_tickets.valid_ticket(issued["ticket"]))

    def test_the_computer_hands_the_login_once_and_gets_no_password_back(self):
        ticket = self.ticket()["ticket"]
        result = self.machine_call({"ticket": ticket, "username": "test01", "password": PASSWORD})
        self.assertTrue(result["ok"])
        self.assertEqual(result["connection"], "ERPC-1")
        self.assertEqual(result["authorized"]["username"], "test01")
        self.assertNotIn(PASSWORD, repr(result))
        self.assertTrue(result["written"])
        self.assertEqual(self.stored, [("ERPC-1", "test01", PASSWORD, "boss@x", "ACC-PC", "site")])
        self.assertNotIn("password", frappe.local.form_dict)
        # 这台电脑已授权的连接记在电脑行上（只有连接号）
        row = fake.STATE.db.table(devices.DEVICE)[self.device]
        self.assertEqual(row["mrerp_logins"], "ERPC-1")
        # 票用过即作废
        again = self.machine_call({"ticket": ticket, "username": "test01", "password": PASSWORD})
        self.assertEqual(again, {"ok": False, "code": "ticket_invalid"})
        self.assertEqual(len(self.stored), 1)

    def test_another_computer_cannot_use_the_ticket(self):
        ticket = self.ticket()["ticket"]
        frappe.session.user = "boss@x"
        other = devices.register(request_key="r2", device_key="m" * 43, device_name="OTHER")["device"]
        result = self.machine_call({"ticket": ticket, "username": "test01", "password": PASSWORD},
                                   context=devices.Context(SITE, other, "boss@x"))
        self.assertEqual(result, {"ok": False, "code": "ticket_invalid"})
        self.assertEqual(self.stored, [])

    def test_permission_is_checked_again_when_the_login_arrives(self):
        ticket = self.ticket()["ticket"]
        self.editors.clear()  # 票是管理员的（site），交来时已不是管理员：拒写，不降成只存本机
        result = self.machine_call({"ticket": ticket, "username": "test01", "password": PASSWORD})
        self.assertEqual(result, {"ok": False, "code": "denied"})
        self.assertEqual(self.stored, [])

    def test_a_member_keeps_it_on_the_computer_only_and_the_site_is_not_written(self):
        ticket = self.ticket(user="staff@x")["ticket"]
        result = self.machine_call({"ticket": ticket, "username": "own", "password": PASSWORD})
        self.assertTrue(result["ok"])
        self.assertFalse(result["written"])
        self.assertEqual(self.stored[0][5], "local")
        self.assertNotIn(PASSWORD, repr(result))
        # 成员拿不到 site 范围的票：服务端按人定范围，不看页面要什么
        self.assertEqual(self.ticket(user="staff@x")["scope"], "local")

    def test_someone_off_the_list_gets_neither(self):
        ticket = self.ticket(user="staff@x")["ticket"]
        self.members.clear()
        result = self.machine_call({"ticket": ticket, "username": "own", "password": PASSWORD})
        self.assertEqual(result, {"ok": False, "code": "denied"})
        with self.assertRaises(frappe.PermissionError):
            self.ticket(user="staff@x")
        self.assertEqual(self.stored, [])

    def test_an_unexpected_failure_is_busy_without_the_password(self):
        ticket = self.ticket()["ticket"]

        def broken(*args, **kwargs):
            raise RuntimeError("boom " + PASSWORD)

        fake.STATE.attrs["t.login_store"] = broken
        with self.assertRaises(frappe.AuthenticationError) as caught:
            self.machine_call({"ticket": ticket, "username": "test01", "password": PASSWORD})
        self.assertEqual(caught.exception.http_status_code, 503)
        self.assertNotIn(PASSWORD, str(caught.exception))


class Paths(unittest.TestCase):
    def channel_paths(self):
        namespace = {}
        exec(compile(ast.parse((COMPANION / "channel.py").read_text(encoding="utf-8")), "channel.py", "exec"),
             namespace)
        return namespace["PATHS"]

    def test_both_channels_have_the_exact_post_method_the_cc_desktop_calls(self):
        paths = self.channel_paths()
        self.assertEqual(paths["core"]["mrerp_login"], http_auth.PATHS["mrerp_login"])
        tree = ast.parse(COWORK_AUTH.read_text(encoding="utf-8"))
        tables = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id == "LOGIN_METHODS":
                tables = ast.literal_eval(node.value)
        self.assertEqual(paths["cowork"]["mrerp_login"], "/api/method/" + tables["mrerp_login"])

    def test_a_session_or_another_http_method_is_not_a_machine_request(self):
        frappe.session.user = "Guest"
        token = contracts.new_token(contracts.new_device_name())

        def request(method="POST", header="Bearer " + token, cookies=None):
            return types.SimpleNamespace(path=http_auth.PATHS["mrerp_login"], method=method,
                                         headers={"Authorization": header}, cookies=cookies or {}, args={}, form={})

        self.assertEqual(http_auth._envelope(request(), {}), token)
        for bad in (request(method="GET"), request(cookies={"sid": "x"}), request(header=""),
                    request(header="Bearer exp_CWEC-1_secret")):
            with self.assertRaises(http_auth.RequestContractError):
                http_auth._envelope(bad, {})
        frappe.session.user = "boss@x"
        with self.assertRaises(http_auth.RequestContractError):
            http_auth._envelope(request(), {})
        frappe.session.user = "Guest"


class HeartbeatLogins(unittest.TestCase):
    def test_only_connection_ids_are_kept_and_an_old_cc_keeps_the_last_value(self):
        self.assertIsNone(route_contracts.stored_logins(None))
        self.assertIsNone(route_contracts.stored_logins("CWEC-1"))
        self.assertEqual(route_contracts.stored_logins([]), "")
        self.assertEqual(route_contracts.stored_logins(["CWEC-2", "CWEC-1", "CWEC-1", "bad id", 3, "a/b"]),
                         "CWEC-1,CWEC-2")
        self.assertEqual(route_contracts.logins_of("CWEC-1,CWEC-2"), frozenset({"CWEC-1", "CWEC-2"}))
        self.assertEqual(route_contracts.with_login("CWEC-2", "CWEC-1"), "CWEC-1,CWEC-2")
        many = ["C" + str(i) for i in range(200)]
        self.assertEqual(len(route_contracts.stored_logins(many).split(",")), route_contracts.MAX_LOGINS)


if __name__ == "__main__":
    unittest.main()
