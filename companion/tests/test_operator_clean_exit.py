# -*- coding: utf-8 -*-
"""代录中途停下的统一收尾（11-24 主会话定）。

1. 唯一收尾入口：保存键按下前不管什么原因停下——转人工、Express 拦单、人点停止、AI 预算用完、工具层报错、没接住的异常——
   都经 ``Engine._leave`` 按放弃收：单据窗口不留在编辑中，账套里不留金额 0 的半截单（11-24 测试机2：点停止后直接关 Express，
   留下 RR0000309 单头金额 0 + 1 行明细、没有凭证和进项税）。
2. 关 Express 只有 ``Runner._close`` 一处，关之前单据还在编辑就先放弃；收不干净（认不出的框挡着）就不关，那张记 ``left_open``、
   Express 原样留给人。
3. 守门：别处直接关 Express、``Engine.run`` 多出不经 ``_leave`` 的出口，这里的静态检查就红。"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _fake_express import FakeBook, FakeExpress, samples
from test_operator_batch import make
from test_operator_engine import RECIPE

from companion.express_operator import contract as K
from companion.express_operator.engine import (LEFT_OPEN, STOPPED, Blocked, Engine, Interrupted, NeedHuman, Stopped,
                                               Unexpected)
from companion.express_operator.helper import BudgetStop

ROOT = Path(__file__).resolve().parents[1]
OPERATOR = ROOT / "src" / "companion" / "express_operator"


def _live_zero_heads(sim):
    """账套里没作废、金额 0 的单头（放弃没收干净、关 Express 留下的半截单就是这个形状）。"""
    return [h["DOCNUM"] for t in ("APTRN", "ARTRN") for h in sim.book[t]
            if h.get("DOCSTAT") != "C" and not float(h.get("NETAMT") or 0)]


EXITS = [
    ("stopped", lambda: Stopped(), STOPPED, STOPPED),
    ("budget", lambda: BudgetStop("budget"), "need_human", "budget"),
    ("tool_error", lambda: Unexpected("refused: command 53504 is disabled"), "need_human", "unknown_screen"),
    ("crash_in_code", lambda: RuntimeError("boom"), "need_human", "unknown_screen"),
    ("need_human", lambda: NeedHuman("unknown_screen", "x"), "need_human", "unknown_screen"),
    ("interrupted", lambda: Interrupted("before_save", "a click"), "need_human", "interrupted"),
    ("blocked", lambda: Blocked("negative_stock", "x"), "blocked", "negative_stock"),
]


@pytest.mark.parametrize("index", [0, 2], ids=["RR", "IV"])
@pytest.mark.parametrize("name,error,outcome,reason", EXITS, ids=[e[0] for e in EXITS])
def test_every_stop_before_save_leaves_no_open_document_and_no_half_document(index, name, error, outcome, reason):
    sim = FakeExpress(RECIPE.data)
    engine = Engine(sim, RECIPE, FakeBook(sim), sleep=lambda _s: None)

    def stop(*_a, **_k):
        raise error()

    engine.check_totals = stop  # 明细已录完（Express 已写下金额 0 的单头占号）、保存键还没按
    rec = engine.run(samples()[index])
    assert (rec["outcome"], rec["reason"]) == (outcome, reason)
    assert not sim.editing and not rec.get("held")
    assert _live_zero_heads(sim) == []  # 有明细时取消即存档 → 作废：账套里只有作废单
    assert rec["voided"] and sim.esc_pressed == 0


def test_a_stop_while_the_person_is_still_clicking_still_cleans_up():
    """收尾途中再遇到「停止」「一直有人在点」不中断：这一步本身就是停下后的收尾。"""
    sim = FakeExpress(RECIPE.data)
    calls = []

    def presence():
        calls.append(1)
        if len(calls) > 3:
            raise Stopped()
        return False

    engine = Engine(sim, RECIPE, FakeBook(sim), presence=presence, sleep=lambda _s: None)
    rec = engine.run(samples()[0])
    assert rec["outcome"] == STOPPED and not sim.editing and _live_zero_heads(sim) == []


def test_clicking_stop_mid_document_stops_the_batch_with_nothing_half_written(tmp_path):
    """11-24 测试机2 复现：暂停等人时点停止，原来直接关 Express → 半截单。现在先收、再关。"""
    r, sim, session, store, _ = make(tmp_path, samples()[:2])
    real = r.engine.check_totals

    def stop_once(*a, **k):
        r.engine.check_totals = real
        raise Stopped()

    r.engine.check_totals = stop_once
    state = r.run()
    first, second = state.docs
    assert state.status == K.BATCH_STOPPED and (first.status, first.reason, first.recheck) == (K.SKIPPED, "stopped", True)
    assert first.voided and second.status == K.PENDING
    assert session.closed == 1 and not sim.editing and _live_zero_heads(sim) == []
    again = r.add(samples()[0], task="w-again")  # 人点「重新开始」：先读账套（只有作废单）、从头录
    assert again.status in (K.PENDING, K.ENTERING)


def test_closing_express_with_a_document_still_in_edit_abandons_it_first(tmp_path):
    """防线二：任何关 Express 的地方（批次收尾、闲置、建主档前、断网恢复）都经 ``_close``，单据还在编辑就先放弃。"""
    r, sim, session, *_ = make(tmp_path, samples()[:1])
    draft = samples()[0]
    r.engine.begin(draft)
    r.engine._before = r.engine.book.counts()
    r.engine.ensure_form(draft.doc_type)
    r.engine.command("new")
    r.engine.walk_header("h")
    r.engine.enter_lines()  # 录到一半（Express 已写下金额 0 的单头）
    r.state.started = 1.0
    assert sim.editing and _live_zero_heads(sim)
    r.finish()
    assert session.closed == 1 and not sim.editing and _live_zero_heads(sim) == []


def test_a_document_that_cannot_be_cancelled_keeps_express_open_and_says_so(tmp_path):
    r, sim, session, store, _ = make(tmp_path, samples()[:1])
    draft = samples()[0]
    r.engine.begin(draft)
    r.engine._before = r.engine.book.counts()
    r.engine.ensure_form(draft.doc_type)
    r.engine.command("new")
    r.engine.walk_header("h")
    r.engine.enter_lines()
    r.state.started, r.state.current = 1.0, draft.doc_id
    sim.dialog("odd", "ExpressI", "something nobody knows")  # 认不出的框挡着：不乱点
    r.finish()
    doc = r.state.docs[0]
    assert session.closed == 0 and sim.alive and sim.editing  # 不关：关了就是半截单
    assert (doc.status, doc.reason, doc.held) == (K.WAITING, LEFT_OPEN, True) and r.state.held == doc.doc_id
    assert store.load(r.state.id).held == doc.doc_id


def test_left_open_is_a_reason_both_sides_know():
    from companion.express_operator.engine import LEFT_OPEN as code

    assert K.REASONS[code] == K.HUMAN and code == "left_open"


# ── 守门：关 Express、停下出口只有一个入口 ──────────────────────────────────────────────────────────────────

def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                yield node.name, inner


def test_express_is_only_closed_in_one_place():
    """``session.close()`` 只在 ``Runner._close`` 里；``close_express`` 动作只在 ``Session`` 的两处（正常退出、收拾还没进公司的）。
    新加的关法不经 ``_close`` 就红：关之前要先看单据是不是还在编辑。"""
    allowed_close = {("batch.py", "_close")}
    allowed_action = {("session.py", "close"), ("session.py", "leave_unopened"), ("win32_desk.py", "do"),
                      ("win32_desk.py", "close_express"), ("bg_desk.py", "do")}
    found_close, found_action = set(), set()
    for path in OPERATOR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func, node in _calls(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "close"
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "session"):
                found_close.add((path.name, func))
            if isinstance(node, ast.Constant) and node.value == "close_express":
                found_action.add((path.name, func))
    assert found_close == allowed_close
    assert found_action <= allowed_action


def test_engine_run_has_one_exit_for_every_stop():
    """``Engine.run`` 只接两种异常：``Crash``（什么都不能点）和其余一切（交 ``_leave``）。多一个 except 不经 ``_leave`` 就红。"""
    tree = ast.parse((OPERATOR / "engine.py").read_text(encoding="utf-8"))
    engine = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine")
    run = next(n for n in engine.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    entry = next(t for t in run.body if isinstance(t, ast.Try)
                 and any(isinstance(n, ast.Attribute) and n.attr == "run_fixed" for n in ast.walk(t)))
    assert [ast.unparse(h.type) for h in entry.handlers] == ["Crash", "Exception"]
    assert any(isinstance(n, ast.Attribute) and n.attr == "_leave" for n in ast.walk(entry.handlers[1]))


def test_budget_after_save_still_stops_the_batch_with_its_reason():
    sim = FakeExpress(RECIPE.data)
    engine = Engine(sim, RECIPE, FakeBook(sim), sleep=lambda _s: None)
    real = engine.saved_check

    def budget(*a, **k):
        if engine._save_pressed:
            raise BudgetStop("budget")
        return real(*a, **k)

    engine.saved_check = budget
    engine._booked = lambda: ""
    engine._book_state = lambda: {}
    rec = engine.run(samples()[0])
    assert rec["reason"] == "budget" and rec["stop_batch"] and rec["held"]
