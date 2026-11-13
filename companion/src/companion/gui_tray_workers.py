"""Background Qt workers used by the system tray application."""

from __future__ import annotations

import threading
from copy import copy

from PySide6.QtCore import QThread, Signal

from . import watchdog
from .config import CompanionConfig
from .i18n import MessageSpec, default_lang, http_status_category, normalize
from .logger import get_logger

log = get_logger(__name__)

def _operator_task(task) -> bool:
    """代录的单（载荷 ``entry_mode=operator``）：在 Express 界面里录，Express 开着这本账套是它在用。"""
    from .payload_contract import ENTRY_OPERATOR

    payload = getattr(task, "payload", None) or {}
    return isinstance(payload, dict) and str(payload.get("entry_mode") or "").strip() == ENTRY_OPERATOR


def friendly_poll_error_spec(error: Exception) -> MessageSpec:
    try:
        import httpx

        if isinstance(error, httpx.HTTPStatusError):
            category = http_status_category(error.response.status_code)
            if category == "auth":
                return MessageSpec("off_auth")
            if category == "disabled":
                return MessageSpec("off_disabled")
            if category == "push_off":
                return MessageSpec("off_push_off")
    except Exception:
        pass
    return MessageSpec("off_network")


def friendly_poll_error(error: Exception, lang: str) -> str:
    return friendly_poll_error_spec(error).render(lang)


class PollWorker(QThread):
    status = Signal(str, str, object)
    update_available = Signal(str, str)
    batch_done = Signal(str, int, int, str)

    def __init__(self, configs, *, announce=None) -> None:
        super().__init__()
        # announce(cfg)：每轮先给 Express 成员认一次电脑（device_link.Announcer.announce，到时候才真发；托盘传入）
        self._announce = announce
        if isinstance(configs, CompanionConfig):
            configs = [configs]
        self.configs = []
        for source in configs:
            runtime = copy(source)
            profile_save = getattr(runtime, "_profile_save", None)
            if callable(profile_save):
                persisted_pause = bool(source.paused)
                object.__setattr__(
                    runtime,
                    "_profile_save",
                    lambda changed, save=profile_save, paused=persisted_pause: save(
                        changed, paused=paused
                    ),
                )
            self.configs.append(runtime)
        self.cfg = self.configs[0] if self.configs else CompanionConfig()
        self._stop = False
        self._batch_stats = {
            cfg.profile_id: {"ok": 0, "fail": 0, "invoice": ""} for cfg in self.configs
        }
        self._logged_offline_errors: dict[str, str] = {}
        self._scheduler = None
        self._live = watchdog.Liveness()
        self._signal_offline_on_stop = True

    def stop(self, *, signal_offline: bool = True) -> None:
        self._signal_offline_on_stop = bool(signal_offline)
        self._stop = True
        for cfg in self.configs:
            cfg.paused = True

    def stall_seconds(self) -> float:
        return self._live.stall_seconds()

    @staticmethod
    def _account_dir(cfg: CompanionConfig, task) -> str:
        """这张单要写的账套目录（不是成员的默认账套：一台电脑管着很多家，worker.task_account_dir）。"""
        from .worker import task_account_dir

        try:
            return task_account_dir(cfg, getattr(task, "payload", None) or {})
        except Exception:
            return ""

    def _beating(self, run):
        """代录一张要几十秒到几分钟（等人停手、Express 慢时等画面）：这段时间旁路每秒盖一次存活时标，看门狗不把取任务线程
        当卡死重启（11-01 录制机：暂停等人时判卡死 184 秒、重启取任务线程，新线程接着领后面的单，和还在录的那张撞在一起，
        后面每张都「命令被禁用」）。代录自己有时限（画面宽限、服务器断线等待），真卡住由它转人工。"""
        done = threading.Event()

        def beat():
            while not done.wait(1.0):
                self._live.tick()

        threading.Thread(target=beat, name="operator-liveness", daemon=True).start()
        try:
            return run()
        finally:
            done.set()

    def _stats(self, profile_id: str) -> dict:
        return self._batch_stats.setdefault(profile_id, {"ok": 0, "fail": 0, "invoice": ""})

    def handle_task(self, cfg, queue, task, index_batch, lang: str, write=None):
        """取任务线程交来的一张单（``write`` = ``worker._handle_dbf_task``，测试换成假的）。"""
        if write is None:
            from .worker import _handle_dbf_task as write
        profile_id = cfg.profile_id
        batch = self._stats(profile_id)
        # Express 开着要写的表：不弹询问框、不拿着取任务线程等人（11-13 真机：两台电脑在「账套正被占用」框前一起停了十几分钟，
        # 心跳与领单都停，Express 关了也不恢复）。不论有没有人在电脑前，一律交给写入时的按表探占用（``worker._handle_dbf_task``：
        # 一个字节不动、按「等账套」回报，服务端歇一会儿再派，Express 关了自动写）；电脑前的人在网页 / LINE / CC 对话里看「在等 Express」、
        # 要放弃就在那里撤回。这台写过、只是回执没送到的单照上次的回执回报（同样在 _handle_dbf_task）。
        if self._stop:
            return "error"
        try:
            if _operator_task(task):
                outcome = self._beating(lambda: write(cfg, queue, task, index_batch=index_batch))
            else:
                outcome = write(cfg, queue, task, index_batch=index_batch)
        except Exception:
            batch["fail"] += 1
            raise
        if outcome == "waiting_lock":
            return "waiting_lock"
        if outcome:
            batch["fail"] += 1
            return "error"
        batch["ok"] += 1
        batch["invoice"] = str(task.invoice_no or "")
        return None

    def run(self) -> None:
        from .multi_profile_worker import MultiProfileWorker

        lang = normalize(getattr(self.cfg, "ui_lang", "") or default_lang())
        stats = self._stats

        def handle(cfg, queue, task, index_batch):
            return self.handle_task(cfg, queue, task, index_batch, lang)

        def batch_done(profile_id: str) -> None:
            batch = stats(profile_id)
            succeeded, failed, invoice = (
                batch["ok"],
                batch["fail"],
                batch["invoice"],
            )
            batch.update(ok=0, fail=0, invoice="")
            if succeeded or failed:
                self.batch_done.emit(profile_id, succeeded, failed, invoice)

        scheduler = MultiProfileWorker(
            self.configs,
            task_handler=handle,
            on_status=lambda profile_id, key, detail: self._emit_scheduler_status(
                scheduler, profile_id, key, detail
            ),
            on_batch_done=batch_done,
            on_update_available=lambda _profile_id, update: self.update_available.emit(
                update["version"], update["url"]
            ),
            announce=self._announce,
        )
        self._scheduler = scheduler
        try:
            scheduler.run_forever(lambda: self._stop, on_tick=self._live.tick)
        finally:
            scheduler.close(signal_offline=self._signal_offline_on_stop)
            self._scheduler = None

    def _emit_scheduler_status(
        self, scheduler, profile_id: str, key: str, detail: str
    ) -> None:
        if key == "offline":
            error = scheduler.state(profile_id).last_exception
            if error is not None:
                signature = repr(error)
                if self._logged_offline_errors.get(profile_id) != signature:
                    log.warning(
                        "Companion 连接离线 · profile=%s · error=%r",
                        profile_id,
                        error,
                    )
                    self._logged_offline_errors[profile_id] = signature
                detail = friendly_poll_error_spec(error)
        elif key == "error":
            if detail:
                log.warning(
                    "Companion 任务状态异常 · profile=%s · detail=%r",
                    profile_id,
                    detail,
                )
            detail = ""
        else:
            self._logged_offline_errors.pop(profile_id, None)
        self.status.emit(profile_id, key, detail)
