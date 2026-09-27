"""State and collector helpers for cloud-requested ERP projection refreshes."""

from __future__ import annotations

import time
from typing import Any, Optional

# 网页「重新读取」:首次带公司名全扫近千份账套在共享盘上要一两分钟,放足余量。
_REFRESH_TIMEOUT_S = 300.0


def command(state: dict) -> tuple[str, str, str]:
    pending = state.get("master_refresh_request")
    if not isinstance(pending, dict):
        return "", "", ""
    return (
        str(pending.get("request_id") or "").strip(),
        str(pending.get("scope_kind") or "").strip(),
        str(pending.get("account_set_key") or "").strip(),
    )


def remember(state: dict, incoming: Any) -> bool:
    if not isinstance(incoming, dict) or not incoming.get("request_id"):
        return False
    current_id, _, _ = command(state)
    if str(incoming.get("request_id")) != current_id:
        state["master_refresh_received_at"] = time.time()
    state["master_refresh_request"] = incoming
    return True


def clear(state: dict) -> None:
    state.pop("master_refresh_request", None)
    state.pop("master_refresh_received_at", None)


def merge_account_sets(previous: list[dict], incoming: list[dict]) -> list[dict]:
    previous_by_path = {
        _norm_path(item.get("path")): item
        for item in previous
        if isinstance(item, dict) and _norm_path(item.get("path"))
    }
    merged = []
    for item in incoming:
        if not isinstance(item, dict):
            continue
        old = previous_by_path.get(_norm_path(item.get("path"))) or {}
        merged.append({**old, **item})
    return merged


def apply_endpoint_snapshot(state: dict, master: dict, version: float) -> None:
    incoming_sets = master.get("account_sets") or []
    state["reported_account_sets"] = (
        incoming_sets
        if "accounts" in master
        else merge_account_sets(state.get("reported_account_sets") or [], incoming_sets)
    )
    if "accounts" in master:
        state["reported_accounts"] = master.get("accounts") or []
    state["last_master"] = version


def heartbeat_ack(
    request_id: str,
    scope: str,
    account_set_key: str,
    *,
    endpoint_ready: bool,
    account_ready: bool,
    error: str,
) -> tuple[dict[str, Optional[str]], bool]:
    acknowledged = (
        account_ready or (scope == "endpoint" and endpoint_ready) or bool(error)
    )
    return (
        {
            "master_refresh_request_id": request_id if acknowledged else None,
            "master_refresh_scope": scope if acknowledged else None,
            "master_refresh_account_set": (
                account_set_key if scope == "account_set" else None
            ),
            "master_refresh_error": error or None,
        },
        acknowledged,
    )


def _timed_out(state: dict, now: float) -> bool:
    received_at = float(state.get("master_refresh_received_at", now))
    return now - received_at >= _REFRESH_TIMEOUT_S


def collect_endpoint(
    state: dict,
    express_root: str,
    account_dir: str,
    now: float,
    force: bool,
    authorized_roots: list[str] | None = None,
    discover: bool = False,
) -> tuple[Optional[dict], float, str]:
    from .master_refresh import refresh_error, request_master

    newer_than = float(state.get("last_master", 0.0) or 0.0)
    master, version = request_master(
        express_root,
        account_dir,
        newer_than=newer_than,
        force=force,
        account_sets_only=force,
        authorized_roots=authorized_roots,
        discover=discover,
    )
    if master is not None or not force:
        return master, version, ""
    error = refresh_error(
        express_root,
        account_dir,
        newer_than=newer_than,
        account_sets_only=True,
        authorized_roots=authorized_roots,
    )
    if error and version:
        state["last_master"] = version
    if not error and _timed_out(state, now):
        error = "ERR_MASTER_REFRESH_TIMEOUT"
    return None, version, error


def collect_account(
    state: dict,
    account_sets: list[dict],
    account_set_key: str,
    now: float,
) -> tuple[Optional[dict], Optional[list], float, str, bool]:
    from .catalog_refresh import request_target_catalog, target_refresh_error

    if _timed_out(state, now):
        return None, None, 0.0, "ERR_MASTER_REFRESH_TIMEOUT", False

    target = next(
        (
            item
            for item in account_sets
            if _norm_path(item.get("path")) == _norm_path(account_set_key)
            and item.get("writable") is not False
        ),
        None,
    )
    if target is None:
        return None, None, 0.0, "ERR_ACCOUNT_SET_UNAVAILABLE", False
    refreshed, version = request_target_catalog(
        str(target.get("path") or account_set_key),
        newer_than=float(state.get("master_refresh_received_at", now)),
    )
    if refreshed is None:
        error = target_refresh_error(
            account_set_key,
            newer_than=float(state.get("master_refresh_received_at", now)),
        )
        return None, None, version, error, False
    catalog = refreshed.get("catalog")
    accounts = refreshed.get("accounts")
    return catalog, accounts, version, "", isinstance(catalog, dict)


def _norm_path(value: object) -> str:
    return str(value or "").strip().rstrip("\\/").casefold()


__all__ = [
    "apply_endpoint_snapshot",
    "clear",
    "collect_account",
    "collect_endpoint",
    "command",
    "heartbeat_ack",
    "merge_account_sets",
    "remember",
]
