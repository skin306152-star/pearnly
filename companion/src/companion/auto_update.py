# -*- coding: utf-8 -*-
"""强制自动更新的流程（用户 2026-10-07：小助手给所有租户强制更新到最新版）。

以前发现新版只弹通知、在托盘菜单里多一项「更新到最新版」，要人点；没人点就一直停在旧版。
现在发现新版就自己更新，但**绝不在写账套的中途换版本**（安装器会强制关掉本进程）：

1. 后台下载安装包（不影响正在干的活；失败隔 10 分钟再试）；
2. 等所有连接都不在推送、不在等账套锁、不在扫账套（``busy()`` 为假）；
3. 让取任务线程与文件夹自动录入线程协作停下（做完手上那一步才停），两边都停稳；
4. 静默安装 → 安装器关旧进程、装新版、自启。安装包**只启动一次**：启动失败、安装包自己退出了而本进程还在、或
   5 分钟后本进程还没被安装器关掉，都算这次没装上 —— 恢复工作，同一版本按 ``retry_delay`` 退避再试（第 1 次隔一天，
   之后翻倍，最多隔 7 天；2026-10-08：以前这里每 2 秒重新启动一次安装包，被拦的电脑上不停弹 Windows 安全提示，
   小助手也一直停着不干活）。

被 Windows 拦下（启动安装包时系统按策略拒绝，见 ``update_block.BLOCKED_ERRORS``；教程问题第 1 条，智能应用控制拦了
没签名的安装包）单独记成 ``blocked``：界面显示一行 + 「下载新版」，心跳如实上报。结果记在本机（``update_block.UpdateMemory``），
重开 CC 也按同一退避；重开后还是旧版本也算这次没装上。不绕开系统的拦截，只让人看得见、有出路。

纯流程，不碰 Qt：托盘用定时器反复调 ``tick()``，各步动作由托盘注入，便于测试。手动点「更新」也走同一条路。
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .update_block import UpdateMemory, blocked_code

RETRY_SECONDS = 600  # 下载失败后隔多久再下
INSTALL_TIMEOUT_SECONDS = 300  # 安装包启动后这么久本进程还在 = 没装上
INSTALL_RETRY_SECONDS = 24 * 3600  # 没装上的同一版本第一次隔多久再试
INSTALL_RETRY_MAX_SECONDS = 7 * 24 * 3600  # 之后每次翻倍，最多隔这么久

log = logging.getLogger("pearnly_companion.auto_update")


def retry_delay(attempts: int) -> float:
    """同一版本第 ``attempts`` 次没装上之后隔多久再试：一天、两天、四天，之后每七天。"""
    return float(min(INSTALL_RETRY_SECONDS * 2 ** max(0, int(attempts) - 1), INSTALL_RETRY_MAX_SECONDS))


class AutoUpdate:
    def __init__(self, *, busy: Callable[[], bool], stop_work: Callable[[], None],
                 work_stopped: Callable[[], bool], resume_work: Callable[[], None],
                 start_download: Callable[[str, str], None], install: Callable[[str], None],
                 clock: Callable[[], float] = time.time, memory: Optional[UpdateMemory] = None,
                 exists: Callable[[str], bool] = lambda path: True) -> None:
        self._busy = busy
        self._stop_work = stop_work
        self._work_stopped = work_stopped
        self._resume_work = resume_work
        self._start_download = start_download
        self._install = install
        self._clock = clock
        self._exists = exists   # 下好的安装包还在不在（隔了一天，系统清理可能删了临时文件）
        self.memory = memory if memory is not None else UpdateMemory(None, publish=False)
        self.pending: Optional[tuple[str, str]] = None
        self.path: Optional[str] = None
        self.path_version = ""  # 下好的安装包是哪个版本
        self._fetching = ""  # 正在下载的是哪个版本
        self._installing = ""  # 启动的安装包是哪个版本（安装途中云端又发了新版时，与 pending 不同）
        self.downloading = False
        self.stopping = False
        self.failed_at = 0.0
        self.retry_after = RETRY_SECONDS
        self.launched_at = 0.0  # 安装包启动的时刻（0 = 还没启动）
        self.installer = None  # 启动的安装包进程（有 poll() 就能看它是否已退出）

    def offer(self, version: str, url: str) -> bool:
        """云端有新版。换了版本就丢掉之前下好的包重新下；同一版本重复提示不重来。返回是否是新提示。

        这个版本以前没装上（本机记录）：照记录的退避等，不因为重开 CC、换了一次提示就马上再试。"""
        if self.pending and self.pending[0] == version:
            return False
        self.pending = (version, url)
        if not self.stopping:
            self.path, self.path_version = None, ""
            record = self.memory.record
            if record is not None and record.version == version and record.state in ("blocked", "failed"):
                self.failed_at, self.retry_after = record.at, retry_delay(record.attempts)
            else:
                self.failed_at, self.retry_after = 0.0, RETRY_SECONDS
        return True

    def downloaded(self, path: str) -> None:
        """下载完成。下载途中云端又发了新版：这份旧的不装，下一拍下新的（以前会装上旧的那份，记录也会记错版本）。"""
        self.downloading = False
        if self.pending and self._fetching == self.pending[0] and not self.stopping:
            self.path, self.path_version = path, self._fetching

    def download_failed(self) -> None:
        self.downloading = False
        self.failed_at, self.retry_after = self._clock(), RETRY_SECONDS

    def retry_now(self) -> None:
        """有人点了「立即更新」：不等失败后的间隔，马上再走一遍（仍等空闲、停稳才装）。"""
        if not self.stopping:
            self.failed_at = 0.0

    def _waiting_retry(self) -> bool:
        return bool(self.failed_at) and self._clock() - self.failed_at < self.retry_after

    def _install_failed(self, code: Optional[int] = None, reason: str = "") -> str:
        """这次没装上：``code`` 是系统拦下的错误码（blocked），没有就是别的原因（failed）。记下并退避，恢复工作。"""
        version = self._installing or (self.pending[0] if self.pending else "")
        state = "blocked" if code is not None else "failed"
        now = self._clock()
        self.stopping, self.launched_at, self.installer, self._installing = False, 0.0, None, ""
        record = self.memory.finish(version, state, now, code)
        self.failed_at, self.retry_after = now, retry_delay(record.attempts)
        log.warning("自动更新 v%s 没装上（%s）：%s；第 %d 次，%.0f 小时后再试", version, state, reason,
                    record.attempts, self.retry_after / 3600)
        self._resume_work()
        return state

    def _installer_gone(self) -> bool:
        poll = getattr(self.installer, "poll", None)
        try:
            return poll is not None and poll() is not None
        except Exception:  # noqa: BLE001 — 看不了就按超时判
            return False

    def blocked(self) -> str:
        """本机记着被拦下、还没装上的版本（不管云端这次有没有再提示）；没有就是空串。"""
        record = self.memory.blocked()
        return record.version if record else ""

    def take_blocked_notice(self) -> str:
        """被拦了、这个版本还没弹过通知：回版本号并记成已弹（同一版本只弹一次）；否则空串。"""
        return self.memory.take_notice() or ""

    def state(self) -> tuple[str, str]:
        """给界面显示的真实状态：(none|found|downloading|failed|ready|installing|blocked, 版本号)。只读，不推进流程。

        被拦下的版本在再试之前（以及再试前的下载、等空闲期间）一直显示 blocked，界面给「下载新版」；正在停手、安装时
        显示 installing。"""
        if not self.pending:
            blocked = self.blocked()
            return ("blocked", blocked) if blocked else ("none", "")
        version = self.pending[0]
        if self.stopping:
            return "installing", version
        if self.memory.blocked(version):
            return "blocked", version
        if self.path is None and self.downloading:
            return "downloading", version
        if self.path is None:
            return ("failed" if self._waiting_retry() else "found"), version
        return ("failed" if self._waiting_retry() else "ready"), version

    def tick(self) -> str:
        if not self.pending:
            return "none"
        if self.path is not None and not self.stopping and not self._exists(self.path):
            self.path, self.path_version = None, ""   # 下好的包不见了（临时文件被清理）：重新下
        if self.path is None:
            if self.downloading:
                return "downloading"
            if self._waiting_retry():
                return "waiting_retry"
            self.downloading, self._fetching = True, self.pending[0]
            self._start_download(*self.pending)
            return "downloading"
        if self.stopping:
            if self.launched_at:
                # 已经启动过安装包：不再启动第二次。安装器会关掉本进程；还活着说明没装上。
                if self._installer_gone():
                    return self._install_failed(reason=f"安装包自己退出了（退出码 {self.installer.poll()}）")
                if self._clock() - self.launched_at >= INSTALL_TIMEOUT_SECONDS:
                    return self._install_failed(reason="安装包启动后本进程一直没被关掉")
                return "installing"
            if not self._work_stopped():
                return "stopping"
            # 先记下「启动了安装包」再启动：安装器随时会关掉本进程，重开后还是旧版本就知道这次没装上
            self._installing = self.path_version or self.pending[0]
            self.memory.attempt(self._installing, self._clock())
            try:
                self.installer = self._install(self.path)
            except Exception as error:
                return self._install_failed(blocked_code(error), f"启动安装包失败：{error!r}")
            self.launched_at = self._clock()
            return "installing"
        if self._waiting_retry():
            return "waiting_retry"
        if self._busy():
            return "busy"
        self.stopping = True
        self._stop_work()
        return "stopping"
