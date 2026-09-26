# -*- coding: utf-8 -*-
"""看门狗纯逻辑:存活信标卡死判定 + 自愈退避封顶(用假时钟,确定性无 sleep)。"""
from companion import watchdog


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


def test_stall_limit_floor_and_scale():
    # 心跳 60s → 3× = 180s;心跳很小时不低于 90s 地板。
    assert watchdog.stall_limit_seconds(60) == 180.0
    assert watchdog.stall_limit_seconds(1) == 90.0
    assert watchdog.stall_limit_seconds(0) == 90.0


def test_is_stalled_threshold():
    assert not watchdog.is_stalled(90.0, 90.0)  # 恰等阈值不算卡(严格大于才判)
    assert watchdog.is_stalled(90.1, 90.0)


def test_liveness_tick_resets_stall():
    clk = _FakeClock()
    live = watchdog.Liveness(clock=clk)
    assert live.stall_seconds() == 0.0
    clk.advance(120)
    assert live.stall_seconds() == 120.0  # 一直没 tick → 停顿累积
    live.tick()
    assert live.stall_seconds() == 0.0  # tick 后归零(循环还在转)


def test_liveness_never_negative():
    clk = _FakeClock()
    live = watchdog.Liveness(clock=clk)
    clk.now -= 5  # 时钟倒退(理论不该发生)也不返负值
    assert live.stall_seconds() == 0.0


def test_heal_backoff_caps_within_window():
    clk = _FakeClock()
    bo = watchdog.HealBackoff(max_heals=3, window_s=300.0, clock=clk)
    for _ in range(3):
        assert bo.allowed()
        bo.record()
    assert not bo.allowed()  # 窗口内第 4 次被封顶 → 停手改报错


def test_heal_backoff_recovers_after_window():
    clk = _FakeClock()
    bo = watchdog.HealBackoff(max_heals=2, window_s=300.0, clock=clk)
    bo.record()
    bo.record()
    assert not bo.allowed()
    clk.advance(301)  # 旧事件滑出窗口 → 重新允许自愈
    assert bo.allowed()
