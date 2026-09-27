"""Shared, non-blocking refresh for Express account sets and chart accounts.

目录同步(用户 2026-09-27):网页打开「新建录入」用服务端存着的目录,不再每次现扫。
小助手这边:
- 平时每分钟在后台看一眼各年度文件夹的登记表 `secure/SCCOMP.DBF`(和当前账套的科目表)有没有变,
  变了才重扫、上报;另有每天一次的兜底全扫(科目表以外的改动也能跟上);
- 网页点「重新读取」(强制刷新)时整套重扫,配对时选了「全部授权」的还会重新找一遍盘上的年度文件夹
  (新建了 70EXP 这种);
- 扫描一律带公司名(读 ISINFO 有按修改时间的缓存,见 account_probe),不再有只读登记表、
  把公司名丢掉的快扫。
所有读盘都在后台线程里,不占拉取任务那条线。
"""

from __future__ import annotations

import ntpath
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .logger import get_logger

log = get_logger(__name__)

_WATCH_INTERVAL_S = 60.0  # 多久看一次登记表有没有变
_FULL_RESCAN_S = 24 * 3600.0  # 兜底全扫


@dataclass
class _Refresh:
    running: bool = False
    payload: Optional[Dict[str, Any]] = None
    completed_at: float = 0.0
    error: str = ""
    checked_at: float = 0.0
    signature: tuple = ()


_lock = threading.Lock()
_refreshes: dict[str, _Refresh] = {}


def _norm(path: str) -> str:
    value = str(path or "").strip().replace("/", "\\").rstrip("\\")
    return ntpath.normcase(ntpath.normpath(value)) if value else ""


def _roots(express_root: str, authorized_roots: Optional[list[str]]) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()
    for value in [express_root, *(authorized_roots or [])]:
        root = str(value or "").strip().rstrip("\\/")
        identity = _norm(root)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        roots.append(root)
    return roots


def _key(
    express_root: str,
    account_dir: str,
    account_sets_only: bool,
    authorized_roots: Optional[list[str]],
) -> str:
    mode = "sets" if account_sets_only else "full"
    roots = ";".join(_norm(root) for root in _roots(express_root, authorized_roots))
    return f"{roots}|{_norm(account_dir)}|{mode}"


def _sccomp_stamp(root: str) -> Optional[tuple]:
    try:
        from .express_locate import sccomp_path

        stat = os.stat(sccomp_path(root))
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _signature(roots: list[str], account_dir: str, with_chart: bool) -> tuple:
    """各年度文件夹登记表(+ 当前账套科目表)的大小与修改时间:没变就不必重扫。"""
    stamps = [(_norm(root), _sccomp_stamp(root)) for root in roots]
    if with_chart and account_dir:
        try:
            stat = os.stat(os.path.join(account_dir, "GLACC.DBF"))
            stamps.append((_norm(account_dir), (stat.st_size, stat.st_mtime_ns)))
        except OSError:
            stamps.append((_norm(account_dir), None))
    return tuple(stamps)


def _discover(roots: list[str]) -> list[str]:
    """重新找盘上的年度文件夹(新建的 70EXP 等);同一份登记表经映射盘和共享路径各见一次的只算一次。"""
    from .express_locate import candidate_roots, sccomp_company_count

    known = {_norm(root) for root in roots}
    stamps = {_sccomp_stamp(root) for root in roots} - {None}
    found = []
    for candidate in candidate_roots():
        root = str(candidate or "").strip().rstrip("\\/")
        stamp = _sccomp_stamp(root) if root else None
        if not root or _norm(root) in known or stamp is None or stamp in stamps:
            continue
        if sccomp_company_count(root) <= 0:
            continue
        known.add(_norm(root))
        stamps.add(stamp)
        found.append(root)
    return found


def _scan(
    key: str,
    express_root: str,
    account_dir: str,
    account_sets_only: bool,
    authorized_roots: Optional[list[str]],
    discover: bool = False,
    previous: Optional[tuple] = None,
) -> None:
    """后台线程:`previous` 给了(平时的变化检查)就先比登记表,没变就不扫、不出新快照。
    整套重扫时先扫、先把结果交出去,再记登记表的修改时间 —— 够不到的共享盘读修改时间会卡好几秒,
    不能让它拖住这次结果。"""
    error = ""
    payload = None
    signature: Optional[tuple] = None
    roots: list[str] = []
    try:
        from .account_probe import account_sets_payload, chart_accounts_payload

        roots = _roots(express_root, authorized_roots)
        if discover:
            roots += _discover(roots)
        if previous is not None:
            signature = _signature(roots, account_dir, not account_sets_only)
            if signature == previous:
                with _lock:
                    refresh = _refreshes.get(key)
                    if refresh is not None:
                        refresh.running = False
                return
        seen_accounts: set[tuple[str, str]] = set()
        account_sets: list[dict] = []
        for root in roots:
            normalized_root = _norm(root)
            for account in account_sets_payload(root):
                identity = (normalized_root, _norm(account.get("path")))
                if not identity[1] or identity in seen_accounts:
                    continue
                seen_accounts.add(identity)
                account_sets.append(account)
        payload = {"account_sets": account_sets}
        if discover:
            payload["roots"] = roots
        if not account_sets_only:
            payload["accounts"] = (
                chart_accounts_payload(account_dir) if account_dir else []
            )
    except Exception:
        log.exception("Express master refresh failed: %s", account_dir)
        payload = None
        error = "ERR_MASTER_SCAN_FAILED"
    with _lock:
        refresh = _refreshes.get(key)
        if refresh is None:
            return
        refresh.payload = payload
        refresh.completed_at = time.time()
        refresh.error = error
        if payload is None:
            refresh.running = False
            return
    if signature is None:
        try:
            signature = _signature(roots, account_dir, not account_sets_only)
        except Exception:
            log.exception("Express registry stamp failed: %s", account_dir)
            signature = ()
    with _lock:
        refresh = _refreshes.get(key)
        if refresh is not None:
            refresh.signature = signature
            refresh.running = False


def request_master(
    express_root: str,
    account_dir: str,
    *,
    newer_than: float = 0.0,
    refresh_interval: float = _FULL_RESCAN_S,
    force: bool = False,
    account_sets_only: bool = False,
    authorized_roots: Optional[list[str]] = None,
    discover: bool = False,
) -> tuple[Optional[Dict[str, Any]], float]:
    """Return one newer snapshot while DBF scans run off the poll lane.

    平时:每 `_WATCH_INTERVAL_S` 秒在后台比一次登记表,变了才重扫;`refresh_interval` 是兜底全扫间隔。
    `force`(网页「重新读取」):立刻整套重扫;`discover` 同时重新找盘上的年度文件夹。
    `account_sets_only` 只表示不读科目表(科目表跟着所选账套另行刷新)。
    """
    if not _norm(express_root) and not _norm(account_dir):
        return None, 0.0
    identity = _key(express_root, account_dir, account_sets_only, authorized_roots)
    now = time.time()
    with _lock:
        refresh = _refreshes.setdefault(identity, _Refresh())
        if refresh.completed_at > newer_than:
            return refresh.payload, refresh.completed_at
        if refresh.running:
            return None, 0.0
        if not force and refresh.completed_at and now - refresh.checked_at < _WATCH_INTERVAL_S:
            return None, 0.0
        full = force or not refresh.completed_at or now - refresh.completed_at >= refresh_interval
        refresh.running = True
        refresh.error = ""
        refresh.checked_at = now
        thread = threading.Thread(
            target=_scan,
            args=(
                identity,
                express_root,
                account_dir,
                account_sets_only,
                authorized_roots,
                bool(force and discover),
                None if full else refresh.signature,
            ),
            name="pearnly-master-refresh",
            daemon=True,
        )
        thread.start()
    return None, 0.0


def refresh_error(
    express_root: str,
    account_dir: str,
    *,
    newer_than: float,
    account_sets_only: bool = False,
    authorized_roots: Optional[list[str]] = None,
) -> str:
    identity = _key(express_root, account_dir, account_sets_only, authorized_roots)
    with _lock:
        refresh = _refreshes.get(identity)
        if refresh and refresh.completed_at > newer_than and refresh.payload is None:
            return refresh.error or "ERR_MASTER_SCAN_FAILED"
    return ""


def _reset_for_tests() -> None:
    with _lock:
        _refreshes.clear()


__all__ = ["refresh_error", "request_master"]
