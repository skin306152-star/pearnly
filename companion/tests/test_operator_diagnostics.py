# -*- coding: utf-8 -*-
"""代录的诊断（1.12.15 测试机实测后补，11-05）：转人工说明分清「这台没接 AI」与「AI 调用出错」，出错时 companion.log 记一行
（批次、单据、哪一步、错误码，不含截图与请求体）；接不上 AI 的原因装配时记一次；没存上的代录结果写进日志。"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from _fake_express import FakeBook, FakeExpress, samples

from companion import operator_ai as site_ai
from companion.config import CompanionConfig
from companion import worker
from companion.express_operator import recipe as R
from companion.express_operator.engine import Engine
from companion.express_operator.helper import ComputerUseHelper

RECIPE = R.load()


def run_one(helper, inject):
    sim = FakeExpress(RECIPE.data, inject=inject)
    engine = Engine(sim, RECIPE, FakeBook(sim), helper=helper, sleep=lambda _s: None)
    engine.batch_id = "B-7"
    return engine.run(samples()[1])


def test_without_ai_the_note_says_ai_is_not_connected():
    rec = run_one(None, {"typo_price:P002": "9"})
    assert rec["reason"] == "unknown_screen"
    assert "ai not connected" in rec["detail"] and "no helper" not in rec["detail"]


def test_an_ai_call_error_is_named_in_the_note_and_logged_without_the_screenshot(caplog):
    helper = ComputerUseHelper(lambda kind, request: {"error": "http_404"}, sleep=lambda _s: None)
    with caplog.at_level(logging.INFO):
        rec = run_one(helper, {"typo_price:P002": "9"})
    assert rec["reason"] == "unknown_screen" and "ai unavailable: http_404" in rec["detail"]
    lines = [r for r in caplog.records if "operator ai unavailable" in r.getMessage()]
    assert lines and all(r.levelno == logging.WARNING for r in lines)
    message = lines[0].getMessage()
    assert "batch=B-7" in message and "doc=S-HP-1" in message and "where=" in message and "error=http_404" in message
    assert "iVBOR" not in caplog.text and "messages" not in caplog.text  # 不带截图、不带请求体


def test_why_the_ai_is_not_connected_is_logged_once(caplog, monkeypatch):
    monkeypatch.setattr(site_ai, "_NOTED", set())
    with caplog.at_level(logging.INFO):
        assert site_ai.transport_for(CompanionConfig(bearer_token="", backend_url="https://a.test"), "B1") is None
        assert site_ai.transport_for(CompanionConfig(bearer_token="", backend_url="https://a.test"), "B2") is None
        assert site_ai.transport_for(CompanionConfig(bearer_token="exp_x", backend_url=""), "B3") is None
    found = [r.getMessage() for r in caplog.records if "operator ai not connected" in r.getMessage()]
    assert found == ["operator ai not connected · reason=no_token · site=https://a.test",
                     "operator ai not connected · reason=no_site · site=-"]


class Queue:
    def __init__(self):
        self.acks = []

    def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
        self.acks.append((task_id, error_code, outcome))


def test_an_operator_result_that_was_not_saved_is_written_to_the_log(caplog):
    task = SimpleNamespace(id="t9", invoice_no="", payload={"operator": {"batch": {"id": "b1", "doc": "CC11214-0002"}}})
    result = {"status": "human", "reason": "menu_choice", "detail": "HP: " + "x" * 400,
              "meta": {"batch": "b1", "doc_type": "HP", "menu_options": ["a", "b"]}}
    with caplog.at_level(logging.INFO):
        out = worker._ack_operator(SimpleNamespace(ui_lang="zh"), Queue(), task, result, {"operator": result["meta"]})
    assert out == "needs_review"
    line = next(r for r in caplog.records if "operator result" in r.getMessage())
    text = line.getMessage()
    assert line.levelno == logging.WARNING
    assert "doc=CC11214-0002" in text and "status=human" in text and "reason=menu_choice" in text
    assert "menu_options=2" in text and "x" * 196 in text and "x" * 201 not in text  # 说明只取前 200 字
    caplog.clear()
    with caplog.at_level(logging.INFO):
        worker._ack_operator(SimpleNamespace(ui_lang="zh"), Queue(), task,
                             {"status": "waiting", "reason": "network_down", "meta": {}}, {"operator": {}})
    line = next(r for r in caplog.records if "operator result" in r.getMessage())
    assert line.levelno == logging.INFO and "menu_options" not in line.getMessage()
