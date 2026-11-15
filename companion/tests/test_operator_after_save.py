# -*- coding: utf-8 -*-
"""按下「保存」之后出现认不出的画面：先读账套核这张存没存，任何情况都不能让上层把「其实已存」当成没录成去重录（11-12 测试机2：
服务品赊销单存档途中 Express 问销项税口径，配方没有这一条，引擎报转人工，单其实已完整存进 IV0000043）。

按类：不只这一个框，所有「保存键按下之后」的意外都走同一条：账套里有这张 → 已写入 + 单号，不放弃、不作废；核不出来 → 转人工，
不碰 Express 上的东西，并标上「再交来先读账套」（``recheck``），重试时先核账本、绝不从头重录。"""
from __future__ import annotations

from _fake_express import samples
from test_operator_batch import make
from test_operator_engine import run_docs

from companion.express_operator import contract as K


def test_a_document_saved_before_an_unknown_box_is_reported_written_with_its_number():
    recs, sim, _, _ = run_docs([samples()[0]], with_ai=False, inject={"odd_after_save": True})
    rec = recs[0]
    assert rec["outcome"] == "saved" and rec["docnum"] == "RR0000001" and rec["readback"]["ok"]
    assert rec["voided"] == [] and not rec.get("placeholders")
    assert sum(1 for r in sim.book["APTRN"] if r["DOCSTAT"] != "C") == 1  # 账套里就这一张、没被作废


def test_an_unknown_box_in_the_middle_of_saving_is_not_abandoned_and_is_rechecked_before_any_retry():
    recs, sim, _, _ = run_docs([samples()[0]], with_ai=False, inject={"odd_mid_save": True})
    rec = recs[0]
    # 录到明细时 Express 已写下金额 0 的单头：账套里留的是半截单，带单号交人
    assert rec["outcome"] == "need_human" and rec["reason"] == "partial_saved"
    assert rec["partial"]["docnum"] == "RR0000001" and "total" in rec["partial"]["missing"]
    assert rec["voided"] == [] and rec.get("recheck") is True and rec.get("held") is True
    assert sim.dialogs  # 认不出的框原样留给人，没乱点、没按取消


def test_a_retry_after_an_unknown_box_mid_save_reads_the_book_and_does_not_enter_it_twice(tmp_path):
    r, sim, *_ = make(tmp_path, [samples()[0]], inject={"choice_mid_save": True})
    r.run()
    d = r.state.docs[0]
    assert d.status == K.WAITING and d.recheck
    sim._press(sim.dialogs[-1], "แบบ ก")  # 人在 Express 上答了那个框，单存完了
    again = r.add(samples()[0], task="retry-1")  # 再交来：先读账套，人已经把它存完了
    done = r.run_one(again.doc_id)
    assert done.status == K.SAVED and done.docnum == "RR0000001"
    assert sum(1 for h in sim.book["APTRN"] if h["DOCSTAT"] != "C") == 1  # 没有重录出第二张
