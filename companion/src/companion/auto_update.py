# -*- coding: utf-8 -*-
"""强制自动更新的流程（用户 2026-10-07：小助手给所有租户强制更新到最新版）。

以前发现新版只弹通知、在托盘菜单里多一项「更新到最新版」，要人点；没人点就一直停在旧版。
现在发现新版就自己更新，但**绝不在写账套的中途换版本**（安装器会强制关掉本进程）：

1. 后台下载安装包（不影响正在干的活；失败隔 10 分钟再试）；
2. 等所有连接都不在推送、不在等账套锁、不在扫账套（``busy()`` 为假）；
3. 让取任务线程与文件夹自动录入线程协作停下（做完手上那一步才停），两边都停稳；
4. 静默安装 → 安装器关旧进程、装新版、自启。安装包**只启动一次**：启动失败、安装包自己退出了而本进程还在、或
   5 分钟后本进程还没被安装器关掉（例如 Windows「智能应用控制」拦下了没签名的安装包），都算这次没装上 ——
   恢复工作，同一版本隔一天再试（2026-10-08：以前这里每 2 秒重新启动一次安装包，被拦的电脑上不停弹 Windows
   安全提示，小助手也一直停着不干活）。

纯流程，不碰 Qt：托盘用定时器反复调 ``tick()``，各步动作由托盘注入，便于测试。手动点「更新」也走同一条路。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

RETRY_SECONDS = 600  # 下载失败后隔多久再下
INSTALL_TIMEOUT_SECONDS = 300  # 安装包启动后这么久本进程还在 = 没装上
INSTALL_RETRY_SECONDS = 24 * 3600  # 没装上的同一版本隔多久再试


class AutoUpdate:
    def __init__(self, *, busy: Callable[[], bool], stop_work: Callable[[], None],
                 work_stopped: Callable[[], bool], resume_work: Callable[[], None],
                 start_download: Callable[[str, str], None], install: Callable[[str], None],
                 clock: Callable[[], float] = time.time) -> None:
        self._busy = busy
        self._stop_work = stop_work
        self._work_stopped = work_stopped
        self._resume_work = resume_work
        self._start_download = start_download
        self._install = install
        self._clock = clock
        self.pending: Optional[tuple[str, str]] = None
        self.path: Optional[str] = None
        self.downloading = False
        self.stopping = False
        self.failed_at = 0.0
        self.retry_after = RETRY_SECONDS
        self.launched_at = 0.0  # 安装包启动的时刻（0 = 还没启动）
        self.installer = None  # 启动的安装包进程（有 poll() 就能看它是否已退出）

    def offer(self, version: str, url: str) -> bool:
        """云端有新版。换了版本就丢掉之前下好的包重新下；同一版本重复提示不重来。返回是否是新提示。"""
        if self.pending and self.pending[0] == version:
            return False
        self.pending = (version, url)
        if not self.stopping:
            self.path = None
        return True

    def downloaded(self, path: str) -> None:
        self.downloading = False
        self.path = path

    def download_failed(self) -> None:
        self.downloading = False
        self.failed_at, self.retry_after = self._clock(), RETRY_SECONDS

    def retry_now(self) -> None:
        """有人点了「立即更新」：不等失败后的间隔，马上再走一遍（仍等空闲、停稳才装）。"""
        if not self.stopping:
            self.failed_at = 0.0

    def _waiting_retry(self) -> bool:
        return bool(self.failed_at) and self._clock() - self.failed_at < self.retry_after

    def _install_failed(self) -> str:
        self.stopping, self.launched_at, self.installer = False, 0.0, None
        self.failed_at, self.retry_after = self._clock(), INSTALL_RETRY_SECONDS
        self._resume_work()
        return "failed"

    def _installer_gone(self) -> bool:
        poll = getattr(self.installer, "poll", None)
        try:
            return poll is not None and poll() is not None
        except Exception:  # noqa: BLE001 — 看不了就按超时判
            return False

    def state(self) -> tuple[str, str]:
        """给界面显示的真实状态：(none|found|downloading|failed|ready|installing, 版本号)。只读，不推进流程。"""
        if not self.pending:
            return "none", ""
        version = self.pending[0]
        if self.stopping:
            return "installing", version
        if self.path is None:
            if self.downloading:
                return "downloading", version
            return ("failed" if self._waiting_retry() else "found"), version
        return ("failed" if self._waiting_retry() else "ready"), version

    def tick(self) -> str:
        if not self.pending:
            return "none"
        if self.path is None:
            if self.downloading:
                return "downloading"
            if self._waiting_retry():
                return "waiting_retry"
            self.downloading = True
            self._start_download(*self.pending)
            return "downloading"
        if self.stopping:
            if self.launched_at:
                # 已经启动过安装包：不再启动第二次。安装器会关掉本进程；还活着说明没装上。
                if self._installer_gone() or self._clock() - self.launched_at >= INSTALL_TIMEOUT_SECONDS:
                    return self._install_failed()
                return "installing"
            if not self._work_stopped():
                return "stopping"
            try:
                self.installer = self._install(self.path)
            except Exception:
                return self._install_failed()
            self.launched_at = self._clock()
            return "installing"
        if self._waiting_retry():
            return "waiting_retry"
        if self._busy():
            return "busy"
        self.stopping = True
        self._stop_work()
        return "stopping"
