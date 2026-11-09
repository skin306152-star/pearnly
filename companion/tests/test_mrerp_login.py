"""MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7 条，评估 §14.4，2026-11-02）：CC 一侧。

- 本机凭据：一条 MR.ERP 连接一条，目标名带站点与连接号；心跳只报这个站点的连接号；断开站点一并删掉（凭据管理器用替身）；
- 交给站点（``mrerp_login.authorize``）：只走这个成员通道的那条精确路径，报文只有票、用户名、密码；
  站点收下了才存本机，没收下本机不存；出错只回码，日志里没有密码；
- 桥（``tomo_bridge`` 的 ``mrerp_authorize``）：只收票，不收页面传来的用户名、密码；对话页不是这台电脑连着的站点不弹窗；
  取消不交；输好后在后台交，结果里没有密码；能力表里有 ``mrerp_login``；
- 心跳带 ``mrerp_logins``（只有连接号），不报就不带这个键。
"""
import json
import logging
import time

import pytest

from companion import channel, mrerp_login, queue_client
from companion.express_operator import credentials

PASSWORD = "S3cret-pass!"
TICKET = "mrl_" + "a" * 43
SITE = "testcowork.pearnly.com"
MRERP = "https://www.mrerp4sme.com"


class Vault:
    """Windows 凭据管理器的替身：目标名 → (用户名, 密码, 备注)。"""

    def __init__(self):
        self.items = {}

    def store(self, target, user, password, comment="Pearnly CC"):
        self.items[target] = (user, password, comment)

    def listing(self, prefix):
        return [(name, item[2]) for name, item in self.items.items() if name.startswith(prefix)]

    def forget(self, target):
        return self.items.pop(target, None) is not None


def test_one_credential_per_connection_named_by_mrerp_host_and_connection():
    # 与第 2 步代录同一个名字：Pearnly CC/MR.ERP/<MR.ERP 主机>/<连接号>
    assert credentials.mrerp_target("https://www.MRERP4SME.com/", "CWEC-1") == "Pearnly CC/MR.ERP/www.mrerp4sme.com/CWEC-1"
    assert credentials.mrerp_target("www.mrerp4sme.com", "CWEC-1") == "Pearnly CC/MR.ERP/www.mrerp4sme.com/CWEC-1"
    for url, connection in (("", "CWEC-1"), (MRERP, ""), (MRERP, "a/b"), ("a b", "C"), (MRERP, "x" * 141)):
        with pytest.raises(ValueError):
            credentials.mrerp_target(url, connection)
    assert credentials.site_comment(SITE) == "Pearnly CC|" + SITE
    # Express 的目标名不受影响
    assert credentials.target("CWEC-9") == "Pearnly CC/Express/CWEC-9"


def test_heartbeat_lists_only_this_sites_connection_ids_and_disconnect_forgets_them():
    vault = Vault()
    mine = credentials.site_comment(SITE)
    vault.store(credentials.mrerp_target(MRERP, "CWEC-2"), "u", PASSWORD, mine)
    vault.store(credentials.mrerp_target(MRERP, "CWEC-1"), "u", PASSWORD, mine)
    vault.store(credentials.mrerp_target(MRERP, "ERPC-1"), "u", PASSWORD, credentials.site_comment("testerp.pearnly.com"))
    vault.store(credentials.mrerp_target(MRERP, "OPR-1"), "u", PASSWORD)  # 第 2 步代录自己存的：不算哪个站点
    vault.store(credentials.target("CWEC-9"), "u", PASSWORD)
    assert credentials.mrerp_connections(SITE, listing=vault.listing) == ["CWEC-1", "CWEC-2"]
    assert credentials.mrerp_connections("", listing=vault.listing) == []
    assert credentials.forget_site(SITE, listing=vault.listing, remove=vault.forget) == 2
    assert sorted(vault.items) == sorted([credentials.target("CWEC-9"), credentials.mrerp_target(MRERP, "ERPC-1"),
                                          credentials.mrerp_target(MRERP, "OPR-1")])


def _server(reply=None, status=200, fail=None):
    sent = []

    def post(backend_url, token, path, body, timeout):
        sent.append((backend_url, token, path, dict(body)))
        if fail:
            raise fail
        return status, reply if reply is not None else {
            "ok": True, "connection": "CWEC-1", "system_url": "https://www.mrerp4sme.com", "written": True,
            "authorized": {"username": "test01", "device": "ACC-PC", "at": "2026-11-02T13:00:00+00:00"}}

    return sent, post


def _authorize(post, vault, token="exp_CWEC-1_secret", ticket=TICKET, username=" test01 "):
    return mrerp_login.authorize(site=SITE, backend_url="https://" + SITE, token=token, ticket=ticket,
                                 username=username, password=PASSWORD, post=post, store=vault.store)


def test_the_site_gets_it_first_then_this_computer_stores_it(caplog):
    caplog.set_level(logging.DEBUG)
    vault = Vault()
    sent, post = _server()
    result = _authorize(post, vault)
    assert result == {"ok": True, "connection": "CWEC-1", "written": True,
                      "authorized": {"username": "test01", "device": "ACC-PC", "at": "2026-11-02T13:00:00+00:00"}}
    assert sent == [("https://" + SITE, "exp_CWEC-1_secret", channel.PATHS["cowork"]["mrerp_login"],
                     {"ticket": TICKET, "username": "test01", "password": PASSWORD})]
    assert vault.items == {credentials.mrerp_target(MRERP, "CWEC-1"): ("test01", PASSWORD, credentials.site_comment(SITE))}
    assert PASSWORD not in json.dumps(result)
    assert PASSWORD not in caplog.text


def test_a_member_keeps_it_on_this_computer_only():
    # 使用成员：站点只核权限、不写（written false），CC 照样存本机（主会话 11-02 定）
    vault = Vault()
    _, post = _server(reply={"ok": True, "connection": "CWEC-1", "system_url": MRERP, "written": False,
                             "authorized": {"username": "own", "device": "ACC-PC", "at": "2026-11-02T13:00:00+00:00"}})
    result = _authorize(post, vault, username="own")
    assert result["ok"] is True and result["written"] is False
    assert vault.items == {credentials.mrerp_target(MRERP, "CWEC-1"): ("own", PASSWORD, credentials.site_comment(SITE))}


def test_the_erp_channel_uses_its_own_path():
    vault = Vault()
    sent, post = _server(reply={"ok": False, "code": "unsupported"})
    assert _authorize(post, vault, token="pcc_PCC-1_secret") == {"ok": False, "code": "unsupported"}
    assert sent[0][2] == channel.PATHS["core"]["mrerp_login"]
    assert vault.items == {}


@pytest.mark.parametrize("status, reply, fail, code", [
    (200, {"ok": False, "code": "ticket_invalid"}, None, "ticket_invalid"),
    (200, {"ok": False, "code": "denied"}, None, "denied"),
    (401, {}, None, "unauthorized"),
    (403, {}, None, "unauthorized"),
    (400, {}, None, "login_invalid"),
    (503, {}, None, "network"),
    (200, {}, OSError("connect failed " + PASSWORD), "network"),
    (200, {"ok": True, "connection": "../x"}, None, "failed"),
    (200, {"ok": True, "connection": "CWEC-1", "system_url": ""}, None, "failed"),
])
def test_nothing_is_stored_when_the_site_does_not_take_it(status, reply, fail, code, caplog):
    caplog.set_level(logging.DEBUG)
    vault = Vault()
    _, post = _server(reply=reply, status=status, fail=fail)
    assert _authorize(post, vault) == {"ok": False, "code": code}
    assert vault.items == {}
    assert PASSWORD not in caplog.text


def test_a_store_failure_is_reported_without_the_password(caplog):
    caplog.set_level(logging.DEBUG)

    class Broken(Vault):
        def store(self, target, user, password, comment="Pearnly CC"):
            raise OSError("CredWriteW " + password)

    _, post = _server()
    assert _authorize(post, Broken()) == {"ok": False, "code": "store_failed"}
    assert PASSWORD not in caplog.text


def test_bad_tickets_and_unknown_tokens_are_not_sent():
    vault = Vault()
    sent, post = _server()
    assert _authorize(post, vault, ticket="bad")["code"] == "login_invalid"
    assert _authorize(post, vault, token="")["code"] == "login_invalid"
    assert sent == []


def test_heartbeat_carries_the_connection_ids_only_when_given():
    import httpx

    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"ok": True}})

    client = queue_client.QueueClient(queue_client.QueueClientConfig("https://" + SITE, "exp_CWEC-1_secret", "c1"))
    client._client = httpx.Client(base_url="https://" + SITE, transport=httpx.MockTransport(handler))
    client.health(mrerp_logins=["CWEC-1"])
    client.health(mrerp_logins=[])
    client.health()
    assert bodies[0]["mrerp_logins"] == ["CWEC-1"]
    assert bodies[1]["mrerp_logins"] == []
    assert "mrerp_logins" not in bodies[2]


# ── 桥 ──────────────────────────────────────────────────────────────────────────

class _Settings:
    def __init__(self, member=(SITE, "https://" + SITE, "exp_CWEC-1_secret")):
        self.member = member
        self.asked = []

    def snapshot(self):
        return {"members": [], "version": "1.13.0"}

    def member_for(self, host):
        self.asked.append(host)
        return self.member if self.member and host == self.member[0] else None


def _bridge(settings, page=f"https://{SITE}/assistant"):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from companion.tomo_bridge import FEATURES, TomoBridge

    QApplication.instance() or QApplication([])
    assert "mrerp_login" in FEATURES
    return TomoBridge(settings, allowed=lambda: True, window=lambda: None, set_lang=lambda lang: None,
                      page_url=lambda: page)


def _wait(bridge, job):
    from PySide6.QtWidgets import QApplication

    done = {}
    bridge.done.connect(lambda key, result: done.setdefault(key, result))
    deadline = time.time() + 5
    while job not in done and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return json.loads(done[job])


def test_bridge_takes_only_the_ticket_and_hands_the_login_over_in_the_background(monkeypatch):
    from companion.express_operator import login_dialog

    settings = _Settings()
    bridge = _bridge(settings)
    asked, sent = [], []
    monkeypatch.setattr(login_dialog, "ask", lambda lang, parent=None, **kw: asked.append(kw) or ("test01", PASSWORD))
    monkeypatch.setattr(mrerp_login, "authorize",
                        lambda **kw: sent.append(kw) or {"ok": True, "connection": "CWEC-1", "authorized": {}})
    first = json.loads(bridge.call("mrerp_authorize", json.dumps({"ticket": TICKET, "username": "x", "password": "y",
                                                                  "lang": "zh"})))
    result = _wait(bridge, first["job"])
    assert result["ok"] is True and PASSWORD not in json.dumps(result)
    assert asked == [{"title_key": "mrerp_login_title"}]
    assert sent[0]["ticket"] == TICKET and sent[0]["username"] == "test01" and sent[0]["password"] == PASSWORD
    assert sent[0]["site"] == SITE and sent[0]["token"] == "exp_CWEC-1_secret"


def test_bridge_refuses_without_a_ticket_or_on_another_site_and_cancel_sends_nothing(monkeypatch):
    from companion.express_operator import login_dialog

    asked = []
    monkeypatch.setattr(login_dialog, "ask", lambda lang, parent=None, **kw: asked.append(kw) and None)
    monkeypatch.setattr(mrerp_login, "authorize", lambda **kw: pytest.fail("must not send"))
    bridge = _bridge(_Settings())
    assert json.loads(bridge.call("mrerp_authorize", json.dumps({})))["code"] == "login_invalid"
    other = _bridge(_Settings(), page="https://testerp.pearnly.com/assistant")
    assert json.loads(other.call("mrerp_authorize", json.dumps({"ticket": TICKET})))["code"] == "not_connected"
    assert asked == []
    assert json.loads(bridge.call("mrerp_authorize", json.dumps({"ticket": TICKET}))) == {"ok": False,
                                                                                          "cancelled": True}
    assert len(asked) == 1


def test_operator_login_for_mrerp_goes_to_the_step_two_module_or_says_unsupported(monkeypatch):
    bridge = _bridge(_Settings())
    args = json.dumps({"target": "mrerp", "url": MRERP, "connection": "CWEC-1"})
    # 这一版没有 mrerp_operator 包（第 2 步合入时接上）：诚实回不支持，不弹窗、不碰 Express 的登录
    for method in ("operator_authorize", "operator_forget"):
        assert json.loads(bridge.call(method, args)) == {"ok": False, "code": "unsupported"}

    calls = []
    fake = type("Login", (), {"ask_and_store": staticmethod(lambda url, c, lang: calls.append(("ask", url, c)) or True),
                              "forget": staticmethod(lambda url, c: calls.append(("forget", url, c)) or True),
                              "stored": staticmethod(lambda: ["CWEC-1"])})
    monkeypatch.setattr(type(bridge), "_mrerp_login_module", staticmethod(lambda: fake))
    assert json.loads(bridge.call("operator_authorize", args)) == {"ok": True}
    assert json.loads(bridge.call("operator_forget", args)) == {"ok": True}
    assert calls == [("ask", MRERP, "CWEC-1"), ("forget", MRERP, "CWEC-1")]


def test_operator_login_for_mrerp_uses_the_step_two_module_only_with_the_switch_on(monkeypatch):
    from companion import payload_contract
    from companion.mrerp_operator import login as step_two

    monkeypatch.delenv(payload_contract.MRERP_OPERATOR_ENV, raising=False)
    assert type(_bridge(_Settings()))._mrerp_login_module() is None  # 默认：与没有执行层时一样
    monkeypatch.setenv(payload_contract.MRERP_OPERATOR_ENV, "1")
    assert type(_bridge(_Settings()))._mrerp_login_module() is step_two
    asked = []
    monkeypatch.setattr(step_two, "ask_and_store", lambda url, c, lang: asked.append((url, c, lang)) or False)
    args = json.dumps({"target": "mrerp", "url": MRERP, "connection": "CWEC-1"})
    assert json.loads(_bridge(_Settings()).call("operator_authorize", args)) == {"ok": False}
    assert asked and asked[0][:2] == (MRERP, "CWEC-1")


def test_login_window_buttons_speak_the_chosen_language(monkeypatch):
    # 11-08 日语窗口截图发现：没装 Qt 自带译文，标准按钮在泰 / 中 / 日界面也是英文「Save / Cancel」
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

    from companion.express_operator import login_dialog

    QApplication.instance() or QApplication([])
    seen = {}

    def fake_exec(dialog):
        box = dialog.findChild(QDialogButtonBox)
        seen["save"] = box.button(QDialogButtonBox.StandardButton.Save).text()
        seen["cancel"] = box.button(QDialogButtonBox.StandardButton.Cancel).text()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    for lang, save, cancel in (("ja", "保存", "キャンセル"), ("th", "บันทึก", "ยกเลิก"), ("zh", "保存", "取消"), ("en", "Save", "Cancel")):
        assert login_dialog.ask(lang, title_key="mrerp_login_title") is None
        assert seen == {"save": save, "cancel": cancel}, lang
