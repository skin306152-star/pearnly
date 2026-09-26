"""Shared, non-blocking refresh for Express account sets and chart accounts."""

from __future__ import annotations

import ntpath
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

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


def _scan(
    key: str,
    express_root: str,
    account_dir: str,
    account_sets_only: bool,
    authorized_roots: Optional[list[str]],
) -> None:
    error = ""
    try:
        from .account_probe import account_sets_payload, chart_accounts_payload

        seen_roots: set[str] = set()
        seen_accounts: set[tuple[str, str]] = set()
        account_sets: list[dict] = []
        for root in _roots(express_root, authorized_roots):
            normalized_root = _norm(root)
            if not normalized_root or normalized_root in seen_roots:
                continue
            seen_roots.add(normalized_root)
            rows = (
                account_sets_payload(root, include_details=False)
                if account_sets_only
                else account_sets_payload(root)
            )
            for account in rows:
                identity = (normalized_root, _norm(account.get("path")))
                if not identity[1] or identity in seen_accounts:
                    continue
                seen_accounts.add(identity)
                account_sets.append(account)
        payload = {"account_sets": account_sets}
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
        refresh.running = False
        refresh.payload = payload
        refresh.completed_at = time.time()
        refresh.error = error


def request_master(
    express_root: str,
    account_dir: str,
    *,
    newer_than: float = 0.0,
    refresh_interval: float = 1800.0,
    force: bool = False,
    account_sets_only: bool = False,
    authorized_roots: Optional[list[str]] = None,
) -> tuple[Optional[Dict[str, Any]], float]:
    """Return one newer snapshot while expensive DBF scans run off the poll lane."""
    if not _norm(express_root) and not _norm(account_dir):
        return None, 0.0
    identity = _key(express_root, account_dir, account_sets_only, authorized_roots)
    now = time.time()
    with _lock:
        refresh = _refreshes.setdefault(identity, _Refresh())
        if refresh.completed_at > newer_than:
            return refresh.payload, refresh.completed_at
        if refresh.running or (
            not force and now - refresh.completed_at < refresh_interval
        ):
            return None, 0.0
        refresh.running = True
        refresh.error = ""
        thread = threading.Thread(
            target=_scan,
            args=(
                identity,
                express_root,
                account_dir,
                account_sets_only,
                authorized_roots,
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
