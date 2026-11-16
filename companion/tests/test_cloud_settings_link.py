"""「设置 → 连接」的「云端 Express」一行背后的桥动作（``cloud_link.settings_link`` 与 ``tomo_bridge`` 的 cloud_prefill /
cloud_connect / cloud_disconnect / cloud_status，步 E）。

守住：预填不含任何密码（Session.rdp 里的 password / credential 行也不碰）；密码不经页面（ADR-026 第 7 条）：桥只收地址和预填的账号，
页面传来的密码一概不看，账号密码由 CC 原生小窗收；connect 把密码只存进本机两条凭据、存好连接与设备密钥，密码不进文件、回包、日志；断开清干净（配置文件、两条凭据，有会话先按人断开）；状态词只有五个且照会话实况；
没有云端连接时 state() 逐字节不变、几个动作都不建文件。"""
from __future__ import annotations

import base64
import json
import logging
import sys
import time
import types

import pytest

from companion import config
from companion.cloud_link import credentials as rdp_credentials
from companion.cloud_link import link_store, settings_link
from companion.cloud_link.link_store import LinkStore
from companion.cloud_link.reasons import Outcome
from companion.cloud_link.session import Snapshot, State

PASSWORD = "Cloud-Pass-9!x"  # scan: fake
HOST = "198.51.100.7"
SESSION_RDP = "\r\n".join([
    "full address:s:198.51.100.7",
    "server port:i:3390",
    "username:s:CLOUDUSER-01",
    "domain:s:example",
    "password 51:b:01000000D08C9DDF0115D1118C7A00C04FC297EB",
    "credential:s:SECRETVALUE",
]) + "\r\n"


class _Protector:
    def protect(self, value: str) -> str:
        return "dpapi-current-user:v1:" + base64.b64encode(value[::-1].encode()).decode()

    def unprotect(self, value: str) -> str:
        return base64.b64decode(value.split(":", 2)[2]).decode()[::-1]


def _store(tmp_path):
    return LinkStore.load(tmp_path / "cloud_link.json", protector=_Protector())


@pytest.fixture
def vault(monkeypatch):
    """假的 Windows 凭据管理器：Generic（express_operator.credentials）与 TERMSRV 域凭据各一份。"""
    generic, domain = {}, {}
    fake = types.SimpleNamespace(
        store=lambda name, user, password, comment="Pearnly CC": generic.__setitem__(name, (user, password)),
        read=lambda name: generic.get(name),
        user_name=lambda name: generic.get(name, ("", ""))[0],
        forget=lambda name: generic.pop(name, None) is not None,
    )
    monkeypatch.setattr(link_store, "_creds", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(rdp_credentials, "store",
                        lambda host, user, dom, password: domain.__setitem__("TERMSRV/" + host, (dom, user, password)))
    monkeypatch.setattr(rdp_credentials, "forget", lambda host: domain.pop("TERMSRV/" + host, None) is not None)
    return generic, domain


def _session_file(tmp_path):
    folder = tmp_path / "home" / "RDP6"
    folder.mkdir(parents=True)
    path = folder / "Session.rdp"
    path.write_bytes(b"\xff\xfe" + SESSION_RDP.encode("utf-16-le"))
    return path


def _connect(tmp_path, store=None, **fields):
    args = {"host": HOST, "port": 3390, "user": "CLOUDUSER-01", "password": PASSWORD, **fields}
    return settings_link.connect(store, new_store=lambda: _store(tmp_path), session_rdp=_session_file(tmp_path)
                                 if not (tmp_path / "home").exists() else tmp_path / "home" / "RDP6" / "Session.rdp",
                                 **args)


# ── 预填 ─────────────────────────────────────────────────────────────────────

def test_prefill_reads_only_the_address_from_session_rdp_and_never_a_password(tmp_path):
    found = settings_link.prefill(None, _session_file(tmp_path))
    assert found == {"ok": True, "host": HOST, "port": 3390, "user": ""}
    text = json.dumps(found)
    assert "SECRET" not in text and "D08C9DDF" not in text and "password" not in text


def test_prefill_without_anything_is_empty(tmp_path):
    assert settings_link.prefill(None, None) == {"ok": True, "host": "", "port": 3389, "user": ""}
    broken = tmp_path / "Session.rdp"
    broken.write_bytes(b"domain:s:corp\n")
    assert settings_link.prefill(_store(tmp_path), broken) == {"ok": True, "host": "", "port": 3389, "user": ""}


def test_prefill_prefers_the_saved_link_and_still_has_no_password(tmp_path, vault):
    _connect(tmp_path)
    found = settings_link.prefill(_store(tmp_path), None)
    assert found == {"ok": True, "host": HOST, "port": 3390, "user": "CLOUDUSER-01"}
    assert PASSWORD not in json.dumps(found)


# ── 连接（存） ─────────────────────────────────────────────────────────────────

def test_connect_stores_the_password_only_in_the_two_local_credentials(tmp_path, vault, caplog, monkeypatch):
    generic, domain = vault
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(logging.getLogger("pearnly_companion"), "propagate", True)
    result = _connect(tmp_path)
    assert result == {"ok": True, "state": "idle", "host": HOST}
    # 只填用户名：域取 Session.rdp 里同一台主机的
    assert generic == {"Pearnly CC/cloud/" + HOST: ("example\\CLOUDUSER-01", PASSWORD)}
    assert domain == {"TERMSRV/" + HOST: ("example", "CLOUDUSER-01", PASSWORD)}
    store = _store(tmp_path)
    link = store.get(HOST)
    assert (link.port, link.user, link.domain) == (3390, "CLOUDUSER-01", "example")
    assert len(store.device_key(HOST)) >= 32
    raw = (tmp_path / "cloud_link.json").read_text(encoding="utf-8")
    assert PASSWORD not in raw and PASSWORD not in json.dumps(result) and PASSWORD not in caplog.text
    assert link_store.password_provider(HOST)() == PASSWORD


def test_connect_refuses_bad_fields_and_changes_nothing(tmp_path, vault):
    generic, domain = vault
    assert _connect(tmp_path, host="bad host!") == {"ok": False, "code": "invalid", "field": "host"}
    assert _connect(tmp_path, port="99999") == {"ok": False, "code": "invalid", "field": "port"}
    assert _connect(tmp_path, user="") == {"ok": False, "code": "invalid", "field": "user"}
    assert _connect(tmp_path, password="") == {"ok": False, "code": "invalid", "field": "password"}
    assert generic == {} and domain == {} and not (tmp_path / "cloud_link.json").exists()


def test_connect_off_windows_is_unsupported_and_writes_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _connect(tmp_path) == {"ok": False, "code": "unsupported"}
    assert not (tmp_path / "cloud_link.json").exists()


def test_connect_to_another_host_replaces_the_old_link(tmp_path, vault):
    generic, domain = vault
    _connect(tmp_path)
    key = _store(tmp_path).device_key(HOST)
    result = _connect(tmp_path, store=_store(tmp_path), host="cloud.example.com", port="", user="corp\\alice")
    assert result["ok"] is True
    store = _store(tmp_path)
    assert [link.host for link in store.links] == ["cloud.example.com"]
    assert (store.links[0].port, store.links[0].domain) == (3389, "corp")
    assert store.device_key("cloud.example.com") != key
    assert set(generic) == {"Pearnly CC/cloud/cloud.example.com"} and set(domain) == {"TERMSRV/cloud.example.com"}


def test_saving_the_same_host_again_keeps_the_device_key(tmp_path, vault):
    _connect(tmp_path)
    key = _store(tmp_path).device_key(HOST)
    assert _connect(tmp_path, store=_store(tmp_path), user="CLOUDUSER-02")["ok"] is True
    store = _store(tmp_path)
    assert store.device_key(HOST) == key and store.get(HOST).user == "CLOUDUSER-02"


# ── 断开、状态 ───────────────────────────────────────────────────────────────

def test_disconnect_cleans_the_file_both_credentials_and_closes_the_session(tmp_path, vault):
    generic, domain = vault
    _connect(tmp_path)
    closed = []
    session = types.SimpleNamespace(user_close=lambda: closed.append(True))
    assert settings_link.disconnect(_store(tmp_path), session) == {"ok": True}
    assert closed == [True]
    assert not (tmp_path / "cloud_link.json").exists() and generic == {} and domain == {}
    assert settings_link.disconnect(None) == {"ok": True}


@pytest.mark.parametrize("state,halt,word", [
    (State.IDLE, None, "idle"),
    (State.CONNECTING, None, "connecting"),
    (State.CONNECTED, None, "connected"),
    (State.RETRY_WAIT, None, "idle"),
    (State.HALTED, Outcome.LOGIN_FAILED, "login_failed"),
    (State.HALTED, Outcome.REPLACED, "replaced"),
])
def test_status_words_follow_the_session(state, halt, word):
    assert settings_link.status_of(Snapshot(state, halt, None, "", 0, None)) == word
    assert word in settings_link.STATES


def test_status_without_a_link_and_with_one(tmp_path, vault):
    assert settings_link.status(None) == {"ok": True, "link": None, "state": "idle", "places": []}
    _connect(tmp_path)
    session = types.SimpleNamespace(snapshot=lambda: Snapshot(State.HALTED, Outcome.REPLACED, Outcome.REPLACED, "x", 0, None))
    found = settings_link.status(_store(tmp_path), session)
    assert found == {"ok": True, "state": "replaced", "places": [],
                     "link": {"host": HOST, "port": 3390, "user": "CLOUDUSER-01", "name": ""}}
    assert PASSWORD not in json.dumps(found)


# ── 桥 ──────────────────────────────────────────────────────────────────────

class _Settings:
    def snapshot(self):
        return {"members": [{"id": "m1"}], "version": "1.12.22", "device": {"name": "OFFICE-PC"}}


def _bridge(tmp_path, holder):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from companion.tomo_bridge import TomoBridge

    QApplication.instance() or QApplication([])
    return TomoBridge(_Settings(), allowed=lambda: True, window=lambda: None, set_lang=lambda lang: None,
                      cloud_links=lambda: holder.get("store"),
                      cloud_new=lambda: holder.setdefault("store", _store(tmp_path)),
                      session_rdp=lambda: holder.get("rdp"), cloud_session=lambda: holder.get("session"),
                      ask_login=lambda lang, user: holder.setdefault("asked", []).append((lang, user)) or holder.get("login"))


def _wait(bridge, reply):
    from PySide6.QtWidgets import QApplication

    first = json.loads(reply)
    if "job" not in first:
        return first
    done = {}
    bridge.done.connect(lambda job, result: done.__setitem__(job, json.loads(result)))
    deadline = time.monotonic() + 5
    while first["job"] not in done and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return done[first["job"]]


def test_bridge_state_stays_byte_identical_and_no_file_without_a_cloud_link(tmp_path, monkeypatch):
    from companion.tomo_bridge import FEATURES

    monkeypatch.setattr(config, "CLOUD_LINK_FILE", tmp_path / "cloud_link.json")
    bridge = _bridge(tmp_path, {})
    expected = json.dumps({"members": [{"id": "m1"}], "version": "1.12.22", "device": {"name": "OFFICE-PC"},
                           "window": {"frameless": True}, "features": list(FEATURES)}, ensure_ascii=False)
    assert bridge.state() == expected
    assert "cloud" not in " ".join(FEATURES)
    assert json.loads(bridge.call("cloud_prefill", "{}")) == {"ok": True, "host": "", "port": 3389, "user": ""}
    assert json.loads(bridge.call("cloud_status", "{}")) == {"ok": True, "link": None, "state": "idle", "places": []}
    assert _wait(bridge, bridge.call("cloud_disconnect", "{}")) == {"ok": True}
    assert bridge.state() == expected
    assert list(tmp_path.iterdir()) == []


def test_bridge_connect_status_disconnect_round_trip(tmp_path, vault):
    generic, domain = vault
    holder = {"rdp": _session_file(tmp_path), "login": ("CLOUDUSER-01", PASSWORD)}
    bridge = _bridge(tmp_path, holder)
    prefill = bridge.call("cloud_prefill", "{}")
    assert json.loads(prefill) == {"ok": True, "host": HOST, "port": 3390, "user": ""}
    # 页面就算带了密码也不看：账号密码只从原生小窗来，页面上的账号只作预填
    args = {"host": HOST, "port": 3390, "user": "CLOUDUSER-01", "password": "typed-in-page", "lang": "th"}
    reply = bridge.call("cloud_connect", json.dumps(args))
    assert PASSWORD not in reply
    result = _wait(bridge, reply)
    assert result == {"ok": True, "state": "idle", "host": HOST}
    assert holder["asked"] == [("th", "CLOUDUSER-01")]
    assert generic == {"Pearnly CC/cloud/" + HOST: ("example\\CLOUDUSER-01", PASSWORD)}
    assert "typed-in-page" not in json.dumps(generic) + json.dumps(domain)
    assert _store(tmp_path).get(HOST).via == "OFFICE-PC"
    assert json.loads(bridge.state())["cloud_link"] is True
    status = bridge.call("cloud_status", "{}")
    assert json.loads(status)["link"]["host"] == HOST and PASSWORD not in status
    assert _wait(bridge, bridge.call("cloud_disconnect", "{}")) == {"ok": True}
    assert generic == {} and domain == {} and not (tmp_path / "cloud_link.json").exists()
    assert "cloud_link" not in json.loads(bridge.state())


def test_bridge_checks_the_address_before_asking_and_cancel_changes_nothing(tmp_path, vault):
    generic, domain = vault
    holder = {}
    bridge = _bridge(tmp_path, holder)
    bad = json.loads(bridge.call("cloud_connect", json.dumps({"host": "bad host!", "user": "u"})))
    assert bad == {"ok": False, "code": "invalid", "field": "host"} and "asked" not in holder
    cancelled = json.loads(bridge.call("cloud_connect", json.dumps({"host": HOST, "port": 3390, "user": "u", "lang": "zh"})))
    assert cancelled == {"ok": False, "cancelled": True} and holder["asked"] == [("zh", "u")]
    assert generic == {} and domain == {} and not (tmp_path / "cloud_link.json").exists()


def test_bridge_connect_failure_is_reported_not_retried(tmp_path, vault, monkeypatch):
    calls = []

    def refuse(host, user, dom, password):
        calls.append(host)
        raise OSError(1312, "CredWriteW")

    monkeypatch.setattr(rdp_credentials, "store", refuse)
    holder = {"login": ("example\\CLOUDUSER-01", PASSWORD)}
    bridge = _bridge(tmp_path, holder)
    args = {"host": HOST, "port": 3390, "user": "CLOUDUSER-01"}
    assert _wait(bridge, bridge.call("cloud_connect", json.dumps(args))) == {"ok": False, "code": "credential_failed"}
    assert calls == [HOST]
    generic, domain = vault
    assert generic == {} and domain == {} and not (tmp_path / "cloud_link.json").exists()


def test_the_native_window_is_the_shared_login_dialog_with_its_own_title():
    pytest.importorskip("PySide6")
    import inspect

    from companion import i18n, tomo_bridge

    source = inspect.getsource(tomo_bridge._ask_cloud_login)
    assert 'login_dialog.ask(lang, title_key="cloud_login_title", user=user)' in source
    assert len(i18n._M["cloud_login_title"]) == 4
