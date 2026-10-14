"""公司的「选择文件夹…」（2026-10-15，CC 按公司分组；规格 §7、§14 C 节）：

- 判定（folder_plan.decide_pick / too_broad，纯函数，按 Windows 路径写）：复用已有根目录、包含已有根目录、程序文件夹、
  整块盘与系统位置、网络共享根、上限；
- 本机设置（desktop_settings.pick_folder）：站点与连接核对、复用回拥有者的读者、新加的根目录记公司来源并马上快速同步、
  先清掉已不属于任何成员的连接；
- roots.json（folder_store）：保留额外键、旧文件照读、加 / 删一个根目录；
- 文件夹线程（folder_worker）：快速同步只给新根目录带下级文件夹、唤醒、按 max_roots 封顶、自动收回只收公司来源且连续
  一小时没人用的；
- 桥（tomo_bridge，要 PySide6）：不收网页传来的路径、features 与 legacy_roots。
"""
import base64
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from companion import folder_plan as plan
from companion import folder_store
from companion.desktop_settings import DesktopSettings
from companion.folder_worker import FolderIntake
from companion.i18n import t
from companion.profile_store import ProfileStore, ProfileStoreError

PROGRAM = "C:\\Users\\a\\AppData\\Local\\Programs\\Pearnly Companion"
ENV = {"SystemRoot": "C:\\Windows", "ProgramFiles": "C:\\Program Files", "ProgramFiles(x86)": "C:\\Program Files (x86)",
       "ProgramData": "C:\\ProgramData", "APPDATA": "C:\\Users\\a\\AppData\\Roaming",
       "LOCALAPPDATA": "C:\\Users\\a\\AppData\\Local", "USERPROFILE": "C:\\Users\\a"}


# ── 判定（纯函数） ────────────────────────────────────────────────────────────────


def _rows(*paths):
    return [{"id": plan.root_id(path), "label": path, "path": path} for path in paths]


def test_same_or_inside_an_existing_root_reuses_it_with_the_owner_and_relative_folder():
    by_key = {"express": _rows("D:\\Clients"), "mrerp": _rows("E:\\Scans")}
    same = plan.decide_pick("d:\\clients", by_key, env=ENV)
    assert same.code == "" and same.owner == "express" and same.folder == "" and same.root["path"] == "D:\\Clients"
    inner = plan.decide_pick("E:\\Scans\\ABC Co\\2026", by_key, env=ENV)
    assert (inner.code, inner.owner, inner.folder) == ("", "mrerp", "ABC Co\\2026")   # 保留原来的大小写，\\ 分隔
    assert inner.root["id"] == plan.root_id("E:\\Scans")


def test_a_folder_containing_an_existing_root_is_refused_with_its_name():
    decision = plan.decide_pick("D:\\", {"k": _rows("D:\\Clients\\ABC")}, env={})
    assert decision.code == "too_broad"   # 盘根先按整块盘拒绝
    decision = plan.decide_pick("D:\\Clients", {"k": _rows("D:\\Clients\\ABC")}, env=ENV)
    assert decision.code == "contains_existing" and decision.conflict == "D:\\Clients\\ABC"


def test_the_program_folder_and_folders_containing_it_are_refused():
    assert plan.decide_pick(PROGRAM + "\\_internal", {}, program=PROGRAM, env=ENV).code == "program_folder"
    assert plan.decide_pick(PROGRAM.upper(), {}, program=PROGRAM, env=ENV).code == "program_folder"
    portable = "D:\\Tools\\CC"
    assert plan.decide_pick("D:\\Tools", {}, program=portable, env=ENV).code == "program_folder"
    assert plan.decide_pick("D:\\Tools2", {}, program=portable, env=ENV).code == ""


@pytest.mark.parametrize("path", [
    "C:\\", "D:\\", "D:", "C:/", "C:\\Windows", "C:\\Windows\\System32", "C:\\Program Files\\Acme",
    "C:\\Program Files (x86)", "C:\\ProgramData\\X", "C:\\Users\\a\\AppData\\Roaming\\Pearnly",
    "C:\\Users\\a\\AppData\\Local", "C:\\Users\\a", "c:\\users\\A", "C:\\Users", "\\\\nas",
])
def test_whole_drives_system_folders_appdata_and_the_home_folder_are_too_broad(path):
    assert plan.too_broad(path, ENV)
    assert plan.decide_pick(path, {}, env=ENV).code == "too_broad"


@pytest.mark.parametrize("path", [
    "\\\\nas\\ClientA", "\\\\nas\\ClientA\\2026", "//nas/ClientA", "C:\\Users\\a\\Documents", "C:\\Users\\a\\Desktop\\ABC",
    "D:\\Clients\\ABC",
])
def test_network_share_roots_and_ordinary_folders_are_allowed(path):
    assert not plan.too_broad(path, ENV)
    assert plan.decide_pick(path, {}, env=ENV).code == ""


def test_the_root_limit_follows_max_roots_and_defaults_to_twenty():
    full = {"k": _rows(*[f"D:\\C{index}" for index in range(20)])}
    assert plan.DEFAULT_MAX_ROOTS == 20
    assert plan.decide_pick("D:\\New", full, env=ENV).code == "roots_full"
    assert plan.decide_pick("D:\\New", full, max_roots=200, env=ENV).code == ""
    # 已满时选已有根目录里面的仍可复用（不新增授权）
    assert plan.decide_pick("D:\\C3\\x", full, env=ENV).owner == "k"


def test_folder_names_and_the_folder_total_cap():
    assert plan.folder_name("D:\\Clients\\ABC Co") == "ABC Co"
    assert plan.folder_name("\\\\nas\\客户A") == "客户A"
    assert plan.FOLDERS_TOTAL == 20_000
    lines = [{"id": "a", "folders": ["x"] * 3}, {"id": "b"}, {"id": "c", "folders": ["y"] * 3}, {"id": "d", "folders": ["z"]}]
    plan.cap_folders(lines, total=4)
    assert [len(line.get("folders", "-")) for line in lines] == [3, 1, 1, 0]
    assert "folders" not in lines[1]   # 快速同步里不带 folders 键的旧根目录不动


# ── roots.json ──────────────────────────────────────────────────────────────────


def test_set_all_roots_keeps_the_company_origin_of_unchanged_rows_and_old_files_still_read(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    added = folder_store.add_root("k", str(a), origin="company")
    assert added["origin"] == "company" and added["added_at"] > 0
    folder_store.set_roots("k", [str(a), str(b)])   # 旧设置页加一个：原来那行的来源不丢
    rows = {row["path"]: row for row in folder_store.roots("k")}
    assert rows[os.path.normpath(str(a))]["origin"] == "company"
    assert "origin" not in rows[os.path.normpath(str(b))]
    # 旧版写的 roots.json（每行只有 id / label / path）照读
    state = folder_store._dir() / folder_store.ROOTS_FILE
    state.write_text(json.dumps({"old": [{"id": "r1", "label": "L", "path": str(tmp_path / "Old")}]}), encoding="utf-8")
    assert folder_store.roots("old") == [{"id": "r1", "label": "L", "path": str(tmp_path / "Old")}]


def test_add_root_refuses_overlap_and_remove_root_drops_one(tmp_path):
    folder_store.add_root("k1", str(tmp_path / "A"), origin="company")
    with pytest.raises(ValueError):
        folder_store.add_root("k2", str(tmp_path / "A" / "inner"), origin="company")
    second = folder_store.add_root("k1", str(tmp_path / "B"))
    assert "origin" not in second
    assert folder_store.remove_root("k1", second["id"]) is True
    assert folder_store.remove_root("k1", second["id"]) is False
    assert [row["path"] for row in folder_store.roots("k1")] == [os.path.normpath(str(tmp_path / "A"))]


# ── 本机设置：pick_folder ──────────────────────────────────────────────────────────


class _Protector:
    prefix = "test:"

    def protect(self, value: str) -> str:
        return self.prefix + base64.b64encode(value.encode()).decode()

    def unprotect(self, value: str) -> str:
        if not value.startswith(self.prefix):
            raise ProfileStoreError("bad test ciphertext")
        return base64.b64decode(value[len(self.prefix):]).decode()


SITE = "testcowork.pearnly.com"


def _store(tmp_path, members=("express",)):
    store = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    for index, kind in enumerate(members):
        cfg = store.new_runtime_config()
        cfg.update(backend_url=f"https://{SITE}", bearer_token=f"exp_conn-{kind}-{index}_secret", paired=True,
                   pearnly_account=f"{kind}@example.com", ui_lang="zh", kind=kind)
        if kind != "folder":
            cfg.update(account_dir=r"S:\\EXP68\\ACME", account_set=r"S:\\EXP68\\ACME", account_company="Acme Co.",
                       express_root=r"S:\\EXP68", authorized_roots=[r"S:\\EXP68"])
        store.save_profile(cfg)
    return ProfileStore.load(store.path, protector=_Protector())


def _key(store, kind):
    record = next(r for r in store.profiles if r.kind == kind)
    return record.profile_id, folder_store.connection_key(store.runtime_config(record.profile_id))


class _Calls:
    def __init__(self, reported=True):
        self.reported, self.log = reported, []

    def kwargs(self, **extra):
        return {"report_roots": self.report, "wake_folders": lambda: self.log.append("wake"),
                "on_folders_changed": lambda: self.log.append("changed"), "program": lambda: "",
                # Windows CI 的临时目录在 AppData\Local 下：判定系统位置用一份空的环境变量
                "env": {}, **extra}

    def report(self, profile_id):
        self.log.append(("report", profile_id))
        if isinstance(self.reported, Exception):
            raise self.reported
        return self.reported


def _settings(store, calls, **extra):
    return DesktopSettings(load_store=lambda: ProfileStore.load(store.path, protector=_Protector()),
                           **calls.kwargs(**extra))


def test_not_connected_and_another_site_are_refused_before_the_dialog(tmp_path):
    empty = ProfileStore.load(tmp_path / "companion.json", protector=_Protector())
    calls = _Calls()
    assert _settings(empty, calls).pick_ready(SITE, "zh") == {
        "ok": False, "code": "not_connected", "message": t("folders_not_connected", "zh")}
    store = _store(tmp_path)
    settings = _settings(store, calls)
    assert settings.pick_ready(SITE, "th") is None
    other = settings.pick_ready("other.pearnly.com", "th")
    assert other == {"ok": False, "code": "other_site", "message": t("folders_other_site", "th")}
    # 后台判定时再核一次（选择框开着时页面换了站点）
    assert settings.pick_folder(str(tmp_path / "ABC"), "other.pearnly.com", "zh")["code"] == "other_site"
    assert calls.log == []


def test_a_new_folder_is_added_under_the_express_member_and_reported_at_once(tmp_path):
    store = _store(tmp_path, members=("folder", "express"))
    profile, key = _key(store, "express")
    calls = _Calls(reported=True)
    picked = tmp_path / "Clients" / "ABC Co"
    picked.mkdir(parents=True)

    result = _settings(store, calls).pick_folder(str(picked), SITE, "zh")

    path = os.path.normpath(str(picked))
    assert result == {"ok": True, "binding": {"reader": "conn-express-1", "root_id": plan.root_id(path), "folder": "",
                                              "label": path, "share": "", "device": result["binding"]["device"]},
                      "name": "ABC Co", "created": True, "reported": True}
    row = folder_store.roots(key)[0]
    assert row["path"] == path and row["origin"] == "company" and time.time() - row["added_at"] < 60
    assert calls.log == ["changed", ("report", profile), "wake"]


def test_a_failed_quick_sync_still_returns_the_binding(tmp_path):
    store = _store(tmp_path)
    for reported in (False, RuntimeError("offline")):
        calls = _Calls(reported=reported)
        result = _settings(store, calls).pick_folder(str(tmp_path / f"X{type(reported).__name__}"), SITE, "zh")
        assert result["ok"] is True and result["created"] is True and result["reported"] is False


def test_a_folder_inside_an_old_root_reuses_it_under_its_owner(tmp_path):
    store = _store(tmp_path, members=("express", "folder"))
    _profile, folder_key = _key(store, "folder")
    old = tmp_path / "Scans"
    (old / "ABC" / "2026").mkdir(parents=True)
    folder_store.set_roots(folder_key, [str(old)])   # 以前在设置里授权给 MR.ERP 成员的
    calls = _Calls()

    result = _settings(store, calls).pick_folder(str(old / "ABC"), SITE, "th")

    assert result["created"] is False and result["reported"] is True and result["name"] == "ABC"
    assert result["binding"]["reader"] == "conn-folder-1"   # 拥有这个根目录的成员，不是主成员
    assert result["binding"]["root_id"] == plan.root_id(os.path.normpath(str(old)))
    assert result["binding"]["folder"] == "ABC"
    assert result["binding"]["label"].endswith("\\ABC")
    assert len(folder_store.roots(folder_key)) == 1 and calls.log == []   # 不新增授权、不同步
    contains = _settings(store, calls).pick_folder(str(tmp_path), SITE, "zh")
    assert contains["code"] == "contains_existing"
    assert contains["message"] == t("folders_contains", "zh", path=os.path.normpath(str(old)))


def test_roots_of_connections_no_longer_paired_are_cleared_first(tmp_path):
    store = _store(tmp_path)
    _profile, key = _key(store, "express")
    gone = tmp_path / "Gone"
    folder_store.set_roots("someone-else", [str(gone)])   # 已移除 / 改配到别家的成员留下的
    result = _settings(store, _Calls()).pick_folder(str(gone / "ABC"), SITE, "zh")
    assert result["ok"] is True and result["created"] is True   # 不再被它挡住，也不复用它
    assert folder_store.roots("someone-else") == []
    assert [row["path"] for row in folder_store.roots(key)] == [os.path.normpath(str(gone / "ABC"))]


def test_the_limit_comes_from_the_folder_thread(tmp_path):
    store = _store(tmp_path)
    _profile, key = _key(store, "express")
    folder_store.set_roots(key, [str(tmp_path / "A")])
    full = _settings(store, _Calls(), max_roots=lambda k: 1).pick_folder(str(tmp_path / "B"), SITE, "zh")
    assert full == {"ok": False, "code": "roots_full", "message": t("folders_full", "zh")}
    assert _settings(store, _Calls(), max_roots=lambda k: 2).pick_folder(str(tmp_path / "B"), SITE, "zh")["ok"]


def test_legacy_roots_list_only_unused_old_grants_after_the_server_answered(tmp_path):
    store = _store(tmp_path)
    profile, key = _key(store, "express")
    folder_store.set_roots(key, [str(tmp_path / "Old1"), str(tmp_path / "Old2")])
    company = folder_store.add_root(key, str(tmp_path / "Company"), origin="company")
    old1, old2 = (plan.root_id(os.path.normpath(str(tmp_path / name))) for name in ("Old1", "Old2"))
    calls = _Calls()
    assert _settings(store, calls).snapshot()["legacy_roots"] == []   # 还没收到 roots_in_use：不列
    used = frozenset({old1})
    snap = _settings(store, calls, roots_in_use=lambda k: used if k == key else None).snapshot()
    assert snap["legacy_roots"] == [{"member": profile, "id": old2, "path": os.path.normpath(str(tmp_path / "Old2"))}]
    assert company["id"] not in {row["id"] for row in snap["legacy_roots"]}   # 经公司添加的不在这里


# ── 文件夹线程 ────────────────────────────────────────────────────────────────────


def _cfg(profile="p1"):
    return SimpleNamespace(backend_url=f"https://{SITE}", bearer_token="exp_CONN1_secret", profile_id=profile,
                           paused=False, paired=True, endpoint_id="", kind="express")


class _Server:
    def __init__(self, **reply):
        self.reply, self.bodies, self.fail = dict(reply), [], None

    def __call__(self, url, token):
        return self

    def close(self):
        pass

    def sync(self, body):
        self.bodies.append(body)
        if self.fail:
            raise self.fail
        return {"ok": True, "server_time": time.time(), "rules": [], "entries": {}, **self.reply}


def test_quick_sync_sends_folders_only_for_new_roots_and_remembers_the_reply(tmp_path):
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    (tmp_path / "Old" / "sub").mkdir(parents=True)
    folder_store.set_roots(key, [str(tmp_path / "Old")])
    server = _Server(max_roots=200, roots_in_use=["r1"])
    worker = FolderIntake(lambda: [cfg], client_factory=server)
    worker.round(cfg, key)   # 完整同步一次：Old 已报过
    assert worker.max_roots(key) == 200 and worker.roots_in_use(key) == frozenset({"r1"})
    (tmp_path / "New" / "a").mkdir(parents=True)
    folder_store.add_root(key, str(tmp_path / "New"), origin="company")
    server.reply = {"max_roots": 150, "roots_in_use": ["r2"]}

    assert worker.report_roots_now("p1") is True

    body = server.bodies[-1]
    assert body["roots_only"] is True and set(body) == {"roots_only", "version", "device", "roots"}
    old, new = body["roots"]
    assert "folders" not in old and new["folders"] == ["a"]
    assert worker.max_roots(key) == 150 and worker.roots_in_use(key) == frozenset({"r2"})
    server.fail = RuntimeError("offline")
    assert worker.report_roots_now("p1") is False
    assert worker.report_roots_now("nobody") is False


def test_reports_are_capped_at_max_roots_and_twenty_without_it(tmp_path):
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    folder_store.set_roots(key, [str(tmp_path / f"R{index:02d}") for index in range(25)])
    server = _Server()   # 旧服务端：不带 max_roots
    worker = FolderIntake(lambda: [cfg], client_factory=server)
    worker.round(cfg, key)
    assert len(server.bodies[-1]["roots"]) == 20 and worker.max_roots(key) == 20
    server.reply = {"max_roots": 200}
    worker.round(cfg, key)   # 这一轮才知道上限
    worker.round(cfg, key)
    assert len(server.bodies[-1]["roots"]) == 25


def test_wake_starts_the_next_round_at_once(tmp_path):
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    folder_store.set_roots(key, [str(tmp_path / "A")])
    server = _Server()
    worker = FolderIntake(lambda: [cfg], client_factory=server)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while len(server.bodies) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        assert len(server.bodies) == 1   # 平时要等 60 秒
        worker.wake()
        deadline = time.monotonic() + 5
        while len(server.bodies) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(server.bodies) == 2
    finally:
        worker.stop()
        worker._thread.join(5)
    assert not worker.running()


def test_auto_release_takes_only_company_roots_unused_for_an_hour(tmp_path):
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    now = [1_000_000.0]
    company = folder_store.add_root(key, str(tmp_path / "Company"), origin="company")
    busy = folder_store.add_root(key, str(tmp_path / "Busy"), origin="company")
    folder_store.set_roots(key, [row["path"] for row in folder_store.roots(key)] + [str(tmp_path / "Legacy")])
    rows = folder_store._read(folder_store.ROOTS_FILE, {})
    for row in rows[key]:
        row["added_at"] = now[0] - 7200 if row.get("origin") else row.get("added_at")
    folder_store._write(folder_store.ROOTS_FILE, rows)
    legacy = next(row for row in folder_store.roots(key) if "origin" not in row)
    changed = []
    server = _Server(roots_in_use=[busy["id"]])
    worker = FolderIntake(lambda: [cfg], client_factory=server, clock=lambda: now[0],
                          on_roots_changed=lambda: changed.append(True))

    def ids():
        return {row["id"] for row in folder_store.roots(key)}

    worker.round(cfg, key)                      # 从这一刻起没人用
    now[0] += 3500
    worker.round(cfg, key)
    assert company["id"] in ids() and changed == []   # 还不到连续一小时
    server.reply = {}                            # 回复里没有 roots_in_use：不收，也不改计时
    now[0] += 200
    worker.round(cfg, key)
    assert company["id"] in ids()
    server.reply = {"roots_in_use": [busy["id"]]}
    worker.round(cfg, key)
    assert ids() == {busy["id"], legacy["id"]} and changed == [True]
    # 旧授权永不自动收回；在用的不收
    now[0] += 10 * 3600
    server.reply = {"roots_in_use": []}
    worker.round(cfg, key)
    now[0] += 2 * 3600
    worker.round(cfg, key)
    assert ids() == {legacy["id"]}


def test_a_root_used_again_restarts_the_hour_and_a_fresh_one_waits_an_hour(tmp_path):
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    now = [time.time()]
    fresh = folder_store.add_root(key, str(tmp_path / "Fresh"), origin="company")   # 刚加（added_at = 现在）
    server = _Server(roots_in_use=[])
    worker = FolderIntake(lambda: [cfg], client_factory=server, clock=lambda: now[0])
    worker.round(cfg, key)
    now[0] += 3000
    server.reply = {"roots_in_use": [fresh["id"]]}   # 绑上了
    worker.round(cfg, key)
    server.reply = {"roots_in_use": []}               # 又没人用了：重新计时
    now[0] += 1000
    worker.round(cfg, key)
    now[0] += 3000
    worker.round(cfg, key)
    assert [row["id"] for row in folder_store.roots(key)] == [fresh["id"]]
    now[0] += 700
    worker.round(cfg, key)
    assert folder_store.roots(key) == []


# ── 桥（要 PySide6；Windows CI 全量跑） ─────────────────────────────────────────────


def test_the_bridge_never_takes_a_path_from_the_page(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from companion import tomo_bridge

    QApplication.instance() or QApplication([])

    class Settings:
        def __init__(self):
            self.calls = []

        def snapshot(self):
            return {"members": [{"id": "m1", "folders": []}], "legacy_roots": [{"member": "m1", "id": "r", "path": "D:\\O"}]}

        def pick_ready(self, host, lang):
            self.calls.append(("ready", host))
            return None if host == SITE else {"ok": False, "code": "other_site", "message": "x"}

        def pick_folder(self, picked, host, lang):
            self.calls.append(("pick", picked, host, lang))
            return {"ok": True, "binding": {}, "name": "ABC", "created": True, "reported": True}

    dialog = []
    monkeypatch.setattr(tomo_bridge.QFileDialog, "getExistingDirectory",
                        lambda *args: dialog.append(args) or "D:/Clients/ABC")
    settings = Settings()
    url = [f"https://{SITE}/assistant"]
    bridge = tomo_bridge.TomoBridge(settings, allowed=lambda: True, window=lambda: None, set_lang=lambda _l: None,
                                    page_url=lambda: url[0])
    state = json.loads(bridge.state())
    assert state["features"] == ["company_folders"] and state["legacy_roots"][0]["id"] == "r"

    done = []
    bridge.done.connect(lambda job, result: done.append((job, json.loads(result))))
    job = json.loads(bridge.call("pick_folder", json.dumps({"lang": "th", "path": "C:\\evil", "folder": "C:\\x"})))["job"]
    deadline = time.monotonic() + 5
    while not done and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert done == [(job, {"ok": True, "binding": {}, "name": "ABC", "created": True, "reported": True})]
    assert settings.calls == [("ready", SITE), ("pick", "D:/Clients/ABC", SITE, "th")]   # 只用选择框里选的
    assert len(dialog) == 1

    url[0] = "https://other.pearnly.com/assistant"
    assert json.loads(bridge.call("pick_folder", "{}"))["code"] == "other_site"
    assert len(dialog) == 1   # 站点不对：不弹选择框

    monkeypatch.setattr(tomo_bridge.QFileDialog, "getExistingDirectory", lambda *args: "")
    url[0] = f"https://{SITE}/assistant"
    assert json.loads(bridge.call("pick_folder", "{}")) == {"ok": False, "cancelled": True}

    refused = tomo_bridge.TomoBridge(settings, allowed=lambda: False, window=lambda: None, set_lang=lambda _l: None)
    assert json.loads(refused.call("pick_folder", "{}"))["refused"] is True


def test_every_pick_failure_code_has_a_message_in_both_languages():
    from companion.desktop_settings import PICK_MESSAGES
    from companion.i18n import _M

    assert set(PICK_MESSAGES) == set(plan.PICK_CODES)
    for key in PICK_MESSAGES.values():
        zh, th = _M[key]
        assert zh and th and "小助手" not in zh
    assert "{path}" in _M["folders_contains"][0] and "{path}" in _M["folders_contains"][1]


def test_threads_are_not_left_running(tmp_path):
    # 上面起的文件夹线程都已停下（守护线程，不挡测试进程退出）
    assert not [thread for thread in threading.enumerate() if thread.name == "pearnly-folder-intake" and thread.is_alive()]
