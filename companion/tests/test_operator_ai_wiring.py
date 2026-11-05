# -*- coding: utf-8 -*-
"""AI 接手的通道用领到这张单的成员配置（11-05 测试机：写账线把 ExpressSettings 交给宿主，装配拿它建通道，它没有令牌，
日志「operator ai not connected · reason=no_token」，每一批代录都没调过 AI）。

从写账线 ``worker._handle_operator_task`` 一路走到宿主与真实的 ``express_factory``：Express 设置用真实的 ``ExpressSettings``
（没有站点与令牌字段），成员配置用真实的 ``CompanionConfig``（带令牌）。只把 Windows 才有的部件换成假的。"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace

from _fake_express import FakeBook, FakeExpress, express_settings

from companion import account_write_lock, operator_ai, worker
from companion.config import CompanionConfig
from companion.express_operator import bg_desk, book, presence, session
from companion.express_operator import host as host_module
from companion.express_operator import masters
from companion.express_operator import recipe as R
from companion.express_operator.engine import NeedHuman
from companion.express_operator.helper import ComputerUseHelper

RECIPE = R.load()


class _Presence:
    evidence = []

    def __init__(self, *_a, **_k):
        pass

    def close(self):
        pass


class _Session:
    """开 Express 这一步停下（这条测试只看装配）：按「这台电脑还没授权登录」整批停。"""

    def __init__(self, desk, *_a, **_k):
        self.desk = desk

    def ensure_open(self):
        raise NeedHuman("login_missing", "test")

    def close(self):
        pass


def test_the_ai_channel_is_built_from_the_member_config_not_the_express_settings(monkeypatch, tmp_path):
    sim = FakeExpress(RECIPE.data)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bg_desk, "BackgroundDesk", lambda exe: sim)
    monkeypatch.setattr(book, "DbfBook", lambda account_dir: FakeBook(sim))
    monkeypatch.setattr(presence, "Presence", _Presence)
    monkeypatch.setattr(presence, "WindowsSignals", lambda: None)
    monkeypatch.setattr(session, "Session", _Session)
    monkeypatch.setattr(masters, "prepare", lambda payload, st, before_create: masters.Prepared(True, payload))

    @contextmanager
    def held(_dir):
        yield

    monkeypatch.setattr(account_write_lock, "held", held)
    built = []
    real = operator_ai.transport_for
    monkeypatch.setattr(operator_ai, "transport_for", lambda member, batch: built.append(member) or real(member, batch))
    host = host_module.Host(tmp_path / "operator")
    monkeypatch.setattr(host_module, "_HOST", host)

    member = CompanionConfig(backend_url="https://testcowork.pearnly.com", bearer_token="exp_test_token")
    member.ui_lang = "zh"
    st = express_settings(str(tmp_path / "book"), exe=r"C:\Express\ExpressI.exe")
    assert not hasattr(st, "bearer_token")  # 写账线交来的 Express 设置本来就没有令牌
    payload = {"doctype": "RR", "docdate_be": "691020", "ref_no": "R1", "supplier": {"code": "P001"},
               "items": [{"code": "501780", "qty": "1", "unit_price": "1"}], "payload_version": 16,
               "entry_mode": "operator", "operator": {"batch": {"id": "b-ai", "index": 1, "total": 1}}}
    task = SimpleNamespace(id="t1", key=None, payload=payload, invoice_no="")
    acks = []
    qc = SimpleNamespace(ack_failure=lambda *a, **k: acks.append((a, k)), ack_success=lambda *a, **k: acks.append(a))
    worker._handle_operator_task(member, qc, task, st)

    assert built == [member]  # 通道用成员配置建，不是 ExpressSettings
    assert isinstance(host.runners["b-ai"].engine.helper, ComputerUseHelper)
    assert acks and acks[0][1]["error_code"] == "OPERATOR_LOGIN_MISSING"  # 后面照常收尾回执
