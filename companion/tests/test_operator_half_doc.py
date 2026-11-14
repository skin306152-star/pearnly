# -*- coding: utf-8 -*-
"""保存途中遇到认不出的框（按类，11-13 测试机2 半截单 IV0000045）：

1. 不在半写状态下退出 Express：框原样留给人（``held``），批次收尾 / 闲置 / 收拾上次留下的 Express 都不关；同一本账套的别的单等着；
2. 读账套判这张「已完整 / 半截 / 没写」（单头金额、明细行、凭证 GLJNL、税记录），半截带单号与缺什么回报（原因 ``partial_saved``）；
3. 再交同一张先读账套：人答完存好了回原单号，半截单不重录、不作废，确认一行都没有才从头录；
4. 配方外的业务选择框（两个以上业务钮）AI 只能看、不能点：不交 AI，工具层也拦。

四种单（赊购 RR、现购 HP、赊销 IV、现销 HS）同一条路。"""
from __future__ import annotations

from dataclasses import replace

import pytest
from _fake_express import FakeBook, FakeExpress, samples
from test_operator_batch import make
from test_operator_engine import RECIPE, run_docs
from test_operator_host import fake_factory, payload

from companion.express_operator import book as B
from companion.express_operator import contract as K
from companion.express_operator.engine import ChoiceBox, Engine, is_choice
from companion.express_operator.host import Host
from companion.secret_store import ExpressSettings


def _live_heads(sim, docnum):
    return [h for t in ("APTRN", "ARTRN") for h in sim.book[t] if h["DOCNUM"] == docnum and h.get("DOCSTAT") != "C"]


@pytest.mark.parametrize("index", [0, 1, 2, 3], ids=["RR", "HP", "IV", "HS"])
def test_an_unknown_choice_mid_save_leaves_a_half_document_reported_with_its_number_and_untouched(index):
    draft = samples()[index]
    recs, sim, site, _ = run_docs([draft], inject={"choice_mid_save": True})  # 接着 AI 也不让它点
    rec = recs[0]
    assert rec["outcome"] == "need_human" and rec["reason"] == "partial_saved"
    docnum = rec["partial"]["docnum"]
    assert docnum.startswith(draft.doc_type) and {"total", "journal"} <= set(rec["partial"]["missing"])
    assert rec["held"] and rec["recheck"] and rec["voided"] == []
    assert site.calls == []  # 配方外的业务选择：不交 AI
    assert [d["name"] for d in sim.dialogs] == ["choice"] and sim.alive and sim.editing  # 框原样留着
    assert len(_live_heads(sim, docnum)) == 1 and sim.esc_pressed == 0 and sim.trap_saves == 0


def test_the_ai_tools_refuse_any_input_while_a_business_choice_box_is_open():
    sim = FakeExpress(RECIPE.data)
    engine = Engine(sim, RECIPE, FakeBook(sim), sleep=lambda _s: None)
    engine.begin(None, "RR")
    sim.dialog("x", "ExpressI", "เลือก", buttons=("แบบ ก", "แบบ ข"))
    for name, inp in (("left_click", {"coordinate": [10, 10]}), ("key", {"text": "Return"}), ("type", {"text": "1"}),
                      ("express_menu", {"path": ["2"]})):
        with pytest.raises(ChoiceBox):
            engine.member(name, inp)
    assert engine.member("screenshot", {})  # 看可以
    assert sim.dialogs  # 一下都没点


def test_only_two_or_more_business_buttons_make_a_choice_box():
    assert is_choice({"buttons": ["&เกณฑ์สิทธิ", "&เกณฑ์เงินสด"]})
    assert is_choice({"buttons": ["ใช่", "ไม่ใช่"]})  # 是 / 否也是替人做决定
    assert not is_choice({"buttons": ["&ตกลง", "&ยกเลิก"]})
    assert not is_choice({"buttons": ["OK"]}) and not is_choice({})


def test_a_held_batch_keeps_express_open_and_the_next_document_waits(tmp_path):
    drafts = [samples()[2], replace(samples()[3], doc_id="S-HS-2")]
    r, sim, session, store, _ = make(tmp_path, drafts, inject={"choice_mid_save": True})
    state = r.run()
    held, rest = state.docs
    assert held.status == K.WAITING and held.reason == "partial_saved" and held.held and state.held == held.doc_id
    assert rest.status == K.PENDING and state.reason == "account_busy"  # 这本账套的别的单等着
    assert session.closed == 0 and sim.alive and sim.dialogs  # 没退出 Express
    r.finish()
    assert session.closed == 0 and sim.alive  # 收尾也不关
    assert store.load(state.id).held == held.doc_id  # 落盘：CC 重启后也知道


def test_after_the_person_answers_the_box_the_retry_returns_the_same_number_and_the_rest_continue(tmp_path):
    drafts = [samples()[2], replace(samples()[3], doc_id="S-HS-2")]
    r, sim, session, *_ = make(tmp_path, drafts, inject={"choice_mid_save": True})
    r.run()
    number = r.state.docs[0].partial["docnum"]
    retry = r.add(samples()[2], task="retry-1")
    assert retry.status == K.WAITING and retry.held  # 框还在：原样回结论，不碰 Express
    assert [d["name"] for d in sim.dialogs] == ["choice"]
    sim._press(sim.dialogs[-1], "แบบ ก")  # 人在 Express 上答了，单存完
    sim.inject.pop("choice_mid_save")  # 下一张不再问
    again = r.add(samples()[2], task="retry-2")
    assert again.status == K.SAVED and again.docnum == number and not r.state.held
    r.run_one("S-HS-2")
    assert r.state.doc("S-HS-2").status == K.SAVED
    assert len([h for h in sim.book["ARTRN"] if h["DOCNUM"].startswith("IV")]) == 1  # 没录出第二张


def test_a_half_document_handed_back_after_express_was_closed_is_not_entered_again(tmp_path):
    r, sim, *_ = make(tmp_path, [samples()[0]], inject={"choice_mid_save": True})
    r.run()
    d = r.state.docs[0]
    sim.dialogs, sim.alive = [], False  # 人把 Express 关了：单停在半截
    again = r.add(samples()[0], task="retry-1")
    done = r.run_one(again.doc_id)
    assert done.status == K.WAITING and done.reason == "partial_saved" and done.partial["docnum"] == "RR0000001"
    assert not r.state.held  # Express 没了：放手
    assert len(sim.book["APTRN"]) == 1 and d.voided == []  # 不重录、不作废


def test_the_book_state_needs_total_lines_journal_and_tax_to_count_as_complete():
    draft = samples()[2]
    want = float(draft.totals()["total"])
    head = {"DOCNUM": "IV0000045", "DOCDAT": draft.date, "CUSCOD": draft.party_code, "DOCSTAT": "N", "NETAMT": 0.0}
    line = {"DOCNUM": "IV0000045"}
    rows = {"ARTRN": [head], "STCRD": [line], "ISVAT": [], "GLJNL": []}
    found = B.find_saved(draft, rows)  # 测试机2 IV0000045 的原生形状
    assert found["docnum"] == "" and found["partial"] == [{"docnum": "IV0000045", "missing": ["total", "journal", "vat"]}]
    rows = {"ARTRN": [dict(head, NETAMT=want)], "STCRD": [line], "ISVAT": [line], "GLJNL": []}
    assert B.find_saved(draft, rows)["partial"][0]["missing"] == ["journal"]  # 金额落了、凭证没写也不算存完
    rows["GLJNL"] = [{"VOUCHER": "IV0000045"}]
    assert B.find_saved(draft, rows) == {"docnum": "IV0000045", "partial": [], "placeholders": []}
    cash_basis = replace(draft, srv_vattyp="2")  # 服务品收付实现：原生不写税记录
    rows["ISVAT"] = []
    assert B.find_saved(cash_basis, rows)["docnum"] == "IV0000045"
    rows["ARTRN"] = [dict(head, DOCSTAT="C")]  # 作废了的不算这张
    assert B.find_saved(draft, rows) == {"docnum": "", "partial": [], "placeholders": []}


def _settings(tmp_path):
    return ExpressSettings(method="dbf", express_root="", express_exe="", account_dir=str(tmp_path / "book"),
                           account_company="x", account_set="TEST", account_set_row=0)


def test_the_host_reports_the_half_number_and_where_express_is_held_and_idle_does_not_close_it(tmp_path):
    sims = []
    factory = fake_factory(sims)

    def with_choice(*args, **kwargs):
        runner, parts = factory(*args, **kwargs)
        sims[-1].inject["choice_mid_save"] = True
        return runner, parts

    host = Host(tmp_path, factory=with_choice, clock=lambda: 10_000.0)
    st = _settings(tmp_path)
    first = host.handle("t1", payload("RR", "P001", "501780", "2", "10.00", 1, 2), st)
    assert first["status"] == "human" and first["reason"] == "partial_saved"
    meta = first["meta"]
    assert meta["docnum"] == "RR0000001" and meta["partial"]["docnum"] == "RR0000001"
    assert meta["held"]["account_set"] == "TEST" and meta["held"]["computer"]
    second = host.handle("t2", payload("HS", "CASH", "500310", "1", "53.50", 2, 2, ref="R2"), st)
    assert second["status"] == "waiting" and second["reason"] == "account_busy"  # 等账套，不录、不关
    host.last_used["b1"] = 0.0
    host.idle()
    assert sims[0].alive and sims[0].dialogs  # 闲置收尾也不退出 Express
    other = host.handle("t3", payload("HS", "CASH", "500310", "1", "53.50", 1, 1, batch="b2", ref="R3"), st)
    assert other["status"] == "waiting" and other["reason"] == "account_busy"  # 同一本账套别的批次也等着
    assert K.REASONS["partial_saved"] == K.HUMAN


def test_a_held_express_left_by_a_restart_is_not_tidied_away(tmp_path):
    from companion.express_operator import host as H

    r, sim, *_ = make(tmp_path / "x", [samples()[0]], inject={"choice_mid_save": True})
    r.run()
    root = tmp_path / "op"
    r.store.root = root / "batches"
    r.store.save(r.state)
    H.mark_opened(root, 4242, r.state.id, "ExpressI.exe", r.state.account_dir)
    touched = []
    host = Host(root, factory=lambda *a, **k: touched.append(a) or (_ for _ in ()).throw(AssertionError("no")),
                alive=lambda _pid: True)
    assert host.recover_orphans() == [] and touched == []
    assert "4242" in H.read_marks(root)  # 记录留着


def test_documents_behind_a_held_express_wait_like_express_is_open(monkeypatch, tmp_path):
    """排在后面的单（同一本账套）：按现成的「Express 正在使用此账套」等（ACCOUNT_BUSY_LOCKED，服务端这本账套歇一会儿再派、
    别的账套照常领），不另起状态、不加文案。"""
    import sys
    from types import SimpleNamespace

    from test_operator_host import Queue

    from companion import worker
    from companion.express_operator import host as host_module

    fake = SimpleNamespace(handle=lambda *_a, **_k: {"status": "waiting", "reason": "account_busy", "express_open": True,
                                                     "meta": {"batch": "b"}})
    monkeypatch.setattr(host_module, "host", lambda notify=None: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    qc = Queue()
    st = ExpressSettings(method="dbf", express_root="", express_exe=r"\\s\ExpressI.exe", account_dir=str(tmp_path),
                         account_company="x", account_set="TEST", account_set_row=0)
    out = worker._handle_operator_task(SimpleNamespace(ui_lang="zh"), qc, SimpleNamespace(id="t2", payload={}, invoice_no=""), st)
    assert out == "waiting_lock" and qc.acks == [("failed", "t2", "ACCOUNT_BUSY_LOCKED", "waiting_lock")]


def test_after_a_restart_a_still_running_held_express_keeps_its_account_set_until_the_person_saves(tmp_path):
    """CC 重启后内存里没有那一批：本机记着、进程还在的 Express 属于留着等人的批次 → 同一本账套的新批次照样等；人答完、单在
    账套里存完 → 放手并记下，再交那张回原单号（由人在任务里点重试 / 整批继续交回，CC 只读账套不碰 Express）。"""
    from companion.express_operator import host as H

    r, sim, *_ = make(tmp_path / "x", [samples()[0]], inject={"choice_mid_save": True})
    r.run()
    root = tmp_path / "op"
    r.store.root = root / "batches"
    r.store.save(r.state)
    H.mark_opened(root, 4242, r.state.id, "ExpressI.exe", r.state.account_dir)
    host = Host(root, factory=fake_factory([]), alive=lambda _pid: True)
    book = FakeBook(sim)
    import companion.express_operator.book as book_module

    original = book_module.DbfBook
    book_module.DbfBook = lambda _dir: book
    try:
        assert host._stored_held(r.state.account_dir) is True
        sim._press(sim.dialogs[-1], "แบบ ก")  # 人答了，单存完
        assert host._stored_held(r.state.account_dir) is False
    finally:
        book_module.DbfBook = original
    stored = r.store.load(r.state.id)
    assert stored.held == "" and stored.docs[0].status == K.SAVED and stored.docs[0].docnum == "RR0000001"
    assert host._stored_held(r.state.account_dir) is False  # Express 没了 / 不再占着


def _held_host(tmp_path):
    sims = []
    factory = fake_factory(sims)

    def with_choice(*args, **kwargs):
        runner, parts = factory(*args, **kwargs)
        sims[-1].inject["choice_mid_save"] = True
        return runner, parts

    clock = [10_000.0]
    host = Host(tmp_path, factory=with_choice, clock=lambda: clock[0])
    first = host.handle("t1", payload("RR", "P001", "501780", "2", "10.00", 1, 1), _settings(tmp_path))
    assert first["reason"] == "partial_saved" and first["meta"]["doc"]
    return host, sims[0], first, clock


def test_once_the_person_finishes_the_box_cc_reports_written_with_the_same_receipt(tmp_path):
    """主会话 11-14：人在 Express 里答完框、单存完后，CC 主动用原回执号补报「已写入 + 原单号」，不等人回来点重试。框还开着时
    不报；隔 HELD_CHECK_S 才看一次；报完就清掉、不重报。"""
    host, sim, first, clock = _held_host(tmp_path)
    host.note_late("b1", first["meta"]["doc"], "DELIVERY-7", "prof-1")
    assert host.late_results("prof-1") == []  # 框还开着
    sim._press(sim.dialogs[-1], "แบบ ก")
    assert host.late_results("prof-1") == []  # 还没到下一次看的时候
    clock[0] += 61
    assert host.late_results("other") == []  # 别的成员档案的不归它报
    clock[0] += 61
    got = host.late_results("prof-1")
    assert got == [{"batch": "b1", "doc": first["meta"]["doc"], "ack": "DELIVERY-7", "docnum": "RR0000001"}]
    host.late_done("b1", first["meta"]["doc"])
    clock[0] += 61
    assert host.late_results("prof-1") == []
    assert len(sim.book["APTRN"]) == 1  # 只读账套，没录第二张


def test_express_closed_by_hand_on_a_half_document_is_not_reported(tmp_path):
    host, sim, first, clock = _held_host(tmp_path)
    host.note_late("b1", first["meta"]["doc"], "DELIVERY-7", "prof-1")
    sim.dialogs, sim.alive = [], False  # 人把 Express 关了，单停在半截
    clock[0] += 61
    assert host.late_results("prof-1") == []
    assert not host.runners["b1"].state.held  # 放手了
    doc = host.runners["b1"].state.docs[0]
    assert doc.status == K.WAITING and doc.late_ack == ""


def test_the_writer_sends_the_late_receipt_and_stops_on_a_refusal(monkeypatch):
    import sys
    from types import SimpleNamespace

    from companion import worker
    from companion.express_operator import host as host_module

    done, sent = [], []
    fake = SimpleNamespace(idle=lambda: None, late_done=lambda b, d: done.append((b, d)),
                           late_results=lambda profile: [{"batch": "b1", "doc": "D1", "ack": "DELIVERY-7", "docnum": "RR0000001"}])
    monkeypatch.setattr(host_module, "_HOST", fake)
    monkeypatch.setitem(sys.modules, "companion.express_operator.host", host_module)

    class Qc:
        def __init__(self, fail=None):
            self.fail = fail

        def ack_success(self, task_id, extracted_invoice_no=None, meta=None, line_modes=None):
            if self.fail:
                raise self.fail
            sent.append((task_id, extracted_invoice_no, meta))
            return True

    cfg = SimpleNamespace(profile_id="prof-1")
    worker.operator_idle(cfg, Qc())
    assert sent == [("DELIVERY-7", "RR0000001", {"operator": {"batch": "b1", "doc": "D1", "docnum": "RR0000001"}})]
    assert done == [("b1", "D1")]
    done.clear()
    refused = RuntimeError("401")
    refused.response = SimpleNamespace(status_code=401)
    worker.operator_idle(cfg, Qc(refused))
    assert done == [("b1", "D1")]  # 服务端不收：不再试
    done.clear()
    worker.operator_idle(cfg, Qc(ConnectionError("down")))
    assert done == []  # 网络错：下次空闲再试


def test_a_held_human_receipt_notes_its_receipt_id_for_the_late_report(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    from test_operator_host import Queue

    from companion import worker
    from companion.express_operator import host as host_module

    noted = []
    fake = SimpleNamespace(handle=lambda *_a, **_k: {"status": "human", "reason": "partial_saved", "detail": "x",
                                                     "meta": {"batch": "b1", "doc": "D1", "held": {"computer": "PC"}}},
                           note_late=lambda *a: noted.append(a))
    monkeypatch.setattr(host_module, "host", lambda notify=None: fake)
    monkeypatch.setattr(sys, "platform", "win32")
    st = ExpressSettings(method="dbf", express_root="", express_exe=r"\\s\ExpressI.exe", account_dir=str(tmp_path),
                         account_company="x", account_set="TEST", account_set_row=0)
    out = worker._handle_operator_task(SimpleNamespace(ui_lang="zh", profile_id="prof-1"), Queue(),
                                       SimpleNamespace(id="DELIVERY-7", payload={}, invoice_no=""), st)
    assert out == "needs_review" and noted == [("b1", "D1", "DELIVERY-7", "prof-1")]
