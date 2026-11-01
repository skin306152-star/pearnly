# -*- coding: utf-8 -*-
"""代录宿主与写账线：载荷 15 的单交给代录、一件一件录、批次最后一张录完退出 Express；
不能代录的电脑转人工（不反复领）；代录包只在领到代录单时导入。"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

from _fake_express import FakeBook, FakeExpress

from companion import worker
from companion.express_operator import recipe as R
from companion.express_operator.batch import Runner, no_lock
from companion.express_operator.engine import Engine
from companion.express_operator.host import Host

RECIPE = R.load()
SRC = Path(__file__).resolve().parents[1] / "src" / "companion"


def payload(doc_type, party, code, qty, price, index, total, batch="b1", ref="R1"):
    side = "supplier" if doc_type in ("RR", "HP") else "customer"
    return {"doctype": doc_type, "docdate_be": "691020", "ref_no": ref, side: {"code": party},
            "items": [{"code": code, "qty": qty, "unit_price": price}], "payload_version": 15,
            "entry_mode": "operator", "operator": {"batch": {"id": batch, "index": index, "total": total}}}


def fake_factory(sims):
    def factory(state, store, settings, root, notify):
        sim = FakeExpress(RECIPE.data)
        sims.append(sim)
        book = FakeBook(sim)
        engine = Engine(sim, RECIPE, book, sleep=lambda _s: None)

        class Session:
            def ensure_open(self):
                sim.alive = True

            def close(self):
                sim.alive = False

        runner = Runner(state, store, engine, Session(), book, lock=no_lock, sleep=lambda _s: None, on_change=notify)
        return runner, {"desk": sim}
    return factory


def test_batch_arrives_one_job_at_a_time_and_express_closes_after_the_last(tmp_path):
    sims, seen = [], []
    host = Host(tmp_path, factory=fake_factory(sims), notify=seen.append)
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    first = host.handle("t1", payload("RR", "P001", "501780", "2", "10.00", 1, 2), st)
    assert first["status"] == "saved" and first["docnum"] == "RR0000001" and sims[0].alive
    second = host.handle("t2", payload("HS", "CASH", "500310", "1", "53.50", 2, 2, ref="R2"), st)
    assert second["status"] == "saved" and not sims[0].alive  # 最后一张录完：正常退出 Express
    assert len(sims) == 1 and seen[-1]["status"] == "finished"
    assert host.snapshots()[0]["counts"] == {"saved": 2}


def test_the_site_document_number_names_the_document_in_the_batch(tmp_path):
    sims = []
    host = Host(tmp_path, factory=fake_factory(sims))
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    job = payload("RR", "P001", "501780", "1", "1.00", 1, 1)
    job["operator"]["batch"]["doc"] = "CWH-op1"
    host.handle("task-uuid-1", job, st)
    assert [d["doc_id"] for d in host.snapshots()[0]["docs"]] == ["CWH-op1"]  # 页面按站点单据号对上，不是写账任务号


def test_a_document_needing_a_human_comes_back_with_its_reason(tmp_path):
    sims = []
    host = Host(tmp_path, factory=fake_factory(sims))
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    out = host.handle("t1", payload("RR", "NOPE", "501780", "1", "1.00", 1, 1), st)
    assert out["status"] == "human" and out["reason"] == "party_missing"


def test_stop_returns_the_remaining_jobs_unwritten(tmp_path):
    sims = []
    host = Host(tmp_path, factory=fake_factory(sims))
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    host.handle("t1", payload("RR", "P001", "501780", "1", "1.00", 1, 3), st)
    assert host.control("b1", "stop")
    out = host.handle("t2", payload("RR", "P001", "501780", "1", "1.00", 2, 3, ref="R2"), st)
    assert out["status"] == "stopped" and not sims[0].alive


class Queue:
    def __init__(self):
        self.acks = []

    def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
        self.acks.append(("success", task_id, extracted_invoice_no, meta))

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.acks.append(("failed", task_id, error_code, outcome))


def test_a_computer_that_cannot_operate_express_hands_the_job_to_a_person(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    qc = Queue()
    task = SimpleNamespace(id="t9", payload=payload("RR", "P001", "A", "1", "1", 1, 1), invoice_no="")
    st = SimpleNamespace(account_dir=str(tmp_path), express_exe="", has_selection=lambda: True)
    out = worker._handle_operator_task(SimpleNamespace(ui_lang="zh"), qc, task, st)
    assert out == "needs_review" and qc.acks == [("failed", "t9", "OPERATOR_UNAVAILABLE", "needs_review")]


def test_operator_results_map_to_the_normal_acks(monkeypatch, tmp_path):
    from companion.express_operator import host as host_module

    results = iter([{"status": "saved", "docnum": "RR0000007", "meta": {"batch": "b"}},
                    {"status": "human", "reason": "negative_stock", "detail": "x", "meta": {}},
                    {"status": "waiting", "reason": "network_down", "meta": {}}])
    fake = SimpleNamespace(handle=lambda *_a: next(results))
    monkeypatch.setattr(host_module, "host", lambda notify=None: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(worker.task_journal, "remember", lambda *_a: None)
    monkeypatch.setattr(worker, "activity", lambda *_a, **_k: None)
    qc = Queue()
    st = SimpleNamespace(account_dir=str(tmp_path), express_exe=r"\\s\ExpressI.exe")
    task = SimpleNamespace(id="t1", payload={}, invoice_no="")
    outs = [worker._handle_operator_task(SimpleNamespace(ui_lang="zh"), qc, task, st) for _ in range(3)]
    assert outs == [None, "needs_review", "waiting_lock"]
    assert qc.acks[0] == ("success", "t1", "RR0000007", {"operator": {"batch": "b"}})
    assert qc.acks[1][2:] == ("OPERATOR_NEGATIVE_STOCK", "needs_review")
    assert qc.acks[2][2:] == ("OPERATOR_NETWORK_DOWN", "waiting_lock")


def test_nothing_imports_the_operator_package_at_module_level():
    """不代录时零占用：托盘、写账线、桥只在函数里按需导入代录包。"""
    offenders = []
    for path in SRC.glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                if any("express_operator" in n for n in names):
                    offenders.append(path.name)
    assert offenders == []


def test_stop_takes_effect_at_once_when_no_document_is_being_entered(tmp_path):
    """10-27 实机：点了停止要等下一张领到才生效，Express 一直开着。现在没有单在录时当场停、正常退出 Express。"""
    sims = []
    host = Host(tmp_path, factory=fake_factory(sims))
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    host.handle("t1", payload("RR", "P001", "501780", "1", "1.00", 1, 3), st)
    assert sims[0].alive
    assert host.control("b1", "stop")
    assert not sims[0].alive  # 不等第二张领到
    snap = host.snapshots()[0]
    assert snap["status"] == "stopped" and snap["counts"] == {"saved": 1}


def test_skipping_a_document_waiting_for_a_person_marks_it_at_once(tmp_path):
    """10-27 实机：停在「需要处理」的那张点跳过没反应、后面的排不上。现在当场标成跳过，后面的照常录。"""
    sims = []
    host = Host(tmp_path, factory=fake_factory(sims))
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    first = host.handle("t1", payload("RR", "NOPE", "501780", "1", "1.00", 1, 2), st)
    assert first["status"] == "human"
    assert host.control("b1", "skip", "t1")
    assert host.snapshots()[0]["docs"][0]["status"] == "skipped"
    second = host.handle("t2", payload("RR", "P001", "501780", "1", "1.00", 2, 2, ref="R2"), st)
    assert second["status"] == "saved"


def test_tasks_come_back_after_a_restart_and_finished_ones_can_be_deleted(tmp_path):
    """10-27 实机：CC 重启后任务列表清空。现在从本机批次文件读回；已结束的可删（只删记录与截图，不动账套）。"""
    sims = []
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    first = Host(tmp_path, factory=fake_factory(sims))
    first.handle("t1", payload("RR", "P001", "501780", "1", "1.00", 1, 1), st)
    restarted = Host(tmp_path, factory=fake_factory(sims))   # 新进程：内存里没有任何批次
    found = restarted.snapshots()
    assert [(b["id"], b["status"], b["counts"]) for b in found] == [("b1", "finished", {"saved": 1})]
    assert found[0]["docs"][0]["docnum"] == "RR0000001"
    assert restarted.control("b1", "delete") and restarted.snapshots() == []
    live = Host(tmp_path, factory=fake_factory(sims))
    live.handle("t2", payload("RR", "P001", "501780", "1", "1.00", 1, 2, batch="b2"), st)
    assert not live.control("b2", "delete")  # 录单中的不能删


def test_building_masters_mid_batch_closes_express_and_the_next_document_reopens_it(tmp_path):
    sims = []
    host = Host(tmp_path, factory=fake_factory(sims))
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    host.handle("t1", payload("RR", "P001", "501780", "1", "1.00", 1, 3), st)
    assert sims[0].alive
    host.close_express(payload("RR", "P001", "501780", "1", "1.00", 2, 3))
    assert not sims[0].alive  # 主档索引要独占重建：先正常退出 Express
    out = host.handle("t2", payload("RR", "P001", "501780", "1", "1.00", 2, 3, ref="R2"), st)
    assert out["status"] == "saved" and sims[0].alive  # 下一张重新开、照常录
    host.close_express(payload("RR", "P001", "501780", "1", "1.00", 1, 1, batch="other"))  # 没开着的批：什么都不做


def test_masters_are_prepared_before_the_operator_and_failures_ack_like_direct_write(monkeypatch, tmp_path):
    from companion.express_operator import host as host_module
    from companion.express_operator import masters

    handled, closed = [], []

    def handle(task_id, given, _st):
        handled.append(given)
        return {"status": "saved", "docnum": "RR0000008", "meta": {"batch": "b"}}

    fake = SimpleNamespace(handle=handle, close_express=lambda p: closed.append(p))
    monkeypatch.setattr(host_module, "host", lambda notify=None: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(worker.task_journal, "remember", lambda *_a: None)
    monkeypatch.setattr(worker, "activity", lambda *_a, **_k: None)
    outcomes = iter([
        lambda p, before: (before(), masters.Prepared(True, {**p, "supplier": {"code": "S0001"}},
                                                      created=[{"kind": "party", "code": "S0001"}]))[1],
        lambda p, before: masters.Prepared(False, p, error_code="SUPPLIER_DUP_SUSPECTED", error="dup",
                                           outcome="needs_review"),
    ])
    monkeypatch.setattr(masters, "prepare", lambda p, _st, before_create: next(outcomes)(p, before_create))
    qc = Queue()
    st = SimpleNamespace(account_dir=str(tmp_path), express_exe=r"\\s\ExpressI.exe")
    task = SimpleNamespace(id="t1", payload={"doctype": "RR"}, invoice_no="")
    assert worker._handle_operator_task(SimpleNamespace(ui_lang="zh"), qc, task, st) is None
    assert handled == [{"doctype": "RR", "supplier": {"code": "S0001"}}] and closed == [{"doctype": "RR"}]
    assert qc.acks[0] == ("success", "t1", "RR0000008",
                          {"operator": {"batch": "b", "masters": [{"kind": "party", "code": "S0001"}]}})
    assert worker._handle_operator_task(SimpleNamespace(ui_lang="zh"), qc, task, st) == "needs_review"
    assert len(handled) == 1 and qc.acks[1] == ("failed", "t1", "SUPPLIER_DUP_SUSPECTED", "needs_review")


def _restarted_mid_batch(tmp_path):
    """三张的批次录完第一张后 CC 重启：本机批次文件停在「进行中」，宿主里没有在跑的。"""
    sims = []
    host = Host(tmp_path, factory=fake_factory(sims))
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    first = payload("RR", "P001", "501780", "1", "1.00", 1, 3)
    first["operator"]["batch"].update(doc="CWH-1", company="CO-1")
    host.handle("t1", first, st)
    from companion.express_operator.batch import BatchStore, DocState

    store = BatchStore(tmp_path / "batches")
    state = store.load("b1")
    state.docs += [DocState("CWH-2", "RR", {}), DocState("CWH-3", "RR", {})]
    store.save(state)
    return Host(tmp_path, factory=fake_factory(sims))


def test_a_batch_left_running_by_a_restart_is_not_shown_as_running_here(tmp_path):
    restarted = _restarted_mid_batch(tmp_path)
    [found] = restarted.snapshots()
    # 本机没在跑：照实标出来、带站点公司，页面据此问站点（断网问不到时显示等待，不显示进行中）
    assert (found["live"], found["status"], found["site"]) == (False, "running", "CO-1")
    assert restarted.control("b1", "settle", results={}) and restarted.snapshots()[0]["status"] == "running"


def test_restart_then_the_site_says_the_rest_were_entered_or_cancelled(tmp_path):
    restarted = _restarted_mid_batch(tmp_path)
    assert restarted.control("b1", "settle", results={"CWH-2": {"state": "saved", "docnum": "RR0000009"},
                                                       "CWH-3": {"state": "cancelled"}})
    [found] = restarted.snapshots()
    assert found["status"] == "stopped" and found["counts"] == {"saved": 2, "skipped": 1}
    assert [d["docnum"] for d in found["docs"]][:2] == ["RR0000001", "RR0000009"]


def test_restart_then_one_is_still_queued_and_one_needs_a_person(tmp_path):
    restarted = _restarted_mid_batch(tmp_path)
    assert restarted.control("b1", "settle", results={"CWH-2": {"state": "queued"},
                                                       "CWH-3": {"state": "not_done", "reason": "[OPERATOR_PARTY_MISSING]"}})
    [found] = restarted.snapshots()
    assert found["status"] == "running" and found["counts"] == {"saved": 1, "pending": 1, "waiting": 1}
    restarted.control("b1", "settle", results={"CWH-2": {"state": "saved", "docnum": "RR0000010"}})
    assert restarted.snapshots()[0]["status"] == "finished"  # 都有结论了：收尾成已完成


def test_settle_never_touches_a_batch_being_entered(tmp_path):
    sims = []
    host = Host(tmp_path, factory=fake_factory(sims))
    st = SimpleNamespace(account_dir=str(tmp_path / "book"), account_company="x", account_set="TEST")
    host.handle("t1", payload("RR", "P001", "501780", "1", "1.00", 1, 2), st)
    assert not host.control("b1", "settle", results={"t1": {"state": "cancelled"}})
    assert host.snapshots()[0]["live"] is True


# ── CC 重启后上次留下的 Express（主会话 10-27：能接手就接手，接手不了按 Express 原生方式收拾，不碰不是自己开的）──────────
class _BoundExpress(FakeExpress):
    """假 Express 带进程号绑定（真桌面的 bind / instances）。"""

    def bind(self, pid):
        self.bound = pid

    def instances(self):
        return []


def _orphan_host(tmp_path, sim, clock=lambda: 1000.0):
    from companion.express_operator.session import Session

    def factory(state, store, settings, root, notify):
        book = FakeBook(sim)
        engine = Engine(sim, RECIPE, book, sleep=lambda _s: None)
        session = Session(sim, RECIPE, express_exe="", account_dir=state.account_dir, company_name="",
                          credential_target="t", sleep=lambda _s: None)
        runner = Runner(state, store, engine, session, book, lock=no_lock, sleep=lambda _s: None, on_change=notify)
        return runner, {"desk": sim}

    return Host(tmp_path, factory=factory, alive=lambda pid: pid == 4242 and sim.alive, clock=clock)


def test_a_left_express_still_on_the_login_window_is_cancelled_and_closed(tmp_path):
    from companion.express_operator import host as host_module

    sim = _BoundExpress(RECIPE.data)
    sim.dialog("login", "เข้าสู่ระบบ/LogIn", "รหัสผู้ใช้", buttons=("OK", "Cancel"))
    host_module.mark_opened(tmp_path, 4242, "b9", "", str(tmp_path / "book"))
    host_module.mark_opened(tmp_path, 777, "b8", "", str(tmp_path / "book"))  # 进程已经没了：只删记录
    host = _orphan_host(tmp_path, sim)
    assert host.recover_orphans() == [4242]
    assert not sim.alive and host_module.read_marks(tmp_path) == {}


def test_a_left_express_in_the_middle_of_a_document_is_abandoned_then_closed(tmp_path):
    from dataclasses import asdict

    from _fake_express import NEW, samples
    from companion.express_operator import host as host_module
    from companion.express_operator.batch import BatchState, BatchStore, DocState

    draft = samples()[0]
    sim = _BoundExpress(RECIPE.data)
    sim.form = draft.doc_type
    sim._command(NEW)  # 新建：单据窗在编辑中、还没录明细（CC 在这时被重启）
    state = BatchState("b1", "x", "TEST", str(tmp_path / "book"),
                       [DocState(draft.doc_id, draft.doc_type, asdict(draft), status="entering")], status="running",
                       started=900.0)
    BatchStore(tmp_path / "batches").save(state)
    host_module.mark_opened(tmp_path, 4242, "b1", "", str(tmp_path / "book"))
    host = _orphan_host(tmp_path, sim)
    host.recover_orphans()
    [found] = host.snapshots()
    assert found["docs"][0]["status"] == "waiting" and found["docs"][0]["reason"] == "cc_restarted"
    assert not sim.editing and not sim.alive  # 按放弃取消了编辑（还没明细：什么都没写），再正常退出


def test_an_express_this_cc_did_not_record_is_never_touched(tmp_path):
    sim = _BoundExpress(RECIPE.data)
    host = _orphan_host(tmp_path, sim)
    assert host.recover_orphans() == [] and sim.alive


def test_left_express_is_only_tidied_after_the_grace_while_nothing_runs(tmp_path):
    from companion.express_operator import host as host_module

    sim = _BoundExpress(RECIPE.data)
    sim.dialog("login", "เข้าสู่ระบบ/LogIn", "รหัสผู้ใช้", buttons=("OK", "Cancel"))
    host_module.mark_opened(tmp_path, 4242, "b9", "", str(tmp_path / "book"))
    now = [1000.0]
    host = _orphan_host(tmp_path, sim, clock=lambda: now[0])
    host.idle()
    assert sim.alive  # 刚启动：先等原批次的单派回来接管
    now[0] += host_module.ORPHAN_GRACE_S
    host.idle()
    assert not sim.alive
