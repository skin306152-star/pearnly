# -*- coding: utf-8 -*-
"""代录批次：进度落盘、暂停 / 停止 / 跳过、断点续录不重录、服务器断线、整批停的原因、探路、等账套、指定时刻开始。"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from _fake_express import FakeBook, FakeExpress, samples

from companion.express_operator import contract as K
from companion.express_operator import recipe as R
from companion.express_operator.batch import BatchState, BatchStore, Runner, no_lock
from companion.express_operator.draft import Draft, Line
from companion.express_operator.engine import Engine, NeedHuman

RECIPE = R.load()


class FakeSession:
    def __init__(self, sim, *, fail_open=None):
        self.sim, self.fail_open = sim, fail_open
        self.opened = self.closed = 0

    def ensure_open(self):
        if self.fail_open:
            raise NeedHuman(self.fail_open)
        self.sim.alive = True
        self.sim.dialogs = []
        self.opened += 1

    def close(self):
        self.sim.alive = False
        self.closed += 1


def make(tmp_path, drafts=None, *, inject=None, **runner):
    sim = FakeExpress(RECIPE.data, inject=inject)
    book = FakeBook(sim)
    engine = Engine(sim, RECIPE, book, sleep=lambda _s: None)
    state = BatchState.new("บริษัท ทดสอบ จำกัด", "TEST", str(tmp_path / "book"), drafts or samples())
    store = BatchStore(tmp_path / "batches")
    seen = []
    session = runner.pop("session", None) or FakeSession(sim)
    r = Runner(state, store, engine, session, book, lock=runner.pop("lock", no_lock), on_change=seen.append,
               sleep=lambda _s: None, **runner)
    return r, sim, session, store, seen


def test_whole_batch_saved_and_express_closed(tmp_path):
    r, sim, session, store, seen = make(tmp_path)
    state = r.run()
    assert state.status == K.BATCH_FINISHED
    assert [d.status for d in state.docs] == [K.SAVED] * 4
    assert session.closed == 1  # 录完正常退出，释放授权名额
    assert store.load(state.id).docs[0].docnum == "RR0000001"  # 进度落盘
    last = seen[-1]
    assert last["counts"] == {K.SAVED: 4} and last["total"] == 4 and last["status"] == K.BATCH_FINISHED


def test_a_document_needing_a_human_is_held_and_the_rest_continue(tmp_path):
    drafts = samples()
    drafts.insert(1, Draft("X", "RR", "2026-10-20", "NOPE", [Line("501780", "1", "1.00")], ref_no="Q"))
    r, *_ = make(tmp_path, drafts)
    state = r.run()
    assert state.status == K.BATCH_FINISHED
    held = state.doc("X")
    assert held.status == K.WAITING and held.reason == "party_missing"
    assert sum(d.status == K.SAVED for d in state.docs) == 4


def test_stop_finishes_the_current_document_then_stops(tmp_path):
    r, sim, session, *_ = make(tmp_path)
    original = r.engine.run

    def run_then_stop(draft):
        rec = original(draft)
        r.stop()
        return rec

    r.engine.run = run_then_stop
    state = r.run()
    assert state.status == K.BATCH_STOPPED
    assert [d.status for d in state.docs] == [K.SAVED, K.PENDING, K.PENDING, K.PENDING]
    assert session.closed == 1


def test_skip_marks_pending_document(tmp_path):
    r, *_ = make(tmp_path)
    r.skip("S-HP-1")
    state = r.run()
    assert state.doc("S-HP-1").status == K.SKIPPED
    assert sum(d.status == K.SAVED for d in state.docs) == 3


def test_resume_after_restart_does_not_enter_a_saved_document_twice(tmp_path):
    r, sim, session, store, _ = make(tmp_path, samples()[:1])
    # 上次录到一半 CC 被关：单其实已经存上了，批次文件里还是「正在录」
    first = r.state.docs[0]
    first.status, first.counts_before = K.ENTERING, r.book.counts()
    sim.alive = True
    Engine(sim, RECIPE, r.book, sleep=lambda _s: None).run(Draft(**first.draft))
    store.save(r.state)
    state = r.run()
    assert state.docs[0].status == K.SAVED and state.docs[0].docnum == "RR0000001"
    assert {"reason": "express_restarted"} in state.docs[0].reports
    assert sim.counters == {"RR": 1}  # 没有再录一张


def test_resume_finds_a_blank_placeholder_and_asks_a_human(tmp_path):
    r, sim, *_ = make(tmp_path, samples()[:1])
    doc = r.state.docs[0]
    doc.status, doc.counts_before = K.ENTERING, r.book.counts()
    sim.book["APTRN"].append({"_row": 0, "_del": False, "DOCNUM": "RR0000009", "DOCDAT": "2026-10-20",
                              "SUPCOD": "P001", "REFNUM": "INV-7781", "NETVAL": 0.0, "VATAMT": 0.0, "NETAMT": 0.0})
    state = r.run()
    assert state.docs[0].status == K.WAITING and state.docs[0].reason == "placeholder_left"
    assert "RR0000009" in state.docs[0].detail


def test_network_error_waits_reopens_and_does_not_enter_twice(tmp_path):
    checks = iter([False, False, True])
    r, sim, session, *_ = make(tmp_path, samples()[2:3] + samples()[3:], inject={"crash_on_save:5001": True},
                               reachable=lambda: next(checks))
    state = r.run()
    crashed, after = state.docs
    assert state.status == K.BATCH_FINISHED and crashed.crashes == 1
    assert session.opened == 2  # 服务器恢复后重开 Express
    # 崩在保存那一下：Express 已写了金额 0 的空单头占号 → 交给人作废，不重录
    assert crashed.status == K.WAITING and crashed.reason == "placeholder_left" and crashed.detail == "IV0000001"
    assert len(sim.book["ARTRN"]) == 2 and after.status == K.SAVED  # 下一张照录


def test_network_down_too_long_stops_the_batch(tmp_path):
    r, *_ = make(tmp_path, samples()[2:3], inject={"crash_on_save:5001": True}, reachable=lambda: False,
                 net_wait_s=120, net_poll_s=60)
    state = r.run()
    assert state.status == K.BATCH_STOPPED and state.reason == "network_down"
    assert state.docs[0].status == K.ENTERING  # 续录时先读账套判断


@pytest.mark.parametrize("reason", ["login_missing", "company_mismatch", "license"])
def test_batch_stoppers_stop_before_entering(tmp_path, reason):
    sim = FakeExpress(RECIPE.data)
    r, *_ = make(tmp_path, session=FakeSession(sim, fail_open=reason))
    state = r.run()
    assert state.status == K.BATCH_STOPPED and state.reason == reason
    assert all(d.status == K.PENDING for d in state.docs)


def test_probe_mismatch_holds_that_document_type(tmp_path):
    r, *_ = make(tmp_path, probe=lambda t: {"ok": t != "IV", "missing": ["รหัสลูกค้า"] if t == "IV" else []})
    state = r.run()
    iv = state.doc("S-IV-1")
    assert iv.status == K.WAITING and iv.reason == "recipe_mismatch"
    assert sum(d.status == K.SAVED for d in state.docs) == 3


def test_writer_busy_waits_for_the_other_computer(tmp_path):
    class Busy(Exception):
        pass

    @contextmanager
    def busy(_dir):
        raise Busy()
        yield

    r, *_ = make(tmp_path, lock=busy)
    state = r.run()
    assert state.status == K.BATCH_READY and state.reason == "account_busy"


def test_scheduled_start_waits_until_the_time(tmp_path):
    now = [1000.0]
    r, *_ = make(tmp_path, samples()[:1], clock=lambda: now[0])
    r.state.start_at = 1100.0
    r.sleep = lambda s: now.__setitem__(0, now[0] + s)
    state = r.run()
    assert state.status == K.BATCH_FINISHED and state.started >= 1100.0


def test_every_reason_has_a_level():
    for reason in ("party_missing", "duplicate_ref", "screen_locked", "placeholder_left", "network_down"):
        assert K.level(reason) in (K.AUTO, K.AUTO_REPORTED, K.HUMAN)
    assert K.BATCH_STOPPERS <= set(K.REASONS)


def test_void_gate_only_allows_documents_new_in_this_batch_and_not_saved(tmp_path):
    """两道硬保护（10-26 实机误作废 HP0000026 / IV0000022 / HS0000007 之后加）。"""
    drafts = [samples()[1], Draft("BAD", "HP", "2026-10-21", "P002", [Line("501871", "1", "1.00")], ref_no="Z")]
    r, *_ = make(tmp_path, drafts, inject={"typo_price:P002": "9"})
    r.book.sim.book["APTRN"].append({"_row": 0, "_del": False, "DOCNUM": "HP0000099", "SUPCOD": "P002", "NETAMT": 1.0})
    state = r.run()
    assert r.may_void("HP0000099") is False  # 批次开始前就有的单：不许
    saved = [d.docnum for d in state.docs if d.status == K.SAVED]
    for docnum in saved:
        assert r.may_void(docnum) is False  # 本批次已存好的单：不许
    assert all(r2.get("DOCSTAT") != "C" for r2 in r.book.sim.book["APTRN"] if r2["DOCNUM"] in saved + ["HP0000099"])


def test_a_held_document_handed_back_after_a_choice_is_entered_again(tmp_path):
    """10-30 实机：任务里选了选项，站点按原批次号把「等人」的这张交回，CC 原样回了上次的结果、没去录。改为换上新草稿重录。"""
    from dataclasses import replace

    drafts = samples()
    drafts.insert(1, Draft("X", "RR", "2026-10-20", "NOPE", [Line("501780", "1", "1.00")], ref_no="Q"))
    r, *_ = make(tmp_path, drafts)
    r.run()
    assert r.state.doc("X").status == K.WAITING
    fixed = replace(samples()[0], doc_id="X", ref_no="Q")
    held = r.add(fixed)
    assert held.status == K.PENDING and held.reason == "" and held.draft["party_code"] == fixed.party_code
    done = r.run_one("X")
    assert done.status == K.SAVED and done.docnum
    assert r.add(fixed).status == K.SAVED  # 录成的再交回不重录
