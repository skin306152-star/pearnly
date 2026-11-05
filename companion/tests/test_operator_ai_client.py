"""AI 接手的模型调用经产品站点（`companion.operator_ai`，2026-11-03）：按成员的通道走 ``operator_ai``，带批次号；
站点回 ``{ok, response}`` 取 response，拒收 / 出错回 ``{"error": …}``（``ComputerUseHelper`` 照它停下或转人工）；没有站点不接。"""
from __future__ import annotations

import json

import httpx

from companion import operator_ai as site_ai
from companion.config import CompanionConfig


def test_the_member_channel_carries_the_batch_and_returns_the_model_response(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers["Authorization"], json.loads(request.content)))
        return httpx.Response(200, json={"message": {"ok": True, "protocol": 1, "response": {"stop_reason": "end_turn"}}})

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler),
                                                                    **{k: v for k, v in kw.items() if k != "verify"}))
    for token, path in (("pcc_abc", "/api/method/pearnly_core.cc_desktop.machine.operator_ai"),
                        ("exp_abc", "/api/method/pearnly_cowork.companion.operator_api.operator_ai")):
        transport = site_ai.transport_for(CompanionConfig(bearer_token=token, backend_url="https://erp.test"), "B1")
        assert transport("turn", {"model": "m"}) == {"stop_reason": "end_turn"}
        assert seen[-1] == (path, f"Bearer {token}", {"batch": "B1", "kind": "turn", "request": {"model": "m"}})


def test_refusals_and_errors_come_back_as_errors(monkeypatch):
    replies = iter([httpx.Response(200, json={"message": {"ok": False, "code": "companion.on_demand_invalid"}}),
                    httpx.Response(503, json={})])
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(lambda r: next(replies)),
                                                                    **{k: v for k, v in kw.items() if k != "verify"}))
    transport = site_ai.transport_for(CompanionConfig(bearer_token="pcc_abc", backend_url="https://erp.test"), "B1")
    assert transport("check", {}) == {"error": "companion.on_demand_invalid"}
    assert transport("check", {}) == {"error": "http_503"}


def test_no_site_means_no_helper():
    assert site_ai.transport_for(CompanionConfig(bearer_token="", backend_url=""), "B1") is None


def test_check_and_turn_requests_use_only_fields_that_need_no_beta_header():
    """站点转发不带任何 anthropic-beta 头（11-05 测试机：接手请求带了上下文编辑 context_management，第一轮就被回 400）。
    看图判断与接手每一轮的请求体只许用正式字段。"""
    from companion.express_operator.helper import ComputerUseHelper

    ga = {"model", "max_tokens", "system", "messages", "tools", "thinking", "output_config", "cache_control"}
    sent = []

    def transport(kind, request):
        sent.append((kind, json.loads(json.dumps(request))))
        if kind == "check":
            return {"content": [{"type": "text", "text": json.dumps({"state": "as_expected"})}], "stop_reason": "end_turn"}
        if len([k for k, _ in sent if k == "turn"]) == 1:
            return {"content": [{"type": "tool_use", "id": "a", "name": "screenshot", "input": {}}], "stop_reason": "tool_use"}
        return {"content": [{"type": "tool_use", "id": "b", "name": "finish",
                             "input": {"status": "resolved", "note": "ok"}}], "stop_reason": "tool_use"}

    tools = type("Tools", (), {"screenshot": lambda self, purpose: "iVBOR",
                               "member": lambda self, name, inp: [{"type": "text", "text": "OK"}]})()
    helper = ComputerUseHelper(transport, sleep=lambda _s: None)
    helper.check("iVBOR", "期望")
    assert helper.take_over("目标", tools) == ("resolved", "ok")
    kinds = [k for k, _ in sent]
    assert kinds == ["check", "turn", "turn"]
    for kind, request in sent:
        assert set(request) <= ga, (kind, set(request) - ga)
        assert "context_management" not in json.dumps(request)


def _scripted_takeover(monkeypatch, tools, turns=16):
    """同一个模型连跑 ``turns`` 轮（远多于每段 5 张图），记下每次发出时请求体的快照。"""
    import copy

    from companion.express_operator import helper as H

    monkeypatch.setattr(H, "CHAIN", ((H.HAIKU, turns + 4, "medium"),))
    sent = []

    def transport(kind, request):
        sent.append(copy.deepcopy(request))  # 发出那一刻的样子：之后再改写会在下一次对比里现形
        n = len(sent)
        usage = {"input_tokens": 1000 + n, "output_tokens": 50}
        if n >= turns:
            return {"content": [{"type": "tool_use", "id": f"f{n}", "name": "finish",
                                 "input": {"status": "resolved", "note": "ok"}}], "stop_reason": "tool_use", "usage": usage}
        name = "left_click" if n % 2 else "screenshot"
        return {"content": [{"type": "thinking", "thinking": "…", "signature": f"sig{n}"},
                            {"type": "tool_use", "id": f"u{n}", "name": name, "input": {"coordinate": [10, n]}}],
                "stop_reason": "tool_use", "usage": usage}

    helper = H.ComputerUseHelper(transport, sleep=lambda _s: None, batch="B-9")
    return helper, sent


def _images(request) -> int:
    from companion.express_operator.helper import count_images

    return count_images(request["messages"])


def test_a_long_takeover_only_appends_and_starts_a_new_conversation_past_five_screenshots(caplog, monkeypatch):
    """thinking 块带签名，已发出的消息一改 Anthropic 就回 400（11-05 测试机第 5 轮）：同一段对话里每次请求都是上一次的严格延伸；
    图多于 5 张就另起一段（目标 + 做过的动作 + 最新一张截图），旧对话整段丢弃。"""
    import logging

    from companion.express_operator.helper import png_block

    tools = type("Tools", (), {"screenshot": lambda self, purpose: f"shot-{purpose}",
                               "member": lambda self, name, inp: [png_block("shot-member")] if name == "screenshot"
                               else [{"type": "text", "text": "OK"}]})()
    helper, sent = _scripted_takeover(monkeypatch, tools)
    with caplog.at_level(logging.INFO):
        assert helper.take_over("目标原文", tools) == ("resolved", "ok")
    assert len(sent) == 16
    segments = 1
    for before, after in zip(sent, sent[1:]):
        old, new = before["messages"], after["messages"]
        if json.dumps(new[0], sort_keys=True) != json.dumps(old[0], sort_keys=True):
            segments += 1  # 换段：新对话首条是摘要 + 1 张图
            first = new[0]["content"]
            assert len(new) == 1 and sum(b.get("type") == "image" for b in first) == 1
            assert first[0]["text"].startswith("目标原文") and "之前已做过的动作" in first[0]["text"]
            assert "不要重做" in first[0]["text"] and "left_click" in first[0]["text"]
            continue
        assert len(new) > len(old)
        assert [json.dumps(m, sort_keys=True) for m in new[:len(old)]] == [json.dumps(m, sort_keys=True) for m in old]
    assert segments >= 2
    assert all(_images(r) <= 5 for r in sent)
    for request in sent:
        messages = request["messages"]
        for ask, answer in zip(messages[1::2], messages[2::2]):
            uses = [b["id"] for b in ask["content"] if b["type"] == "tool_use"]
            assert [r["tool_use_id"] for r in answer["content"] if r["type"] == "tool_result"] == uses
    lines = [r.getMessage() for r in caplog.records if "operator ai usage" in r.getMessage()]
    assert len(lines) == 16
    assert lines[0].startswith("operator ai usage · batch=B-9 · kind=turn · model=claude-haiku-5-5 · input=1001 · output=50")
    assert "shot-" not in caplog.text


def test_a_new_conversation_after_lines_were_entered_says_what_is_already_in_express(monkeypatch):
    """换段的首条消息写明 Express 里已经落下的（引擎读的：已录完的行、单号格、存没存），并要求先核对现状、不要重做。"""
    from _fake_express import FakeBook, FakeExpress, samples

    from companion.express_operator import recipe as R
    from companion.express_operator.engine import Engine

    recipe = R.load()
    sim = FakeExpress(recipe.data)
    engine = Engine(sim, recipe, FakeBook(sim), sleep=lambda _s: None)
    draft = samples()[1]
    engine.begin(draft)
    engine.ensure_form(draft.doc_type)
    engine.command("new")
    engine._stage, engine._lines_done = "line", 1
    helper, sent = _scripted_takeover(monkeypatch, engine, turns=12)
    helper.take_over("目标原文", engine)
    starts = [r["messages"][0]["content"][0]["text"] for r in sent if len(r["messages"]) == 1][1:]
    assert starts, "没有换段"
    text = starts[0]
    line = draft.lines[0]
    assert f"明细已录完 1 行" in text and f"品码 {line.item_code} 数量 {line.qty}" in text
    assert "还没保存" in text and "单号格" in text
    assert "先看最新截图核对 Express 当前状态" in text and "不要重做" in text
