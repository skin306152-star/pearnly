# -*- coding: utf-8 -*-
"""Single-writer runtime for multiple Companion profiles.

Each profile owns its HTTP client, heartbeat cadence and failure state.  A round
offers every active profile exactly one lease opportunity in stable order.  The
leased task is handled immediately with the same client that leased it, so an
ACK can never cross profile credentials.  All handlers run on this scheduler's
single thread; DBF writes therefore remain strictly serial.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

from . import projection_refresh
from .config import CompanionConfig
from .logger import get_logger
from .queue_client import QueueClient, QueueClientConfig
from .worker import OFFLINE_AFTER_FAILS, _run_periodic, route_task

log = get_logger(__name__)

QueueFactory = Callable[[QueueClientConfig], QueueClient]
PeriodicRunner = Callable[
    [CompanionConfig, QueueClient, dict, Optional[Callable]], None
]
TaskHandler = Callable[[CompanionConfig, QueueClient, object, object], object]
StatusCallback = Callable[[str, str, str], None]
BatchCallback = Callable[[str], None]
UpdateCallback = Callable[[str, dict], None]


def _profile_id(cfg: CompanionConfig, position: int) -> str:
    value = str(getattr(cfg, "profile_id", "") or "").strip()
    return value or f"legacy-{position + 1}"


def _default_index_batch():
    from .cdx_reindex import IndexBatch

    return IndexBatch()


def _default_handle(
    cfg: CompanionConfig, qc: QueueClient, task: object, index_batch: object
) -> object:
    return route_task(cfg, qc, task, index_batch=index_batch)


@dataclass
class ProfileRuntimeState:
    profile_id: str
    cfg: CompanionConfig
    client: Optional[QueueClient] = None
    periodic: Dict[str, float] = field(
        default_factory=lambda: {"last_heartbeat": 0.0}
    )
    fail_streak: int = 0
    status: str = "starting"
    last_error: str = ""
    last_exception: Optional[Exception] = None
    processed_in_batch: int = 0
    index_batch: object = None

    @property
    def paused(self) -> bool:
        return bool(getattr(self.cfg, "paused", False))


@dataclass(frozen=True)
class RoundResult:
    polled_profiles: int = 0
    handled_tasks: int = 0
    failed_profiles: int = 0


class MultiProfileWorker:
    """Fair, injectable multi-profile scheduler with a single write lane."""

    def __init__(
        self,
        configs: Iterable[CompanionConfig],
        *,
        queue_factory: QueueFactory = QueueClient,
        periodic_runner: PeriodicRunner = _run_periodic,
        task_handler: TaskHandler = _default_handle,
        index_batch_factory: Callable[[], object] = _default_index_batch,
        on_status: Optional[StatusCallback] = None,
        on_batch_done: Optional[BatchCallback] = None,
        on_update_available: Optional[UpdateCallback] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._queue_factory = queue_factory
        self._periodic_runner = periodic_runner
        self._task_handler = task_handler
        self._on_status = on_status
        self._on_batch_done = on_batch_done
        self._on_update_available = on_update_available
        self._sleep = sleeper
        self._closed = False

        states: List[ProfileRuntimeState] = []
        seen: set[str] = set()
        for position, cfg in enumerate(configs):
            pid = _profile_id(cfg, position)
            if pid in seen:
                raise ValueError(f"duplicate Companion profile_id: {pid}")
            seen.add(pid)
            states.append(
                ProfileRuntimeState(
                    profile_id=pid,
                    cfg=cfg,
                    status=(
                        "paused" if bool(getattr(cfg, "paused", False)) else "starting"
                    ),
                    index_batch=index_batch_factory(),
                )
            )
        self._states = states

    @property
    def states(self) -> tuple[ProfileRuntimeState, ...]:
        return tuple(self._states)

    def state(self, profile_id: str) -> ProfileRuntimeState:
        for state in self._states:
            if state.profile_id == profile_id:
                return state
        raise KeyError(profile_id)

    def _emit_status(
        self, state: ProfileRuntimeState, status: str, detail: str = ""
    ) -> None:
        state.status = status
        if self._on_status is None:
            return
        try:
            self._on_status(state.profile_id, status, detail)
        except Exception:
            log.exception(
                "profile status callback failed · profile=%s", state.profile_id
            )

    def _client(self, state: ProfileRuntimeState) -> QueueClient:
        if state.client is None:
            state.client = self._queue_factory(
                QueueClientConfig(
                    backend_url=state.cfg.backend_url,
                    bearer_token=state.cfg.bearer_token,
                    companion_id=state.cfg.companion_id,
                )
            )
        return state.client

    def _periodic_update(self, state: ProfileRuntimeState, update: dict) -> None:
        if self._on_update_available is None:
            return
        try:
            self._on_update_available(state.profile_id, update)
        except Exception:
            log.exception(
                "profile update callback failed · profile=%s", state.profile_id
            )

    def _mark_failure(self, state: ProfileRuntimeState, exc: Exception) -> None:
        state.fail_streak += 1
        state.last_error = str(exc)
        state.last_exception = exc
        if state.fail_streak >= OFFLINE_AFTER_FAILS:
            self._emit_status(state, "offline", state.last_error)

    def _mark_poll_ok(self, state: ProfileRuntimeState) -> None:
        recovered = state.fail_streak > 0
        state.fail_streak = 0
        state.last_error = ""
        state.last_exception = None
        if recovered:
            self._emit_status(state, "idle")

    def _flush_batch(self, state: ProfileRuntimeState) -> None:
        if not state.processed_in_batch:
            return
        try:
            state.index_batch.flush()
        except Exception:
            log.exception(
                "profile index batch flush failed · profile=%s", state.profile_id
            )
        state.processed_in_batch = 0
        if self._on_batch_done is not None:
            try:
                self._on_batch_done(state.profile_id)
            except Exception:
                log.exception(
                    "profile batch callback failed · profile=%s", state.profile_id
                )

    def run_round(self) -> RoundResult:
        """Offer each active profile one poll; handle leased tasks serially."""
        if self._closed:
            raise RuntimeError("MultiProfileWorker is closed")

        polled = handled = failed = 0
        update_owner = next((s.profile_id for s in self._states if not s.paused), None)
        for state in self._states:
            if state.paused:
                if state.status != "paused":
                    self._emit_status(state, "paused")
                continue

            qc = self._client(state)
            try:
                update_callback = None
                if (
                    self._on_update_available is not None
                    and state.profile_id == update_owner
                ):

                    def update_callback(update, current=state):
                        self._periodic_update(current, update)

                self._periodic_runner(
                    state.cfg,
                    qc,
                    state.periodic,
                    update_callback,
                )
                task = qc.poll()
                take_refresh = getattr(qc, "take_master_refresh", None)
                refresh_command = take_refresh() if callable(take_refresh) else None
                if projection_refresh.remember(state.periodic, refresh_command):
                    state.periodic["last_heartbeat"] = 0.0
                polled += 1
            except Exception as exc:
                failed += 1
                self._mark_failure(state, exc)
                continue

            self._mark_poll_ok(state)
            if task is None:
                self._flush_batch(state)
                self._emit_status(state, "idle")
                continue

            self._emit_status(
                state, "working", str(getattr(task, "invoice_no", "") or "")
            )
            try:
                outcome = self._task_handler(state.cfg, qc, task, state.index_batch)
            except Exception as exc:
                failed += 1
                state.last_error = str(exc)
                self._emit_status(state, "error", state.last_error)
            else:
                self._emit_status(
                    state,
                    "idle" if not outcome else str(outcome),
                    str(getattr(task, "invoice_no", "") or ""),
                )
            state.processed_in_batch += 1
            handled += 1

        return RoundResult(polled, handled, failed)

    def run_forever(
        self,
        should_stop: Callable[[], bool],
        *,
        on_tick: Optional[Callable[[], None]] = None,
    ) -> None:
        """Drain busy rounds immediately; sleep once after a fully idle round."""
        while not should_stop():
            if on_tick is not None:
                on_tick()
            result = self.run_round()
            if result.handled_tasks:
                continue
            intervals = [
                max(1, int(getattr(s.cfg, "poll_interval_ms", 3000) or 3000))
                for s in self._states
                if not s.paused
            ]
            self._sleep((min(intervals) if intervals else 3000) / 1000.0)

    def close(self, *, signal_offline: bool = False) -> None:
        """Flush batches and close every client; one failure never hides the rest."""
        if self._closed:
            return
        for state in self._states:
            self._flush_batch(state)
            client = state.client
            if client is None:
                continue
            if signal_offline:
                try:
                    client.health(offline=True)
                except Exception:
                    log.debug(
                        "profile offline signal failed · profile=%s",
                        state.profile_id,
                        exc_info=True,
                    )
            try:
                client.close()
            except Exception:
                log.debug(
                    "profile client close failed · profile=%s",
                    state.profile_id,
                    exc_info=True,
                )
        self._closed = True


__all__ = ["MultiProfileWorker", "ProfileRuntimeState", "RoundResult"]
