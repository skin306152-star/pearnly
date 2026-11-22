# -*- coding: utf-8 -*-
"""账本变化即时同步（11-22）：CC 桌面版盯账套、报「哪本、变了哪几张表」。系统调用那层用替身，Mac / Linux 上跑纯逻辑；
Win32 句柄那段在 Windows 检查机上由 test_win32_directory_notifies_a_change 实测。"""
import sys
import threading
import time

import pytest

import src.companion.ledger_watch as lw


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def entry(name, last=True):
    raw = name.encode("utf-16-le")
    size = 12 + len(raw)
    padded = size + (-size % 4)
    head = (0 if last else padded).to_bytes(4, "little") + (3).to_bytes(4, "little") + len(raw).to_bytes(4, "little")
    return (head + raw).ljust(padded, b"\0")


def test_notify_buffer_parses_names_and_overflow():
    buffer = entry("ARMAS.DBF", last=False) + entry("lis1122.tmp")
    assert lw.parse_notify(buffer, len(buffer)) == ["ARMAS.DBF", "lis1122.tmp"]
    assert lw.parse_notify(b"", 0) is None  # 缓冲溢出：系统丢了事件


def test_only_agreed_tables_count():
    assert lw.table_of("armas.cdx") == "ARMAS"
    assert lw.table_of(r"S:\70EXP\T9\STLOC.DBF") == "STLOC"
    assert lw.table_of("ISRUN.DBF") == ""     # 每写一张单都变，不进（同 express_masters.MARK_TABLES）
    assert lw.table_of("PEARNLY.LCK") == ""
    assert lw.table_of("ARMAS.BAK") == ""


def test_changes_settle_then_ride_one_heartbeat_and_return_if_it_fails():
    clock = Clock()
    watcher = lw.Watcher(clock=clock, enabled=False)
    watcher.note("k1", ["ARMAS.DBF", "ARMAS.CDX", "junk.tmp"])
    watcher.note("k2", ["STCRD.DBF"])
    assert not watcher.ready()               # 还在变：不报
    clock.now += lw.SETTLE_S
    watcher.note("k2", ["STLOC.DBF"])         # k2 又变了：这次只报静下来的 k1
    assert watcher.ready()
    assert watcher.take() == [{"key": "k1", "tables": ["ARMAS"]}]
    watcher.done(False)                       # 心跳没报上：放回去
    clock.now += lw.SETTLE_S
    assert sorted(watcher.take(), key=lambda item: item["key"]) == [
        {"key": "k1", "tables": ["ARMAS"]}, {"key": "k2", "tables": ["STCRD", "STLOC"]}]
    watcher.done(True)
    assert watcher.take() == [] and not watcher.ready()


def test_unknown_changes_are_reported_as_all():
    clock = Clock()
    watcher = lw.Watcher(clock=clock, enabled=False)
    watcher.note("k1", ["ARMAS.DBF"])
    watcher.note("k1", None)
    watcher.note("k3", ["noise.tmp"])          # 全是不认识的：不报
    clock.now += lw.SETTLE_S
    assert watcher.take() == [{"key": "k1", "tables": ["*"]}]


class FakeDir:
    """一个目录句柄替身：read 从队列取一批名字；None = 溢出；Exception 实例 = 断开。"""

    def __init__(self, script):
        self.script = script
        self.cancelled = threading.Event()
        self.closed = False

    def read(self):
        while True:
            if self.cancelled.is_set():
                raise OSError(995, "cancelled")
            if self.script:
                item = self.script.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            time.sleep(0.01)

    def cancel(self):
        self.cancelled.set()

    def close(self):
        self.closed = True


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_books_follow_the_heartbeat_list_and_threads_stop(monkeypatch):
    monkeypatch.setattr(lw, "SETTLE_S", 0.0)
    opened = {}

    def opener(path):
        opened[path] = FakeDir([["STMAS.DBF"]])
        return opened[path]

    watcher = lw.Watcher(opener=opener, enabled=True)
    watcher.sync([{"key": "k1", "path": r"S:\A"}, {"key": "k2", "path": r"S:\B"}, {"key": "", "path": "x"}, "bad"])
    assert watcher.watching() == {"k1": r"S:\A", "k2": r"S:\B"}
    seen = []
    assert wait_for(lambda: seen.extend(watcher.take()) or {item["key"] for item in seen} == {"k1", "k2"})
    assert all(item["tables"] == ["STMAS"] for item in seen)
    watcher.sync([{"key": "k2", "path": r"S:\B2"}])     # k1 没了、k2 换了路径
    assert watcher.watching() == {"k2": r"S:\B2"}
    assert wait_for(lambda: opened[r"S:\A"].closed)
    watcher.close()
    assert watcher.watching() == {}
    assert wait_for(lambda: opened[r"S:\B2"].closed)


def test_a_broken_handle_reports_all_and_is_opened_again(monkeypatch):
    monkeypatch.setattr(lw, "SETTLE_S", 0.0)
    monkeypatch.setattr(lw, "RETRY_S", (0.01, 0.01, 0.01))
    handles = []

    def opener(path):
        if len(handles) == 1:
            handles.append(None)
            raise OSError(53, "network path not found")   # 重挂第一次也失败（文件服务器关机）
        handle = FakeDir([OSError(64, "network name deleted")] if not handles else [["APMAS.DBF"]])
        handles.append(handle)
        return handle

    watcher = lw.Watcher(opener=opener, enabled=True)
    watcher.sync([{"key": "k1", "path": r"S:\A"}])
    seen = []
    assert wait_for(lambda: len(handles) == 3 and handles[2].script == [])   # 断了、重挂失败一次、第三次挂上并读到改动
    assert wait_for(lambda: seen.extend(watcher.take()) or bool(seen))
    assert seen == [{"key": "k1", "tables": ["*"]}]   # 断开期间看不到的改动：报「全部」（重挂后读到的 APMAS 并进去）
    watcher.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 change notification")
def test_win32_directory_notifies_a_change(tmp_path, monkeypatch):
    monkeypatch.setattr(lw, "SETTLE_S", 0.0)
    watcher = lw.Watcher(enabled=True)
    watcher.sync([{"key": "k1", "path": str(tmp_path)}])
    time.sleep(0.3)
    (tmp_path / "ARMAS.DBF").write_bytes(b"x" * 32)
    seen = []
    assert wait_for(lambda: seen.extend(watcher.take()) or any("ARMAS" in item["tables"] for item in seen), 10)
    watcher.close()
