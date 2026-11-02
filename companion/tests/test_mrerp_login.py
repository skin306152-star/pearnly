"""MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7 条，评估 §14.4，2026-11-02）：CC 一侧。

- 本机凭据：一条 MR.ERP 连接一条，目标名带站点与连接号；心跳只报这个站点的连接号；断开站点一并删掉（凭据管理器用替身）；
- 交给站点（``express_operator.mrerp_login.authorize``）：只走这个成员通道的那条精确路径，报文只有票、用户名、密码；
  站点收下了才存本机，没收下本机不存；出错只回码，日志里没有密码；
- 桥（``tomo_bridge`` 的 ``mrerp_authorize``）：只收票，不收页面传来的用户名、密码；对话页不是这台电脑连着的站点不弹窗；
  取消不交；输好后在后台交，结果里没有密码；能力表里有 ``mrerp_login``；
- 心跳带 ``mrerp_logins``（只有连接号），不报就不带这个键。
"""
import json
import logging
import time

import pytest

from companion import channel, queue_client
from companion.express_operator import credentials, mrerp_login

PASSWORD = "S3cret-pass!"
TICKET = "mrl_" + "a" * 43
SITE = "testcowork.pearnly.com"


class Vault:
    """Windows 凭据管理器的替身：目标名 → (用户名, 密码)。"""

    def __init__(self):
        self.items = {}

    def store(self, target, user, password):
        self.items[target] = (user, password)

    def listing(self, prefix):
        return [name for name in self.items if name.startswith(prefix)]

    def forget(self, target):
        return self.items.pop(target, None) is not None


def test_one_credential_per_connection_named_by_site_and_connection():
    assert credentials.mrerp_target("TestCowork.pearnly.com", "CWEC-1") == "Pearnly CC/MR.ERP/testcowork.pearnly.com/CWEC-1"
    for site, connection in (("", "CWEC-1"), (SITE, ""), (SITE, "a/b"), ("a b", "C"), (SITE, "x" * 141)):
        with pytest.raises(ValueError):
            credentials.mrerp_target(site, connection)
    # Express 的目标名不受影响
    assert credentials.target("CWEC-9") == "Pearnly CC/Express/CWEC-9"


def test_heartbeat_lists_only_this_sites_connection_ids_and_disconnect_forgets_them():
    vault = Vault()
    vault.store(credentials.mrerp_target(SITE, "CWEC-2"), "u", PASSWORD)
    vault.store(credentials.mrerp_target(SITE, "CWEC-1"), "u", PASSWORD)
    vault.store(credentials.mrerp_target("testerp.pearnly.com", "ERPC-1"), "u", PASSWORD)
    vault.store(credentials.target("CWEC-9"), "u", PASSWORD)
    assert credentials.mrerp_connections(SITE, listing=vault.listing) == ["CWEC-1", "CWEC-2"]
    assert credentials.mrerp_connections("", listing=vault.listing) == []
    assert credentials.forget_site(SITE, listing=vault.listing, remove=vault.forget) == 2
    assert sorted(vault.items) == [credentials.target("CWEC-9"), credentials.mrerp_target("testerp.pearnly.com", "ERPC-1")]


def _server(reply=None, status=200, fail=None):
    sent = []

    def post(backend_url, token, path, body, timeout):
        sent.append((backend_url, token, path, dict(body)))
        if fail:
            raise fail
        return status, reply if reply is not None else {
            "ok": True, "connection": "CWEC-1", "system_url": "https://www.mrerp4sme.com",
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
    assert result == {"ok": True, "connection": "CWEC-1",
                      "authorized": {"username": "test01", "device": "ACC-PC", "at": "2026-11-02T13:00:00+00:00"}}
    assert sent == [("https://" + SITE, "exp_CWEC-1_secret", channel.PATHS["cowork"]["mrerp_login"],
                     {"ticket": TICKET, "username": "test01", "password": PASSWORD})]
    assert vault.items == {credentials.mrerp_target(SITE, "CWEC-1"): ("test01", PASSWORD)}
    assert PASSWORD not in json.dumps(result)
    assert PASSWORD not in caplog.text


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
        def store(self, target, user, password):
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
