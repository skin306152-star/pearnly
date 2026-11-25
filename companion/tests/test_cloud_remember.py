"""云端 Express 输一次记住、自动连、弹窗收密码、代录登录（第二阶段 D5）。
守住：页面带 ``inline: true`` 才用弹窗里填的密码（旧页面照旧弹原生小窗、交的密码不看）；不带密码时同一账号且本机存过就用存的、
否则回 ``password_required``；登录失败的状态带原因；CC 启动时存着连接与密码就自动连，上次被别处挤下线就不连（免得开机挤掉正在用的人），
人连上了清掉这个标记；「代录登录」页面交用户名密码只存桥本机。Windows 凭据管理器、连接窗口都是假的。"""
from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("PySide6.QtWidgets")   # 先导入：假凭据管理器会把 sys.platform 改成 win32，之后再导入 Qt 会走 Windows 的 DLL 路径

from companion.cloud_link import settings_link  # noqa: E402
from companion.cloud_link.reasons import Outcome  # noqa: E402
from companion.cloud_link.session import Snapshot, State  # noqa: E402

from test_cloud_settings_link import HOST, PASSWORD, _bridge, _connect, _store, _wait, vault  # noqa: E402,F401


# ── settings_link ────────────────────────────────────────────────────────────

def test_connect_without_a_password_uses_the_stored_one_for_the_same_account(tmp_path, vault):
    generic, _domain = vault
    _connect(tmp_path)
    stored = dict(generic)
    result = _connect(tmp_path, _store(tmp_path), password=None, port=3391)
    assert result == {"ok": True, "state": "idle", "host": HOST}
    assert generic == stored and _store(tmp_path).get(HOST).port == 3391   # 密码没动，端口改了照存


def test_connect_without_a_password_asks_for_one_when_nothing_matches(tmp_path, vault):
    assert _connect(tmp_path, None, password=None) == {"ok": False, "code": "password_required"}
    _connect(tmp_path)
    assert _connect(tmp_path, _store(tmp_path), password=None, user="OTHER") == {
        "ok": False, "code": "password_required"}
    generic, _domain = vault
    generic.clear()
    assert _connect(tmp_path, _store(tmp_path), password=None) == {"ok": False, "code": "password_required"}


@pytest.mark.parametrize("name, reason", [
    ("SSL_ERR_LOGON_FAILURE", "password"),
    ("SSL_ERR_NO_SUCH_USER", "password"),
    ("disconnectReasonByServer/exDiscReasonRdpEncInvalidCredentials", "password"),
    ("LOGON_FAILED_BAD_PASSWORD", "password"),
    ("SSL_ERR_PASSWORD_EXPIRED", "password_change"),
    ("STATUS_PASSWORD_MUST_CHANGE", "password_change"),
    ("SSL_ERR_ACCOUNT_LOCKED_OUT", "account"),
    ("disconnectReasonByServer/exDiscReasonServerInsufficientPrivileges", "account"),
    ("disc_123", ""),
])
def test_login_reason(name, reason):
    snapshot = Snapshot(State.HALTED, Outcome.LOGIN_FAILED, Outcome.LOGIN_FAILED, name, 0, None)
    assert settings_link.login_reason(snapshot) == reason


def test_status_says_why_the_login_failed(tmp_path, vault):
    _connect(tmp_path)
    session = types.SimpleNamespace(snapshot=lambda: Snapshot(State.HALTED, Outcome.LOGIN_FAILED, Outcome.LOGIN_FAILED,
                                                              "SSL_ERR_LOGON_FAILURE", 0, None))
    found = settings_link.status(_store(tmp_path), session)
    assert found["state"] == "login_failed" and found["reason"] == "password" and found["remembered"] is True
    assert PASSWORD not in json.dumps(found)


def test_link_store_keeps_the_replaced_mark_and_old_files_read_without_it(tmp_path, vault):
    _connect(tmp_path)
    assert _store(tmp_path).get(HOST).halted == ""
    assert "halted" not in (tmp_path / "cloud_link.json").read_text(encoding="utf-8")
    _store(tmp_path).set_halted(HOST, "replaced")
    assert _store(tmp_path).get(HOST).halted == "replaced"
    _store(tmp_path).set_halted(HOST, "whatever")
    assert _store(tmp_path).get(HOST).halted == ""


# ── 桥：弹窗收密码 ─────────────────────────────────────────────────────────────

def test_inline_connect_takes_the_dialog_password_without_a_native_window(tmp_path, vault):
    generic, _domain = vault
    holder = {}
    bridge = _bridge(tmp_path, holder)
    reply = bridge.call("cloud_connect", json.dumps({"host": HOST, "port": 3390, "user": "CLOUDUSER-01",
                                                     "password": PASSWORD, "inline": True}))
    assert PASSWORD not in reply
    assert _wait(bridge, reply)["ok"] is True
    assert "asked" not in holder
    assert list(generic.values())[0][1] == PASSWORD


def test_inline_connect_without_a_password_uses_the_stored_one_or_asks_the_page(tmp_path, vault):
    holder = {}
    bridge = _bridge(tmp_path, holder)
    args = {"host": HOST, "port": 3390, "user": "CLOUDUSER-01", "inline": True}
    assert _wait(bridge, bridge.call("cloud_connect", json.dumps(args))) == {"ok": False, "code": "password_required"}
    _wait(bridge, bridge.call("cloud_connect", json.dumps({**args, "password": PASSWORD})))
    assert _wait(bridge, bridge.call("cloud_connect", json.dumps(args)))["ok"] is True
    assert "asked" not in holder
    assert json.loads(bridge.call("cloud_connect", json.dumps({**args, "password": ""}))) == {
        "ok": False, "code": "invalid", "field": "password"}
    assert json.loads(bridge.call("cloud_connect", json.dumps({**args, "user": " "}))) == {
        "ok": False, "code": "invalid", "field": "user"}


def test_old_pages_still_get_the_native_window_and_their_password_is_ignored(tmp_path, vault):
    generic, _domain = vault
    holder = {"login": ("CLOUDUSER-01", PASSWORD)}
    bridge = _bridge(tmp_path, holder)
    reply = bridge.call("cloud_connect", json.dumps({"host": HOST, "port": 3390, "user": "CLOUDUSER-01",
                                                     "password": "typed-in-page"}))
    assert _wait(bridge, reply)["ok"] is True
    assert [item[1] for item in holder["asked"]] == ["cloud_login_title"]
    assert list(generic.values())[0][1] == PASSWORD


# ── 桥：启动自动连 ─────────────────────────────────────────────────────────────

class _Host:
    def __init__(self, host, on_change):
        self.host, self.on_change, self.started, self.running = host, on_change, 0, False
        self.session = None

    def start(self):
        self.started += 1
        self.running = True

    def stop(self):
        self.running = False

    def request_forget(self, connection):
        pass


def _auto_bridge(tmp_path, holder):
    bridge = _bridge(tmp_path, holder)
    made = []
    bridge._cloud_host_new = lambda host, on_change: made.append(_Host(host, on_change)) or made[-1]
    bridge._refresh_cloud_package = lambda host: None
    return bridge, made


def test_autoconnect_starts_the_saved_link_once(tmp_path, vault):
    _connect(tmp_path)
    bridge, made = _auto_bridge(tmp_path, {"store": _store(tmp_path)})
    assert bridge.cloud_autoconnect() is True
    assert [(h.host, h.started) for h in made] == [(HOST, 1)]
    assert bridge.cloud_autoconnect() is False   # 已在连
    assert len(made) == 1


def test_autoconnect_skips_without_a_link_or_password_or_after_being_replaced(tmp_path, vault):
    generic, _domain = vault
    bridge, made = _auto_bridge(tmp_path, {})
    assert bridge.cloud_autoconnect() is False
    _connect(tmp_path)
    _store(tmp_path).set_halted(HOST, "replaced")
    bridge, made = _auto_bridge(tmp_path, {"store": _store(tmp_path)})
    assert bridge.cloud_autoconnect() is False
    _store(tmp_path).set_halted(HOST, "")
    generic.clear()
    bridge, made = _auto_bridge(tmp_path, {"store": _store(tmp_path)})
    assert bridge.cloud_autoconnect() is False and made == []


def test_being_replaced_is_remembered_and_cleared_on_connect(tmp_path, vault):
    _connect(tmp_path)
    holder = {"store": _store(tmp_path)}
    bridge, _made = _auto_bridge(tmp_path, holder)
    bridge.cloud_autoconnect()
    bridge._cloud_changed(Snapshot(State.HALTED, Outcome.REPLACED, Outcome.REPLACED, "x", 0, None))
    assert _store(tmp_path).get(HOST).halted == "replaced"
    holder["store"] = _store(tmp_path)
    bridge._cloud_changed(Snapshot(State.CONNECTING, None, None, "", 0, None))
    assert _store(tmp_path).get(HOST).halted == "replaced"
    bridge._cloud_changed(Snapshot(State.CONNECTED, None, None, "", 0, None))
    assert _store(tmp_path).get(HOST).halted == ""


# ── 桥：代录登录 ─────────────────────────────────────────────────────────────

def test_operator_login_from_the_dialog_is_stored_only_on_the_bridge(tmp_path, vault):
    _connect(tmp_path)
    vaulted = {}
    holder = {"store": _store(tmp_path)}
    bridge = _bridge(tmp_path, holder)
    bridge._cloud_cred_store = lambda target, user, password: vaulted.__setitem__(target, (user, password))
    reply = bridge.call("cloud_operator_authorize", json.dumps({"connection": "default", "inline": True,
                                                                "username": " EXPUSER ", "password": "exp-pass-1"}))
    assert json.loads(reply) == {"ok": True} and "exp-pass-1" not in reply
    assert vaulted == {f"Pearnly CC/cloud-express/{HOST}/default": ("EXPUSER", "exp-pass-1")}
    assert "asked" not in holder
    assert json.loads(bridge.call("cloud_operator_authorize", json.dumps(
        {"connection": "default", "inline": True, "username": "x"}))) == {"ok": False, "code": "invalid"}


def test_a_leftover_tunnel_drive_is_released_when_we_do_not_connect(tmp_path, vault, monkeypatch):
    # 11-25 测试机1 真机：CC 被强行结束后 Z: 一直留着，重启后被挤下线标记不自动连，盘还在
    from companion.cloud_link import host as host_mod

    released = []
    monkeypatch.setattr(host_mod, "release_stale_drive", lambda host, letter: released.append((host, letter)) or True)
    _connect(tmp_path)
    store = _store(tmp_path)
    store.set_tunnel_drive(HOST, "Z")
    store.set_halted(HOST, "replaced")
    bridge, made = _auto_bridge(tmp_path, {"store": _store(tmp_path)})
    assert bridge.cloud_autoconnect() is False and made == []
    assert released == [(HOST, "Z")]
    _store(tmp_path).set_halted(HOST, "")
    bridge, made = _auto_bridge(tmp_path, {"store": _store(tmp_path)})
    assert bridge.cloud_autoconnect() is True and released == [(HOST, "Z")]   # 要连：交给宿主接着用，不撤


def test_release_stale_drive_only_removes_our_own_mapping(tmp_path):
    from companion.cloud_link import host as host_mod
    from test_cloud_host import FakeDrives

    root = host_mod.tunnel_root(HOST, tmp_path)
    drives = FakeDrives(maps={"Z": "\\??\\" + str(root), "Y": "\\??\\C:\\elsewhere"})
    assert host_mod.release_stale_drive(HOST, "Y", drives=drives, base=tmp_path) is False
    assert host_mod.release_stale_drive(HOST, "", drives=drives, base=tmp_path) is False
    assert host_mod.release_stale_drive(HOST, "C:", drives=drives, base=tmp_path) is False
    assert host_mod.release_stale_drive(HOST, "z", drives=drives, base=tmp_path) is True
    assert ("unsubst", "Z") in drives.events and ("unsubst", "Y") not in drives.events
    assert host_mod.release_stale_drive(HOST, "Z", drives=None, base=tmp_path) in (False, True)   # 非 Windows：不抛
