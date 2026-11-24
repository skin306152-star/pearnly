from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from src.companion.multi_profile_worker import MultiProfileWorker


def _cfg(profile_id: str, *, paused: bool = False):
    return SimpleNamespace(
        profile_id=profile_id,
        paused=paused,
        backend_url="https://pearnly.com",
        bearer_token=f"token-{profile_id}",
        companion_id="one-machine",
        poll_interval_ms=1,
    )


def _task(profile_id: str, number: int):
    return SimpleNamespace(
        id=f"{profile_id}-{number}",
        invoice_no=f"INV-{profile_id}-{number}",
        payload={"profile": profile_id},
    )


class _Batch:
    def __init__(self):
        self.flushes = 0

    def flush(self):
        self.flushes += 1


class _Client:
    def __init__(self, cfg, scripts):
        self.cfg = cfg
        self.profile_id = cfg.bearer_token.removeprefix("token-")
        self.script = list(scripts.get(self.profile_id, []))
        self.poll_count = 0
        self.health_calls = []
        self.closed = False

    def poll(self, index_pending=None):
        self.poll_count += 1
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        return item

    def health(self, **kwargs):
        self.health_calls.append(kwargs)
        return {"ok": True}

    def close(self):
        self.closed = True


def _factory(scripts, made):
    def build(cfg):
        client = _Client(cfg, scripts)
        made[client.profile_id] = client
        return client

    return build


def _no_periodic(_cfg, _client, state, _on_update):
    state["calls"] = state.get("calls", 0) + 1


def test_round_robin_is_fair_and_task_keeps_its_own_client():
    scripts = {
        "A": [_task("A", 1), _task("A", 2)],
        "B": [_task("B", 1), _task("B", 2)],
    }
    made, handled = {}, []
    concurrent = {"now": 0, "max": 0}

    def handle(cfg, client, task, _batch):
        concurrent["now"] += 1
        concurrent["max"] = max(concurrent["max"], concurrent["now"])
        assert cfg.profile_id == client.profile_id == task.payload["profile"]
        handled.append((cfg.profile_id, task.id, client.cfg.bearer_token))
        concurrent["now"] -= 1

    worker = MultiProfileWorker(
        [_cfg("A"), _cfg("B")],
        queue_factory=_factory(scripts, made),
        periodic_runner=_no_periodic,
        task_handler=handle,
        index_batch_factory=_Batch,
    )

    first = worker.run_round()
    second = worker.run_round()

    assert first.handled_tasks == second.handled_tasks == 2
    assert [entry[0] for entry in handled] == ["A", "B", "A", "B"]
    assert [entry[2] for entry in handled] == [
        "token-A",
        "token-B",
        "token-A",
        "token-B",
    ]
    assert concurrent["max"] == 1
    assert made["A"] is not made["B"]


def test_one_profile_failure_does_not_block_the_next_profile():
    scripts = {
        "A": [RuntimeError("401 unauthorized")],
        "B": [_task("B", 1)],
    }
    made, handled, statuses = {}, [], []
    worker = MultiProfileWorker(
        [_cfg("A"), _cfg("B")],
        queue_factory=_factory(scripts, made),
        periodic_runner=_no_periodic,
        task_handler=lambda cfg, client, task, batch: handled.append(task.id),
        index_batch_factory=_Batch,
        on_status=lambda pid, status, detail: statuses.append((pid, status, detail)),
    )

    result = worker.run_round()

    assert result.failed_profiles == 1
    assert result.handled_tasks == 1
    assert handled == ["B-1"]
    assert worker.state("A").fail_streak == 1
    assert worker.state("B").fail_streak == 0
    assert not [entry for entry in statuses if entry[0] == "A"]

    worker.run_round()
    worker.run_round()
    assert any(pid == "A" and status == "idle" for pid, status, _ in statuses)


def test_profile_is_only_marked_offline_after_consecutive_failures():
    scripts = {"A": [RuntimeError("down"), RuntimeError("down"), RuntimeError("down")]}
    made, statuses = {}, []
    worker = MultiProfileWorker(
        [_cfg("A")],
        queue_factory=_factory(scripts, made),
        periodic_runner=_no_periodic,
        task_handler=lambda *args: None,
        index_batch_factory=_Batch,
        on_status=lambda pid, status, detail: statuses.append((pid, status, detail)),
    )

    worker.run_round()
    worker.run_round()
    assert statuses == []
    worker.run_round()
    assert statuses == [("A", "offline", "down")]


def test_each_active_profile_is_polled_at_most_once_per_round():
    scripts = {"A": [_task("A", 1), _task("A", 2)]}
    made, handled = {}, []
    worker = MultiProfileWorker(
        [_cfg("A")],
        queue_factory=_factory(scripts, made),
        periodic_runner=_no_periodic,
        task_handler=lambda cfg, client, task, batch: handled.append(task.id),
        index_batch_factory=_Batch,
    )

    worker.run_round()
    assert handled == ["A-1"]
    assert made["A"].poll_count == 1
    worker.run_round()
    assert handled == ["A-1", "A-2"]
    assert made["A"].poll_count == 2


def test_refresh_command_from_poll_forces_next_heartbeat():
    class RefreshClient(_Client):
        def __init__(self, cfg, scripts):
            super().__init__(cfg, scripts)
            self.refresh = {
                "request_id": "refresh-1",
                "scope_kind": "endpoint",
                "account_set_key": "@endpoint",
            }

        def take_master_refresh(self):
            refresh, self.refresh = self.refresh, None
            return refresh

    made = {}

    def factory(cfg):
        client = RefreshClient(cfg, {"A": [None]})
        made["A"] = client
        return client

    heartbeat_values = []

    def periodic(_cfg, _client, state, _update):
        heartbeat_values.append(state["last_heartbeat"])
        state["last_heartbeat"] = 99.0

    worker = MultiProfileWorker(
        [_cfg("A")],
        queue_factory=factory,
        periodic_runner=periodic,
        task_handler=lambda *args: None,
        index_batch_factory=_Batch,
    )

    worker.run_round()
    worker.run_round()

    assert heartbeat_values == [0.0, 0.0]
    assert worker.state("A").periodic["master_refresh_request"]["request_id"] == (
        "refresh-1"
    )


def test_paused_profile_has_no_client_heartbeat_or_poll():
    scripts = {"B": [_task("B", 1)]}
    made, handled = {}, []
    worker = MultiProfileWorker(
        [_cfg("A", paused=True), _cfg("B")],
        queue_factory=_factory(scripts, made),
        periodic_runner=_no_periodic,
        task_handler=lambda cfg, client, task, batch: handled.append(task.id),
        index_batch_factory=_Batch,
    )

    result = worker.run_round()

    assert result.polled_profiles == 1
    assert handled == ["B-1"]
    assert set(made) == {"B"}
    assert worker.state("A").periodic.get("calls") is None
    assert worker.state("A").status == "paused"


def test_heartbeat_cadence_and_failure_state_are_isolated_per_profile():
    made, periodic_ids = {}, {}

    def periodic(cfg, _client, state, _update):
        periodic_ids.setdefault(cfg.profile_id, id(state))
        state["profile"] = cfg.profile_id
        state["calls"] = state.get("calls", 0) + 1

    worker = MultiProfileWorker(
        [_cfg("A"), _cfg("B")],
        queue_factory=_factory({}, made),
        periodic_runner=periodic,
        task_handler=lambda *args: None,
        index_batch_factory=_Batch,
    )

    worker.run_round()
    worker.run_round()

    assert periodic_ids["A"] != periodic_ids["B"]
    assert worker.state("A").periodic == {
        "last_heartbeat": 0.0,
        "profile": "A",
        "calls": 2,
    }
    assert worker.state("B").periodic["profile"] == "B"
    assert worker.state("B").periodic["calls"] == 2


def test_process_update_check_is_owned_by_one_active_profile():
    made, callbacks = {}, []

    def periodic(cfg, _client, _state, on_update):
        callbacks.append((cfg.profile_id, on_update))
        if on_update is not None:
            on_update({"version": "1.1.65"})

    updates = []
    worker = MultiProfileWorker(
        [_cfg("A", paused=True), _cfg("B"), _cfg("C")],
        queue_factory=_factory({}, made),
        periodic_runner=periodic,
        task_handler=lambda *args: None,
        index_batch_factory=_Batch,
        on_update_available=lambda pid, update: updates.append((pid, update)),
    )

    worker.run_round()

    assert [pid for pid, callback in callbacks if callback is not None] == ["B"]
    assert updates == [("B", {"version": "1.1.65"})]


def test_idle_after_task_flushes_only_that_profiles_batch():
    scripts = {"A": [_task("A", 1), None], "B": [None, None]}
    made, batches = {}, []

    def batch_factory():
        batch = _Batch()
        batches.append(batch)
        return batch

    worker = MultiProfileWorker(
        [_cfg("A"), _cfg("B")],
        queue_factory=_factory(scripts, made),
        periodic_runner=_no_periodic,
        task_handler=lambda *args: None,
        index_batch_factory=batch_factory,
    )

    worker.run_round()
    worker.run_round()

    assert batches[0].flushes == 1
    assert batches[1].flushes == 0


def test_close_signals_and_closes_every_started_profile_even_if_one_fails():
    made = {}
    worker = MultiProfileWorker(
        [_cfg("A"), _cfg("B")],
        queue_factory=_factory({}, made),
        periodic_runner=_no_periodic,
        task_handler=lambda *args: None,
        index_batch_factory=_Batch,
    )
    worker.run_round()

    def broken_health(**kwargs):
        raise RuntimeError("offline")

    made["A"].health = broken_health
    worker.close(signal_offline=True)

    assert made["A"].closed is True
    assert made["B"].closed is True
    assert made["B"].health_calls == [{"offline": True}]
    worker.close(signal_offline=True)
    with pytest.raises(RuntimeError, match="closed"):
        worker.run_round()


def test_duplicate_profile_ids_fail_closed():
    with pytest.raises(ValueError, match="duplicate Companion profile_id"):
        MultiProfileWorker([_cfg("A"), _cfg("A")])


# ── 密钥失效（401）退避：不每 3 秒打服务器（11-08 erpcowork 日志：一台电脑 3 分半里 401 约 70 次） ──

def _status_error(code: int):
    import httpx

    request = httpx.Request("POST", "https://erpcowork.pearnly.com/api/method/pearnly_cowork.companion.queue_api.lease")
    return httpx.HTTPStatusError(str(code), request=request, response=httpx.Response(code, request=request))


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Rejections:
    def __init__(self):
        self.calls = []

    def rejected(self, cfg):
        self.calls.append(("rejected", cfg.profile_id))

    def ok(self, cfg):
        self.calls.append(("ok", cfg.profile_id))


def _poll_times(worker, clock, made, profile_id, seconds, step=1.0):
    """每 ``step`` 秒跑一轮（跟 run_forever 每轮睡 3 秒等价但更细），记下真正打到服务器的时刻。"""
    times, last = [], 0
    end = clock.now + seconds
    while clock.now < end:
        worker.run_round()
        client = made.get(profile_id)
        if client is not None and client.poll_count != last:
            last = client.poll_count
            times.append(clock.now)
        clock.now += step
    return times


def test_a_key_the_server_refuses_backs_off_exponentially_up_to_ten_minutes():
    clock = _Clock()
    made, rejections = {}, _Rejections()
    cfg = _cfg("A")
    cfg.poll_interval_ms = 3000
    worker = MultiProfileWorker(
        [cfg], queue_factory=_factory({"A": [_status_error(401)] * 100}, made),
        periodic_runner=_no_periodic, clock=clock, rejections=rejections,
    )
    times = _poll_times(worker, clock, made, "A", 3000)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps[:6] == [3.0, 6.0, 12.0, 24.0, 48.0, 96.0]
    assert max(gaps) == 600.0 and gaps[-1] == 600.0
    assert len(times) < 15                      # 原来 3000 秒里打 1000 次
    assert worker.state("A").status == "offline"   # 托盘按 401 显示「连接已失效，请在设置中重新连接」
    assert rejections.calls and all(call == ("rejected", "A") for call in rejections.calls)


def test_one_success_resets_the_back_off_and_5xx_backs_off_without_marking_the_key():
    clock = _Clock()
    made, rejections = {}, _Rejections()
    cfg = _cfg("A")
    cfg.poll_interval_ms = 3000
    script = [_status_error(401)] * 4 + [None] + [_status_error(502)] * 5
    worker = MultiProfileWorker(
        [cfg], queue_factory=_factory({"A": script}, made),
        periodic_runner=_no_periodic, clock=clock, rejections=rejections, jitter=lambda: 0.5,
    )
    times = _poll_times(worker, clock, made, "A", 200)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps[:4] == [3.0, 6.0, 12.0, 24.0]      # 4 次 401，第 5 次成功
    # 成功后回到每轮都领（节奏由 run_forever 每轮睡 poll_interval 定）；之后 5xx 按网络退避（1.12.25），不记成密钥失效
    assert gaps[4:9] == [1.0, 3.0, 6.0, 12.0, 24.0]
    assert ("ok", "A") in rejections.calls
    assert rejections.calls.count(("rejected", "A")) == 4


def test_while_the_server_is_unreachable_polls_back_off_to_thirty_seconds_with_spread():
    """断网 / 上游挡住时不再每 3 秒新建一次连接（1.12.25）：3 秒起翻倍、最多 30 秒，上下浮动两成；连上一次就回到每轮都领。"""
    for draw, cap in ((0.5, 30.0), (0.0, 24.0), (1.0, 36.0)):
        clock = _Clock()
        made = {}
        cfg = _cfg("A")
        cfg.poll_interval_ms = 3000
        script = [httpx.ConnectTimeout("down")] * 8 + [None] * 3
        worker = MultiProfileWorker(
            [cfg], queue_factory=_factory({"A": script}, made),
            periodic_runner=_no_periodic, clock=clock, rejections=_Rejections(), jitter=lambda: draw,
        )
        times = _poll_times(worker, clock, made, "A", 400, step=0.5)
        gaps = [b - a for a, b in zip(times, times[1:])]
        spread = 1.0 + 0.2 * (2 * draw - 1)
        expected = [min(3.0 * 2 ** n, 30.0) * spread for n in range(7)]
        assert all(abs(gap - value) <= 0.5 for gap, value in zip(gaps[:7], expected)), (draw, gaps[:7])
        assert max(gaps[:7]) <= cap + 0.5
        assert gaps[8:10] == [0.5, 0.5]           # 连上了：每轮都领（这里每步 0.5 秒）
        assert worker.state("A").net_streak == 0


def test_a_backed_off_profile_does_not_hold_up_the_others():
    clock = _Clock()
    made = {}
    a, b = _cfg("A"), _cfg("B")
    a.poll_interval_ms = b.poll_interval_ms = 3000
    worker = MultiProfileWorker(
        [a, b], queue_factory=_factory({"A": [_status_error(401)] * 100}, made),
        periodic_runner=_no_periodic, clock=clock, rejections=_Rejections(),
    )
    _poll_times(worker, clock, made, "A", 60)
    assert made["A"].poll_count <= 5 and made["B"].poll_count == 60


def test_tasks_run_inside_the_write_window_the_on_demand_thread_watches():
    """做单期间（1.12.25）领单停着，按需请求线程据 request_wake.writing 回到 15 秒一问；做完就清掉。"""
    from src.companion import request_wake

    seen = []
    clock = _Clock()
    made = {}
    worker = MultiProfileWorker(
        [_cfg("A")], queue_factory=_factory({"A": [_task("A", "T1"), None]}, made),
        periodic_runner=_no_periodic, clock=clock, rejections=_Rejections(),
        task_handler=lambda cfg, qc, task, batch: seen.append(request_wake._WRITING.is_set()),
    )
    worker.run_round()
    assert seen == [True] and not request_wake._WRITING.is_set()
