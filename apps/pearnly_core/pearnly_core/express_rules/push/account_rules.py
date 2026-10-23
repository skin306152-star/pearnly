"""Read-only account/stock policy over an explicit endpoint snapshot."""
from __future__ import annotations
import ntpath
from typing import Any, Dict, Optional
from pearnly_core.express_rules.targets.identity import reported_account_set_roots

def _path(value: object) -> str:
    raw = str(value or "").strip().replace("/", "\\").rstrip("\\")
    return ntpath.normcase(ntpath.normpath(raw)) if raw else ""


def wire_account_set(account_set: object, account_dir: object) -> str:
    """Keep the server-selected directory spelling for the legacy exact whitelist.

    Normalized identities still authorize and lock the account. This changes
    only its wire spelling, and never substitutes a different directory.
    """
    selected, directory = str(account_set or '').strip(), str(account_dir or '').strip()
    return directory if directory and _path(directory) == _path(selected) else selected


def authorized_account_sets(endpoint: Dict[str, Any]) -> list[str]:
    """Return the endpoint default plus writable account sets reported by its agent."""
    config = (endpoint or {}).get("config") or {}
    allowed = {_path(config.get("account_set") or config.get("account_dir"))}
    reported = config.get("reported_account_sets")
    for row in reported if isinstance(reported, list) else []:
        if not isinstance(row, dict) or row.get("writable") is not True:
            continue
        allowed.add(_path(row.get("path")))
    allowed.discard("")
    return sorted(allowed)


def account_set_allowed(
    account_set: str, endpoint: Dict[str, Any], account_root: object = None
) -> bool:
    """Allow only a writable account path reported by this exact Express connection."""
    selected = _path(account_set)
    if not selected or selected not in authorized_account_sets(endpoint):
        return False
    config = (endpoint or {}).get("config") or {}
    default = _path(config.get("account_set") or config.get("account_dir"))
    if selected == default:
        return True
    requested_root = _path(account_root)
    reported_root = reported_account_set_roots(config.get("reported_account_sets")).get(
        selected, ""
    )
    if reported_root:
        return bool(requested_root and requested_root == reported_root)
    # 老 Agent 只报数据目录、不报程序目录时才用数据目录的上一层兜底。这条 fallback 只属于本
    # 白名单(放行侧、缺配对时尽量不误拦);证据闸门不这样推断 —— 见 target_catalog_evidence。
    for row in config.get("reported_account_sets") or []:
        if not isinstance(row, dict) or row.get("writable") is not True:
            continue
        if _path(row.get("path")) != selected:
            continue
        legacy_root = _path(ntpath.dirname(str(row.get("path") or "")))
        return bool(requested_root and requested_root == legacy_root)
    return False


def stock_lane_enabled(config: Dict[str, Any]) -> bool:
    """Keep the deployed automatic profile gate permanently false.

    This only prevents a profile from automatically selecting the stock lane.
    An explicit batch posting_kind="stock" or per-item stock declaration still
    selects the purchase/sales stock payload, exactly as the deployed mappers do.
    """
    return False


def chart_codes(config: Dict[str, Any]) -> Optional[set]:
    """账套上报的可记账科目码集合(写前白名单数据源)。

    未上报(旧 Agent / 心跳还没带科目表)→ None:跳过校验,不阻塞;有上报才钉。
    入队闸(enqueue)与待补科目卡重推(erp_express_account_routes)共用一份口径。
    """
    reported = (config or {}).get("reported_accounts")
    if not isinstance(reported, list) or not reported:
        return None
    codes = {str((a or {}).get("code") or "").strip() for a in reported}
    codes.discard("")
    return codes or None
