"""守门:出站拉取循环的离线判定 + 断线恢复。

回归:此前 on_poll_error 一拍失败就报「离线」+ 无成功恢复信号 → 部署重启/瞬时闪断
误报离线且托盘卡死回不来(明明心跳还在到云端)。现在:连续 OFFLINE_AFTER_FAILS 次才判离线,
失败后首个成功拍发 on_poll_ok(恢复)。
"""

import types
from unittest import mock

from src.companion import worker


def _cfg():
    return types.SimpleNamespace(
        poll_interval_ms=1, backend_url="x", bearer_token="t", companion_id="c"
    )


class _FakeQC:
    """按脚本逐拍 raise / 返回 None(无任务)。"""

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.i = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def poll(self):
        b = self.behaviors[self.i] if self.i < len(self.behaviors) else "ok"
        self.i += 1
        if b == "raise":
            raise RuntimeError("boom")
        return None


def _run(behaviors):
    fake = _FakeQC(behaviors)
    errs, oks = [], []
    with (
        mock.patch.object(worker, "QueueClient", lambda *a, **k: fake),
        mock.patch.object(worker, "_run_periodic", lambda *a, **k: None),
        mock.patch.object(worker.time, "sleep", lambda *a, **k: None),
    ):
        worker.run_poll_loop(
            _cfg(),
            handle_task=lambda qc, t: None,
            should_stop=lambda: fake.i >= len(behaviors),
            on_poll_error=lambda e: errs.append(e),
            on_poll_ok=lambda: oks.append(1),
        )
    return errs, oks


def test_transient_blip_under_threshold_no_offline():
    # 连续 2 次失败(< 3)不报离线 —— 容忍部署重启/闪断。
    errs, oks = _run(["raise", "raise"])
    assert errs == []
    assert oks == []


def test_offline_only_after_threshold():
    errs, _ = _run(["raise", "raise", "raise"])
    assert len(errs) == 1  # 第 3 拍才判离线


def test_recovery_emits_ok_after_failures():
    # 失败 3 次(已判离线)后成功 → 发恢复信号,托盘自动回在线。
    errs, oks = _run(["raise", "raise", "raise", "ok"])
    assert len(errs) == 1
    assert len(oks) == 1


def test_no_ok_when_never_failed():
    errs, oks = _run(["ok", "ok"])
    assert errs == []
    assert oks == []  # 没失败过不发恢复


def test_on_tick_fires_each_iteration():
    # 看门狗存活信标:每拍循环开头都盖一次 on_tick(即便随后 poll 失败也已盖过)。
    fake = _FakeQC(["ok", "raise", "ok"])
    ticks = []
    with (
        mock.patch.object(worker, "QueueClient", lambda *a, **k: fake),
        mock.patch.object(worker, "_run_periodic", lambda *a, **k: None),
        mock.patch.object(worker.time, "sleep", lambda *a, **k: None),
    ):
        worker.run_poll_loop(
            _cfg(),
            handle_task=lambda qc, t: None,
            should_stop=lambda: fake.i >= 3,
            on_tick=lambda: ticks.append(1),
        )
    assert len(ticks) == 3  # 三拍各盖一次
