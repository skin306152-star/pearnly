# -*- coding: utf-8 -*-
"""Express 开着要写的表时 CC 桌面版不弹「账套正被占用」框、不拿着取任务线程等人（11-13 真机事故 + 主会话定方案甲）。

事故：测试机1 用 Express 打开 69EXP\\TEST 的赊销单窗口后，一张有人值守的收款单让两台 1.12.21 都弹了框、取任务线程在框前一直等，
心跳与领单停了十几分钟，Express 关了也不恢复。现在不论有没有人在电脑前（服务端的 attended 不再起作用），一律走写入时的按表探占用：
一个字节不动、按「等账套」回报，服务端歇一会儿再派，Express 关了自动写；电脑前的人在网页 / LINE / CC 对话里看「在等 Express」、
在那里撤回。这里用收款写路（LINE 收款单）与合成账套真写。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from companion import account_lock, worker
from companion.gui_tray_workers import PollWorker
from test_dbf_receipt import _payload, acct  # noqa: F401  收款的合成账套夹具与载荷
from test_express_open_no_touch import _held_by_express, _snapshot


class _Server:
    """服务端的写账队列：一件收款单，谁来领给谁，回报成功后不再派；回报「等账套」放回排队。"""

    def __init__(self, payload):
        self.task = SimpleNamespace(id="CCJ-LINE-1", payload=payload, invoice_no="COL-LINE-1", attended=True)
        self.done = False
        self.writes = []
        self.waits = []

    def lease(self):
        return None if self.done else self.task

    def queue_for(self, computer):
        server = self

        class Queue:
            def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
                server.done = True
                server.writes.append((computer, extracted_invoice_no))
                return True

            def ack_failure(self, task_id, error, error_code=None, outcome=None, meta=None):
                server.waits.append((computer, error_code, outcome))
                return True

        return Queue()


def _computer(monkeypatch, account_dir):
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, payload: SimpleNamespace(
        account_dir=str(account_dir), account_set="NLTTEST", account_company="", has_selection=lambda: True))
    return PollWorker([])


def _cfg(profile_id):
    return SimpleNamespace(profile_id=profile_id, default_depcod="", ui_lang="zh")


def test_a_line_receipt_while_express_has_the_book_open_waits_without_a_prompt_then_writes(acct, monkeypatch):
    pw = _computer(monkeypatch, acct)
    assert not hasattr(pw, "busy_prompt")   # 没有询问框这回事了
    server = _Server(_payload())
    monkeypatch.setattr(account_lock, "_exclusive_error", _held_by_express("ISRUN", "ARTRN"), raising=False)
    before = _snapshot(acct)
    started = time.monotonic()
    for _ in range(3):   # 服务端歇一会儿又派来、人在 Express 里一直没关
        assert pw.handle_task(_cfg("ERP"), server.queue_for("PC1"), server.lease(), None, "zh") == "waiting_lock"
    assert time.monotonic() - started < 10   # 取任务线程没在等人
    assert _snapshot(acct) == before and server.writes == []
    assert server.waits == [("PC1", "ACCOUNT_BUSY_LOCKED", "waiting_lock")] * 3
    monkeypatch.setattr(account_lock, "_exclusive_error", lambda path: 0, raising=False)   # Express 关了
    assert pw.handle_task(_cfg("ERP"), server.queue_for("PC1"), server.lease(), None, "zh") is None
    assert server.writes == [("PC1", "RE2403080")] and server.lease() is None


def test_two_computers_on_one_book_both_keep_going_and_write_it_once(acct, monkeypatch):
    """同一本账套两台电脑都报了（11-13 测试机1、测试机2）：Express 开着时两台轮流领到、都按「等账套」回报、都不卡；关了以后只写一次。"""
    first, second = _computer(monkeypatch, acct), _computer(monkeypatch, acct)
    server = _Server(_payload())
    monkeypatch.setattr(account_lock, "_exclusive_error", _held_by_express("ISRUN"), raising=False)
    for _ in range(2):
        for name, pw in (("PC1", first), ("PC2", second)):
            assert pw.handle_task(_cfg("ERP"), server.queue_for(name), server.lease(), None, "zh") == "waiting_lock"
    monkeypatch.setattr(account_lock, "_exclusive_error", lambda path: 0, raising=False)
    for name, pw in (("PC2", second), ("PC1", first)):
        task = server.lease()
        if task is not None:
            pw.handle_task(_cfg("ERP"), server.queue_for(name), task, None, "zh")
    assert server.writes == [("PC2", "RE2403080")]
    assert len([w for w in server.waits if w[1] == "ACCOUNT_BUSY_LOCKED"]) == 4
