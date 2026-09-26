# -*- coding: utf-8 -*-
"""账套占用探测:错误码判定 · 缺路径/哨兵不拦 · 占用→True/空闲→False(打桩 Win32)。"""
import sys

import pytest

import src.companion.account_lock as al


def test_busy_from_winerror_sharing_and_lock():
    assert al.busy_from_winerror(32) is True  # ERROR_SHARING_VIOLATION
    assert al.busy_from_winerror(33) is True  # ERROR_LOCK_VIOLATION


def test_busy_from_winerror_other_codes_not_busy():
    for code in (0, 2, 3, 5):  # 成功/找不到文件/路径/拒绝访问 → 非「占用」
        assert al.busy_from_winerror(code) is False


def test_missing_dir_not_busy():
    assert al.is_account_busy("") is False
    assert al.is_account_busy(None) is False


def test_missing_sentinel_not_busy(tmp_path):
    # 目录在但哨兵表不存在 → 不拦(交正常写盘路径暴露真因)。
    assert al.is_account_busy(tmp_path) is False


def _fake_kernel32(create_ret, closed_counter=None):
    """伪 kernel32:CreateFileW/CloseHandle 用真函数(可接受 .argtypes/.restype 赋值)。"""

    class _K32:
        pass

    def CreateFileW(*_a):
        return create_ret

    def CloseHandle(*_a):
        if closed_counter is not None:
            closed_counter["n"] += 1
        return 1

    k = _K32()
    k.CreateFileW = CreateFileW
    k.CloseHandle = CloseHandle
    return k


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 路径 / 系统接口")
def test_busy_when_sharing_violation(tmp_path, monkeypatch):
    (tmp_path / al.SENTINEL_TABLE).write_bytes(b"x")
    monkeypatch.setattr(sys, "platform", "win32")
    invalid = al.ctypes.cast(-1, al.wintypes.HANDLE).value
    monkeypatch.setattr(al.ctypes, "WinDLL", lambda *_a, **_k: _fake_kernel32(invalid))
    monkeypatch.setattr(al.ctypes, "get_last_error", lambda: 32)
    assert al.is_account_busy(tmp_path) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 路径 / 系统接口")
def test_free_when_open_succeeds(tmp_path, monkeypatch):
    (tmp_path / al.SENTINEL_TABLE).write_bytes(b"x")
    monkeypatch.setattr(sys, "platform", "win32")
    closed = {"n": 0}
    monkeypatch.setattr(al.ctypes, "WinDLL", lambda *_a, **_k: _fake_kernel32(1234, closed))
    assert al.is_account_busy(tmp_path) is False
    assert closed["n"] == 1  # 探测后必须关闭句柄(只探测不持有)
