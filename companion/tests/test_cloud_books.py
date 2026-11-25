"""云端找账套、状态文件、重新查找 / 添加文件夹（第二阶段 D4）：云端报 ``ctl\\status.json``（会话加密，内容变了才写），桥读来给设置页；
「重新查找」「添加文件夹」经 ``ctl`` 交云端那份做，带编号、做过不再做；手填路径不许在 ``\\tsclient`` 下、要在、下面要找得到登记表；
另加直接探 ``<盘>:\\<用户名>\\`` 与其下一层（不列盘根）。离线测：盘、登记表、远程桌面都是假的。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from companion.cloud_link import cloud_books, cloud_members, cloud_side, ctl_files, settings_link
from companion.cloud_link import tunnel_protocol as protocol

from test_cloud_host import CORE_TOKEN, HOST, _host, _saved
from test_cloud_members import SITE_ERP, _Protector

ROOT = r"D:\CLOUDUSER01\ExpressI-Cloud"


def _link(tmp_path):
    for name in ("in", "out", "ctl"):
        (tmp_path / name).mkdir(exist_ok=True)
    return cloud_side.CloudLink(tmp_path, protocol.new_session(), take=lambda root: None)


# ── 找账套：用户名目录 ─────────────────────────────────────────────────────────

def test_user_dirs_list_only_the_users_own_folder_and_one_level_below():
    dirs = {"D:\\CLOUDUSER01": ["ExpressI-Cloud", "notes.txt"], "C:\\CLOUDUSER01": None}
    listed = []

    def listdir(path):
        listed.append(path)
        return dirs[path]

    found = cloud_books.user_dirs(user="CLOUDUSER01", drives=lambda: ["C:\\", "D:\\"], listdir=listdir,
                                  is_dir=lambda p: p in dirs and dirs[p] is not None or p.endswith("ExpressI-Cloud"))
    assert found == ["D:\\CLOUDUSER01", ROOT]
    assert listed == ["D:\\CLOUDUSER01"]   # 盘根从不列


@pytest.mark.parametrize("user", ["", "..\\x", "a/b", "C:"])
def test_user_dirs_refuse_odd_user_names(user):
    assert cloud_books.user_dirs(user=user, drives=lambda: ["D:\\"], listdir=lambda p: pytest.fail("listed"),
                                 is_dir=lambda p: True) == []


def test_user_dirs_skip_folders_that_cannot_be_listed():
    def listdir(path):
        raise PermissionError(path)

    assert cloud_books.user_dirs(user="u", drives=lambda: ["D:\\"], listdir=listdir, is_dir=lambda p: True) == ["D:\\u"]


# ── 手填路径 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, code", [
    ("", cloud_books.BAD_PATH),
    ("relative\\x", cloud_books.BAD_PATH),
    ("D:\\" + "x" * 300, cloud_books.BAD_PATH),
    ("D:\\a\x07b", cloud_books.BAD_PATH),
    ("\\\\tsclient\\C\\Express", cloud_books.NOT_ALLOWED),
    ("D:\\missing", cloud_books.BAD_PATH),
    (ROOT, cloud_books.OK),
])
def test_check_place(path, code):
    assert cloud_books.check_place(path, exists=lambda p: p == ROOT) == code


def test_add_place_uses_found_roots_and_records_the_result_with_its_id(tmp_path):
    link = _link(tmp_path)
    link.last_members = [ctl_files.member_entry(SITE_ERP, "core")]
    synced = []

    def sync(members, detect):
        synced.append((members, detect(None)))
        return cloud_members.READY, True

    load = lambda: SimpleNamespace(profiles=())  # noqa: E731
    code = cloud_books.add_place(link, ROOT, "a" * 32, load=load, find_under=lambda p: [p], sync=sync,
                                 check=lambda p: cloud_books.OK)
    assert code == cloud_books.OK and link.add_result == {"id": "a" * 32, "code": "ok"}
    assert synced == [([{"site": SITE_ERP, "channel": "core", "endpoint": ""}], [ROOT])]
    assert link.members_code == cloud_members.READY and link.generation == 1


def test_add_place_reports_not_found_and_changes_nothing(tmp_path):
    link = _link(tmp_path)
    code = cloud_books.add_place(link, ROOT, "b" * 32, load=lambda: pytest.fail("loaded"), find_under=lambda p: [],
                                 sync=lambda *a, **k: pytest.fail("synced"), check=lambda p: cloud_books.OK)
    assert code == cloud_books.NOT_FOUND and link.add_result["code"] == "not_found"
    code = cloud_books.add_place(link, "\\\\tsclient\\C", "c" * 32, find_under=lambda p: pytest.fail("searched"))
    assert code == cloud_books.NOT_ALLOWED


def test_rescan_with_members_writes_the_roots_into_them(tmp_path, monkeypatch):
    from companion import express_machine

    link = _link(tmp_path)
    store = SimpleNamespace(profiles=(SimpleNamespace(profile_id="cloud-x", kind="express"),))
    applied = []
    monkeypatch.setattr(express_machine, "apply_roots", lambda s, roots: applied.append(roots) or 1)
    assert cloud_books.rescan(link, load=lambda: store, find=lambda s: [ROOT], clock=lambda: 1700000000) == "found"
    assert applied == [[ROOT]] and link.scanned_at == 1700000000
    assert cloud_books.rescan(link, load=lambda: store, find=lambda s: []) == "not_found"
    assert applied == [[ROOT]]


# ── 状态文件 ─────────────────────────────────────────────────────────────────

def test_status_is_sealed_with_the_session_and_rewritten_only_when_it_changes(tmp_path):
    link = _link(tmp_path)
    link.members_code = "ready"
    places = [{"path": ROOT, "ledgers": 12}]
    payload = lambda lk, store: cloud_books.status_payload(lk, store, places_of=lambda s: places, version="1.12.26")  # noqa: E731
    path = link.ctl_dir / ctl_files.STATUS_FILE
    assert cloud_books.write_status(link, load=lambda: None, payload_of=payload) is True
    got = ctl_files.open_(path.read_bytes(), link.session, "status")[1]
    assert got == {"version": "1.12.26", "members": "ready", "places": places, "scan": "", "scanned_at": 0, "add": {}}
    path.unlink()   # 桥读走了
    assert cloud_books.write_status(link, load=lambda: None, payload_of=payload) is False
    places.append({"path": "E:\\x", "ledgers": 1})
    assert cloud_books.write_status(link, load=lambda: None, payload_of=payload) is True
    assert cloud_books.write_status(link, load=lambda: None, payload_of=payload, force=True) is True


def test_report_status_waits_five_minutes_unless_forced(tmp_path):
    link = _link(tmp_path)
    writes = []
    write = lambda lk, force: writes.append(force) or True  # noqa: E731
    assert cloud_side.report_status(link, write=write, clock=lambda: 100.0) is True
    assert cloud_side.report_status(link, write=write, clock=lambda: 200.0) is False
    assert cloud_side.report_status(link, force=True, write=write, clock=lambda: 200.0) is True
    assert cloud_side.report_status(link, write=write, clock=lambda: 200.0 + cloud_books.STATUS_EVERY) is True
    assert writes == [False, True, False]


# ── 命令文件 ─────────────────────────────────────────────────────────────────

def test_commands_run_once_per_id_and_old_session_files_are_dropped(tmp_path):
    link = _link(tmp_path)
    ran = []
    run = lambda lk, kind, payload, item_id: ran.append((kind, payload, item_id))  # noqa: E731
    rescan = link.ctl_dir / ctl_files.RESCAN_FILE
    add = link.ctl_dir / ctl_files.ADD_PLACE_FILE
    rescan.write_bytes(ctl_files.seal(link.session, "rescan", {}, item_id="1" * 32))
    add.write_bytes(ctl_files.seal(link.session, "add_place", {"path": ROOT}, item_id="2" * 32))
    assert cloud_side.take_commands(link, run=run) == ["rescan", "add_place"]
    assert ran == [("rescan", {}, "1" * 32), ("add_place", {"path": ROOT}, "2" * 32)]
    assert not rescan.exists() and not add.exists()

    rescan.write_bytes(ctl_files.seal(link.session, "rescan", {}, item_id="1" * 32))   # 同一编号再来一次
    rescan_old = ctl_files.seal(protocol.new_session(), "rescan", {}, item_id="3" * 32)
    assert cloud_side.take_commands(link, run=run) == []
    rescan.write_bytes(rescan_old)
    assert cloud_side.take_commands(link, run=run) == []
    assert len(ran) == 2


def test_check_ctl_reports_status_at_once_after_a_new_session(tmp_path):
    link = _link(tmp_path)
    new = protocol.new_session()
    link._take = lambda root: new
    forced = []
    cloud_side.check_ctl(link, members=lambda lk: None, commands=lambda lk: [],
                         status=lambda lk, force: forced.append(force))
    link._take = lambda root: None
    cloud_side.check_ctl(link, members=lambda lk: None, commands=lambda lk: [],
                         status=lambda lk, force: forced.append(force))
    assert forced == [True, False]


# ── 桥：读状态、发命令、设置页 ─────────────────────────────────────────────────

def test_the_bridge_reads_the_cloud_status_and_shows_places(tmp_path):
    _saved(tmp_path).set_token(HOST, SITE_ERP, "https://" + SITE_ERP, CORE_TOKEN)
    host, *_ = _host(tmp_path)
    host.start()
    status = {"version": "1.12.26", "members": "ready", "places": [{"path": ROOT, "ledgers": 12}], "scan": "found",
              "scanned_at": 1, "add": {"id": "a" * 32, "code": "ok"}}
    (host.ctl_dir / ctl_files.STATUS_FILE).write_bytes(ctl_files.seal(host._session, "status", status))
    host.pump()
    assert host.cloud_state == status and not (host.ctl_dir / ctl_files.STATUS_FILE).exists()
    shown = settings_link.status(_saved(tmp_path), None, host.cloud_state)
    assert shown["places"] == [{"path": ROOT, "ledgers": 12}]
    assert shown["cloud"] == {"version": "1.12.26", "members": "ready", "scan": "found", "scanned_at": 1,
                              "add": {"id": "a" * 32, "code": "ok"}}
    (host.ctl_dir / ctl_files.STATUS_FILE).write_bytes(ctl_files.seal(protocol.new_session(), "status", {}))
    assert host.pump_state() is False and host.cloud_state == status   # 旧会话的不认
    host.stop()
    assert host.cloud_state is None


def test_the_bridge_sends_commands_sealed_with_the_session(tmp_path):
    _saved(tmp_path)
    host, *_ = _host(tmp_path)
    with pytest.raises(Exception):
        host.request("rescan")
    host.start()
    item_id = host.request("add_place", {"path": ROOT})
    got = ctl_files.open_((host.ctl_dir / ctl_files.ADD_PLACE_FILE).read_bytes(), host._session, "add_place")
    assert got == (item_id, {"path": ROOT})
    host.stop()
    assert not (host.ctl_dir / ctl_files.ADD_PLACE_FILE).exists()


def test_status_without_cloud_state_keeps_the_old_shape(tmp_path):
    assert settings_link.status(_saved(tmp_path))["places"] == []
    assert "cloud" not in settings_link.status(_saved(tmp_path))
    assert settings_link.places_of({"places": [{"path": "x", "ledgers": "no"}, {"path": ""}, "junk"]}) == [
        {"path": "x", "ledgers": -1}]


def test_bridge_command_actions(tmp_path):
    from companion import tomo_bridge

    bridge = tomo_bridge.TomoBridge.__new__(tomo_bridge.TomoBridge)
    bridge._cloud_host = None
    assert bridge._cloud_command("cloud_rescan", {}) == {"ok": False, "code": "not_running"}
    sent = []
    bridge._cloud_host = SimpleNamespace(running=True, request=lambda kind, payload: sent.append((kind, payload)) or "i")
    assert bridge._cloud_command("cloud_rescan", {}) == {"ok": True, "id": "i"}
    assert bridge._cloud_command("cloud_add_place", {"path": " " + ROOT + " "}) == {"ok": True, "id": "i"}
    assert bridge._cloud_command("cloud_add_place", {"path": "x" * 300}) == {"ok": False, "code": "invalid"}
    assert sent == [("rescan", {}), ("add_place", {"path": ROOT})]
