# -*- coding: utf-8 -*-
"""自动更新被 Windows 拦下（教程问题第 1 条，2026-10-13 录制笔记本：智能应用控制拦了没签名的安装包）。

只让「被拦」看得见、有出路，不绕开系统的拦截。Windows 专属的现象（CreateProcess 被拒、安装包进程）用假的错误码、
假进程来造；在 Windows 上另核对真 OSError 的样子。按真实顺序：发现新版 → 下载 → 等空闲 → 停手 → 启动安装包 → 被拒。
"""
import json
import sys

import pytest

from src.companion import update_block
from src.companion.auto_update import (
    INSTALL_RETRY_MAX_SECONDS, INSTALL_RETRY_SECONDS, INSTALL_TIMEOUT_SECONDS, AutoUpdate, retry_delay,
)
from src.companion.update_block import BLOCKED_ERRORS, UpdateMemory, blocked_code

AT = 1_791_890_280.0   # 2026-10-13 11:18 UTC = 曼谷 18:18（教程笔记本报告的时刻）
AT_ISO = "2026-10-13T11:18:00Z"


class WinError(OSError):
    """Windows 上 CreateProcess 失败时 Python 抛的样子（OSError.winerror）；别的系统上也能造出来。"""

    def __init__(self, code):
        super().__init__(13, "blocked")
        self._code = code

    @property
    def winerror(self):
        return self._code


class Installer:
    """安装包进程：exit_code 为 None = 还在跑。"""

    def __init__(self):
        self.exit_code = None

    def poll(self):
        return self.exit_code


class Box:
    """托盘注入的各步动作；clock 由测试推进。memory 用本次的临时文件，模拟重开 CC 就用同一个文件再建一份。"""

    def __init__(self, path, current="1.5.1", now=AT):
        self.path, self.current = path, current
        self.now = now
        self.busy = False
        self.stopped = False
        self.calls = []
        self.install_error = None
        self.installer = Installer()
        self.files = set()
        self.start()

    def start(self):
        """（重新）启动 CC：从本机记录读起，换一个流程对象。"""
        self.memory = UpdateMemory(self.path, current=self.current)
        self.flow = AutoUpdate(
            busy=lambda: self.busy, stop_work=lambda: self.calls.append("stop"),
            work_stopped=lambda: self.stopped, resume_work=lambda: self.calls.append("resume"),
            start_download=lambda version, url: self.calls.append(("download", version)),
            install=self.install, clock=lambda: self.now, memory=self.memory,
            exists=lambda path: path in self.files)
        return self.flow

    def install(self, path):
        self.calls.append(("install", path))
        if self.install_error:
            raise self.install_error
        return self.installer

    def to_install(self, version="1.5.2"):
        """新版提示 → 下载完 → 空闲 → 停稳，走到「要启动安装包」那一拍（返回那一拍的结果）。"""
        self.flow.offer(version, f"/r/{version}/CC-Setup.exe")
        assert self.flow.tick() == "downloading"
        path = f"C:/Temp/CC-Setup_{version}.exe"
        self.files.add(path)
        self.flow.downloaded(path)
        self.stopped = False
        assert self.flow.tick() == "stopping"
        self.stopped = True
        return self.flow.tick()


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path / "update_state.json")


# ── 判据 ──────────────────────────────────────────────────────────────────────


def test_only_policy_and_security_refusals_count_as_blocked():
    # 4551「An Application Control policy has blocked this file」（智能应用控制 / 应用控制）、577 代码完整性、
    # 1260 组策略 / AppLocker、225 杀毒：系统拒绝运行这个文件
    for code in (4551, 4556, 4580, 577, 1260, 786, 225, 226):
        assert blocked_code(WinError(code)) == code, code
    # 找不到文件、拒绝访问、坏包、普通异常：原因说不准，不说成被 Windows 拦下
    for error in (WinError(2), WinError(5), WinError(193), OSError("boom"), RuntimeError("x")):
        assert blocked_code(error) is None
    assert 4551 in BLOCKED_ERRORS and 5 not in BLOCKED_ERRORS


@pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 的 OSError 带 winerror")
def test_a_real_windows_oserror_is_read_the_same_way():
    assert blocked_code(OSError(None, "An Application Control policy has blocked this file.", None, 4551)) == 4551
    assert blocked_code(OSError(None, "The system cannot find the file specified.", None, 2)) is None


def test_retries_back_off_one_two_four_days_then_weekly():
    assert [retry_delay(n) / 86400 for n in (1, 2, 3, 4, 5, 9)] == [1, 2, 4, 7, 7, 7]
    assert retry_delay(1) == INSTALL_RETRY_SECONDS and retry_delay(50) == INSTALL_RETRY_MAX_SECONDS


# ── 被拦下：看得见、记得住、不轰炸 ──────────────────────────────────────────────


def test_a_blocked_installer_resumes_work_and_shows_blocked_with_a_notice_once(box):
    box.install_error = WinError(4551)
    assert box.to_install() == "blocked"
    assert box.calls == [("download", "1.5.2"), "stop", ("install", "C:/Temp/CC-Setup_1.5.2.exe"), "resume"]
    assert box.flow.state() == ("blocked", "1.5.2")
    assert box.flow.take_blocked_notice() == "1.5.2"
    assert box.flow.take_blocked_notice() == ""   # 同一版本只弹一次
    record = json.loads(box.path.read_text(encoding="utf-8"))
    assert record == {"version": "1.5.2", "state": "blocked", "at": AT, "attempts": 1, "code": 4551, "notified": True}
    # 心跳、文件夹同步报这一份（UTC，带 Z）
    assert update_block.reported() == {"version": "1.5.2", "at": AT_ISO}


def test_the_same_version_is_not_retried_until_the_backoff_and_then_slower(box):
    box.install_error = WinError(4551)
    box.to_install()
    assert box.flow.take_blocked_notice() == "1.5.2"   # 托盘在第一次被拦时弹过
    for step in (60, 3600, INSTALL_RETRY_SECONDS - 3600 - 61):
        box.now += step
        assert box.flow.tick() == "waiting_retry"
        assert box.flow.state() == ("blocked", "1.5.2")
    assert box.calls.count(("install", "C:/Temp/CC-Setup_1.5.2.exe")) == 1   # 一天之内只启动过一次
    box.now += 1
    box.stopped = False
    assert box.flow.tick() == "stopping"
    assert box.flow.state() == ("installing", "1.5.2")
    box.stopped = True
    assert box.flow.tick() == "blocked"
    assert box.memory.record.attempts == 2
    assert box.flow.take_blocked_notice() == ""   # 第二次被拦不再弹通知（菜单与设置里照样有「下载新版」）
    box.now += INSTALL_RETRY_SECONDS
    assert box.flow.tick() == "waiting_retry"     # 第二次之后隔两天
    box.now += INSTALL_RETRY_SECONDS
    box.stopped = False
    assert box.flow.tick() == "stopping"


def test_restarting_cc_keeps_the_block_the_backoff_and_the_notice(box):
    box.install_error = WinError(577)
    box.to_install()
    assert box.flow.take_blocked_notice() == "1.5.2"
    box.now += 3600
    flow = box.start()                       # 重开 CC：心跳还没带来新版提示之前就显示被拦
    assert flow.state() == ("blocked", "1.5.2")
    assert update_block.reported() == {"version": "1.5.2", "at": AT_ISO}
    assert flow.offer("1.5.2", "/r/1.5.2/CC-Setup.exe") is True
    assert flow.tick() == "waiting_retry"    # 不下载、不停手、不再启动安装包
    assert flow.take_blocked_notice() == ""
    assert box.calls.count("stop") == 1


def test_installing_the_new_version_by_hand_clears_everything(box):
    box.install_error = WinError(4551)
    box.to_install()
    box.current = "1.5.2"                    # 用「下载新版」手动装上了
    flow = box.start()
    assert flow.state() == ("none", "")
    assert update_block.reported() is None   # 下一拍心跳报 null，网页那一句消失
    assert not box.path.exists()


def test_a_newer_release_is_tried_at_once_even_after_an_older_one_was_blocked(box):
    box.install_error = WinError(4551)
    box.to_install("1.5.2")
    box.install_error = None
    assert box.flow.offer("1.5.3", "/r/1.5.3/CC-Setup.exe") is True
    assert box.flow.state() == ("found", "1.5.3")
    assert box.flow.tick() == "downloading"
    assert box.calls[-1] == ("download", "1.5.3")


# ── 别的没装上：如实算 failed，不说成被 Windows 拦下 ──────────────────────────────


def test_an_installer_that_exits_by_itself_is_failed_not_blocked(box):
    assert box.to_install() == "installing"
    box.installer.exit_code = 1
    assert box.flow.tick() == "failed"
    assert box.flow.state() == ("failed", "1.5.2")
    assert box.flow.take_blocked_notice() == ""
    assert update_block.reported() is None
    assert box.memory.record.state == "failed"


def test_a_launch_error_that_is_not_a_policy_refusal_is_failed(box):
    box.install_error = WinError(5)
    assert box.to_install() == "failed"
    assert update_block.reported() is None


def test_still_the_old_version_after_the_installer_closed_cc_counts_as_not_installed(box):
    # 安装包启动了、本进程被关掉（或超时前退出），重开后还是旧版本：这次没装上，按退避等，不是一开机就再装一遍
    assert box.to_install() == "installing"
    assert json.loads(box.path.read_text(encoding="utf-8"))["state"] == "launched"
    box.now += 120
    flow = box.start()
    assert box.memory.record.state == "failed" and box.memory.record.attempts == 1
    flow.offer("1.5.2", "/r/1.5.2/CC-Setup.exe")
    assert flow.tick() == "waiting_retry"
    assert flow.state() == ("failed", "1.5.2")
    box.now += INSTALL_RETRY_SECONDS
    assert flow.tick() == "downloading"


def test_the_installer_timeout_is_still_failed(box):
    box.installer = object()   # 看不了进程状态：只按时间判
    assert box.to_install() == "installing"
    box.now += INSTALL_TIMEOUT_SECONDS
    assert box.flow.tick() == "failed"


# ── 下载的那份包 ──────────────────────────────────────────────────────────────


def test_a_download_overtaken_by_a_newer_release_is_not_installed(box):
    box.flow.offer("1.5.2", "/a.exe")
    assert box.flow.tick() == "downloading"
    box.flow.offer("1.5.3", "/b.exe")         # 下载途中云端又发了新版
    box.flow.downloaded("C:/Temp/old.exe")    # 旧的那份下完了：不装
    assert box.flow.path is None
    assert box.flow.tick() == "downloading"
    assert box.calls[-1] == ("download", "1.5.3")


def test_an_installer_file_cleaned_up_while_waiting_is_downloaded_again(box):
    box.install_error = WinError(4551)
    box.to_install()
    box.files.clear()                         # 隔了一天，临时文件被系统清理了
    box.now += INSTALL_RETRY_SECONDS
    assert box.flow.tick() == "downloading"
    assert box.calls[-1] == ("download", "1.5.2")


def test_the_attempt_is_recorded_for_the_file_that_was_actually_started(box):
    box.flow.offer("1.5.2", "/a.exe")
    box.flow.tick()
    box.files.add("C:/a.exe")
    box.flow.downloaded("C:/a.exe")
    box.flow.tick()                           # 开始停手
    box.flow.offer("1.5.3", "/b.exe")         # 停手途中又发了新版：照旧装手上这份
    box.stopped = True
    box.install_error = WinError(4551)
    assert box.flow.tick() == "blocked"
    assert box.memory.record.version == "1.5.2"


# ── 本机记录 ──────────────────────────────────────────────────────────────────


def test_a_broken_or_foreign_record_is_ignored(tmp_path):
    path = tmp_path / "update_state.json"
    for text in ("{", "[]", json.dumps({"version": "x", "state": "blocked", "at": AT}),
                 json.dumps({"version": "1.5.2", "state": "odd", "at": AT}),
                 json.dumps({"version": "1.5.2", "state": "blocked", "at": "soon"})):
        path.write_text(text, encoding="utf-8")
        assert UpdateMemory(path, current="1.5.1").record is None, text
    assert update_block.reported() is None


def test_a_record_that_cannot_be_written_does_not_stop_the_flow(tmp_path):
    folder = tmp_path / "update_state.json"
    folder.mkdir()                            # 写不进去（这里是个文件夹）：本次运行内存里照样记着
    memory = UpdateMemory(folder, current="1.5.1")
    memory.attempt("1.5.2", AT)
    record = memory.finish("1.5.2", "blocked", AT, 4551)
    assert record.state == "blocked" and memory.blocked("1.5.2") is record
    assert update_block.reported() == {"version": "1.5.2", "at": AT_ISO}


def test_a_flow_without_a_tray_memory_never_changes_what_the_heartbeat_reports():
    flow = AutoUpdate(busy=lambda: False, stop_work=lambda: None, work_stopped=lambda: True,
                      resume_work=lambda: None, start_download=lambda v, u: None,
                      install=lambda path: (_ for _ in ()).throw(WinError(4551)), clock=lambda: AT)
    flow.offer("1.5.2", "/a.exe")
    flow.tick()
    flow.downloaded("C:/a.exe")
    flow.tick()
    assert flow.tick() == "blocked"
    assert update_block.reported() is None


# ── 心跳与文件夹同步带上它 ──────────────────────────────────────────────────────


class _Response:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": self._body}


def test_every_heartbeat_carries_update_blocked_and_only_an_old_caller_leaves_it_out(monkeypatch):
    from src.companion import queue_client

    posted = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, json=None, **kwargs):
            posted.append(json)
            return _Response({"ok": True})

        def close(self):
            pass

    monkeypatch.setattr(queue_client.httpx, "Client", _Client)
    qc = queue_client.QueueClient(queue_client.QueueClientConfig(backend_url="https://acme.pearnly.com",
                                                                 bearer_token="exp_x", companion_id="c"))
    qc.health(update_blocked=None)
    qc.health(update_blocked={"version": "1.5.2", "at": AT_ISO})
    qc.health()
    assert [("update_blocked" in body, body.get("update_blocked")) for body in posted] == [
        (True, None), (True, {"version": "1.5.2", "at": AT_ISO}), (False, None)]


def test_the_periodic_heartbeat_reports_what_the_tray_memory_holds(tmp_path):
    import types

    from src.companion import worker

    calls = []
    qc = types.SimpleNamespace(health=lambda **kwargs: calls.append(kwargs) or {})
    cfg = types.SimpleNamespace(heartbeat_interval_s=60, method="dbf", account_set="", account_dir="",
                                account_company="", account_set_row=0, revenue_acc="", ar_acc="", vat_output_acc="",
                                fallback_acc="", ap_acc="", vat_input_acc="", resolved_express_root=lambda: "",
                                backend_url="https://x")
    worker._run_periodic(cfg, qc, {"last_heartbeat": 0.0}, on_update_available=None)
    memory = UpdateMemory(tmp_path / "update_state.json", current="1.5.1")
    memory.attempt("1.5.2", AT)
    memory.finish("1.5.2", "blocked", AT, 4551)
    worker._run_periodic(cfg, qc, {"last_heartbeat": 0.0}, on_update_available=None)
    assert [call["update_blocked"] for call in calls] == [None, {"version": "1.5.2", "at": AT_ISO}]
