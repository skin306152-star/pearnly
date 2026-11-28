# -*- coding: utf-8 -*-
"""段 ③ a 云端 CC 首次自动安装的按键序列（auto_install）：只验发什么、按什么顺序、失败不重试；真发键要 Windows 真机。"""
from __future__ import annotations

from companion.cloud_link import auto_install

COMMAND = "powershell -NoProfile -ExecutionPolicy Bypass -File \\\\tsclient\\Z\\cc\\cloud_install.ps1 -Token ABCD-EFGH"  # scan: fake


class _Sender:
    def __init__(self, fail_on=None):
        self.done, self.fail_on = [], fail_on

    def __getattr__(self, name):
        def act(*args):
            if name == self.fail_on:
                raise OSError(5, "SendInput")
            self.done.append((name, *args))
        return act


def test_the_steps_focus_click_open_the_start_menu_start_powershell_then_type_the_command():
    sender, waited = _Sender(), []
    assert auto_install.run(COMMAND, sender, sleep=waited.append) is True
    assert sender.done == [("focus",), ("click",), ("combo", ("ALT", "HOME")), ("text", "powershell"), ("key", "ENTER"),
                           ("text", COMMAND), ("key", "ENTER")]
    # 每一步之间都等（开始菜单、PowerShell 起来要时间），命令紧跟回车
    assert waited == [auto_install.AFTER_CLICK, auto_install.AFTER_CLICK, auto_install.AFTER_START_MENU,
                      auto_install.AFTER_SEARCH, auto_install.AFTER_SHELL]


def test_a_send_failure_stops_and_is_not_retried():
    sender = _Sender(fail_on="combo")
    assert auto_install.run(COMMAND, sender, sleep=lambda s: None) is False
    assert sender.done == [("focus",), ("click",)]


def test_the_command_with_its_token_never_reaches_the_log(monkeypatch):
    lines = []
    monkeypatch.setattr(auto_install.log, "info", lambda msg, *a: lines.append(msg % a if a else msg))
    monkeypatch.setattr(auto_install.log, "warning", lambda msg, *a: lines.append(msg % a if a else msg))
    auto_install.run(COMMAND, _Sender(), sleep=lambda s: None)
    auto_install.run(COMMAND, _Sender(fail_on="text"), sleep=lambda s: None)
    assert lines and not any("ABCD-EFGH" in line for line in lines)
