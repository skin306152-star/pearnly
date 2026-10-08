# -*- coding: utf-8 -*-
"""强制自动更新（auto_update.py）：发现新版就下载，空闲才停手，停稳才装；不在写账套中途换版本。"""
from src.companion.auto_update import INSTALL_RETRY_SECONDS, INSTALL_TIMEOUT_SECONDS, RETRY_SECONDS, AutoUpdate


class Box:
    def __init__(self):
        self.now = 1000.0
        self.busy = False
        self.stopped = False
        self.calls = []
        self.install_error = None
        self.installer = Installer()
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
        return self.installer


class Installer:
    """安装包进程：exit_code 为 None = 还在跑。"""

    def __init__(self):
        self.exit_code = None

    def poll(self):
        return self.exit_code


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
    assert box.flow.tick() == "waiting_retry"  # 安装没成：同一版本隔一天再试，不是 10 分钟
    box.now += INSTALL_RETRY_SECONDS
    box.install_error = None
    box.stopped = False
    assert box.flow.tick() == "stopping"


def ready_to_install(box):
    box.flow.offer("1.3.8", "/x.exe")
    box.flow.tick()
    box.flow.downloaded("C:/setup.exe")
    box.flow.tick()
    box.stopped = True
    assert box.flow.tick() == "installing"


def test_the_installer_is_started_only_once_while_it_runs():
    # 2026-10-08：以前「installing」之后每 2 秒又启动一次安装包，Windows 智能应用控制拦下时不停弹提示
    box = Box()
    ready_to_install(box)
    for _ in range(50):
        box.now += 2
        assert box.flow.tick() == "installing"
    assert box.calls.count(("install", "C:/setup.exe")) == 1
    assert "resume" not in box.calls


def test_an_installer_that_exits_while_we_still_run_did_not_install():
    box = Box()
    ready_to_install(box)
    box.installer.exit_code = 1  # 被拦 / 出错退出了，本进程没被关掉
    assert box.flow.tick() == "failed"
    assert box.calls[-1] == "resume"
    assert box.flow.state() == ("failed", "1.3.8")
    box.now += INSTALL_RETRY_SECONDS - 1
    assert box.flow.tick() == "waiting_retry"
    assert box.calls.count(("install", "C:/setup.exe")) == 1


def test_an_installer_that_never_closes_us_counts_as_failed_after_a_while():
    box = Box()
    box.installer = object()  # 看不了进程状态：只按时间判
    ready_to_install(box)
    box.now += INSTALL_TIMEOUT_SECONDS - 1
    assert box.flow.tick() == "installing"
    box.now += 1
    assert box.flow.tick() == "failed"
    assert box.calls[-1] == "resume"


def test_update_now_retries_at_once_after_a_failed_install():
    box = Box()
    ready_to_install(box)
    box.installer.exit_code = 1
    box.flow.tick()
    box.flow.retry_now()
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


def test_state_reports_the_real_step_without_moving_the_flow():
    # 主窗口底部显示（2026-10-08）：只读，不推进下载 / 安装
    box = Box()
    assert box.flow.state() == ("none", "")
    box.flow.offer("1.4.0", "/x.exe")
    assert box.flow.state() == ("found", "1.4.0")
    assert box.calls == []
    box.flow.tick()
    assert box.flow.state() == ("downloading", "1.4.0")
    box.flow.downloaded("C:/setup.exe")
    assert box.flow.state() == ("ready", "1.4.0")
    box.flow.tick()
    assert box.flow.state() == ("installing", "1.4.0")
    box.flow.offer("1.4.1", "/y.exe")  # 安装途中又发了新版：不打断，显示仍是在装
    assert box.flow.state()[0] == "installing"


def test_state_shows_a_failed_download_until_the_retry():
    box = Box()
    box.flow.offer("1.4.0", "/x.exe")
    box.flow.tick()
    box.flow.download_failed()
    assert box.flow.state() == ("failed", "1.4.0")
    box.now += RETRY_SECONDS + 1
    assert box.flow.state() == ("found", "1.4.0")
