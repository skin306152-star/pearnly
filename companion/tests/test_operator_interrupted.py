# -*- coding: utf-8 -*-
"""被人打断按结果判（CC 桌面版 1.12.23 ⑥，后台做法、钩子挂着、已绑定 Express）：

- 只认点到 / 按在本批 Express 上的（移动鼠标、滚轮、切前后台、在别的程序里打字都不算）；
- 人停手 3 秒就接着录（不再等 30 秒）；等满 60 秒人还在点 → 这张转人工 ``interrupted``；
- 人动过之后核对画面对不上：不交 AI 去改人的操作，如实转人工 ``interrupted``，结果带停在哪。"""
from __future__ import annotations

import pytest
from _fake_express import FakeBook, FakeExpress, samples
from test_operator_engine import RECIPE

from companion.express_operator.engine import Engine, Interrupted
from companion.express_operator.presence import Presence


class Signals:
    def __init__(self):
        self.last, self.now, self.express = 0, 0, 0

    def locked(self):
        return False

    def last_input_tick(self):
        return self.last

    def now_tick(self):
        return self.now

    def last_express_tick(self):
        return self.express


def _presence(sig, timeline=None):
    events = []
    timeline = dict(timeline or {})

    def sleep(seconds):
        sig.now += int(seconds * 1000)
        for when in sorted(list(timeline)):
            if sig.now >= when:
                timeline.pop(when)(sig)

    p = Presence(sig, lambda: 0, stop_requested=lambda: False, on_pause=lambda r: events.append(("pause", r)),
                 on_resume=lambda: events.append(("resume",)), idle_s=30, poll_s=3, sleep=sleep, background=True,
                 foreground=lambda: {"exe": "ExpressI.exe", "title": "t", "mine": True}, clock=lambda: sig.now / 1000)
    return p, events


def test_moving_the_mouse_or_typing_elsewhere_does_not_pause():
    sig = Signals()
    sig.now = 10_000
    p, events = _presence(sig)
    p()  # 开局
    sig.last, sig.now = 12_000, 12_500  # 有输入，但没有一下落在本批 Express 上
    assert p() is False and events == []


def test_a_click_on_express_pauses_until_three_quiet_seconds():
    sig = Signals()
    sig.now = 10_000
    p, events = _presence(sig)
    p()
    sig.last = sig.express = 12_000
    sig.now = 12_100
    assert p() is True
    assert events == [("pause", "user_active"), ("resume",)]
    assert 2.9 <= p.evidence[0]["paused_s"] <= 4  # 停手 3 秒就接着录
    events.clear()
    assert p() is False and events == []  # 这次点击已经处理过


def test_a_person_still_clicking_after_a_minute_stops_the_document():
    sig = Signals()
    sig.now = 10_000
    p, events = _presence(sig)
    p()
    sig.last = sig.express = 12_000
    sig.now = 12_100
    clicks = {t: (lambda s: setattr(s, "express", s.now)) for t in range(13_000, 80_000, 1_000)}
    p, events = _presence(sig, clicks)
    p()  # 新开局：此前那下不算
    sig.express = sig.now + 10
    with pytest.raises(Interrupted) as stop:
        p()
    assert stop.value.reason == "interrupted" and events[0] == ("pause", "user_active")


def test_after_a_person_touched_express_a_mismatch_is_interrupted_not_handed_to_the_ai():
    sim = FakeExpress(RECIPE.data)
    engine = Engine(sim, RECIPE, FakeBook(sim), sleep=lambda _s: None)
    engine.begin(samples()[0])
    engine._touched = True
    with pytest.raises(Interrupted) as stop:
        engine._recover("line1.qty", "cell is '5', want '3'", "qty typed", {"stage": "line"})
    assert stop.value.interrupt == {"stage": "editing", "where": "line1.qty", "what": "cell is '5', want '3'"}
