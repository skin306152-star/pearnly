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

    def __call__(self, url, token):
        return self

    def close(self):
        pass

    def sync(self, body):
        self.calls.append(("sync", body))
        return {"ok": True, "server_time": time.time(), "rules": self.rules,
                "entries": {name: self.entries[name] for name in body["watch"] if name in self.entries}}

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
    assert len([c for c in fake.calls if c[0] == "begin"]) == 1
    written = list((folder / expected).rglob("*.xlsx"))
    assert [p.name for p in written] == ["INV-1_2808.xlsx"] and written[0].read_bytes() == b"PK-xlsx"


def test_no_roots_means_no_thread_and_no_network(tmp_path):
    fake = FakeClient([])
    worker = FolderIntake(lambda: [_cfg()], client_factory=fake)
    worker.start()
    assert worker._thread is None and fake.calls == []


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
