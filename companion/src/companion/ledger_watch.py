# -*- coding: utf-8 -*-
"""账本变化即时同步：盯着服务端要的那几本 Express 账套，报「哪本、变了哪几张表」（评估与主会话审定
docs/work/2026-11-22-ledger-instant-sync.md；服务端 `pearnly_core.cc_desktop.machine` 的 ``watch_books`` / ``ledger_changes``）。

- **盯哪几本**：心跳回复的 ``watch_books``（``[{key, path, root}]``，已接公司的现用账套、本机路径）；回复没带 = 一本都不盯。
- **怎么盯**：每本一个后台线程，Win32 ``ReadDirectoryChangesW`` 阻塞等（ctypes 直调，同 `account_lock` 写法，不加依赖），
  空闲 CPU 为 0；目录句柄只读打开、读写删都共享，不锁 Express 的文件。11-22 测试机1 实测（办公室文件服务器 SMB1 + 无线）：写完关文件
  约 11 秒到、建删文件立即到、空闲 15 分钟后照样到；文件一直开着原地改（大小不变）要等关闭才到——那是文件系统本身的限制。
- **报什么**：只报约定的表（:data:`WATCH_TABLES`，与服务端 `pearnly_core.ledger_sync.contracts.EXPRESS_TABLES` 同一份，
  `tests/test_companion_contract.py` 守住）；同一本 :data:`SETTLE_S` 秒内的事件合成一次。我们自己写账也会触发：照报（服务端
  读一次、落库只写变化），不做特判。
- **断了怎么办**：缓冲溢出（一次改太多）、句柄出错（无线断、文件服务器晚上关机、电脑睡眠唤醒）都报 :data:`ALL`（说不清变了什么），
  出错的按 :data:`RETRY_S` 退避重挂，重挂成功再报一次 :data:`ALL`（断开期间的改动看不到）。10 分钟看标记、每小时整读仍由服务端兜底。
- **怎么交**：心跳循环（`worker._run_periodic`）有待报的就提前报（离上一拍至少 5 秒，同「扫完即报」），:meth:`Watcher.take` 取出、
  报成功 :meth:`Watcher.done` 清掉、失败放回下一拍再带。

非 Windows（开发机、CI 的 Linux）只有纯逻辑，不开句柄。
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Set

from .logger import get_logger

log = get_logger(__name__)

# 约定的表（服务端 `ledger_sync.contracts.EXPRESS_TABLES` 的键）：主档（express_masters.MARK_TABLES）、应收（AR_MARK_TABLES）、
# 单据与库存流水（写账读写的同几张）
WATCH_TABLES = frozenset(("ARMAS", "APMAS", "STMAS", "STLOC", "ISTAB", "ISINFO", "OESLM", "BKMAS",
                          "ARTRN", "STCRD", "STTRN", "APTRN"))
WATCH_EXTS = frozenset(("DBF", "CDX", "FPT"))
ALL = "*"
SETTLE_S = 3.0                # 同一本这么久没有新事件才报（连写几张表合成一次）
RETRY_S = (5.0, 30.0, 120.0)  # 句柄出错后重挂的间隔（之后一直按最后一档）
MAX_BOOKS = 50                # 一台电脑最多盯这么多本（服务端心跳也只收这么多）
BUFFER = 64 * 1024            # 网络盘上 ReadDirectoryChangesW 的缓冲上限


def table_of(name: str) -> str:
    """文件名 → 约定的表名（大写主名）；不是约定表的 DBF / CDX / FPT、锁文件、临时文件回空串。"""
    text = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].upper()
    stem, _, ext = text.partition(".")
    return stem if stem in WATCH_TABLES and ext in WATCH_EXTS else ""


def parse_notify(buffer: bytes, length: int) -> Optional[List[str]]:
    """``FILE_NOTIFY_INFORMATION`` 链 → 文件名列表；``length`` 为 0（缓冲溢出，系统丢了事件）回 None。"""
    if length <= 0:
        return None
    names, offset = [], 0
    while offset + 12 <= length:
        next_entry = int.from_bytes(buffer[offset:offset + 4], "little")
        name_len = int.from_bytes(buffer[offset + 8:offset + 12], "little")
        raw = buffer[offset + 12:offset + 12 + name_len]
        names.append(raw.decode("utf-16-le", "replace"))
        if not next_entry:
            break
        offset += next_entry
    return names


class _Win32Dir:
    """一本账套目录的通知句柄（只在 Windows 上）。"""

    _FILE_LIST_DIRECTORY = 0x0001
    _SHARE_ALL = 0x7
    _OPEN_EXISTING = 3
    _BACKUP_SEMANTICS = 0x02000000
    _FILTER = 0x1 | 0x8 | 0x10  # FILE_NAME | SIZE | LAST_WRITE

    def __init__(self, path: str):
        from ctypes import wintypes

        self._wintypes = wintypes
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k.CreateFileW.restype = wintypes.HANDLE
        self._k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self._k.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.BOOL,
                                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
                                                  wintypes.LPVOID]
        self._k.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self._k.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = self._k.CreateFileW(path, self._FILE_LIST_DIRECTORY, self._SHARE_ALL, None, self._OPEN_EXISTING,
                                     self._BACKUP_SEMANTICS, None)
        if handle in (None, ctypes.cast(-1, wintypes.HANDLE).value):
            raise OSError(ctypes.get_last_error(), "open directory for change notification failed")
        self._handle = handle
        self._buffer = ctypes.create_string_buffer(BUFFER)

    def read(self) -> Optional[List[str]]:
        """阻塞到有变化：回文件名列表；缓冲溢出回 None；句柄出错 / 被取消抛 OSError。"""
        got = self._wintypes.DWORD(0)
        ok = self._k.ReadDirectoryChangesW(self._handle, self._buffer, BUFFER, False, self._FILTER,
                                           ctypes.byref(got), None, None)
        if not ok:
            raise OSError(ctypes.get_last_error(), "ReadDirectoryChangesW failed")
        return parse_notify(self._buffer.raw, got.value)

    def cancel(self) -> None:
        if self._handle:
            self._k.CancelIoEx(self._handle, None)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            self._k.CloseHandle(handle)


class _Book(threading.Thread):
    """一本账套一个线程：开句柄 → 等变化 → 记进 :class:`Watcher`；出错退避重挂。"""

    def __init__(self, watcher: "Watcher", key: str, path: str):
        super().__init__(name=f"ledger-watch-{key[:8]}", daemon=True)
        self.watcher, self.key, self.path = watcher, key, path
        self.stopped = threading.Event()
        self._dir = None
        self._lock = threading.Lock()

    def stop(self) -> None:
        self.stopped.set()
        with self._lock:
            current = self._dir
        if current is not None:
            try:
                current.cancel()
            except Exception:  # noqa: BLE001 — 取消不了就等线程自己醒来看到 stopped
                pass

    def _wait(self, failures: int) -> None:
        self.stopped.wait(RETRY_S[min(failures, len(RETRY_S)) - 1])

    def run(self) -> None:
        failures = 0
        while not self.stopped.is_set():
            try:
                handle = self.watcher.opener(self.path)
            except Exception as error:  # noqa: BLE001
                failures += 1
                if failures == 1:
                    log.info("[ledger-watch] 打不开 %s：%s", self.path, error)
                self._wait(failures)
                continue
            with self._lock:
                self._dir = handle
            if failures:
                self.watcher.note(self.key, None)  # 断开期间的改动看不到：重挂成功报一次「全部」
                log.info("[ledger-watch] 重新盯上 %s", self.path)
            failures = 0
            try:
                while not self.stopped.is_set():
                    names = handle.read()
                    if self.stopped.is_set():
                        break
                    self.watcher.note(self.key, names)
            except Exception as error:  # noqa: BLE001 — 网络盘断开、睡眠唤醒、被取消
                if not self.stopped.is_set():
                    failures = 1
                    log.info("[ledger-watch] %s 断开：%s", self.path, error)
                    self.watcher.note(self.key, None)
            finally:
                with self._lock:
                    self._dir = None
                try:
                    handle.close()
                except Exception:  # noqa: BLE001
                    pass
            if failures:
                self._wait(failures)


def _default_opener(path: str):
    return _Win32Dir(path)


class Watcher:
    """一个成员（站点）的盯账套：名单由心跳回复定（:meth:`sync`），待报的变化由心跳取走（:meth:`take` / :meth:`done`）。"""

    def __init__(self, opener: Optional[Callable[[str], object]] = None, clock: Callable[[], float] = time.monotonic,
                 enabled: Optional[bool] = None):
        self.opener = opener or _default_opener
        self.clock = clock
        self.enabled = (sys.platform == "win32") if enabled is None else enabled
        self._lock = threading.Lock()
        self._books: Dict[str, _Book] = {}
        self._pending: Dict[str, Set[str]] = {}
        self._last: Dict[str, float] = {}
        self._taken: Dict[str, Set[str]] = {}

    # ── 名单 ──────────────────────────────────────────────
    def sync(self, books: Optional[Iterable[dict]]) -> None:
        """心跳回复的 ``watch_books``：新的开线程、没了的关掉、路径变了的重开。``None`` / 空 = 全部关掉。"""
        wanted: Dict[str, str] = {}
        for item in list(books or [])[:MAX_BOOKS]:
            if isinstance(item, dict) and item.get("key") and item.get("path"):
                wanted[str(item["key"])] = str(item["path"])
        if not self.enabled:
            return
        with self._lock:
            current = dict(self._books)
        for key, book in current.items():
            if wanted.get(key) != book.path:
                book.stop()
                log.info("[ledger-watch] 不再盯 %s", book.path)
                with self._lock:
                    self._books.pop(key, None)
        for key, path in wanted.items():
            with self._lock:
                if key in self._books:
                    continue
                book = _Book(self, key, path)
                self._books[key] = book
            book.start()
            log.info("[ledger-watch] 盯上 %s", path)

    def watching(self) -> Dict[str, str]:
        with self._lock:
            return {key: book.path for key, book in self._books.items()}

    def close(self) -> None:
        self.sync([])

    # ── 变化 ──────────────────────────────────────────────
    def note(self, key: str, names: Optional[Iterable[str]]) -> None:
        """线程报来的一批文件名；``None`` = 说不清变了什么（溢出 / 断开），记 :data:`ALL`。"""
        if names is None:
            tables = {ALL}
        else:
            tables = {table for table in (table_of(name) for name in names) if table}
        if not tables:
            return
        with self._lock:
            self._pending.setdefault(key, set()).update(tables)
            self._last[key] = self.clock()

    def ready(self) -> bool:
        """有一本静下来（:data:`SETTLE_S` 秒没有新事件）的待报变化。"""
        now = self.clock()
        with self._lock:
            return any(now - self._last.get(key, 0.0) >= SETTLE_S for key in self._pending)

    def take(self) -> List[dict]:
        """取出静下来的那几本 → 心跳 ``ledger_changes``；还在变的留到下一次。取出的在 :meth:`done` 之前算「在路上」。"""
        now = self.clock()
        out = []
        with self._lock:
            for key in list(self._pending):
                if now - self._last.get(key, 0.0) < SETTLE_S:
                    continue
                tables = self._pending.pop(key)
                self._taken.setdefault(key, set()).update(tables)
                out.append({"key": key, "tables": [ALL] if ALL in tables else sorted(tables)})
        return out

    def done(self, ok: bool) -> None:
        """心跳报成功：清掉在路上的；失败：放回待报，下一拍再带。"""
        with self._lock:
            taken, self._taken = self._taken, {}
            if ok:
                paths = {key: book.path for key, book in self._books.items()}
            else:
                for key, tables in taken.items():
                    self._pending.setdefault(key, set()).update(tables)
                    self._last.setdefault(key, 0.0)
        if ok and taken:
            # 低频（每次账本保存最多一行）：现场排错和实测计时都看这一行。
            log.info("[ledger-watch] 已报 %s", "；".join(
                f"{paths.get(key, key)}: {','.join(sorted(tables))}" for key, tables in sorted(taken.items())))


_WATCHERS: Dict[str, Watcher] = {}
_REGISTRY_LOCK = threading.Lock()


def for_member(profile_id: str) -> Watcher:
    """每个成员（站点配对）一个 :class:`Watcher`：心跳状态重置也不会留下没人管的线程。"""
    with _REGISTRY_LOCK:
        watcher = _WATCHERS.get(profile_id)
        if watcher is None:
            watcher = _WATCHERS[profile_id] = Watcher()
        return watcher


def close_all() -> None:
    with _REGISTRY_LOCK:
        watchers = list(_WATCHERS.values())
        _WATCHERS.clear()
    for watcher in watchers:
        watcher.close()


__all__ = ["ALL", "WATCH_TABLES", "Watcher", "close_all", "for_member", "parse_notify", "table_of"]
