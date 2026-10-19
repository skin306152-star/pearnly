"""文件夹自动录入（小助手侧）：定时、扫描、上传、结果表格落盘，以及不碰配对配置。"""

import base64
import datetime as dt
import hashlib
import json
import os
import time
from types import SimpleNamespace

import pytest

from companion import folder_plan as plan
from companion import folder_store
from companion.folder_worker import FolderIntake

BKK = dt.timezone(dt.timedelta(hours=7))


def _at(*parts):
    return dt.datetime(*parts, tzinfo=BKK).timestamp()


def test_latest_slot_and_month_end():
    assert plan.latest_slot(_at(2026, 10, 7, 12, 0), 1, "22:00") == _at(2026, 10, 1, 22, 0)
    assert plan.latest_slot(_at(2026, 10, 1, 21, 0), 1, "22:00") == _at(2026, 9, 1, 22, 0)
    assert plan.latest_slot(_at(2026, 3, 1, 0, 0), 0, "23:30") == _at(2026, 2, 28, 23, 30)
    # 选 29 / 31 日：当月没有这一天就在当月最后一天跑（2026-10-09）
    assert plan.latest_slot(_at(2026, 3, 1, 0, 0), 29, "22:00") == _at(2026, 2, 28, 22, 0)
    assert plan.latest_slot(_at(2028, 3, 1, 0, 0), 29, "22:00") == _at(2028, 2, 29, 22, 0)
    assert plan.latest_slot(_at(2026, 5, 1, 0, 0), 31, "22:00") == _at(2026, 4, 30, 22, 0)
    assert plan.latest_slot(_at(2026, 4, 1, 0, 0), 31, "22:00") == _at(2026, 3, 31, 22, 0)


def test_due_runs_once_and_catches_up_only_the_latest():
    rule = {"run_day": 1, "run_time": "22:00", "active_since": _at(2026, 8, 15), "run_request": 0}
    state = {}
    first = plan.due(rule, state, _at(2026, 10, 1, 22, 5))
    assert first.trigger == "schedule"
    state["last_slot"] = first.slot
    assert plan.due(rule, state, _at(2026, 10, 2, 9, 0)) is None
    # 关机错过 11 月、12 月：开机只补最近那一次
    late = plan.due(rule, state, _at(2026, 12, 3, 8, 0))
    assert late.trigger == "catch_up" and late.slot == _at(2026, 12, 1, 22, 0)
    # 规则启用前的时刻不算
    assert plan.due({**rule, "active_since": _at(2026, 10, 2)}, {}, _at(2026, 10, 5)) is None
    # 立即运行优先
    now = plan.due({**rule, "run_request": 2}, {"last_request": 1, "last_slot": late.slot}, _at(2026, 12, 3, 9))
    assert now.trigger == "manual" and now.request == 2


def test_scan_skips_results_hidden_and_other_types(tmp_path):
    folder = tmp_path / "rule"
    (folder / "sub").mkdir(parents=True)
    (folder / plan.RESULT_FOLDER / "2026-10").mkdir(parents=True)
    for name in ("a.pdf", "sub/b.JPG", "c.xlsx", "~$d.pdf", ".e.pdf", "f.tmp",
                 plan.RESULT_FOLDER + "/2026-10/x.pdf"):
        (folder / name).write_bytes(b"x")
    (folder / "empty.pdf").write_bytes(b"")
    for other in plan.RESULT_FOLDERS[1:]:
        (folder / other).mkdir()
        (folder / other / "y.pdf").write_bytes(b"x")
    found = sorted(c.relpath for c in plan.scan(folder))
    assert found == ["a.pdf", "sub\\b.JPG"]
    assert plan.folders_of(folder) == ["sub"]


def test_result_folder_comes_from_the_server_and_falls_back_to_the_old_name(tmp_path):
    assert plan.result_folder("_ผลลัพธ์ Pearnly") == "_ผลลัพธ์ Pearnly"
    assert plan.result_folder(None) == plan.RESULT_FOLDER == "_Pearnly结果"
    assert plan.result_folder("..\\evil") == plan.RESULT_FOLDER
    target = plan.result_path(tmp_path, "2026-10", "a.xlsx", set(), "_Pearnly Results")
    assert target.parent.parent.name == "_Pearnly Results"


def test_new_and_stable():
    c = plan.Candidate("a.pdf", None, 1, mtime=1000.0, created=1000.0)
    assert plan.is_new(c, 1050, clock_offset=0)          # 2 分钟余量
    assert not plan.is_new(c, 1200, clock_offset=0)
    assert plan.is_new(c, 1200, clock_offset=300)        # 本机时钟慢 5 分钟
    assert not plan.is_stable(c, 1100) and plan.is_stable(c, 1121)


def test_result_names_never_collide(tmp_path):
    taken = {"inv1_2808.xlsx"}
    target = plan.result_path(tmp_path, "2026-10", "INV1_2808.xlsx", taken)
    assert target.name == "INV1_2808 (2).xlsx" and target.parent.name == "2026-10"


def _cfg(url="https://a.pearnly.com", token="exp_CONN1_secret", profile="p1"):
    return SimpleNamespace(backend_url=url, bearer_token=token, profile_id=profile, paused=False, paired=True,
                           endpoint_id="", endpoint_name="Express", pearnly_account="acc@x")


def test_roots_are_per_connection_and_cannot_overlap(tmp_path):
    a, b = folder_store.connection_key(_cfg()), folder_store.connection_key(_cfg(token="exp_CONN2_s"))
    assert a and b and a != b
    # 同一条连接换了配对密钥（重置）仍是同一把键；换了租户就不是
    assert folder_store.connection_key(_cfg(token="exp_CONN1_other")) == a
    assert folder_store.connection_key(_cfg(url="https://b.pearnly.com")) != a
    root = tmp_path / "S"
    (root / "inner").mkdir(parents=True)
    folder_store.set_roots(a, [str(root)])
    with pytest.raises(ValueError):
        folder_store.set_roots(b, [str(root / "inner")])
    # 同一条连接里互相包含也不行（同一份文件会被两条规则各收一次）；整体检查失败时什么都不写
    with pytest.raises(ValueError):
        folder_store.set_all_roots({a: [str(root), str(root / "inner")], b: [str(tmp_path / "T")]})
    assert folder_store.roots(b) == []
    assert [row["path"] for row in folder_store.roots(a)] == [os.path.normpath(str(root))]
    assert folder_store.roots(b) == []


class FakeClient:
    def __init__(self, rules, *, route="inline"):
        self.rules, self.route, self.calls = rules, route, []
        self.entries = {}
        self.extra = {}  # 回复里另带的键（notices、companion_latest…）

    def __call__(self, url, token):
        return self

    def close(self):
        pass

    def sync(self, body):
        self.calls.append(("sync", body))
        return {"ok": True, "server_time": time.time(), "rules": self.rules,
                "entries": {name: self.entries[name] for name in body["watch"] if name in self.entries},
                **self.extra}

    def begin(self, body):
        self.calls.append(("begin", body))
        entry = hashlib.sha256(body["relpath"].encode()).hexdigest()
        return {"ok": True, "entry": entry, "state": "registered", "route": self.route, "max_bytes": 1 << 20}

    def finish(self, entry, content=None):
        self.calls.append(("finish", entry, content))
        return {"ok": True, "entry": entry, "state": "uploaded"}

    result_folder = None  # 旧云端不给结果文件夹名

    def workbook(self, entry, history):
        self.calls.append(("workbook", entry, history))
        answer = {"ok": True, "filename": "INV-1_2808.xlsx", "revision": 1,
                  "content": base64.b64encode(b"PK-xlsx").decode()}
        if self.result_folder:
            answer["result_folder"] = self.result_folder
        return answer


@pytest.mark.parametrize("given, expected", [(None, "_Pearnly结果"), ("_ผลลัพธ์ Pearnly", "_ผลลัพธ์ Pearnly")])
def test_round_uploads_new_files_and_writes_result(tmp_path, monkeypatch, given, expected):
    root = tmp_path / "S"
    folder = root / "ice-purchase"
    folder.mkdir(parents=True)
    old = folder / "old.pdf"
    old.write_bytes(b"old")
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    folder_store.set_roots(key, [str(root)])
    root_id = folder_store.roots(key)[0]["id"]
    active_since = time.time() + 10
    new = folder / "new.pdf"
    new.write_bytes(b"new invoice")
    past = time.time() - 600
    os.utime(new, (past, past))
    monkeypatch.setattr(plan, "created_at", lambda stat: active_since + 5 if stat.st_size == len(b"new invoice")
                        else active_since - 3600)
    rule = {"name": "R1", "title": "ice", "root_id": root_id, "folder": "ice-purchase", "run_day": 1,
            "run_time": "22:00", "active_since": active_since, "run_request": 1, "ready": True}
    fake = FakeClient([rule])
    fake.result_folder = given
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    begins = [c for c in fake.calls if c[0] == "begin"]
    assert [c[1]["relpath"] for c in begins] == ["new.pdf"]
    assert begins[0][1]["sha256"] == hashlib.sha256(b"new invoice").hexdigest()
    finish = [c for c in fake.calls if c[0] == "finish"][0]
    assert finish[2] == b"new invoice"
    # 运行记录下一次同步时上报；同一份文件不再传
    entry = hashlib.sha256(b"new.pdf").hexdigest()
    fake.entries[entry] = {"state": "done", "finished": True, "settled": True,
                           "items": [{"history": "H1", "revision": 1, "filename": "INV-1_2808.xlsx"}]}
    worker.round(cfg, key)
    sync_body = [c for c in fake.calls if c[0] == "sync"][-1][1]
    assert sync_body["runs"][0]["new_files"] == 1 and sync_body["runs"][0]["skipped"] == 1
    assert sync_body["rule_health"] == {"R1": "ok"}  # 上一轮看到规则的文件夹在,报给云端
    assert len([c for c in fake.calls if c[0] == "begin"]) == 1
    written = list((folder / expected).rglob("*.xlsx"))
    assert [p.name for p in written] == ["INV-1_2808.xlsx"] and written[0].read_bytes() == b"PK-xlsx"


def test_no_roots_tells_the_cloud_once_then_stays_quiet(tmp_path):
    # 没有文件夹：只报一次空的（启动时 / 刚删光时），云端就不再留着旧文件夹；之后不再联网。
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    fake = FakeClient([])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    assert worker.wanted()
    worker.round(cfg, key)
    assert [c[1]["roots"] for c in fake.calls if c[0] == "sync"] == [[]]
    assert not worker.wanted()
    folder_store.set_roots(key, [str(tmp_path / "S")])
    (tmp_path / "S").mkdir()
    assert worker.wanted()
    worker.round(cfg, key)
    folder_store.set_roots(key, [])
    assert worker.wanted()  # 刚删光：再报一次空的
    worker.round(cfg, key)
    assert [c[1]["roots"] for c in fake.calls if c[0] == "sync"][-1] == []
    assert not worker.wanted()


def test_companion_json_untouched(tmp_path, monkeypatch):
    from companion import config

    marker = tmp_path / "companion.json"
    marker.write_text(json.dumps({"schema_version": 4}))
    monkeypatch.setattr(config, "CONFIG_FILE", marker)
    before = marker.read_bytes()
    key = folder_store.connection_key(_cfg())
    folder_store.set_roots(key, [str(tmp_path / "S")])
    assert marker.read_bytes() == before
    assert not str(folder_store._dir()).endswith("Pearnly")


def _setup_rule(tmp_path, monkeypatch, *, created):
    root = tmp_path / "S"
    folder = root / "ice"
    folder.mkdir(parents=True)
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    folder_store.set_roots(key, [str(root)])
    active_since = time.time() - 3600
    monkeypatch.setattr(plan, "created_at", lambda stat: created.get(stat.st_size, active_since - 86400))
    rule = {"name": "R1", "title": "ice", "root_id": folder_store.roots(key)[0]["id"], "folder": "ice",
            "run_day": 1, "run_time": "22:00", "active_since": active_since, "run_request": 0, "ready": True}
    return cfg, key, folder, rule


def _old(path, data):
    path.write_bytes(data)
    past = time.time() - 600
    os.utime(path, (past, past))


SALES_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".xlsx", ".xlsm", ".csv", ".xls"]


def test_pos_exports_are_sent_only_for_tasks_the_server_gives_them_to(tmp_path, monkeypatch):
    """L9（2026-10-13）：云端给推往 Express 的销售任务下发 POS 导出的扩展名，小助手才扫 Excel / CSV（含旧版 .xls：
    云端登记即记失败、写明原因）；旧云端不下发、进项任务照旧只收 PDF / 图片。各文件夹的 POS 导出另报一份计数。"""
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    for name in ("bill.pdf", "pos-oct.xlsx", "pos-oct.csv", "old.xls"):
        _old(folder / name, name.encode())
    rule.update(existing=1, run_request=1)
    fake = FakeClient([rule])
    FolderIntake(lambda: [cfg], client_factory=fake).round(cfg, key)
    assert [c[1]["relpath"] for c in fake.calls if c[0] == "begin"] == ["bill.pdf"]
    report = [c for c in fake.calls if c[0] == "sync"][0][1]["roots"][0]
    assert (report["counts"], report["table_counts"]) == ({"": 1, "ice": 1}, {"": 3, "ice": 3})
    rule.update(name="R2", run_request=2, extensions=SALES_EXTENSIONS)
    fake = FakeClient([rule])
    FolderIntake(lambda: [cfg], client_factory=fake).round(cfg, key)
    assert sorted(c[1]["relpath"] for c in fake.calls if c[0] == "begin") == ["bill.pdf", "old.xls", "pos-oct.csv",
                                                                               "pos-oct.xlsx"]



def test_old_task_does_not_send_tables_already_there_when_it_starts_taking_them(tmp_path, monkeypatch):
    """老任务（existing=0）按旧扩展名拍过底（1.4，或云端还没下发 extensions）：云端开始给它下发 POS 导出的扩展名后，
    文件夹里早就在的表格（启用前、或启用后会计手工录过的）补进底里、不上传；之后放进来的照常处理，待处理数也不算它们。"""
    created = {}
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created=created)
    _old(folder / "before.pdf", b"before")
    _old(folder / "pos-sep.xlsx", b"pos-sep")                # 启用前就在
    _old(folder / "pos-oct.csv", b"pos-oct-entered-by-hand")  # 启用后放的，任务那时还不收表格
    created[len(b"pos-oct-entered-by-hand")] = rule["active_since"] + 60
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)                                    # 1.4 口径：只按 PDF / 图片拍底
    ledger = folder_store.Ledger(key)
    assert ledger.rule_state("R1")["baseline"] == ["before.pdf"]
    ledger.rule_state("R1").pop("baseline_exts")              # 1.4 拍的底没记扩展名
    ledger.save()
    rule.update(extensions=SALES_EXTENSIONS, run_request=1)
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    assert _begins(fake) == []
    state = folder_store.Ledger(key).rule_state("R1")
    assert state["baseline"] == ["before.pdf", "pos-oct.csv", "pos-sep.xlsx"]
    assert state["baseline_exts"] == SALES_EXTENSIONS[:6] + [".xlsx", ".xlsm", ".csv", ".xls"]
    worker.round(cfg, key)
    assert _syncs(fake)[-1]["rule_pending"] == {"R1": 0}
    _old(folder / "pos-nov.xlsx", b"pos-nov-new")
    created[len(b"pos-nov-new")] = time.time()
    rule["run_request"] = 2
    worker.round(cfg, key)
    assert _begins(fake) == ["pos-nov.xlsx"]


def test_new_task_from_before_1_5_does_not_send_tables_already_there(tmp_path, monkeypatch):
    """1.4.4 时建的新口径任务（existing=1，那时只收 PDF / 图片）：升级到 1.5、云端开始下发 POS 导出的扩展名后，
    文件夹里早就在的表格（可能已手工录过）补进底里、不上传，待处理数也不算；之后放进来的照常处理，PDF 照旧不拍底。"""
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "bill.pdf", b"bill")
    _old(folder / "pos-sep.xlsx", b"pos-sep-entered-by-hand")
    rule.update(existing=1, run_request=1)
    fake = FakeClient([rule])
    FolderIntake(lambda: [cfg], client_factory=fake).round(cfg, key)   # 1.4.4：任务只收 PDF / 图片
    assert _begins(fake) == ["bill.pdf"]
    ledger = folder_store.Ledger(key)
    ledger.rule_state("R1").pop("seen_exts")                             # 1.4.4 的台账没记这个
    ledger.save()
    _old(folder / "late.pdf", b"late-bill")
    rule.update(extensions=SALES_EXTENSIONS, run_request=2)
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    assert _begins(fake) == ["bill.pdf", "late.pdf"]
    state = folder_store.Ledger(key).rule_state("R1")
    assert state["added_baseline"] == ["pos-sep.xlsx"] and state["seen_exts"] == SALES_EXTENSIONS
    assert "baseline" not in state
    worker.round(cfg, key)
    assert _syncs(fake)[-1]["rule_pending"] == {"R1": 0}
    _old(folder / "pos-oct.xlsx", b"pos-oct-new")
    rule["run_request"] = 3
    worker.round(cfg, key)
    assert _begins(fake) == ["bill.pdf", "late.pdf", "pos-oct.xlsx"]


def test_new_task_takes_tables_already_there_when_first_seen_with_them(tmp_path, monkeypatch):
    # 1.5 上第一次见到的新任务（网页数的「现有 N 份」含 POS 导出）、老任务换了文件夹转成新任务：现有表格都处理
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "pos-sep.xlsx", b"pos-sep")
    rule.update(existing=1, run_request=1, extensions=SALES_EXTENSIONS)
    fake = FakeClient([rule])
    FolderIntake(lambda: [cfg], client_factory=fake).round(cfg, key)
    assert _begins(fake) == ["pos-sep.xlsx"]
    moved = {**rule, "name": "R2", "existing": 0, "run_request": 0}
    fake = FakeClient([moved])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)                                                # 老任务：拍底（含表格）
    assert folder_store.Ledger(key).rule_state("R2")["baseline"] == ["pos-sep.xlsx"]
    moved.update(existing=1, run_request=1)                               # 换了文件夹：转成新任务
    worker.round(cfg, key)
    assert _begins(fake) == ["pos-sep.xlsx"]


def test_old_task_takes_new_extensions_only_once_the_folder_was_read_in_full(tmp_path, monkeypatch):
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "pos-sep.xlsx", b"pos-sep")
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    real_scan = plan.scan

    def flaky(folder_path, errors=None, extensions=plan.EXTENSIONS):
        if errors is not None:
            errors.append(OSError("share dropped"))
        return real_scan(folder_path, errors, extensions)

    monkeypatch.setattr(plan, "scan", flaky)
    rule.update(extensions=SALES_EXTENSIONS, run_request=1)
    worker.round(cfg, key)                                    # 补拍底没读全：这一轮照旧只扫 PDF / 图片
    assert _begins(fake) == []
    assert ".xlsx" not in plan.baselined_extensions(folder_store.Ledger(key).rule_state("R1"))
    monkeypatch.setattr(plan, "scan", real_scan)
    rule["run_request"] = 2
    worker.round(cfg, key)
    assert _begins(fake) == []
    assert "pos-sep.xlsx" in folder_store.Ledger(key).rule_state("R1")["baseline"]


def test_moved_in_old_file_is_new_but_baseline_is_skipped(tmp_path, monkeypatch):
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "before.pdf", b"before")
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)                       # 第一次见到规则：拍底，不跑
    assert not [c for c in fake.calls if c[0] == "begin"]
    _old(folder / "moved.pdf", b"moved-in")      # 创建时间很早（同盘移动），但不在底里
    rule["run_request"] = 1
    worker.round(cfg, key)
    assert [c[1]["relpath"] for c in fake.calls if c[0] == "begin"] == ["moved.pdf"]


def test_failed_upload_is_retried_next_round(tmp_path, monkeypatch):
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    _old(folder / "a.pdf", b"aaa")
    rule["run_request"] = 1
    calls = {"n": 0}
    real = fake.finish

    def flaky(entry, content=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("network down")
        return real(entry, content)

    fake.finish = flaky
    worker.round(cfg, key)                       # 这一次传失败
    worker.round(cfg, key)                       # 下一轮补传，不等下个月
    assert calls["n"] == 2
    ledger = folder_store.Ledger(key)
    row = ledger.files[ledger.file_key("R1", "a.pdf")]
    assert row.get("entry") and not row.get("retry")


def test_share_path_turns_mapped_drives_into_network_paths():
    mapped = {"S": "\\\\SERVER\\scans"}
    assert plan.share_path("\\\\server\\scans\\A", mapped) == "\\\\server\\scans\\A"
    assert plan.share_path("S:\\2569\\A", mapped) == "\\\\SERVER\\scans\\2569\\A"
    assert plan.share_path("C:\\Users\\a\\scans", mapped) == ""  # 本机盘:各台电脑各自的文件夹


def test_rule_health_reports_missing_folders(tmp_path, monkeypatch):
    from companion.folder_worker import _folder_health

    (tmp_path / "ok").mkdir()
    root = {"id": "R", "path": str(tmp_path)}
    assert _folder_health({"folder": "ok"}, root) == "ok"
    assert _folder_health({"folder": "gone"}, root) == "folder_missing"
    assert _folder_health({"folder": "ok"}, None) == "root_missing"


# ── 2026-10 CC 闭环第一批：新任务不拍底、文件夹计数与待处理数、台账墓碑、对话提醒与新版提示 ─────────────


def _begins(fake):
    return [c[1]["relpath"] for c in fake.calls if c[0] == "begin"]


def _syncs(fake):
    return [c[1] for c in fake.calls if c[0] == "sync"]


def test_new_task_processes_files_already_in_the_folder(tmp_path, monkeypatch):
    # 新任务（云端 existing=1）不拍底：建任务前就在文件夹（含子文件夹）里的也处理，「立即运行」也一样
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    (folder / "2026-09").mkdir()
    for name in ("a.pdf", "b.jpg", "e.PDF", "2026-09/c.pdf", "2026-09/d.png"):
        _old(folder / name, name.encode())
    rule.update(existing=1, run_request=1)
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    assert sorted(_begins(fake)) == ["2026-09\\c.pdf", "2026-09\\d.png", "a.pdf", "b.jpg", "e.PDF"]
    worker.round(cfg, key)
    run = _syncs(fake)[-1]["runs"][0]
    assert run["new_files"] == 5 and run["skipped"] == 0
    assert "slot" not in run  # 立即运行不属于哪个定时时刻
    assert "baseline" not in folder_store.Ledger(key).rule_state("R1")


def test_old_task_keeps_its_baseline_even_when_run_now(tmp_path, monkeypatch):
    # 老任务（existing 缺省或为 0）照旧：启用前就在的文件拍底，「立即运行」也不处理
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "before.pdf", b"before")
    rule.update(existing=0, run_request=1)
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    worker.round(cfg, key)
    assert _begins(fake) == []
    assert _syncs(fake)[-1]["runs"][0]["skipped"] == 1


def test_reenabled_new_task_is_not_rebaselined(tmp_path, monkeypatch):
    # 新任务停用后再启用（active_since 变成现在）：不重新拍底；停用期间放进来的照常处理，处理过的不再传
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "a.pdf", b"aaa")
    rule.update(existing=1, run_request=1)
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    assert _begins(fake) == ["a.pdf"]
    fake.rules = []                              # 停用：云端不再下发
    worker.round(cfg, key)
    _old(folder / "b.pdf", b"bbbb")              # 停用期间放进来的
    fake.rules = [{**rule, "active_since": time.time(), "run_request": 2}]
    worker.round(cfg, key)
    assert _begins(fake) == ["a.pdf", "b.pdf"]


def test_switching_to_existing_drops_the_old_baseline(tmp_path, monkeypatch):
    # 老任务改了文件夹，云端改下发 existing=1：旧底丢掉，文件夹里没处理过的都处理
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "a.pdf", b"aaa")
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    assert folder_store.Ledger(key).rule_state("R1")["baseline"] == ["a.pdf"]
    rule.update(existing=1, run_request=1)
    worker.round(cfg, key)
    assert _begins(fake) == ["a.pdf"]
    assert "baseline" not in folder_store.Ledger(key).rule_state("R1")


def _tree(root):
    """含隐藏、临时、0 字节、表格、结果文件夹、4 层深的一棵树。"""
    for name in ("a.pdf", "A/b.jpg", "A/sub/c.png", "A/sub/deep/er/d.pdf", "A/sub/deep/er/e.heic", "B/f.PDF",
                 "B/~$g.pdf", "B/.h.pdf", "B/i.tmp", "B/j.xlsx", ".hidden/k.pdf", "A/" + plan.RESULT_FOLDER + "/l.pdf",
                 "B/" + plan.RESULT_FOLDERS[1] + "/2026-10/m.pdf"):
        path = root.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    (root / "B" / "empty.pdf").write_bytes(b"")
    (root / "C").mkdir()


def test_survey_agrees_with_scan(tmp_path):
    root = tmp_path / "S"
    _tree(root)
    folders = plan.folders_of(root)
    seen, complete = plan.survey(root, deadline=time.monotonic() + 60, budget=[plan.SURVEY_ENTRIES])
    assert complete
    counts = plan.folder_counts(seen, ["", *folders])
    for name in ["", *folders]:
        assert counts.get(name, 0) == len(list(plan.scan(plan.rule_folder(str(root), name)))), name
    assert counts == {"": 6, "A": 4, "A\\sub": 3, "A\\sub\\deep": 2, "B": 1}  # 没有文件的 C 不列；4 层深的算进上级
    # 大小与修改时间与 scan 一致（台账靠它们认文件）
    by_path = {item.relpath: item for item in seen}
    for candidate in plan.scan(root):
        assert (by_path[candidate.relpath].size, by_path[candidate.relpath].mtime) == (candidate.size, candidate.mtime)


def test_survey_stops_at_the_entry_budget_or_the_deadline(tmp_path):
    root = tmp_path / "S"
    _tree(root)
    seen, complete = plan.survey(root, deadline=time.monotonic() + 60, budget=[3])
    assert not complete and len(seen) <= 3
    ticks = iter(range(1000))
    seen, complete = plan.survey(root, deadline=4, budget=[10 ** 6], clock=lambda: next(ticks))
    assert not complete and len(seen) <= 4


def test_folder_counts_roll_up_to_parents_and_keep_the_reported_spelling():
    seen = [plan.Seen("a.pdf", 1, 1.0), plan.Seen("Ice\\Jan\\b.pdf", 1, 1.0), plan.Seen("ice\\c.pdf", 1, 1.0),
            plan.Seen("Other\\x\\y\\z\\d.pdf", 1, 1.0)]
    counts = plan.folder_counts(seen, ["", "ICE", "ice\\jan", "Other\\x\\y", "Empty"])
    assert counts == {"": 4, "ICE": 2, "ice\\jan": 1, "Other\\x\\y": 1}


def test_pending_count_skips_the_ledger_and_the_baseline():
    seen = [plan.Seen("Ice\\a.pdf", 3, 10.0), plan.Seen("ice\\Jan\\b.pdf", 4, 20.0), plan.Seen("ice\\c.pdf", 5, 30.0),
            plan.Seen("other\\d.pdf", 6, 40.0), plan.Seen("icebox\\e.pdf", 7, 50.0)]
    known = {("a.pdf", 3, 10.0)}
    count = plan.pending_count(seen, "ice", lambda relpath, size, mtime: (relpath, size, mtime) in known, {"c.pdf"})
    assert count == 1  # 只剩 Jan\b.pdf；别的文件夹、名字前缀相同的 icebox 都不算
    assert plan.pending_count(seen, "", lambda *_: False, set()) == 5


def test_counts_and_rule_pending_are_reported_and_drop_after_a_run(tmp_path, monkeypatch):
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        _old(folder / name, name.encode())
    rule.update(existing=1)
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    first = _syncs(fake)[0]
    assert first["rule_pending"] == {} and first["notices_after"] == 0
    assert first["roots"][0]["counts"] == {"": 3, "ice": 3} and first["roots"][0]["partial"] is False
    worker.round(cfg, key)                       # 上一轮算好的待处理数，下一次同步报
    assert _syncs(fake)[-1]["rule_pending"] == {"R1": 3}
    rule["run_request"] = 1
    worker.round(cfg, key)
    worker.round(cfg, key)
    assert len(_begins(fake)) == 3 and _syncs(fake)[-1]["rule_pending"] == {"R1": 0}


def test_old_task_pending_leaves_out_its_baseline(tmp_path, monkeypatch):
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "before.pdf", b"before")
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    _old(folder / "after.pdf", b"after!")
    worker.round(cfg, key)
    worker.round(cfg, key)
    assert _syncs(fake)[-1]["rule_pending"] == {"R1": 1}
    # 负责人还没准备好的老任务没拍过底：不知道几份，不报
    fresh = {**rule, "name": "R2", "ready": False}
    fake.rules = [rule, fresh]
    worker.round(cfg, key)
    worker.round(cfg, key)
    assert _syncs(fake)[-1]["rule_pending"] == {"R1": 1}


def test_report_cadence_follows_the_cost_of_the_last_survey(tmp_path, monkeypatch):
    from companion import folder_worker as fw

    assert fw._report_interval(0.3, True) == 0
    assert fw._report_interval(2.5, True) == fw.FOLDER_REPORT_SECONDS
    assert fw._report_interval(0.3, False) == fw.FOLDER_REPORT_PARTIAL_SECONDS
    # 两个根目录共用一份条数：第一个数不完（不全），第二个没轮到就不带 counts；10 分钟内不再重数
    first, second = tmp_path / "S", tmp_path / "T"
    _tree(first)
    (second / "x").mkdir(parents=True)
    (second / "x" / "a.pdf").write_bytes(b"a")
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    folder_store.set_roots(key, [str(first), str(second)])
    monkeypatch.setattr(plan, "SURVEY_ENTRIES", 4)
    now = [1000.0]
    worker = FolderIntake(lambda: [cfg], client_factory=FakeClient([]), clock=lambda: now[0])
    rows = worker._report(key, folder_store.roots(key))
    assert rows[0]["partial"] is True and "counts" in rows[0]
    assert "counts" not in rows[1] and "partial" not in rows[1] and rows[1]["folders"] == ["x"]
    monkeypatch.setattr(plan, "SURVEY_ENTRIES", 10 ** 6)
    now[0] += fw.FOLDER_REPORT_SECONDS - 1
    assert worker._report(key, folder_store.roots(key)) is rows
    # 数不全时计数 10 分钟才重数，但文件夹名照样 2 分钟刷新（新建的子文件夹不用等 10 分钟才出现在网页下拉）
    (second / "2026-11").mkdir()
    now[0] += 2
    names = worker._report(key, folder_store.roots(key))
    assert names[1]["folders"] == ["2026-11", "x"] and "counts" not in names[1]
    assert names[0]["counts"] == rows[0]["counts"] and names[0]["partial"] is True
    assert worker._surveyed[key] == 1
    assert worker._report(key, folder_store.roots(key)) is names   # 刚刷过文件夹名
    now[0] = 1000.0 + fw.FOLDER_REPORT_PARTIAL_SECONDS - 1
    assert worker._report(key, folder_store.roots(key))[0]["partial"] is True and worker._surveyed[key] == 1
    now[0] += 1
    again = worker._report(key, folder_store.roots(key))
    assert worker._surveyed[key] == 2
    assert again[0]["partial"] is False and again[1]["counts"] == {"": 1, "x": 1}
    assert worker._report(key, folder_store.roots(key)) is not again  # 一秒内看全了：每轮都刷


def test_schedule_runs_carry_their_slot(tmp_path, monkeypatch):
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "a.pdf", b"aaa")
    slot = plan.latest_slot(time.time(), 1, "22:00")
    rule.update(existing=1, active_since=slot - 60)
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    worker.round(cfg, key)
    run = _syncs(fake)[-1]["runs"][0]
    assert run["slot"] == slot and run["trigger"] in ("schedule", "catch_up")


def test_old_rows_leave_a_tombstone_and_are_not_sent_again(tmp_path, monkeypatch):
    # 120 天后台账只留墓碑：同一份文件不再登记、结果表格不再写一份到当月；文件删了、完整扫描一次后墓碑清掉
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "a.pdf", b"aaa")
    rule.update(existing=1, run_request=1)
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    entry = hashlib.sha256(b"a.pdf").hexdigest()
    fake.entries[entry] = {"state": "done", "finished": True, "settled": True,
                           "items": [{"history": "H1", "revision": 1, "filename": "INV-1_2808.xlsx"}]}
    worker.round(cfg, key)
    ledger = folder_store.Ledger(key)
    file_key = ledger.file_key("R1", "a.pdf")
    assert ledger.files[file_key]["closed_at"]
    ledger.files[file_key]["closed_at"] -= 121 * 86400
    ledger.save()
    tomb = folder_store.Ledger(key).files[file_key]
    assert set(tomb) == set(folder_store.TOMBSTONE_FIELDS) and "entry" not in tomb
    rule["run_request"] = 2
    worker.round(cfg, key)
    assert _begins(fake) == ["a.pdf"] and len([c for c in fake.calls if c[0] == "workbook"]) == 1
    assert entry not in _syncs(fake)[-1]["watch"]
    (folder / "a.pdf").unlink()
    rule["run_request"] = 3
    worker.round(cfg, key)
    assert file_key not in folder_store.Ledger(key).files


def test_tombstones_stay_when_the_scan_could_not_read_everything(tmp_path, monkeypatch):
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    ledger = folder_store.Ledger(key)
    ledger.files[ledger.file_key("R1", "gone.pdf")] = {"rule": "R1", "relpath": "gone.pdf", "size": 3, "mtime": 1.0,
                                                       "closed_at": 1.0}
    ledger.save()
    real = plan.scan

    def flaky(path, errors=None, **kwargs):
        if errors is not None:
            errors.append(OSError("network share hiccup"))
        return real(path, errors, **kwargs)

    monkeypatch.setattr(plan, "scan", flaky)
    rule.update(existing=1, run_request=1)
    worker = FolderIntake(lambda: [cfg], client_factory=FakeClient([rule]))
    worker.round(cfg, key)
    assert ledger.file_key("R1", "gone.pdf") in folder_store.Ledger(key).files
    monkeypatch.setattr(plan, "scan", real)
    rule["run_request"] = 2
    worker.round(cfg, key)
    assert ledger.file_key("R1", "gone.pdf") not in folder_store.Ledger(key).files


def _notice(n, **extra):
    return {"id": f"pnotice_{n}", "title": f"T{n}", "text": f"text {n}", "created": float(n), "session": "", "view": "",
            **extra}


def test_notices_carry_the_page_to_open_and_old_servers_without_it_still_work(tmp_path):
    # 1.9.1（T3）：服务端提醒带 view（company:<编号> = 这家公司的主档页）；旧服务端不带也收（当作定时任务页），
    # 不是字符串的当作没有、不丢整条
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    fake = FakeClient([])
    got = []
    worker = FolderIntake(lambda: [cfg], client_factory=fake, on_notices=lambda site, items: got.extend(items))
    fake.extra = {"notices": []}
    worker.round(cfg, key)
    old = {key_: value for key_, value in _notice(100).items() if key_ != "view"}
    fake.extra = {"notices": [old, _notice(200, view="company:abc1234567"), _notice(300, view=7)]}
    worker.round(cfg, key)
    assert [(item["id"], item["view"]) for item in got] == [("pnotice_100", ""), ("pnotice_200", "company:abc1234567"),
                                                           ("pnotice_300", "")]


def test_notices_reach_the_tray_once_and_move_the_cursor(tmp_path):
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    fake = FakeClient([])
    got = []
    worker = FolderIntake(lambda: [cfg], client_factory=fake, on_notices=lambda site, items: got.append((site, items)))
    fake.extra = {"notices": []}
    worker.round(cfg, key)                       # 第一次同步、还没有提醒：之后带来的都是新的
    assert folder_store.Ledger(key).notices_after == 0.0
    fake.extra = {"notices": [_notice(100, session="sess-1"), {"id": "pnotice_x", "title": "no text field"},
                              _notice(200), {**_notice(300), "created": "late"}, {**_notice(400), "id": 7}]}
    worker.round(cfg, key)
    assert got == [("https://a.pearnly.com", [_notice(100, session="sess-1"), _notice(200)])]
    assert folder_store.Ledger(key).notices_after == 200.0
    fake.extra = {"notices": []}
    worker.round(cfg, key)
    assert _syncs(fake)[-1]["notices_after"] == 200.0 and len(got) == 1
    fake.extra = {"notices": "junk"}
    worker.round(cfg, key)
    assert len(got) == 1


def test_the_first_sync_of_a_connection_pops_only_the_newest_notice(tmp_path):
    # companion-8：刚装 / 刚升级到 1.4.4 / 重新配对的第一次同步（台账里还没有 notices_after）会带回最近 12 小时最多
    # 20 条，多半已在手机或网页看过：只弹最新一条，游标推到最新一条；之后来的照常全交给托盘
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    folder_store._write(f"ledger-{key}.json", {"rules": {"R1": {"last_request": 3}}, "files": {}, "runs": []})
    assert folder_store.Ledger(key).notices_after is None   # 1.4.3 的台账：没有 notices_after
    fake = FakeClient([])
    got = []
    worker = FolderIntake(lambda: [cfg], client_factory=fake, on_notices=lambda site, items: got.append(items))
    fake.extra = {"notices": [_notice(n) for n in (100, 300, 200)]}
    worker.round(cfg, key)
    assert _syncs(fake)[0]["notices_after"] == 0
    assert got == [[_notice(300)]] and folder_store.Ledger(key).notices_after == 300.0
    fake.extra = {"notices": [_notice(400), _notice(500)]}
    worker.round(cfg, key)
    assert got[-1] == [_notice(400), _notice(500)]


def test_folder_only_members_hear_about_new_versions_from_the_sync(tmp_path):
    from companion.version import VERSION

    cfg = _cfg()
    cfg.kind = "folder"
    key = folder_store.connection_key(cfg)
    fake = FakeClient([])
    offered = []
    worker = FolderIntake(lambda: [cfg], client_factory=fake, on_update=lambda v, u: offered.append((v, u)))
    fake.extra = {"companion_latest": {"version": VERSION, "url": "/same.exe"}}
    worker.round(cfg, key)
    fake.extra = {"companion_latest": {"version": "99.0.0", "url": "/new.exe"}}
    worker.round(cfg, key)
    fake.extra = {"companion_latest": "junk"}
    worker.round(cfg, key)
    assert offered == [("99.0.0", "/new.exe")]


def test_folder_only_members_report_a_blocked_update_in_the_sync(tmp_path):
    # 教程问题第 1 条：只读文件夹的成员没有心跳，「自动更新被 Windows 拦下」随文件夹同步报（版本 + UTC 时刻）；没被拦报 None
    from companion import update_block

    cfg = _cfg()
    cfg.kind = "folder"
    key = folder_store.connection_key(cfg)
    fake = FakeClient([])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    memory = update_block.UpdateMemory(tmp_path / "update_state.json", current="1.5.1")
    memory.attempt("1.5.2", 1_760_000_000.0)
    memory.finish("1.5.2", "blocked", 1_760_000_000.0, 4551)
    worker.round(cfg, key)
    assert [sync["update_blocked"] for sync in _syncs(fake)] == [
        None, {"version": "1.5.2", "at": "2025-10-09T08:53:20Z"}]


def test_rules_run_in_the_order_the_server_sends(tmp_path, monkeypatch):
    # F7：同一账套的存货进货任务由云端排在销售之前，本机不另排序
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    (folder.parent / "sales").mkdir()
    _old(folder / "p.pdf", b"purchase")
    _old(folder.parent / "sales" / "s.pdf", b"sale")
    purchase = {**rule, "name": "P", "existing": 1, "run_request": 1}
    sale = {**rule, "name": "A-sale", "folder": "sales", "existing": 1, "run_request": 1}
    fake = FakeClient([purchase, sale])
    FolderIntake(lambda: [cfg], client_factory=fake).round(cfg, key)
    assert [c[1]["rule"] for c in fake.calls if c[0] == "begin"] == ["P", "A-sale"]


# ── 2026-10-13 复核修正：中途叫停、根目录读不到、台账上限与重写、待处理数一次算完 ─────────────────────────


class StopAfter(FakeClient):
    """传完第 n 份就叫停（自动更新、退出托盘时 stop 只在两份文件之间生效）；记下每次登记时线程算不算忙。"""

    def __init__(self, rules, n):
        super().__init__(rules)
        self.n, self.worker, self.busy = n, None, []

    def begin(self, body):
        self.busy.append(self.worker.busy())
        return super().begin(body)

    def finish(self, entry, content=None):
        answer = super().finish(entry, content)
        if self.n is not None and len([c for c in self.calls if c[0] == "finish"]) == self.n:
            self.worker.stop()
        return answer


def test_a_run_stopped_midway_is_resumed_under_the_same_run(tmp_path, monkeypatch):
    # companion-1：到点那一轮传到第 2 份被叫停：这个时刻不算处理过、运行记录不带 scanned；下次启动同一时刻照样到点，
    # 用同一个运行号接着传剩下的 3 份（传过的台账认得），跑完才记处理过、报 scanned，传上的份数连前一段一起算
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    for n in range(5):
        _old(folder / f"f{n}.pdf", f"file {n}".encode())
    slot = plan.latest_slot(time.time(), 1, "22:00")
    rule.update(existing=1, active_since=slot - 60)
    fake = StopAfter([rule], 2)
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    fake.worker = worker
    worker.round(cfg, key)
    assert _begins(fake) == ["f0.pdf", "f1.pdf"]
    assert fake.busy == [True, True] and not worker.busy()
    ledger = folder_store.Ledger(key)
    state = ledger.rule_state("R1")
    assert "last_slot" not in state and plan.due(rule, state, time.time()).slot == slot
    interrupted = ledger.runs[-1]
    assert interrupted["note"] == "interrupted" and "scanned" not in interrupted
    assert interrupted["new_files"] == 2 and interrupted["slot"] == slot
    worker._stop.clear()                         # 装好新版、重新启动
    fake.n = None
    worker.round(cfg, key)
    assert _begins(fake) == [f"f{n}.pdf" for n in range(5)]
    assert _syncs(fake)[1]["runs"] == [interrupted]
    worker.round(cfg, key)
    final = _syncs(fake)[-1]["runs"][0]
    assert final["run_key"] == interrupted["run_key"] and final["scanned"] is True
    assert (final["new_files"], final["found"], final["slot"]) == (5, 5, slot)
    state = folder_store.Ledger(key).rule_state("R1")
    assert state["last_slot"] == slot and "open_run" not in state
    assert plan.due(rule, state, time.time()) is None


def test_an_unreadable_root_is_unknown_not_zero(tmp_path, monkeypatch):
    # companion-4：根目录读不到（不是文件夹、NAS / 映射盘掉线、列目录被拒）是「不知道」：这一行不带 counts、
    # 它下面的任务不报待处理数；同一次上报里别的根目录照数
    budget = [plan.SURVEY_ENTRIES]
    assert plan.survey(tmp_path / "gone", deadline=time.monotonic() + 60, budget=budget) == (None, True)
    (tmp_path / "file.pdf").write_bytes(b"x")
    assert plan.survey(tmp_path / "file.pdf", deadline=time.monotonic() + 60, budget=budget) == (None, True)
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "a.pdf", b"aaa")
    other = tmp_path / "T"
    (other / "x").mkdir(parents=True)
    (other / "x" / "b.pdf").write_bytes(b"b")
    folder_store.set_roots(key, [str(folder.parent), str(other)])
    rule.update(existing=1)
    real = os.scandir

    def refuse(path):
        if os.fspath(path) == str(folder.parent):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(plan.os, "scandir", refuse)
    assert plan.survey(folder.parent, deadline=time.monotonic() + 60, budget=budget) == (None, True)
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    worker.round(cfg, key)
    rows = _syncs(fake)[-1]["roots"]
    assert "counts" not in rows[0] and "partial" not in rows[0]
    assert rows[1]["counts"] == {"": 1, "x": 1} and rows[1]["partial"] is False
    assert _syncs(fake)[-1]["rule_pending"] == {}   # 以前报 {"R1": 0}


def test_rows_of_tasks_gone_from_the_server_for_long_are_dropped(tmp_path):
    # companion-5：回复带这台电脑读的全部任务名（rule_names，含停用的）时，超过 KEEP_SECONDS 没再出现的任务（已删除、
    # 换到别的电脑读）连同台账行删掉；以前没记时间的从现在起算。只带启用中的任务（rule_names 缺）时不删：停用很久
    # 再启用的新任务靠这些行认出处理过的文件
    cfg = _cfg()
    key = folder_store.connection_key(cfg)
    ledger = folder_store.Ledger(key)
    long_ago = time.time() - folder_store.KEEP_SECONDS - 60
    for name in ("GONE", "PAUSED", "LIVE", "OLD"):
        ledger.files[ledger.file_key(name, "a.pdf")] = {"rule": name, "relpath": "a.pdf", "size": 1, "mtime": 1.0,
                                                        "closed_at": 1.0}
        if name != "OLD":
            ledger.rule_state(name)["seen_at"] = long_ago
    ledger.save()
    fake = FakeClient([{"name": "LIVE", "ready": False}])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    assert {row["rule"] for row in folder_store.Ledger(key).files.values()} == {"GONE", "PAUSED", "LIVE", "OLD"}
    fake.extra = {"rule_names": ["LIVE", "PAUSED", 7]}
    worker.round(cfg, key)
    after = folder_store.Ledger(key)
    assert {row["rule"] for row in after.files.values()} == {"PAUSED", "LIVE", "OLD"}
    assert "GONE" not in after.rules and after.rules["OLD"]["seen_at"] > long_ago
    assert after.rules["PAUSED"]["seen_at"] > long_ago


def test_a_read_error_only_keeps_the_rows_under_the_failing_folder(tmp_path, monkeypatch):
    # companion-5：某个子文件夹读不了（盘根的 System Volume Information、NAS 的 #recycle…）只保护它下面的行，
    # 别处文件已不在的墓碑照清（以前整条任务永远不清）
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    (folder / "locked").mkdir()
    ledger = folder_store.Ledger(key)
    for relpath in ("gone.pdf", "locked\\inside.pdf", "lockedness\\x.pdf"):
        ledger.files[ledger.file_key("R1", relpath)] = {"rule": "R1", "relpath": relpath, "size": 3, "mtime": 1.0,
                                                        "closed_at": 1.0}
    ledger.save()
    real = plan.scan

    def flaky(path, errors=None, **kwargs):
        if errors is not None:
            errors.append(PermissionError(13, "Access is denied", str(folder / "locked")))
        return real(path, errors, **kwargs)

    monkeypatch.setattr(plan, "scan", flaky)
    rule.update(existing=1, run_request=1)
    FolderIntake(lambda: [cfg], client_factory=FakeClient([rule])).round(cfg, key)
    left = folder_store.Ledger(key).files
    assert ledger.file_key("R1", "locked\\inside.pdf") in left
    assert ledger.file_key("R1", "gone.pdf") not in left and ledger.file_key("R1", "lockedness\\x.pdf") not in left


def test_the_ledger_is_rewritten_only_when_it_changed(tmp_path, monkeypatch):
    # companion-5：台账每轮整份重写（大台账时很费盘）：内容没变就不写
    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    _old(folder / "a.pdf", b"aaa")
    rule.update(existing=1, run_request=1)
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake)
    worker.round(cfg, key)
    worker.round(cfg, key)                       # 运行记录报上去了
    writes = []
    real = folder_store._write_text
    monkeypatch.setattr(folder_store, "_write_text", lambda name, text: (writes.append(name), real(name, text)))
    worker.round(cfg, key)
    worker.round(cfg, key)
    assert writes == [] and folder_store.Ledger(key).save() is False
    rule["run_request"] = 2
    worker.round(cfg, key)
    assert writes


def test_pending_counts_walk_the_files_once_for_all_tasks():
    # companion-6：同一根目录下几条任务一起算，文件只走一遍：每份文件只对它所在的任务问一次台账（拍底里的不用问）；
    # 结果与逐条算一致
    seen = [plan.Seen("Ice\\a.pdf", 3, 10.0), plan.Seen("ice\\Jan\\b.pdf", 4, 20.0), plan.Seen("ice\\c.pdf", 5, 30.0),
            plan.Seen("other\\d.pdf", 6, 40.0), plan.Seen("icebox\\e.pdf", 7, 50.0), plan.Seen("f.pdf", 8, 60.0)]
    calls = []

    def known(relpath, size, mtime):
        calls.append(relpath)
        return (relpath, size) == ("a.pdf", 3)

    counts = plan.pending_counts(seen, [("ICE", "ice", known, {"c.pdf"}), ("OTHER", "Other", known, set()),
                                        ("NONE", "nothing", known, set())])
    assert counts == {"ICE": 1, "OTHER": 1, "NONE": 0}
    assert sorted(calls) == ["Jan\\b.pdf", "a.pdf", "d.pdf"]
    for name, folder, baseline in (("ICE", "ice", {"c.pdf"}), ("OTHER", "Other", set())):
        assert plan.pending_count(seen, folder, lambda r, s, m: (r, s) == ("a.pdf", 3), baseline) == counts[name]
    assert [item.relpath for item in plan.within(seen, {plan.folder_key("ICE")})] == [
        "Ice\\a.pdf", "ice\\Jan\\b.pdf", "ice\\c.pdf"]
    assert plan.within(seen, set()) == [] and len(plan.within(seen, {""})) == len(seen)
    assert not hasattr(seen[0], "__dict__")      # slots：十几万份时少占内存


def test_only_files_under_task_folders_stay_in_memory(tmp_path, monkeypatch):
    # companion-6：数过之后只留落在任务文件夹里的文件；之后新加的任务落在没留的文件夹里：这次不报（不用 0 冒充），
    # 下一轮重新数后照报
    from companion import folder_worker as fw

    cfg, key, folder, rule = _setup_rule(tmp_path, monkeypatch, created={})
    (folder.parent / "sales").mkdir()
    _old(folder / "a.pdf", b"aaa")
    _old(folder.parent / "sales" / "s.pdf", b"sale")
    _old(folder.parent / "top.pdf", b"top")
    rule.update(existing=1)
    monkeypatch.setattr(fw, "FOLDER_REPORT_CHEAP_SECONDS", -1.0)   # 上一次数得慢：2 分钟内不重数
    fixed = time.time()
    fake = FakeClient([rule])
    worker = FolderIntake(lambda: [cfg], client_factory=fake, clock=lambda: fixed)
    worker.round(cfg, key)
    root = rule["root_id"]
    assert [item.relpath for item in worker._seen[key][root]] == ["ice\\a.pdf"]
    fake.rules = [rule, {**rule, "name": "S", "folder": "sales"}]
    worker.round(cfg, key)
    worker.round(cfg, key)
    assert _syncs(fake)[-1]["rule_pending"] == {"R1": 1}
    worker.round(cfg, key)
    assert _syncs(fake)[-1]["rule_pending"] == {"R1": 1, "S": 1}
    assert sorted(item.relpath for item in worker._seen[key][root]) == ["ice\\a.pdf", "sales\\s.pdf"]
