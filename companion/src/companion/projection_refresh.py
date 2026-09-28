"""State and collector helpers for cloud-requested ERP projection refreshes."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from .logger import get_logger

log = get_logger(__name__)

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


def _sets_cache_file(profile_id: str):
    from .config import APPDATA_DIR

    name = "".join(ch for ch in str(profile_id or "default") if ch.isalnum() or ch in "-_") or "default"
    return APPDATA_DIR / f"account_sets_{name}.json"


def load_account_sets(state: dict, profile_id: str) -> None:
    """这次启动还没扫完账套时，先用上次扫到的清单（大数据根一次要扫几分钟）。

    2026-09-28 真机复测：刚启动那几分钟心跳没有账套清单，服务端认不出按账套刷新带回的主档，
    刷新一直停在「处理中」；这期间推的票也准备不出小助手凭据。扫完后照常换成新清单。"""
    if "reported_account_sets" in state or state.get("_sets_cache_tried"):
        return
    state["_sets_cache_tried"] = True
    try:
        rows = json.loads(_sets_cache_file(profile_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(rows, list) and rows:
        state["reported_account_sets"] = rows
        log.info("[heartbeat] 先用上次扫到的账套清单 %d 个（这次还在扫）", len(rows))


def save_account_sets(state: dict, profile_id: str) -> None:
    rows = state.get("reported_account_sets")
    if not rows or rows is state.get("_sets_cache_saved"):
        return
    try:
        _sets_cache_file(profile_id).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        state["_sets_cache_saved"] = rows
    except OSError:
        log.warning("[heartbeat] 账套清单存不进本机缓存", exc_info=True)


def apply_endpoint_snapshot(state: dict, master: dict, version: float) -> None:
    incoming_sets = master.get("account_sets") or []
    previous = state.get("reported_account_sets") or []
    if not incoming_sets and previous:
        # 重扫一个账套都没读到（登记表这会儿打不开，如 Express 正在打开账套）：保留上一份，
        # 不让网页和服务端看到「0 个账套」（2026-09-28 录制：心跳连续 2 分钟报 0 个，之后自行恢复）。
        log.warning("[heartbeat] 重扫没读到账套，沿用上一份 %d 个", len(previous))
        incoming_sets = previous
    state["reported_account_sets"] = (
        incoming_sets
        if "accounts" in master
        else merge_account_sets(previous, incoming_sets)
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


def _probe_account(account_set_key: str, roots: list[str]) -> Optional[dict]:
    """缓存的账套清单里没有时,照推送时的做法直接读一次各数据根的登录清单(worker._task_settings 同一口径)。"""
    from .account_probe import probe_account_sets

    for root in roots:
        if not str(root or "").strip():
            continue
        try:
            accounts = probe_account_sets(root)
        except Exception:
            log.warning("[refresh] 读不了数据根的登录清单: %s", root, exc_info=True)
            continue
        for account in accounts:
            if _norm_path(account.path) == _norm_path(account_set_key):
                return {"path": str(account.path), "writable": bool(account.writable)}
    return None


def collect_account(
    state: dict,
    account_sets: list[dict],
    account_set_key: str,
    now: float,
    roots: Optional[list[str]] = None,
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
    if target is None and roots:
        # 2026-09-28:推送能写的账套(SINCTEST),按账套刷新却报「账套不可用」—— 缓存清单里没有它。
        probed = _probe_account(account_set_key, list(roots))
        if probed is not None and probed["writable"]:
            target = probed
        log.info(
            "[refresh] 账套 %s 不在缓存清单(%d 个)里,直接读登录清单:%s",
            account_set_key,
            len(account_sets),
            "找到" if probed else "没有",
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
