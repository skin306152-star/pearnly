"""桥侧云端连接宿主（``companion.cloud_link.host``）：隧道盘、会话交接、中转线程、连接窗口串起来，Mac 上离线测。

守住：盘符先用记下的、被占就从 Z 往 D 重找并记下、全占报错；subst 参数列表不经 shell、只撤映射到本隧道根目录的那个盘；
``ctl\\session.json`` 只有 v / epoch / key、先写 .part 再改名、日志里没有密钥；start / stop 顺序与可重复调，stop 半途出错也撤干净；
每次起连接换会话（start 后第一次沿用）；会话结束删交接文件；关窗即停；多站点按路径分通道、同通道只用第一个；
不是 Windows 时 start 不做任何事；对话页桥：连接后起宿主、断开先停宿主、状态取宿主会话、退出时停。"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
import time
import types

import pytest

from companion.cloud_link import host as host_mod
from companion.cloud_link import tunnel_protocol as protocol
from companion.cloud_link.host import CloudHost, HostUnavailable, TunnelDriveError
from companion.cloud_link.link_store import LinkStore
from companion.cloud_link.session import CloudSession, Snapshot, State
from companion.cloud_link.tunnel_relay import Incoming

HOST = "198.51.100.7"
PASSWORD = "Cloud-Pass-9!x"  # scan: fake
COWORK_TOKEN = "exp_CWEC-7_secrettokenvalue"
CORE_TOKEN = "pcc_ABCD_secretcorevalue"
HEARTBEAT_CORE = "/api/method/pearnly_core.cc_desktop.machine.heartbeat"
LEASE_COWORK = "/api/method/pearnly_cowork.companion.queue_api.lease"


class _Protector:
    def protect(self, value: str) -> str:
        return "dpapi-current-user:v1:" + base64.b64encode(value[::-1].encode()).decode()

    def unprotect(self, value: str) -> str:
        return base64.b64decode(value.split(":", 2)[2]).decode()[::-1]


def _load(tmp_path):
    return LinkStore.load(tmp_path / "cloud_link.json", protector=_Protector())


def _saved(tmp_path, **fields):
    store = _load(tmp_path)
    store.save_link(host=HOST, port=3390, user="CLOUDUSER-01", domain="example")
    if fields.get("drive"):
        store.set_tunnel_drive(HOST, fields["drive"])
    return store


class FakeDrives:
    """假盘符表：``used`` 是已占的盘符，``maps`` 是 subst 盘 → 目录。"""

    def __init__(self, used=("C",), maps=None, fail=(), events=None):
        self.taken = set(used)
        self.maps = dict(maps or {})
        self.fail = set(fail)
        self.events = events if events is not None else []

    def used(self):
        return set(self.taken) | set(self.maps)

    def target(self, letter):
        return self.maps.get(letter)

    def add(self, letter, root):
        self.events.append(("subst", letter))
        if letter in self.fail:
            raise TunnelDriveError("subst exit 1")
        self.maps[letter] = "\\??\\" + str(root)

    def remove(self, letter):
        self.events.append(("unsubst", letter))
        self.maps.pop(letter, None)


class FakeWindow:
    """假的连接窗口：照 CloudWindow 的样子，起连接时调一次 password_provider，关窗 = 断开并通知。"""

    def __init__(self, params, *, password_provider, nonscriptable, on_change, on_closed, events, fail_close=False):
        self.params = params
        self._provider = password_provider
        self.on_change = on_change
        self.on_closed = on_closed
        self.events = events
        self.fail_close = fail_close
        self.passwords = []
        self.session = CloudSession(self._open, lambda: events.append("disconnect"), time.monotonic)
        events.append("window")

    def _open(self):
        self.passwords.append(self._provider())
        self.events.append("connect")

    def start(self):
        self.events.append("start")
        self.session.connect()

    def connect_now(self):
        return self.session.connect()

    def close(self):
        self.events.append("close")
        if self.fail_close:
            raise RuntimeError("boom")
        self.session.user_close()
        self.on_change(self.session.snapshot(), "")


class FakeClient:
    def __init__(self, events, fail=False):
        self.events, self.fail = events, fail

    def close(self):
        self.events.append("client_close")
        if self.fail:
            raise RuntimeError("boom")


def _host(tmp_path, *, drives=None, available=True, events=None, site=None, window_kwargs=None, sessions=None,
          changes=None, client_fail=False):
    events = events if events is not None else []
    drives = drives or FakeDrives(events=events)
    windows = []

    def factory(params, **kwargs):
        window = FakeWindow(params, events=events, **kwargs, **(window_kwargs or {}))
        windows.append(window)
        return window

    seen = site if site is not None else []

    def forward(out):
        seen.append(out)
        return Incoming(200, {"content-type": "application/json"}, b'{"message": {"ok": true}}')

    made = iter(sessions) if sessions is not None else None
    host = CloudHost(HOST, links=lambda: _load(tmp_path), base_dir=tmp_path / "tunnel", drives=drives,
                     window_factory=factory, available=lambda: available,
                     forward_factory=lambda: (FakeClient(events, client_fail), forward),
                     password_provider=lambda h: (lambda: PASSWORD), nonscriptable=lambda: "nonscriptable",
                     new_session=(lambda: next(made)) if made is not None else protocol.new_session,
                     on_change=(lambda snapshot, description: changes.append(snapshot.state)) if changes is not None
                     else None,
                     run_thread=False)
    return host, drives, windows, events, seen


# ── 盘符 ─────────────────────────────────────────────────────────────────────

def test_candidates_prefer_the_remembered_letter_then_z_down_to_d():
    assert host_mod.candidates("", {"C"})[:3] == ["Z", "Y", "X"]
    assert host_mod.candidates("P", {"C"})[:2] == ["P", "Z"]
    assert host_mod.candidates("P", {"C", "P", "Z"})[:2] == ["Y", "X"]
    assert host_mod.candidates("", set(host_mod.LETTERS)) == []
    assert "C" not in host_mod.candidates("", set()) and host_mod.candidates("", set())[-1] == "D"


def test_remembered_letter_is_used_first(tmp_path):
    _saved(tmp_path, drive="P")
    host, drives, windows, events, _ = _host(tmp_path)
    host.start()
    assert host.drive == "P" and ("subst", "P") in events
    assert windows[0].params.tunnel_drive == "P"


def test_taken_remembered_letter_is_found_again_and_remembered(tmp_path):
    _saved(tmp_path, drive="Z")
    drives = FakeDrives(used=("C", "Z"))
    host, *_ = _host(tmp_path, drives=drives)
    host.start()
    assert host.drive == "Y"
    assert _load(tmp_path).get(HOST).tunnel_drive == "Y"


def test_a_letter_that_fails_to_map_is_skipped(tmp_path):
    _saved(tmp_path)
    drives = FakeDrives(fail={"Z"})
    host, *_ = _host(tmp_path, drives=drives)
    host.start()
    assert host.drive == "Y" and drives.events[:2] == [("subst", "Z"), ("subst", "Y")]


def test_remembered_letter_already_mapped_to_this_tunnel_is_reused_without_subst(tmp_path):
    _saved(tmp_path, drive="T")
    root = host_mod.tunnel_root(HOST, tmp_path / "tunnel")
    drives = FakeDrives(maps={"T": "\\??\\" + str(root).upper() + "\\"})
    host, *_ = _host(tmp_path, drives=drives)
    host.start()
    assert host.drive == "T" and not any(e[0] == "subst" for e in drives.events if isinstance(e, tuple))


def test_all_letters_taken_is_an_error_and_leaves_nothing_behind(tmp_path):
    _saved(tmp_path)
    drives = FakeDrives(used=tuple(host_mod.LETTERS) + ("C",))
    host, _, windows, events, _ = _host(tmp_path, drives=drives)
    with pytest.raises(TunnelDriveError):
        host.start()
    assert windows == [] and not host.running and not host.ctl_file.exists()
    assert not any(e == ("unsubst", letter) for e in events for letter in host_mod.LETTERS)


def test_old_link_files_without_a_tunnel_drive_still_load(tmp_path):
    _saved(tmp_path)
    raw = json.loads((tmp_path / "cloud_link.json").read_text(encoding="utf-8"))
    assert "tunnel_drive" not in raw["links"][0]
    raw["links"][0]["tunnel_drive"] = "not-a-drive"
    (tmp_path / "cloud_link.json").write_text(json.dumps(raw), encoding="utf-8")
    assert _load(tmp_path).get(HOST).tunnel_drive == ""


# ── subst ────────────────────────────────────────────────────────────────────

def test_subst_runs_as_an_argument_list_without_a_shell_and_without_a_window(monkeypatch, tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    drives = host_mod.WindowsDrives()
    drives.add("y", tmp_path / "t")
    drives.remove("Y")
    (add_args, add_kw), (rm_args, rm_kw) = calls
    exe = add_args[0]
    assert exe.endswith("subst.exe") and exe.startswith(r"C:\Windows")
    assert add_args[1:] == ["Y:", str(tmp_path / "t")] and rm_args[1:] == ["Y:", "/D"]
    for kwargs in (add_kw, rm_kw):
        assert kwargs["shell"] is False and kwargs["creationflags"] == 0x08000000 and kwargs["timeout"] > 0
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: types.SimpleNamespace(returncode=1))
    with pytest.raises(TunnelDriveError):
        drives.add("Y", tmp_path / "t")
    with pytest.raises(Exception):
        drives.add("C", tmp_path / "t")   # 不在 D–Z


def test_stop_only_removes_the_mapping_that_points_at_this_tunnel(tmp_path):
    _saved(tmp_path)
    host, drives, _, events, _ = _host(tmp_path)
    host.start()
    drives.maps["Z"] = r"\??\D:\someone-else"   # 期间被别人换成别的目录
    host.stop()
    assert ("unsubst", "Z") not in events and drives.maps["Z"] == r"\??\D:\someone-else"


def test_same_path_ignores_case_prefix_and_separators():
    assert host_mod.same_path("\\??\\C:\\Users\\A\\Pearnly\\x\\", "c:/users/a/pearnly/x")
    assert not host_mod.same_path("", "")
    assert not host_mod.same_path("C:\\a\\b", "C:\\a\\bc")


# ── 会话交接 ─────────────────────────────────────────────────────────────────

def test_ctl_file_has_only_version_epoch_and_key(tmp_path):
    _saved(tmp_path)
    session = protocol.new_session()
    host, *_ = _host(tmp_path, sessions=[session])
    host.start()
    raw = json.loads(host.ctl_file.read_bytes())
    assert set(raw) == {"v", "epoch", "key"} and raw["v"] == protocol.VERSION
    assert protocol.decode_session(host.ctl_file.read_bytes()) == session
    assert not host.ctl_file.with_name(host.ctl_file.name + ".part").exists()
    for name in ("in", "out", "ctl"):
        assert (host.root / name).is_dir()
    assert host.root == tmp_path / "tunnel" / HOST


def test_ctl_file_is_written_atomically(tmp_path, monkeypatch):
    _saved(tmp_path)
    first, second = protocol.new_session(), protocol.new_session()
    host, _, windows, *_ = _host(tmp_path, sessions=[first, second])
    host.start()
    windows[0].session.user_close()
    real_replace = host_mod.os.replace
    monkeypatch.setattr(host_mod.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError(5, "denied")))
    host.ctl_file.write_bytes(protocol.encode_session(first))
    with pytest.raises(OSError):
        windows[0].connect_now()
    monkeypatch.setattr(host_mod.os, "replace", real_replace)
    # 改名失败：旧文件原样、没有留下 .part
    assert protocol.decode_session(host.ctl_file.read_bytes()) == first
    assert not host.ctl_file.with_name(host.ctl_file.name + ".part").exists()


def test_session_codec_rejects_bad_files_and_never_shows_the_key():
    session = protocol.new_session()
    assert session.key.hex() not in repr(session)
    for data in (b"", b"[]", b'{"v":2,"epoch":"' + b"a" * 32 + b'","key":"AA=="}',
                 b'{"v":1,"epoch":"x","key":"AA=="}', b'{"v":1,"epoch":"' + b"a" * 32 + b'","key":"AA=="}'):
        with pytest.raises(protocol.TunnelError):
            protocol.decode_session(data)


def test_logs_never_contain_the_key(tmp_path, caplog, monkeypatch):
    _saved(tmp_path)
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(logging.getLogger("pearnly_companion"), "propagate", True)
    sessions = [protocol.new_session() for _ in range(3)]
    host, _, windows, *_ = _host(tmp_path, sessions=sessions)
    host.start()
    windows[0].session.user_close()
    windows[0].connect_now()
    host.stop()
    assert caplog.text
    for session in sessions[:2]:
        for shown in (base64.b64encode(session.key).decode(), session.key.hex()):
            assert shown not in caplog.text
    assert PASSWORD not in caplog.text


def test_each_connect_gets_a_new_session_and_the_first_reuses_starts(tmp_path):
    _saved(tmp_path)
    first, second = protocol.new_session(), protocol.new_session()
    host, _, windows, *_ = _host(tmp_path, sessions=[first, second])
    host.start()
    assert windows[0].passwords == [PASSWORD]
    assert protocol.decode_session(host.ctl_file.read_bytes()) == first
    windows[0].session.user_close()
    windows[0].connect_now()
    assert protocol.decode_session(host.ctl_file.read_bytes()) == second
    assert host._relay.session == second


def test_session_end_removes_the_handover_file(tmp_path):
    _saved(tmp_path)
    changes = []
    host, _, windows, *_ = _host(tmp_path, changes=changes)
    host.start()
    window = windows[0]
    window.session.on_login_complete()
    window.on_change(window.session.snapshot(), "")
    assert host.ctl_file.exists()
    window.session.on_disconnected(3, 0)   # 普通断线：等重连
    window.on_change(window.session.snapshot(), "")
    assert not host.ctl_file.exists()
    assert changes == [State.CONNECTED, State.RETRY_WAIT]


# ── 起停 ─────────────────────────────────────────────────────────────────────

def test_start_and_stop_order_and_both_are_repeatable(tmp_path):
    _saved(tmp_path)
    host, drives, windows, events, _ = _host(tmp_path)
    host.start()
    host.start()
    assert len(windows) == 1
    assert events == [("subst", "Z"), "window", "start", "connect"]
    assert host.session is windows[0].session and host.running
    host.stop()
    host.stop()
    assert events[4:] == ["close", "disconnect", "client_close", ("unsubst", "Z")]
    assert not host.running and host.session is None and not host.ctl_file.exists() and "Z" not in drives.maps


def test_stop_cleans_up_even_when_steps_fail_halfway(tmp_path):
    _saved(tmp_path)
    host, drives, windows, events, _ = _host(tmp_path, window_kwargs={"fail_close": True}, client_fail=True)
    host.start()
    host.stop()
    assert "close" in events and "client_close" in events and ("unsubst", "Z") in events
    assert not host.ctl_file.exists() and not host.running and host.drive == ""


def test_a_failing_window_start_undoes_everything(tmp_path):
    _saved(tmp_path)

    def broken(**kwargs):
        raise RuntimeError("control")

    host, drives, windows, events, _ = _host(tmp_path)
    host._window_factory = lambda params, **kwargs: broken()
    with pytest.raises(RuntimeError):
        host.start()
    assert ("unsubst", "Z") in events and not host.ctl_file.exists() and not host.running


def test_closing_the_window_stops_the_host(tmp_path):
    _saved(tmp_path)
    host, drives, windows, events, _ = _host(tmp_path)
    host.start()
    windows[0].close()
    windows[0].on_closed()
    assert not host.running and ("unsubst", "Z") in events and not host.ctl_file.exists()


def test_off_windows_start_has_no_side_effects(tmp_path):
    called = []
    drives = FakeDrives(events=called)
    host = CloudHost(HOST, links=lambda: called.append("links"), base_dir=tmp_path / "tunnel", drives=drives,
                     window_factory=lambda *a, **k: called.append("window"), available=lambda: False,
                     forward_factory=lambda: called.append("client"), run_thread=False)
    with pytest.raises(HostUnavailable):
        host.start()
    assert called == [] and not (tmp_path / "tunnel").exists()


def test_the_default_availability_is_false_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    host = CloudHost(HOST, links=lambda: None, base_dir=tmp_path / "tunnel")
    with pytest.raises(HostUnavailable):
        host.start()
    assert not (tmp_path / "tunnel").exists()


def test_the_relay_thread_runs_and_stops(tmp_path):
    _saved(tmp_path)
    host, *_ = _host(tmp_path)
    host._run_thread = True
    host.start()
    thread = host._thread
    assert thread is not None and thread.is_alive()
    host.stop()
    assert not thread.is_alive()


# ── 中转与多站点 ─────────────────────────────────────────────────────────────

def _ask(host, session, path, seq=1):
    request_id = protocol.new_id()
    data = protocol.encode_request(session, seq=seq, request_id=request_id, method="POST", path=path,
                                   headers={"content-type": "application/json"}, body=b"{}")
    (host.root / "in" / protocol.request_name(seq, request_id)).write_bytes(data)
    host.pump()
    return protocol.decode_reply((host.root / "out" / protocol.reply_name(request_id)).read_bytes(), session,
                                 request_id)


def test_requests_go_to_the_site_of_their_channel_with_the_bridge_token(tmp_path):
    store = _saved(tmp_path)
    store.set_token(HOST, "office.pearnly.com", "https://office.pearnly.com", COWORK_TOKEN)
    store.set_token(HOST, "erp.pearnly.com", "https://erp.pearnly.com", CORE_TOKEN)
    host, _, _, _, seen = _host(tmp_path)
    host.start()
    session = protocol.decode_session(host.ctl_file.read_bytes())
    assert _ask(host, session, HEARTBEAT_CORE, 1).status == 200
    assert _ask(host, session, LEASE_COWORK, 2).status == 200
    assert [out.url for out in seen] == ["https://erp.pearnly.com" + HEARTBEAT_CORE,
                                         "https://office.pearnly.com" + LEASE_COWORK]
    assert [out.headers["authorization"] for out in seen] == ["Bearer " + CORE_TOKEN, "Bearer " + COWORK_TOKEN]


def test_a_channel_without_a_paired_site_is_refused_and_a_later_pairing_is_picked_up(tmp_path):
    _saved(tmp_path)
    host, _, _, _, seen = _host(tmp_path)
    host.start()
    session = protocol.decode_session(host.ctl_file.read_bytes())
    assert _ask(host, session, HEARTBEAT_CORE, 1).refused == protocol.PATH_NOT_ALLOWED and seen == []
    _load(tmp_path).set_token(HOST, "erp.pearnly.com", "https://erp.pearnly.com", CORE_TOKEN)
    assert _ask(host, session, HEARTBEAT_CORE, 2).status == 200 and len(seen) == 1


def test_two_sites_on_the_same_channel_use_only_the_first(tmp_path):
    store = _saved(tmp_path)
    store.set_token(HOST, "a.pearnly.com", "https://a.pearnly.com", "exp_A_first")
    store.set_token(HOST, "b.pearnly.com", "https://b.pearnly.com", "exp_B_second")
    host, _, _, _, seen = _host(tmp_path)
    host.start()
    session = protocol.decode_session(host.ctl_file.read_bytes())
    _ask(host, session, LEASE_COWORK, 1)
    assert seen[0].url.startswith("https://a.pearnly.com/") and seen[0].headers["authorization"] == "Bearer exp_A_first"


def test_channel_of_path_covers_every_allowed_path():
    assert {host_mod.channel_of_path(path) for path in protocol.ALLOWED_PATHS} == {"cowork", "core"}
    assert host_mod.channel_of_path("/api/method/frappe.auth.get_logged_user") == ""


# ── 对话页桥 ─────────────────────────────────────────────────────────────────

class _FakeHost:
    def __init__(self, host, on_change, log, fail=None):
        self.host, self.on_change, self.log, self.fail = host, on_change, log, fail
        self.session = None
        log.append(("new", host))

    def start(self):
        self.log.append(("start", self.host))
        if self.fail:
            raise self.fail
        from companion.cloud_link.session import Snapshot

        self.session = types.SimpleNamespace(snapshot=lambda: Snapshot(State.CONNECTING, None, None, "", 0, None),
                                             user_close=lambda: self.log.append(("user_close", self.host)))

    def stop(self):
        self.log.append(("stop", self.host))
        self.session = None


@pytest.fixture
def vault(monkeypatch):
    from companion.cloud_link import credentials as rdp_credentials
    from companion.cloud_link import link_store

    generic = {}
    fake = types.SimpleNamespace(
        store=lambda name, user, password, comment="Pearnly CC": generic.__setitem__(name, (user, password)),
        read=lambda name: generic.get(name), user_name=lambda name: generic.get(name, ("", ""))[0],
        forget=lambda name: generic.pop(name, None) is not None)
    monkeypatch.setattr(link_store, "_creds", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(rdp_credentials, "store", lambda *a: None)
    monkeypatch.setattr(rdp_credentials, "forget", lambda host: True)
    return generic


def _bridge(tmp_path, holder, log, fail=None):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from companion.tomo_bridge import TomoBridge

    QApplication.instance() or QApplication([])
    settings = types.SimpleNamespace(snapshot=lambda: {"members": [], "device": {"name": "OFFICE-PC"}})
    return TomoBridge(settings, allowed=lambda: True, window=lambda: None, set_lang=lambda lang: None,
                      cloud_links=lambda: holder.get("store"),
                      cloud_new=lambda: holder.setdefault("store", _load(tmp_path)),
                      session_rdp=lambda: None, ask_login=lambda lang, title_key, user: ("CLOUDUSER-01", PASSWORD),
                      cloud_host=lambda host, on_change: _FakeHost(host, on_change, log, fail))


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


def test_bridge_connect_starts_the_host_and_disconnect_stops_it_first(tmp_path, vault):
    log, holder = [], {}
    bridge = _bridge(tmp_path, holder, log)
    args = json.dumps({"host": HOST, "port": 3390, "user": "CLOUDUSER-01"})
    assert _wait(bridge, bridge.call("cloud_connect", args)) == {"ok": True, "state": "connecting", "host": HOST}
    assert log == [("new", HOST), ("start", HOST)]
    assert json.loads(bridge.call("cloud_status", "{}"))["state"] == "connecting"
    # 再点「连接」：先停旧的再起
    _wait(bridge, bridge.call("cloud_connect", args))
    assert log[2:] == [("stop", HOST), ("new", HOST), ("start", HOST)]
    assert _wait(bridge, bridge.call("cloud_disconnect", "{}")) == {"ok": True}
    assert log[5] == ("stop", HOST) and ("user_close", HOST) not in log
    # 11-28 用户：断开只断开——连接记录与已存密码留着（重新登录免输），记下是人断开的（启动不自动连）
    status = json.loads(bridge.call("cloud_status", "{}"))
    assert status["state"] == "idle" and status["link"]["host"] == HOST and status["remembered"] is True
    assert holder["store"].get(HOST).halted == "user"
    assert status["forget"] is True   # 页面看这一项出「删除连接」
    # 「删除连接」才删：连接记录、远程桌面密码一起没了
    assert _wait(bridge, bridge.call("cloud_forget", "{}")) == {"ok": True}
    assert json.loads(bridge.call("cloud_status", "{}")) == {"ok": True, "link": None, "state": "idle", "places": [],
                                                              "forget": True, "operator_remembered": False}
    bridge.shutdown()
    assert log.count(("stop", HOST)) == 2


def test_bridge_keeps_the_link_when_the_host_cannot_start(tmp_path, vault):
    log, holder = [], {}
    bridge = _bridge(tmp_path, holder, log, fail=HostUnavailable("x"))
    args = json.dumps({"host": HOST, "port": 3390, "user": "CLOUDUSER-01"})
    assert _wait(bridge, bridge.call("cloud_connect", args)) == {"ok": True, "state": "idle", "host": HOST}
    assert _load(tmp_path).get(HOST) is not None
    assert json.loads(bridge.call("cloud_status", "{}"))["state"] == "idle"


def test_bridge_host_shutdown_stops_the_running_host(tmp_path, vault):
    log, holder = [], {}
    bridge = _bridge(tmp_path, holder, log)
    _wait(bridge, bridge.call("cloud_connect", json.dumps({"host": HOST, "port": 3390, "user": "u"})))
    bridge.shutdown()
    assert log[-1] == ("stop", HOST)


# ── 设备密钥的一次性配对 ─────────────────────────────────────────────────────

def test_host_pairing_hands_the_device_key_over_and_restores_the_title(tmp_path, caplog):
    pytest.importorskip("cryptography")
    from companion.cloud_link import device_pairing as pairing
    from companion.cloud_link.window import WINDOW_TITLE
    from companion.i18n import t

    caplog.set_level(logging.DEBUG)
    store = _saved(tmp_path)
    device_key = store.device_key(HOST)
    host, *_ = _host(tmp_path)
    titles = []
    host._set_title = lambda window, text: titles.append(text)
    with pytest.raises(host_mod.HostError):
        host.begin_pairing("zh")
    host.start()
    ticket = host.begin_pairing("zh")
    assert titles == [WINDOW_TITLE + " · " + t("btn_pair", "zh")]
    assert host.pairing_status()["state"] == pairing.WAITING
    vault = {}
    code = pairing.pair(host.ctl_dir, ticket.token, HOST, timeout=0.5, poll=0, now=time.time,
                        sleep=lambda _: host.pump(), store=lambda h, key: vault.__setitem__(h, key),
                        read_back=lambda h: vault.get(h, ""))
    host.pump()
    assert code == pairing.OK and vault == {HOST: device_key}
    assert host.pairing_status()["state"] == pairing.PAIRED
    assert titles[-1] == WINDOW_TITLE
    assert ticket.token not in caplog.text and device_key not in caplog.text
    host.stop()
    assert host.pairing_status()["state"] == pairing.IDLE


def test_bridge_pair_actions_return_only_the_token_and_the_status(tmp_path, vault):
    log, holder = [], {}
    bridge = _bridge(tmp_path, holder, log)
    assert json.loads(bridge.call("cloud_pair_begin", "{}")) == {"ok": False, "code": "not_running"}
    _wait(bridge, bridge.call("cloud_connect", json.dumps({"host": HOST, "port": 3390, "user": "u"})))
    fake = bridge._cloud_host
    fake.running = True
    fake.begin_pairing = lambda lang: types.SimpleNamespace(token="ABCD-EFGH", expires=123)  # scan: fake
    fake.pairing_status = lambda: {"state": "waiting", "code": "", "expires": 123}
    fake.root, fake.drive = tmp_path / "tunnel", "Z"
    bridge._cloud_prepare = lambda root, host: None   # 放 cc\ 的细节在 test_cloud_install_files.py
    result = _wait(bridge, bridge.call("cloud_pair_begin", "{}"))
    assert {key: result[key] for key in ("ok", "token", "expires", "drive")} == {"ok": True, "token": "ABCD-EFGH",
                                                                                "expires": 123, "drive": "Z"}
    assert result["command"].endswith("\\\\tsclient\\Z\\cc\\cloud_install.ps1 -Token ABCD-EFGH")
    assert json.loads(bridge.call("cloud_pair_status", "{}")) == {"ok": True, "state": "waiting", "code": "",
                                                                  "expires": 123}
    bridge.shutdown()


# ── 有请求才触发（dir_watch） ────────────────────────────────────────────────

def _watched(tmp_path, *, fallback=60.0):
    from companion.cloud_link import dir_watch

    _saved(tmp_path).set_token(HOST, "erp.pearnly.com", "https://erp.pearnly.com", CORE_TOKEN)
    host, _, _, _, seen = _host(tmp_path)
    watchers = []

    def factory(root):
        watcher = dir_watch.ManualWatcher()
        watchers.append((root, watcher))
        return watcher

    host._watch_factory = factory
    host._fallback = fallback
    host._run_thread = True
    calls = []
    real = host.pump

    def counted():
        calls.append(time.monotonic())
        return real()

    host.pump = counted
    return host, watchers, calls, seen


def _drop_request(host, session, seq=1):
    request_id = protocol.new_id()
    data = protocol.encode_request(session, seq=seq, request_id=request_id, method="POST", path=HEARTBEAT_CORE,
                                   headers={"content-type": "application/json"}, body=b"{}")
    (host.root / "in" / protocol.request_name(seq, request_id)).write_bytes(data)
    return host.root / "out" / protocol.reply_name(request_id)


def _until(check, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return check()


def test_idle_relay_never_enters_the_handler(tmp_path):
    host, watchers, calls, _ = _watched(tmp_path)
    host.start()
    try:
        assert [root for root, _ in watchers] == [host.root]
        time.sleep(0.4)
        assert calls == []
    finally:
        host.stop()


def test_a_change_notification_handles_the_request(tmp_path):
    host, watchers, calls, seen = _watched(tmp_path)
    host.start()
    try:
        session = protocol.decode_session(host.ctl_file.read_bytes())
        reply = _drop_request(host, session)
        time.sleep(0.2)
        assert calls == [] and not reply.exists()     # 没通知、没到兜底扫描：不碰
        watchers[0][1].notify()
        assert _until(reply.exists)
        assert len(calls) == 1 and len(seen) == 1
    finally:
        host.stop()


def test_stop_wakes_the_relay_thread_within_a_second(tmp_path):
    host, _, _, _ = _watched(tmp_path)
    host.start()
    thread = host._thread
    started = time.monotonic()
    host.stop()
    assert time.monotonic() - started < 1.0
    assert not thread.is_alive()


def test_the_fallback_scan_picks_up_a_missed_request(tmp_path):
    host, _, calls, seen = _watched(tmp_path, fallback=0.3)
    host.start()
    try:
        session = protocol.decode_session(host.ctl_file.read_bytes())
        reply = _drop_request(host, session)      # 不发通知（模拟漏掉）
        assert _until(reply.exists, 2.0)
        assert calls and len(seen) == 1
    finally:
        host.stop()


def test_due_timers_run_without_entering_the_handler(tmp_path):
    host, _, calls, _ = _watched(tmp_path)
    host.start()
    try:
        ctl = host.ctl_dir
        (ctl / "cred-resp.json").write_bytes(b"{}")
        host._sweeper.schedule(time.monotonic() + 0.2, ("cred-resp.json",))
        host._watcher.poke()                    # 让线程按新的兜底删时刻重算（poke 本身算一次变化）
        assert _until(lambda: len(calls) == 1)
        assert _until(lambda: not (ctl / "cred-resp.json").exists(), 2.0)
        assert len(calls) == 1                  # 兜底删到点只做定时事，不再进处理函数
    finally:
        host.stop()


def test_manual_and_polling_watchers():
    from companion.cloud_link import dir_watch

    manual = dir_watch.ManualWatcher()
    assert manual.wait(0.01) is False
    manual.notify()
    assert manual.wait(1) is True and manual.wait(0.01) is False
    manual.interrupt()
    started = time.monotonic()
    assert manual.wait(5) is False and time.monotonic() - started < 0.5
    polling = dir_watch.PollingWatcher(0.05)
    assert polling.wait(1) is True and polling.wait(0.01) is False
    polling.interrupt()
    assert polling.wait(1) is False


def test_off_windows_the_default_watcher_only_waits():
    from companion.cloud_link import dir_watch

    assert isinstance(dir_watch.open_watcher("."), dir_watch.ManualWatcher) == (sys.platform != "win32")


# ── 段 ③ a 首次自动安装（桥这边：等连上 → 备 cc\ → 令牌 → 发键 → 等配对） ────────────────────

class _InstallHost:
    def __init__(self, paired=True):
        self.host, self.root, self.drive, self.cloud_state = HOST, "Z:\\", "Z:", None
        self.session = types.SimpleNamespace(snapshot=lambda: Snapshot(State.CONNECTED, None, None, "", 0, None))
        self.paired, self.minimized, self.running = paired, 0, True

    def window_handle(self):
        return 4242

    def begin_pairing(self, lang=""):
        return types.SimpleNamespace(token="7K3M-Q9TB-ABCD-EFGH-JKMN-PQRS-TVXX", expires=0)  # scan: fake

    def pairing_status(self):
        return {"state": "paired" if self.paired else "failed"}

    def minimize_window(self):
        self.minimized += 1


def _auto(tmp_path, monkeypatch, host, sent_ok=True):
    from companion.cloud_link import auto_install

    bridge = _bridge(tmp_path, {}, [])
    bridge._later = lambda ms, fn: fn()
    bridge._cloud_prepare = lambda root, name: None
    bridge._cloud_host = host
    sent = []
    monkeypatch.setattr(auto_install, "Sender", lambda handle: ("sender", handle))
    monkeypatch.setattr(auto_install, "run", lambda command, sender: sent.append((command, sender)) or sent_ok)
    seen = []
    bridge._start_auto_install(host)
    first = json.loads(bridge.call("cloud_status", "{}"))
    seen.append(first.get("installing"))
    seen.append(first.get("step"))
    from PySide6.QtWidgets import QApplication
    deadline = time.monotonic() + 5
    while bridge._auto_installing and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return bridge, sent, seen


def test_the_bridge_installs_the_cloud_cc_once_by_itself_and_minimizes_the_window(tmp_path, monkeypatch):
    host = _InstallHost()
    bridge, sent, seen = _auto(tmp_path, monkeypatch, host)
    assert seen[0] is True and seen[1] in ("preparing", "installing", "pairing")   # 进行中：installing + 在做哪一步
    [(command, sender)] = sent
    assert command.endswith("cloud_install.ps1 -Token 7K3M-Q9TB-ABCD-EFGH-JKMN-PQRS-TVXX") and sender == ("sender", 4242)
    assert bridge._auto_installing == "" and host.minimized == 1
    done = json.loads(bridge.call("cloud_status", "{}"))
    assert "installing" not in done and done["step"] == "finding"   # 配好了：云端 CC 去找账套，直到它报来状态
    host.cloud_state = {"version": "1.12.26"}
    assert "step" not in json.loads(bridge.call("cloud_status", "{}"))
    bridge._start_auto_install(host)   # 同一台主机本进程不再试
    assert len(sent) == 1
    bridge.shutdown()


def test_when_sending_or_pairing_fails_the_page_falls_back_to_the_install_command(tmp_path, monkeypatch):
    host = _InstallHost(paired=False)
    bridge, sent, _ = _auto(tmp_path, monkeypatch, host)
    assert len(sent) == 1 and bridge._auto_installing == "" and host.minimized == 0
    bridge.shutdown()
    other = _InstallHost()
    bridge, sent, _ = _auto(tmp_path / "b", monkeypatch, other, sent_ok=False)
    assert bridge._auto_installing == "" and other.minimized == 0
    bridge.shutdown()


def test_a_host_the_cloud_cc_already_reports_from_is_not_installed_again(tmp_path, monkeypatch):
    host = _InstallHost()
    host.cloud_state = {"version": "1.12.26"}
    bridge, sent, seen = _auto(tmp_path, monkeypatch, host)
    assert sent == [] and seen == [None, None]
    bridge.shutdown()
