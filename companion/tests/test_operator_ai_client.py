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
