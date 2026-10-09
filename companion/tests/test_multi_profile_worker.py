from __future__ import annotations

from types import SimpleNamespace

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

    def poll(self):
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
