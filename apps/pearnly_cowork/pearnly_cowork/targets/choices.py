"""Source account-choice projection, without readiness queries or IO."""
from __future__ import annotations
import ntpath
from typing import Any
from .identity import normalize_express_account_key

_MASTER_REFRESH_MIN_VERSION = (1, 1, 76)

def supports_master_refresh(version: Any) -> bool:
    try:
        parsed = tuple(int(part) for part in str(version or "").strip().split("."))
    except ValueError:
        return False
    return parsed >= _MASTER_REFRESH_MIN_VERSION

def _choice_key(*parts: object) -> str:
    return ":".join(str(part or "").strip() for part in parts)

def _root_label(root: str) -> str:
    clean = str(root or "").strip().rstrip("\\/")
    return ntpath.basename(clean) or clean

def _path_identity(path: object) -> str:
    value = str(path or "").strip()
    return ntpath.normcase(ntpath.normpath(value)) if value else ""

def _int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _mrerp_account_choices(probe: dict[str, Any] | None) -> list[dict[str, Any]]:
    choices = []
    for company in (probe or {}).get("companies") or []:
        if not isinstance(company, dict):
            continue
        comidyear = str(company.get("comidyear") or "").strip()
        seldb = str(company.get("seldb") or "").strip()
        if not comidyear or not seldb:
            continue
        label = str(company.get("label") or company.get("name") or "").strip()
        choices.append(
            {
                "key": _choice_key(comidyear, seldb),
                "label": label or f"{comidyear}/{seldb}",
                "comidyear": comidyear,
                "seldb": seldb,
            }
        )
    return choices

def _mrerp_default_account_choice(endpoint: dict[str, Any]) -> list[dict[str, Any]]:
    config = endpoint.get("config") if isinstance(endpoint.get("config"), dict) else {}
    comidyear = str(config.get("comidyear") or "6").strip()
    seldb = str(config.get("seldb") or "1").strip()
    key = _choice_key(comidyear, seldb)
    label = str(config.get("account_set_label") or config.get("company_name") or key).strip()
    return [{"key": key, "label": label, "comidyear": comidyear, "seldb": seldb}]

def _projection_express_choices(probe: dict[str, Any] | None) -> list[dict[str, Any]]:
    choices = []
    for row in (probe or {}).get("account_sets") or []:
        if not isinstance(row, dict):
            continue
        attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        key = normalize_express_account_key(row.get("source_id"))
        if not key:
            continue
        account_dir = str(attributes.get("path") or key).strip()
        root = str(attributes.get("root") or "").strip() or ntpath.dirname(
            account_dir.rstrip("\\/")
        )
        choices.append(
            {
                "key": key,
                "label": str(row.get("label") or key).strip(),
                "root_key": root,
                "root_label": str(attributes.get("root_label") or "").strip() or _root_label(root),
                "account_set": key,
                "account_dir": account_dir,
                "account_company": str(attributes.get("company") or "").strip(),
                "account_set_row": _int_value(attributes.get("row")),
                "writable": bool(attributes.get("writable", True)),
                "mapping": (
                    attributes.get("mapping") if isinstance(attributes.get("mapping"), dict) else {}
                ),
            }
        )
    return choices

def _express_account_choices(
    endpoint: dict[str, Any], probe: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    projected = _projection_express_choices(probe)
    if projected:
        return projected
    config = endpoint.get("config") if isinstance(endpoint.get("config"), dict) else {}
    reported = config.get("reported_account_sets")
    rows = reported if isinstance(reported, list) else []
    choices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "").strip()
        identity = _path_identity(path)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        root = str(row.get("root") or "").strip() or ntpath.dirname(path.rstrip("\\/"))
        name = str(row.get("name") or row.get("company") or row.get("code") or "").strip()
        choices.append(
            {
                "key": path,
                "label": name or ntpath.basename(path.rstrip("\\/")) or path,
                "root_key": root,
                "root_label": str(row.get("root_label") or "").strip() or _root_label(root),
                "account_set": path,
                "account_dir": path,
                "account_company": str(row.get("company") or "").strip(),
                "account_set_row": _int_value(row.get("row")),
                "writable": bool(row.get("writable")),
                "mapping": row.get("mapping") if isinstance(row.get("mapping"), dict) else {},
            }
        )
    current = str(config.get("account_set") or config.get("account_dir") or "").strip()
    if current and _path_identity(current) not in seen:
        root = str(config.get("express_root") or "").strip() or ntpath.dirname(
            current.rstrip("\\/")
        )
        choices.append(
            {
                "key": current,
                "label": str(config.get("account_set_label") or "").strip()
                or ntpath.basename(current.rstrip("\\/"))
                or current,
                "root_key": root,
                "root_label": _root_label(root),
                "account_set": current,
                "account_dir": str(config.get("account_dir") or current).strip(),
                "account_company": str(config.get("account_company") or "").strip(),
                "account_set_row": _int_value(config.get("account_set_row")),
                "writable": True,
                "mapping": {},
            }
        )
    return choices

def _express_default_account_choice(endpoint: dict[str, Any]) -> list[dict[str, Any]]:
    config = endpoint.get("config") if isinstance(endpoint.get("config"), dict) else {}
    current = str(config.get("account_set") or config.get("account_dir") or "").strip()
    key = normalize_express_account_key(current)
    if not key:
        return []
    root = str(config.get("express_root") or "").strip() or ntpath.dirname(current.rstrip("\\/"))
    return [
        {
            "key": key,
            "label": str(config.get("account_set_label") or "").strip()
            or ntpath.basename(current.rstrip("\\/"))
            or current,
            "root_key": root,
            "root_label": _root_label(root),
            "account_set": key,
            "account_dir": str(config.get("account_dir") or current).strip(),
            "account_company": str(config.get("account_company") or "").strip(),
            "account_set_row": _int_value(config.get("account_set_row")),
            "writable": True,
            "mapping": {},
        }
    ]

def _legacy_account_choices(
    endpoint: dict[str, Any], adapter: str, probe: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if adapter == "mrerp":
        return _mrerp_account_choices(probe)
    if adapter == "express":
        return _express_account_choices(endpoint, probe)
    return []

def account_choices_for_endpoint(
    endpoint: dict[str, Any], probe: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Return the canonical selectable accounts for one ERP connection."""
    adapter = str(endpoint.get("adapter") or "").strip().lower()
    return _legacy_account_choices(endpoint, adapter, probe)
