# -*- coding: utf-8 -*-
"""加 / 删 Express 位置、「重新查找」之后账套清单扫完一份完整快照，心跳不等下一拍（每分钟一拍）就整份上报（11-06 用户：加了位置
要等一分钟网页「添加公司」才有）。只看这个成员自己的扫描键；「进行中」快照、还在扫、已经报过的、离上一拍不到几秒的都不算。"""

from __future__ import annotations

import types

from src.companion import master_refresh, worker

ROOT, BOOK = r"\\srv\d$\ACCOUNT\70EXP", r"\\srv\d$\ACCOUNT\70EXP\TEST"


def _cfg(places=(ROOT,)):
    return types.SimpleNamespace(resolved_express_root=lambda: ROOT, account_dir=BOOK, authorized_roots=[ROOT],
                                 express_places=places)


def _put(payload, completed_at, running=False, root=ROOT):
    key = master_refresh._key(root, BOOK, False, [root])
    master_refresh._refreshes[key] = master_refresh._Refresh(running=running, payload=payload, completed_at=completed_at)


def setup_function(_fn):
    master_refresh._reset_for_tests()


def test_a_finished_full_snapshot_newer_than_the_last_report_sends_the_heartbeat_now():
    _put({"account_sets": [{"path": BOOK}]}, completed_at=200.0)
    state = {"last_heartbeat": 1000.0, "last_master": 100.0}
    assert worker._snapshot_waiting(_cfg(), state, now=1006.0) is True
    assert worker._snapshot_waiting(_cfg(), state, now=1003.0) is False   # 离上一拍不到 5 秒
    state["last_master"] = 200.0                                         # 这一份已经报过
    assert worker._snapshot_waiting(_cfg(), state, now=1006.0) is False


def test_partial_running_or_another_members_snapshot_does_not_count():
    state = {"last_heartbeat": 0.0, "last_master": 0.0}
    _put({"account_sets": [], "partial": True}, completed_at=50.0)       # 「进行中」快照
    assert worker._snapshot_waiting(_cfg(), state, now=100.0) is False
    _put({"account_sets": []}, completed_at=50.0, running=True)          # 还在扫
    assert worker._snapshot_waiting(_cfg(), state, now=100.0) is False
    master_refresh._reset_for_tests()
    _put({"account_sets": []}, completed_at=50.0, root=r"\\srv\d$\ACCOUNT\69EXP")  # 别的成员 / 别的根
    assert worker._snapshot_waiting(_cfg(), state, now=100.0) is False
    assert master_refresh.complete_after("", "", 0.0) is False


def test_the_periodic_loop_reports_as_soon_as_the_snapshot_is_ready(monkeypatch):
    """取任务循环每拍调 `_run_periodic`：一分钟没到，但完整快照扫完了 → 这一拍就上报（不等下一分钟）。"""
    calls = []
    monkeypatch.setattr(worker, "_snapshot_waiting", lambda cfg, state, now: True)
    monkeypatch.setattr(worker, "_adopt_machine_roots", lambda cfg: (_ for _ in ()).throw(RuntimeError("reported")))
    cfg = types.SimpleNamespace(kind="express", heartbeat_interval_s=60)
    state = {"last_heartbeat": 10_000_000_000.0}                          # 刚报过（离下一拍还早）
    worker._run_periodic(cfg, types.SimpleNamespace(health=lambda **kw: calls.append(kw)), state)
    assert state["last_heartbeat"] != 10_000_000_000.0                   # 这一拍进了上报（_adopt 之后被替身打断，异常被吞）
