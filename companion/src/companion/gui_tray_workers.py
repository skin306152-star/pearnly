"""Background Qt workers used by the system tray application."""

from __future__ import annotations

import threading
from copy import copy

from PySide6.QtCore import QThread, Signal

from . import account_lock, watchdog
from .config import CompanionConfig
from .i18n import MessageSpec, default_lang, http_status_category, normalize, t
from .logger import activity, get_logger

log = get_logger(__name__)


class BusyPrompt:
    def __init__(self, lang: str) -> None:
        self.lang = lang
        self.choice = "retry"
        self.done = threading.Event()


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
    busy_prompt = Signal(object)

    def __init__(self, configs) -> None:
        super().__init__()
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
        self._abort_batches: set[str] = set()
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

    def _account_dir(self, cfg: CompanionConfig) -> str:
        from .secret_store import load_express_settings

        try:
            return load_express_settings(cfg).account_dir or ""
        except Exception:
            return ""

    def _ask_busy(self, lang: str) -> str:
        prompt = BusyPrompt(lang)
        self.busy_prompt.emit(prompt)
        while not self._stop:
            if prompt.done.wait(0.2):
                return prompt.choice
        return "abort"

    def _abort_task(self, profile_id: str, queue, task, lang: str) -> None:
        try:
            queue.ack_failure(
                task.id, t("busy_title", lang), error_code="USER_ABORTED_BUSY"
            )
        except Exception:
            log.exception("终止推送回报云端失败")
        self.status.emit(profile_id, "error", str(task.invoice_no or ""))
        activity("log_busy_aborted", lang=lang)

    def run(self) -> None:
        from .multi_profile_worker import MultiProfileWorker
        from .worker import _handle_dbf_task

        lang = normalize(getattr(self.cfg, "ui_lang", "") or default_lang())

        def stats(profile_id: str) -> dict:
            return self._batch_stats.setdefault(
                profile_id, {"ok": 0, "fail": 0, "invoice": ""}
            )

        def handle(cfg, queue, task, index_batch):
            profile_id = cfg.profile_id
            batch = stats(profile_id)
            if profile_id in self._abort_batches:
                self._abort_task(profile_id, queue, task, lang)
                batch["fail"] += 1
                return "error"
            account_dir = self._account_dir(cfg)
            while (
                not self._stop
                and account_dir
                and account_lock.is_account_busy(account_dir)
            ):
                self.status.emit(profile_id, "waiting_lock", str(task.invoice_no or ""))
                if self._ask_busy(lang) == "abort":
                    self._abort_batches.add(profile_id)
                    self._abort_task(profile_id, queue, task, lang)
                    batch["fail"] += 1
                    return "error"
            if self._stop:
                return "error"
            try:
                outcome = _handle_dbf_task(cfg, queue, task, index_batch=index_batch)
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

        def batch_done(profile_id: str) -> None:
            self._abort_batches.discard(profile_id)
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


class UpdateWorker(QThread):
    done = Signal(str)
    failed = Signal()

    def __init__(self, backend_url: str, rel_url: str) -> None:
        super().__init__()
        self._backend = backend_url
        self._url = rel_url

    def run(self) -> None:
        try:
            from .updater import download_installer

            path = download_installer(self._backend, self._url)
            self.done.emit(path)
        except Exception:
            log.exception("自动更新下载失败")
            self.failed.emit()
