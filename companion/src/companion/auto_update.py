# -*- coding: utf-8 -*-
"""强制自动更新的流程（用户 2026-10-07：小助手给所有租户强制更新到最新版）。

以前发现新版只弹通知、在托盘菜单里多一项「更新到最新版」，要人点；没人点就一直停在旧版。
现在发现新版就自己更新，但**绝不在写账套的中途换版本**（安装器会强制关掉本进程）：

1. 后台下载安装包（不影响正在干的活；失败隔 10 分钟再试）；
2. 等所有连接都不在推送、不在等账套锁、不在扫账套（``busy()`` 为假）；
3. 让取任务线程与文件夹自动录入线程协作停下（做完手上那一步才停），两边都停稳；
4. 静默安装 → 安装器关旧进程、装新版、自启。启动安装失败就恢复工作，隔 10 分钟再来。

纯流程，不碰 Qt：托盘用定时器反复调 ``tick()``，各步动作由托盘注入，便于测试。手动点「更新」也走同一条路。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

RETRY_SECONDS = 600


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
        self.failed_at = self._clock()

    def _waiting_retry(self) -> bool:
        return bool(self.failed_at) and self._clock() - self.failed_at < RETRY_SECONDS

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
            if not self._work_stopped():
                return "stopping"
            try:
                self._install(self.path)
            except Exception:
                self.stopping = False
                self.failed_at = self._clock()
                self._resume_work()
                return "failed"
            return "installing"
        if self._waiting_retry():
            return "waiting_retry"
        if self._busy():
            return "busy"
        self.stopping = True
        self._stop_work()
        return "stopping"
