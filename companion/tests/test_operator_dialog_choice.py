# -*- coding: utf-8 -*-
"""中途配方外的业务选择框带回对话（CC 桌面版 1.12.23 ③）：

1. 框不点、单据窗口不关、Express 留着；结果带框的样子 ``dialog``（标题、正文、按钮、单种、在哪一步、指纹）；
2. 录入中出的框回 ``dialog_choice``；保存途中出的、账套已留半截单的照旧 ``partial_saved``（1.12.22 补报不变），另带 ``dialog``；
3. 载荷带了人答过的答案（``dialog_answers`` 指纹 → 按钮）且按钮还在：照点、接着存完，汇报里记一条 ``dialog_answered``；
   指纹对不上 / 按钮不在：不点，照旧停下。"""
from __future__ import annotations

from dataclasses import replace

import pytest
from _fake_express import FakeBook, FakeExpress, samples
from test_operator_engine import RECIPE, run_docs

from companion.express_operator.engine import DialogHeld, Engine


def test_a_choice_mid_save_carries_the_box_and_keeps_the_half_document_reason():
    recs, sim, site, _ = run_docs([samples()[0]], inject={"choice_mid_save": True})
    rec = recs[0]
    assert rec["reason"] == "partial_saved" and rec["held"]
    box = rec["dialog"]
    assert box["buttons"] == ["แบบ ก", "แบบ ข"] and box["stage"] == "saving" and box["doc_type"] == "RR"
    assert "เลือกวิธีบันทึกรายการนี้" in box["text"] and len(box["digest"]) == 16
    assert [d["name"] for d in sim.dialogs] == ["choice"]  # 一下都没点


def test_a_remembered_answer_is_pressed_and_the_document_saves():
    first, *_ = run_docs([samples()[0]], inject={"choice_mid_save": True})
    digest = first[0]["dialog"]["digest"]
    draft = replace(samples()[0], dialog_answers={digest: "แบบ ข"})
    recs, sim, site, _ = run_docs([draft], inject={"choice_mid_save": True})
    rec = recs[0]
    assert rec["outcome"] == "saved" and rec["readback"]["ok"], rec
    assert {"reason": "dialog_answered", "digest": digest, "button": "แบบ ข"} in rec["reports"]
    assert sim.dialogs == [] and site.calls == []


@pytest.mark.parametrize("answers", [{"0123456789abcdef": "แบบ ข"}, None])
def test_a_wrong_fingerprint_or_a_missing_button_is_not_pressed(answers):
    first, *_ = run_docs([samples()[0]], inject={"choice_mid_save": True})
    digest = first[0]["dialog"]["digest"]
    draft = replace(samples()[0], dialog_answers=answers or {digest: "แบบ ค"})
    recs, sim, *_ = run_docs([draft], inject={"choice_mid_save": True})
    assert recs[0]["outcome"] == "need_human" and recs[0]["dialog"]["digest"] == digest
    assert [d["name"] for d in sim.dialogs] == ["choice"]


def test_a_choice_while_entering_stops_as_dialog_choice_on_the_editing_stage():
    sim = FakeExpress(RECIPE.data)
    engine = Engine(sim, RECIPE, FakeBook(sim), sleep=lambda _s: None)
    engine.begin(samples()[0])
    sim.dialog("x", "ExpressI", "เลือกคลัง", buttons=("คลัง 1", "คลัง 2"))
    engine.act([{"do": "wait", "ms": 0}])
    with pytest.raises(DialogHeld) as held:
        engine._recover("head", "box", "header filled", {})
    box = held.value.dialog
    assert held.value.reason == "dialog_choice" and box["stage"] == "editing" and box["buttons"] == ["คลัง 1", "คลัง 2"]
    assert sim.dialogs  # 没点


# ── ④ 对话里答了留着的框：核框没变、按钮还在才点；存完由留着等人的检查补报（1.12.23）──────────────────────
def test_an_answer_from_the_conversation_presses_the_held_box_and_the_document_completes(tmp_path):
    from test_operator_batch import make

    from companion.express_operator import contract as K

    r, sim, *_ = make(tmp_path, [samples()[0]], inject={"choice_mid_save": True})
    r.run()
    d = r.state.docs[0]
    assert d.status == K.WAITING and d.held and d.dialog["buttons"] == ["แบบ ก", "แบบ ข"]
    assert r.answer(d.doc_id, "0" * 16, "แบบ ข") == {"state": "changed", "dialog": d.dialog}  # 指纹不对：不点
    assert sim.dialogs
    assert r.answer(d.doc_id, d.dialog["digest"], "แบบ ค")["state"] == "no_button"
    assert r.answer(d.doc_id, d.dialog["digest"], "แบบ ข") == {"state": "pressed"}
    assert sim.dialogs == []
    assert r.still_held() is False  # 框没了、账套里存完了：记已写入、等补报
    assert d.status == K.SAVED and d.docnum.startswith("RR")
    assert sum(1 for h in sim.book["APTRN"] if h["DOCSTAT"] != "C") == 1


def test_an_answer_for_a_document_that_is_not_held_is_not_pressed(tmp_path):
    from test_operator_batch import make

    r, sim, *_ = make(tmp_path, [samples()[0]])
    r.run()
    assert r.answer(r.state.docs[0].doc_id, "0" * 16, "แบบ ข") == {"state": "not_held"}


def test_the_lease_reply_controls_are_taken_once_and_run_through_the_host(monkeypatch):
    import sys
    import types

    from companion import worker

    calls = []

    class FakeHost:
        def answer_dialog(self, batch, doc, digest, button):
            calls.append((batch, doc, digest, button))
            return {"state": "pressed"}

    module = types.ModuleType("companion.express_operator.host")
    module._HOST = FakeHost()
    monkeypatch.setitem(sys.modules, "companion.express_operator.host", module)

    class FakeQc:
        def __init__(self):
            self.controls = [{"action": "answer", "batch": "b1", "doc": "d1", "digest": "a" * 16, "button": "แบบ ข"},
                             {"action": "reboot", "batch": "b1"}]

        def take_operator_controls(self):
            out, self.controls = self.controls, []
            return out

    qc = FakeQc()
    out = worker.operator_controls(qc)
    assert calls == [("b1", "d1", "a" * 16, "แบบ ข")]
    assert [o["state"] for o in out] == ["pressed", "not_held"]  # 认不出的指令不执行
    assert worker.operator_controls(qc) == []  # 取一次就没了
    assert worker.operator_controls(object()) == []  # 旧的领单客户端没有这个方法
