# -*- coding: utf-8 -*-
"""云端模式 ``--cloud``（runtime_mode.py，docs/work/2026-11-11-express-cloud-assessment.md 第 4.2 节、
docs/work/2026-11-15-express-cloud-build-assessment.md 第 13 节步 D）：云端主机上那份 CC 不起托盘、窗口、通知、自动更新、
开机自启、防睡眠、文件夹自动录入、MR.ERP 成员；只跑直写循环、按需请求与代录。找 Express 数据根不碰 ``\\\\tsclient`` 与局域网共享。
心跳在云端模式带 ``link``，本地模式逐字节不变。"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from src.companion import (
    auto_update,
    autostart,
    express_locate,
    folder_worker,
    keep_awake,
    main,
    payload_contract,
    queue_client,
    runtime_mode,
    toast,
)
from src.companion.config import polls_queue

SITE = "erp.example.test"


@pytest.fixture
def local(monkeypatch):
    """本地模式（默认）：两个环境变量都没有；用 setenv 先记下原值，用例里 ``enable`` 改过也会被还原。"""
    monkeypatch.setenv(runtime_mode.CLOUD_ENV, "")
    monkeypatch.setenv(runtime_mode.VIA_ENV, "")
    monkeypatch.delenv(runtime_mode.CLOUD_ENV)
    monkeypatch.delenv(runtime_mode.VIA_ENV)
    return monkeypatch


@pytest.fixture
def cloud(local):
    local.setenv(runtime_mode.CLOUD_ENV, "1")
    return local


# ── 集中判断 ─────────────────────────────────────────────────────────


def test_only_the_cloud_flag_turns_cloud_mode_on(local):
    assert runtime_mode.cloud() is False
    local.setenv(runtime_mode.VIA_ENV, "OFFICE-PC")   # 只有桥电脑名不算
    assert runtime_mode.cloud() is False
    runtime_mode.enable("BRIDGE-1")
    assert runtime_mode.cloud() is True
    assert runtime_mode.link()["via"] == "BRIDGE-1"


def test_enable_without_a_bridge_name_keeps_the_one_from_the_environment(local):
    local.setenv(runtime_mode.VIA_ENV, "OFFICE-PC")
    runtime_mode.enable(None)
    assert runtime_mode.link()["via"] == "OFFICE-PC"


# ── 启动：不起托盘，只跑直写 + 按需请求（MR.ERP 成员去掉） ──────────────


class _Store:
    def __init__(self, profiles=()):
        self.profiles = list(profiles)

    def runtime_configs(self, *, include_paused=False):
        return list(self.profiles)

    def new_runtime_config(self):
        return SimpleNamespace(backend_url="", ui_lang="")


def _member(profile_id, kind="express", **extra):
    return SimpleNamespace(profile_id=profile_id, kind=kind, paused=False, account_dir=r"D:\W\ExpressI-Cloud\ACC1",
                           account_set=r"D:\W\ExpressI-Cloud\ACC1", **extra)


def _no_tray(monkeypatch):
    launched = []
    tray = ModuleType("src.companion.gui_tray")

    class FakeTrayApp:
        def __init__(self, configs, *, open_tomo=False):
            launched.append(list(configs))

        def run(self):
            return 17

    tray.TrayApp = FakeTrayApp
    monkeypatch.setitem(sys.modules, "src.companion.gui_tray", tray)
    return launched


def test_cloud_runs_the_cloud_loop_and_never_the_tray(cloud):
    launched = _no_tray(cloud)
    ran = []
    cloud.setattr(main, "step_cloud_loop", lambda: ran.append("cloud"))
    express, mrerp = _member("p-express"), _member("p-mrerp", kind="folder")

    assert main._run_dbf(_Store([express, mrerp]), SimpleNamespace(background=False, headless=False)) == 0
    assert ran == ["cloud"]
    assert launched == []


def test_cloud_without_a_member_waits_instead_of_exiting(cloud):
    """D3（11-24 首测）：云端那份从启动文件夹起来、还没有成员时不以 2 退出（一退出就要等到下次登录），进云端循环等桥下发。"""
    launched = _no_tray(cloud)
    ran = []
    cloud.setattr(main, "step_cloud_loop", lambda: ran.append("cloud"))
    assert main._run_dbf(_Store([_member("p-mrerp", kind="folder")]),
                         SimpleNamespace(background=False, headless=False)) == 0
    assert main._run_dbf(_Store([]), SimpleNamespace(background=False, headless=False)) == 0
    assert ran == ["cloud", "cloud"]
    assert launched == []


def test_headless_without_a_member_still_exits(local):
    launched = _no_tray(local)
    assert main._run_dbf(_Store([]), SimpleNamespace(background=False, headless=True)) == 2
    assert launched == []


def test_local_mode_still_starts_the_tray(local):
    launched = _no_tray(local)
    local.setattr(main, "step_cloud_loop", lambda: pytest.fail("cloud loop in local mode"))
    assert main._run_dbf(_Store([_member("p-express"), _member("p-mrerp", kind="folder")]),
                         SimpleNamespace(background=False, headless=False)) == 17
    assert [[c.profile_id for c in configs] for configs in launched] == [["p-express", "p-mrerp"]]


def test_cloud_configs_keep_express_members_with_a_book_only(cloud):
    from src.companion import profile_store

    members = [_member("p-express"), _member("p-mrerp", kind="folder"),
               SimpleNamespace(profile_id="p-nobook", kind="express", paused=False, account_dir="", account_set="")]
    cloud.setattr(profile_store.ProfileStore, "load", classmethod(lambda cls: _Store(members)))
    assert [c.profile_id for c in main._cloud_configs()] == ["p-express"]


class _Link:
    def __init__(self):
        self.generation = 0
        self.members_code = ""
        self.waits = []
        self.quit = threading.Event()   # 段 ③：自升级 / 断开删安装时置位，循环退出

    def wait_change(self, generation, stop):
        self.waits.append(generation)
        self.generation += 1   # 等的时候桥下发了成员


def _cloud_loop(cloud, link, configs_seq, poll):
    events = []

    class FakeRequests:
        def __init__(self, configs):
            self.configs = configs

        def start(self):
            events.append(("requests",))

        def stop(self):
            events.append(("requests-stopped",))

    from src.companion import request_worker
    from src.companion.cloud_link import cloud_side

    cloud.setattr(request_worker, "RequestWorker", FakeRequests)
    cloud.setattr(cloud_side, "start", lambda stop: (events.append(("tunnel",)) or
                                                    SimpleNamespace(link=link,
                                                                    stop=lambda: events.append(("tunnel-stopped",)))))
    cloud.setattr(cloud_side, "check_ctl", lambda got: events.append(("ctl",)))
    seq = iter(configs_seq)
    cloud.setattr(main, "_cloud_configs", lambda: next(seq))
    cloud.setattr(main, "step_poll_loop", lambda configs, stop=None: poll(events, configs, stop))
    cloud.setattr(main, "_STOP", False)
    main.step_cloud_loop()
    return events


def test_the_cloud_loop_waits_for_members_then_runs_the_write_loop(cloud):
    link = _Link()

    def poll(events, configs, stop):
        events.append(("poll", [c.profile_id for c in configs]))
        cloud.setattr(main, "_STOP", True)

    events = _cloud_loop(cloud, link, [[], [_member("p-express")]], poll)
    assert link.waits == [0]
    assert events == [("tunnel",), ("ctl",), ("requests",), ("poll", ["p-express"]), ("requests-stopped",),
                      ("tunnel-stopped",)]


def test_the_cloud_loop_reloads_when_the_members_change(cloud):
    link = _Link()
    stops = []

    def poll(events, configs, stop):
        events.append(("poll", [c.profile_id for c in configs]))
        stops.append(stop())
        if len(stops) == 1:
            link.generation += 1   # 桥下发了新站点：直写循环该停
            stops.append(stop())
        else:
            cloud.setattr(main, "_STOP", True)

    events = _cloud_loop(cloud, link, [[_member("p-a")], [_member("p-a"), _member("p-b")]], poll)
    assert stops[:2] == [False, True]
    assert [e for e in events if e[0] == "poll"] == [("poll", ["p-a"]), ("poll", ["p-a", "p-b"])]


def test_the_cloud_loop_stopped_while_waiting_for_the_tunnel_starts_nothing(cloud):
    from src.companion import request_worker
    from src.companion.cloud_link import cloud_side

    cloud.setattr(cloud_side, "start", lambda stop: None)
    cloud.setattr(request_worker, "RequestWorker", lambda *_a: pytest.fail("requests started without a tunnel"))
    cloud.setattr(main, "step_poll_loop", lambda configs, stop=None: pytest.fail("poll started without a tunnel"))
    main.step_cloud_loop()


def _stub_main_entry(monkeypatch, store):
    calls = {"aumid": 0, "run": []}
    from src.companion import profile_store, single_instance

    monkeypatch.setattr(main.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(profile_store, "ProfileStore", SimpleNamespace(load=lambda: store))
    monkeypatch.setattr(single_instance, "acquire", lambda: object())
    monkeypatch.setattr(main, "setup_logging", lambda cfg: None)
    monkeypatch.setattr(main, "_register_app_user_model_id", lambda: calls.__setitem__("aumid", calls["aumid"] + 1))
    monkeypatch.setattr(main, "_run_dbf", lambda store, args: calls["run"].append(runtime_mode.cloud()) or 0)
    monkeypatch.delenv("PEARNLY_COMPANION_PORT", raising=False)
    return calls


def test_the_cloud_argument_enters_cloud_mode_and_skips_the_notification_identity(local):
    calls = _stub_main_entry(local, _Store([_member("p-express")]))
    local.setattr(sys, "argv", ["companion", "--cloud", "--cloud-via", "OFFICE-PC"])
    assert main.main() == 0
    assert calls == {"aumid": 0, "run": [True]}
    assert runtime_mode.link()["via"] == "OFFICE-PC"


def test_without_the_argument_the_entry_is_unchanged(local):
    calls = _stub_main_entry(local, _Store([_member("p-express")]))
    local.setattr(sys, "argv", ["companion", "--background"])
    assert main.main() == 0
    assert calls == {"aumid": 1, "run": [False]}


def test_a_second_cloud_instance_only_prints(cloud, capsys):
    cloud.setitem(sys.modules, "src.companion.gui_font", None)   # 碰界面就会导入失败
    main._notify_already_running(SimpleNamespace(ui_lang="en"), headless=True)
    assert capsys.readouterr().out.strip()


# ── 每一样在云端模式下都不起 ───────────────────────────────────────────


class _Power:
    def __init__(self):
        self.calls = []

    def set_state(self, flags):
        self.calls.append(flags)
        return True

    def on_battery(self):
        return False

    def modern_standby(self):
        return False


def test_cloud_never_keeps_the_host_awake(cloud):
    power = _Power()
    awake = keep_awake.KeepAwake(power, poll=0.01)
    awake.apply(enabled=True, on_battery=True, members=True)
    assert not awake.active and awake.flags == 0
    assert all(flags == keep_awake.ES_CONTINUOUS for flags in power.calls)


def test_local_mode_still_keeps_the_computer_awake(local):
    power = _Power()
    awake = keep_awake.KeepAwake(power, poll=0.01)
    awake.apply(enabled=True, on_battery=True, members=True)
    try:
        assert awake.active
    finally:
        awake.stop()


def test_cloud_never_shows_a_windows_notification(cloud):
    shown = []
    module = ModuleType("windows_toasts")
    module.Toast = lambda fields: shown.append(fields)
    cloud.setitem(sys.modules, "windows_toasts", module)
    cloud.setattr(toast, "_WINDOWS", True)
    assert toast.show("T", "B") is False
    assert shown == []


def _updater(**overrides):
    calls = []
    memory = SimpleNamespace(record=None)
    kwargs = dict(busy=lambda: False, stop_work=lambda: calls.append("stop"), work_stopped=lambda: True,
                  resume_work=lambda: calls.append("resume"), start_download=lambda v, u: calls.append(("download", v)),
                  install=lambda path: calls.append(("install", path)), memory=memory, exists=lambda path: True)
    kwargs.update(overrides)
    return auto_update.AutoUpdate(**kwargs), calls


def test_cloud_takes_no_update_offer(cloud):
    updater, calls = _updater()
    assert updater.offer("9.9.9", "/download/cc.exe") is False
    assert updater.pending is None
    updater.tick()
    assert calls == []


def test_local_mode_still_takes_the_update_offer(local):
    updater, _calls = _updater()
    assert updater.offer("9.9.9", "/download/cc.exe") is True


def test_cloud_never_writes_autostart(cloud):
    touched = []
    cloud.setattr(autostart, "_winreg", lambda: touched.append("registry"))
    cloud.setattr(autostart.sys, "frozen", True, raising=False)
    assert autostart.enable() == ""
    assert autostart.repoint() is False
    assert touched == []


def test_local_mode_still_writes_autostart(local):
    written = []

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake = SimpleNamespace(HKEY_CURRENT_USER=object(), KEY_SET_VALUE=2, REG_SZ=1,
                           CreateKeyEx=lambda *a: _Key(), SetValueEx=lambda key, name, _r, _t, cmd: written.append(name))
    local.setattr(autostart, "_winreg", lambda: fake)
    local.setattr(autostart.package_identity, "packaged", lambda: False)
    assert autostart.enable("cc.exe --background") == "cc.exe --background"
    assert written == [autostart._VALUE_NAME]


def test_cloud_never_starts_folder_intake(cloud):
    intake = folder_worker.FolderIntake(lambda: [_member("p-mrerp", kind="folder")])
    intake.wanted = lambda: pytest.fail("cloud asked whether folders are wanted")
    intake.start()
    assert not intake.running()


def test_cloud_never_runs_mrerp_members_or_mrerp_operator(cloud):
    cloud.setenv(payload_contract.MRERP_OPERATOR_ENV, "1")
    assert payload_contract.mrerp_operator_enabled() is False
    assert "mrerp" not in payload_contract.operator_targets()
    assert polls_queue(SimpleNamespace(kind="folder", pool=False)) is False
    assert runtime_mode.serves(SimpleNamespace(kind="folder")) is False
    assert runtime_mode.serves(SimpleNamespace(kind="express")) is True


def test_local_mode_keeps_the_mrerp_switch(local):
    local.setenv(payload_contract.MRERP_OPERATOR_ENV, "1")
    assert payload_contract.mrerp_operator_enabled() is True
    assert polls_queue(SimpleNamespace(kind="folder", pool=False)) is True
    assert runtime_mode.serves(SimpleNamespace(kind="folder")) is True


def test_the_cloud_path_never_imports_tray_window_or_folder_modules():
    """直写循环那条路（main 的 step_cloud_loop / step_poll_loop）不导入托盘、窗口、文件夹、防睡眠、自动更新。"""
    import ast

    source = (Path(main.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned = {"gui_tray", "window_main", "folder_worker", "keep_awake", "auto_update", "updater", "toast", "autostart"}
    for fn in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
               and node.name in ("step_cloud_loop", "step_poll_loop")):
        imported = {alias.name for node in ast.walk(fn) if isinstance(node, ast.ImportFrom) for alias in node.names}
        imported |= {(node.module or "").rsplit(".", 1)[-1] for node in ast.walk(fn) if isinstance(node, ast.ImportFrom)}
        assert not (imported & banned), (fn.name, imported & banned)


# ── 找 Express 数据根：云端不碰 \\tsclient 与局域网共享 ─────────────────


def _registry(root: Path, companies: int = 1) -> None:
    import dbf

    secure = root / "secure"
    secure.mkdir(parents=True, exist_ok=True)
    table = dbf.Table(str(secure / "SCCOMP.DBF"), "COMPCOD C(3)", codepage="cp874")
    table.open(dbf.READ_WRITE)
    for index in range(companies):
        table.append((f"{index:03d}",))
    table.close()


def _scan_setup(monkeypatch, tmp_path, *, client_drive: Path, share: Path, local_drive: Path):
    scanned = []
    monkeypatch.delenv("PEARNLY_EXPRESS_ROOT", raising=False)
    monkeypatch.setattr(express_locate, "_registry_install_dirs", lambda: [])
    monkeypatch.setattr(express_locate, "_shortcut_candidate_roots", lambda: [])
    monkeypatch.setattr(express_locate, "_mounted_drive_roots", lambda: [str(local_drive), str(client_drive)])
    monkeypatch.setattr(express_locate, "_SHARE_PARENTS", (str(share),))
    monkeypatch.setattr(express_locate, "_network_share_roots", lambda: [r"\\tsclient\C"])
    monkeypatch.setattr(express_locate, "_user_folders", lambda: [])
    # 映射到客户电脑那个盘的「盘符」：测试里用目录代替，按它映射到 \\tsclient\C 回报
    monkeypatch.setattr(express_locate, "_drive_remote",
                        lambda root: r"\\tsclient\C" if str(root).startswith(str(client_drive)) else "")
    real_deep = express_locate._deep_scan

    def deep(parents, *args, **kwargs):
        scanned.extend(parents)
        return real_deep(parents, *args, **kwargs)

    monkeypatch.setattr(express_locate, "_deep_scan", deep)
    return scanned


def test_cloud_scans_only_this_hosts_drives(cloud, tmp_path):
    local_drive, client_drive, share = tmp_path / "D", tmp_path / "client", tmp_path / "fileserver"
    _registry(local_drive / "CLOUDUSER" / "ExpressI-Cloud", 3)
    _registry(client_drive / "70EXP", 5)
    _registry(share / "69EXP", 4)
    scanned = _scan_setup(cloud, tmp_path, client_drive=client_drive, share=share, local_drive=local_drive)

    found = express_locate.candidate_roots()
    assert found == [str(local_drive / "CLOUDUSER" / "ExpressI-Cloud")]
    assert scanned == [str(local_drive)]   # 客户电脑的盘、局域网共享、\\tsclient 都不去深扫


def test_cloud_drops_tsclient_paths_from_every_source(cloud, tmp_path):
    cloud.setenv("PEARNLY_EXPRESS_ROOT", r"\\tsclient\C\70EXP")
    cloud.setattr(express_locate, "_registry_install_dirs", lambda: [r"\\TSCLIENT\D\Express"])
    cloud.setattr(express_locate, "_shortcut_candidate_roots", lambda: [r"\\tsclient\X\dist"])
    cloud.setattr(express_locate, "_mounted_drive_roots", lambda: [])
    cloud.setattr(express_locate, "_user_folders", lambda: [])
    cloud.setattr(express_locate, "_network_share_roots", lambda: pytest.fail("cloud read the LAN shares"))
    seen = []
    cloud.setattr(express_locate, "_scan_exp_roots", lambda parents: seen.extend(parents) or [])
    cloud.setattr(express_locate, "_deep_scan", lambda parents, **kw: seen.extend(parents) or [])
    assert express_locate.candidate_roots() == []
    assert seen == []


def test_local_mode_still_scans_shares_and_mapped_drives(local, tmp_path):
    local_drive, client_drive, share = tmp_path / "D", tmp_path / "client", tmp_path / "fileserver"
    _registry(local_drive / "EXP70", 3)
    _registry(client_drive / "70EXP", 5)
    _registry(share / "69EXP", 4)
    scanned = _scan_setup(local, tmp_path, client_drive=client_drive, share=share, local_drive=local_drive)

    found = express_locate.candidate_roots()
    assert set(found) == {str(local_drive / "EXP70"), str(client_drive / "70EXP"), str(share / "69EXP")}
    assert scanned == [str(local_drive), str(client_drive), str(share), r"\\tsclient\C"]


def test_the_parameter_overrides_the_mode(local, tmp_path):
    local_drive, client_drive, share = tmp_path / "D", tmp_path / "client", tmp_path / "fileserver"
    _registry(local_drive / "EXP70", 3)
    _registry(share / "69EXP", 4)
    _scan_setup(local, tmp_path, client_drive=client_drive, share=share, local_drive=local_drive)
    assert express_locate.candidate_roots(local_only=True) == [str(local_drive / "EXP70")]


def test_client_side_paths_are_recognised():
    assert express_locate._client_side(r"\\tsclient\C\70EXP")
    assert express_locate._client_side("//TSCLIENT/x/dist")
    assert not express_locate._client_side(r"D:\CLOUDUSER\ExpressI-Cloud")
    assert not express_locate._client_side(r"\\fileserver\ACCOUNT")


# ── 心跳 link ──────────────────────────────────────────────────────────


def _heartbeat_bodies(monkeypatch, calls):
    monkeypatch.setattr(queue_client, "_device_name", lambda: "CLOUD-HOST")
    raw = []

    def handler(request):
        raw.append(request.content)
        return httpx.Response(200, json={"message": {"ok": True}})

    client = queue_client.QueueClient(queue_client.QueueClientConfig("https://" + SITE, "exp_CWEC-1_secret", "c1"))
    client._client = httpx.Client(base_url="https://" + SITE, transport=httpx.MockTransport(handler))
    for kwargs in calls:
        client.health(**kwargs)
    return raw


CALLS = (
    {"companion_version": "1.12.22", "max_payload_version": 21, "operator_targets": ["express"],
     "selected": {"account_set": "A", "account_dir": "D:\\A", "account_company": "C", "account_set_row": 1,
                  "express_root": "D:\\"}},
    {"offline": True},
)


def test_cloud_heartbeat_carries_the_link(cloud):
    cloud.setenv(runtime_mode.VIA_ENV, "OFFICE-PC")
    online, offline = [json.loads(body) for body in _heartbeat_bodies(cloud, CALLS)]
    assert online["link"] == {"kind": "cloud", "via": "OFFICE-PC", "host": "CLOUD-HOST"}
    assert "link" not in offline   # 优雅退出那一下只说离线（服务端离线时也不改 link）


def test_cloud_without_a_bridge_name_sends_no_link(cloud):
    online, _offline = [json.loads(body) for body in _heartbeat_bodies(cloud, CALLS)]
    assert "link" not in online


def test_bridge_name_is_cleaned_and_capped(cloud):
    cloud.setenv(runtime_mode.VIA_ENV, "  OFFICE\x07-PC" + "x" * 100 + "  ")
    online, _offline = [json.loads(body) for body in _heartbeat_bodies(cloud, CALLS)]
    assert online["link"]["via"] == ("OFFICE-PC" + "x" * 100)[: runtime_mode.TEXT_MAX]


def test_local_heartbeat_is_byte_for_byte_unchanged(local):
    plain = _heartbeat_bodies(local, CALLS)
    local.setenv(runtime_mode.VIA_ENV, "OFFICE-PC")   # 本地模式下有桥电脑名也不带
    with_name = _heartbeat_bodies(local, CALLS)
    assert plain == with_name
    # 键与顺序都和加 link 之前一样（序列化的空格写法随 httpx 版本，这里按解析后的键值与顺序比）
    expected = [("companion_version", "1.12.22"), ("max_payload_version", 21), ("operator_targets", ["express"]),
                ("device", "CLOUD-HOST"), ("account_set", "A"), ("account_dir", "D:\\A"), ("account_company", "C"),
                ("account_set_row", 1), ("express_root", "D:\\")]
    assert list(json.loads(plain[0]).items()) == expected
    assert json.loads(plain[1]) == {"offline": True}
