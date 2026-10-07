# -*- coding: utf-8 -*-
"""强制自动更新（auto_update.py）：发现新版就下载，空闲才停手，停稳才装；不在写账套中途换版本。"""
from src.companion.auto_update import RETRY_SECONDS, AutoUpdate


class Box:
    def __init__(self):
        self.now = 1000.0
        self.busy = False
        self.stopped = False
        self.calls = []
        self.install_error = None
        self.flow = AutoUpdate(
            busy=lambda: self.busy,
            stop_work=lambda: self.calls.append("stop"),
            work_stopped=lambda: self.stopped,
            resume_work=lambda: self.calls.append("resume"),
            start_download=lambda version, url: self.calls.append(("download", version)),
            install=self.install,
            clock=lambda: self.now)

    def install(self, path):
        if self.install_error:
            raise self.install_error
        self.calls.append(("install", path))


def test_nothing_to_do_without_a_new_version():
    box = Box()
    assert box.flow.tick() == "none"
    assert box.calls == []


def test_downloads_then_waits_for_idle_then_stops_and_installs():
    box = Box()
    assert box.flow.offer("1.3.8", "/x.exe") is True
    assert box.flow.offer("1.3.8", "/x.exe") is False  # 同版本只提示一次
    assert box.flow.tick() == "downloading"
    assert box.flow.tick() == "downloading"  # 不重复下载
    box.flow.downloaded("C:/setup.exe")
    box.busy = True
    assert box.flow.tick() == "busy"  # 正在推送：不动
    box.busy = False
    assert box.flow.tick() == "stopping"
    assert box.flow.tick() == "stopping"  # 还没停稳：不装
    box.stopped = True
    assert box.flow.tick() == "installing"
    assert box.calls == [("download", "1.3.8"), "stop", ("install", "C:/setup.exe")]


def test_failed_download_retries_after_a_while():
    box = Box()
    box.flow.offer("1.3.8", "/x.exe")
    box.flow.tick()
    box.flow.download_failed()
    assert box.flow.tick() == "waiting_retry"
    box.now += RETRY_SECONDS + 1
    assert box.flow.tick() == "downloading"
    assert box.calls.count(("download", "1.3.8")) == 2


def test_installer_that_cannot_start_resumes_work_and_tries_later():
    box = Box()
    box.flow.offer("1.3.8", "/x.exe")
    box.flow.tick()
    box.flow.downloaded("C:/setup.exe")
    box.flow.tick()
    box.stopped = True
    box.install_error = OSError("blocked")
    assert box.flow.tick() == "failed"
    assert box.calls[-1] == "resume"
    assert box.flow.tick() == "waiting_retry"
    box.now += RETRY_SECONDS + 1
    box.install_error = None
    box.stopped = False
    assert box.flow.tick() == "stopping"


def test_a_newer_release_replaces_a_downloaded_older_one():
    box = Box()
    box.flow.offer("1.3.8", "/a.exe")
    box.flow.tick()
    box.flow.downloaded("C:/a.exe")
    box.busy = True
    box.flow.tick()
    assert box.flow.offer("1.3.9", "/b.exe") is True
    box.busy = False
    assert box.flow.tick() == "downloading"
    assert box.calls[-1] == ("download", "1.3.9")
