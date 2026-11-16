# -*- coding: utf-8 -*-
"""1.12.22 本批四项的电脑这一侧（`docs/work/2026-11-15-cc-desktop-1122-server.md`）：

- 领单回复带 ``requests_waiting`` 就叫醒按需请求线程（``request_wake``），没带不叫；空闲照旧一次等满；
- 未收发票读回带 ``docnums`` 时另报这几张的状态（未收清 / 已收清 / 作废），账套里没有的不列。

主档上报带 ``prename`` 在 ``test_express_masters.py``，直写赊购口径 2 单头 VATDAT 留空在 ``test_service_vat.py``。"""

from __future__ import annotations

import time

import pytest

from companion import request_wake
from companion.queue_client import QueueClient, QueueClientConfig
from companion.request_contract import RequestFailed
from test_express_masters import _params, books  # noqa: F401
from test_express_receivables import _open, receipts  # noqa: F401


class _Response:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": self.body}


class _Client:
    def __init__(self, body):
        self.body = body

    def post(self, path, json=None):
        return _Response(self.body)

    def close(self):
        return None


def _lease(monkeypatch, **extra):
    import companion.queue_client as module

    monkeypatch.setattr(module.httpx, "Client", lambda *a, **kw: _Client({"ok": True, "lease_seconds": 120, "jobs": [], **extra}))
    QueueClient(QueueClientConfig(backend_url="https://lab.invalid", bearer_token="exp_x", companion_id="c")).poll()


def test_the_lease_reply_wakes_the_request_thread_only_when_it_says_so(monkeypatch):
    request_wake.nap(0)  # 清掉别的测试留下的
    _lease(monkeypatch)
    assert request_wake.nap(0.01) is False
    _lease(monkeypatch, requests_waiting="yes")  # 只认 true
    assert request_wake.nap(0.01) is False
    _lease(monkeypatch, requests_waiting=True)
    started = time.monotonic()
    assert request_wake.nap(5) is True and time.monotonic() - started < 1
    assert request_wake.nap(0.01) is False  # 叫一次只醒一次


def test_stopping_the_request_thread_ends_its_idle_wait():
    from companion import request_worker

    worker = request_worker.RequestWorker(lambda: [])
    request_wake.nap(0)
    worker.stop()
    started = time.monotonic()
    worker._rest(5)
    assert time.monotonic() - started < 1


def test_invoice_numbers_are_looked_up_whatever_their_state(receipts):
    # IV001 未收清、IV003 已收清、IV005 作废、IV008 服务品现金制（列表里不列，按单号照报）、IV999 账套里没有
    params = {**_params(receipts), "customer": "C01", "docnums": ["IV003", "IV001", "IV005", "IV008", "IV999", "IV004"]}
    _result, data = _open(receipts, params)
    assert [row["docnum"] for row in data["items"]] == ["IV001", "IV002"]  # 列表照旧（只这位客户、未收清）
    assert sorted(data["looked_up"], key=lambda row: row["docnum"]) == [
        {"docnum": "IV001", "customer": "C01", "state": "open", "remaining": "9972.00"},
        {"docnum": "IV003", "customer": "C01", "state": "settled", "remaining": "0.00"},
        {"docnum": "IV004", "customer": "C09", "state": "open", "remaining": "120.50"},  # 别的客户名下的也报
        {"docnum": "IV005", "customer": "C01", "state": "cancelled", "remaining": "50.00"},
        {"docnum": "IV008", "customer": "C01", "state": "open", "remaining": "107.00"},
    ]


def test_without_invoice_numbers_the_result_is_as_before(receipts):
    _result, data = _open(receipts)
    assert "looked_up" not in data


@pytest.mark.parametrize("docnums", ["IV001", [1], [""], ["X" * 41], ["IV%03d" % i for i in range(21)]])
def test_bad_invoice_numbers_are_refused(receipts, docnums):
    with pytest.raises(RequestFailed) as error:
        _open(receipts, {**_params(receipts), "docnums": docnums})
    assert error.value.code == "params_invalid"

