"""Non-blocking, shared Express catalog refreshes for Companion heartbeats."""

from __future__ import annotations

import ntpath
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .catalog_probe import catalog_payload
from .logger import get_logger

log = get_logger(__name__)


@dataclass
class _Refresh:
    running: bool = False
    payload: Optional[Dict[str, Any]] = None
    completed_at: float = 0.0
    error: str = ""


_lock = threading.Lock()
_refreshes: dict[str, _Refresh] = {}
_target_refreshes: dict[str, _Refresh] = {}


def _key(account_dir: str) -> str:
    return ntpath.normcase(
        ntpath.normpath(str(account_dir or "").strip().replace("/", "\\"))
    )


def _stamp(previous: float) -> float:
    """完成时间严格递增:Windows 的 time.time() 精度约 15ms,同一刻度内完成的新快照不能看起来"不更新"。"""
    return max(time.time(), previous + 1e-6)


def _scan(key: str, account_dir: str) -> None:
    try:
        payload = catalog_payload(account_dir)
    except Exception:
        log.exception("catalog refresh failed: %s", account_dir)
        payload = None
    with _lock:
        refresh = _refreshes.get(key)
        if refresh is None:
            return
        refresh.running = False
        refresh.payload = payload
        refresh.completed_at = _stamp(refresh.completed_at) if payload is not None else 0.0


def _scan_target(key: str, account_dir: str) -> None:
    error = ""
    try:
        from .account_probe import chart_accounts_payload

        # 完整目录(含记账指纹、可选存货科目组):推到非默认账套时服务端按这份做库存检查
        # (用户 2026-09-27;以前只读商品 / 客户,库存检查拿不到这两样)。
        payload = {
            "catalog": catalog_payload(account_dir),
            "accounts": chart_accounts_payload(account_dir),
        }
    except Exception:
        log.exception("target catalog refresh failed: %s", account_dir)
        payload = None
        error = "ERR_TARGET_CATALOG_SCAN_FAILED"
    with _lock:
        refresh = _target_refreshes.get(key)
        if refresh is None:
            return
        refresh.running = False
        refresh.payload = payload
        refresh.completed_at = _stamp(refresh.completed_at)
        refresh.error = error


def request_catalog(
    account_dir: str, *, newer_than: float = 0.0
) -> tuple[Optional[Dict[str, Any]], float]:
    """Return a newer snapshot or start one daemon refresh and return immediately."""
    normalized = _key(account_dir)
    if not normalized:
        return None, 0.0
    with _lock:
        refresh = _refreshes.setdefault(normalized, _Refresh())
        if refresh.payload is not None and refresh.completed_at > newer_than:
            return refresh.payload, refresh.completed_at
        if refresh.running:
            return None, 0.0
        refresh.running = True
        thread = threading.Thread(
            target=_scan,
            args=(normalized, account_dir),
            name="pearnly-catalog-refresh",
            daemon=True,
        )
        thread.start()
    return None, 0.0


def target_refresh_error(account_dir: str, *, newer_than: float) -> str:
    normalized = _key(account_dir)
    with _lock:
        refresh = _target_refreshes.get(normalized)
        if refresh and refresh.completed_at > newer_than and refresh.payload is None:
            return refresh.error or "ERR_TARGET_CATALOG_SCAN_FAILED"
    return ""


def request_target_catalog(
    account_dir: str, *, newer_than: float = 0.0
) -> tuple[Optional[Dict[str, Any]], float]:
    """Refresh the full catalog (products, customers, bookkeeping fingerprint, stock account groups)
    and chart accounts for one requested account set."""
    normalized = _key(account_dir)
    if not normalized:
        return None, 0.0
    with _lock:
        refresh = _target_refreshes.setdefault(normalized, _Refresh())
        if refresh.completed_at > newer_than:
            return refresh.payload, refresh.completed_at
        if refresh.running:
            return None, 0.0
        refresh.running = True
        refresh.error = ""
        thread = threading.Thread(
            target=_scan_target,
            args=(normalized, account_dir),
            name="pearnly-target-catalog-refresh",
            daemon=True,
        )
        thread.start()
    return None, 0.0


def _reset_for_tests() -> None:
    with _lock:
        _refreshes.clear()
        _target_refreshes.clear()


__all__ = ["request_catalog", "request_target_catalog", "target_refresh_error"]
