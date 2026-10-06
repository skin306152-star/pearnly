"""Pure legacy Companion report rules; authentication and persistence stay outside.

Source ec2e5c957e6b. Raw caches and target projections deliberately retain
separate sanitizers. The caller supplies current report/config values and time.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping

from pearnly_cowork.targets.identity import normalize_express_account_key
from pearnly_cowork.targets.projection import MAX_ACCOUNT_SETS



# Fixed-source pure definitions: services/erp/express_push/agent_account_sets.py


_ACCOUNT_SET_KEYS = (
    "code",
    "name",
    "name_en",
    "company",
    "tax_id",
    "path",
    "root",
    "root_label",
    "row",
    "writable",
)

_ACCOUNT_MAPPING_KEYS = (
    "revenue_acc",
    "ar_acc",
    "vat_output_acc",
    "fallback_acc",
    "ap_acc",
    "vat_input_acc",
)

# 有意改动（用户 2026-10-07）：来源版本这里只留前 50 个。推送前核「小助手报过这份账套、可写」
# （`connections.preparation.reported_writable`、`push.express.account_rules`、选目标 / 冻结目标的数据目录配对）
# 读的就是这份，而网页可选的账套来自目录投影（上限 ``MAX_ACCOUNT_SETS``）：排在 50 名以外的账套能选、推不了。
# 两边用同一个上限。
_MAX_ACCOUNT_SETS = MAX_ACCOUNT_SETS

def _sanitize_account_sets(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:_MAX_ACCOUNT_SETS]:
        if not isinstance(item, dict):
            continue
        clean: dict[str, Any] = {}
        for key in _ACCOUNT_SET_KEYS:
            value = item.get(key)
            if key == "writable":
                clean[key] = bool(value)
            elif key == "row" and value not in (None, ""):
                try:
                    clean[key] = max(0, int(value))
                except (TypeError, ValueError):
                    pass
            elif value is not None:
                clean[key] = str(value)[:200]
        mapping = item.get("mapping")
        if isinstance(mapping, dict):
            clean_mapping = {
                key: str(mapping.get(key) or "").strip()[:40]
                for key in _ACCOUNT_MAPPING_KEYS
                if str(mapping.get(key) or "").strip()
            }
            if clean_mapping:
                clean["mapping"] = clean_mapping
        if clean.get("code") or clean.get("name"):
            out.append(clean)
    return out


# Fixed-source pure definitions: services/erp/express_push/agent_reporting.py


_ACCOUNT_KEYS = ("code", "name", "type")

_MAX_ACCOUNTS = 3000

def _sanitize_accounts(raw: Any) -> List[Dict[str, Any]]:
    """净化 Agent 上报的科目表:只留 code/name/type、限长限量。code 必填(科目码=记账锚)。"""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:_MAX_ACCOUNTS]:
        if not isinstance(item, dict):
            continue
        clean: Dict[str, Any] = {}
        for k in _ACCOUNT_KEYS:
            v = item.get(k)
            if v is not None and str(v).strip() != "":
                clean[k] = str(v).strip()[:120]
        if clean.get("code"):
            out.append(clean)
    return out

_STOCK_ACC_GROUP_KEYS = (
    "acccod",
    "name",
    "method",
    "stock_acc",
    "stock_acc_name",
    "cogs_acc",
    "cogs_acc_name",
)

_ISACC_ALIASES = {
    "accdes": "name",
    "accnum01": "stock_acc",
    "accnum02": "cogs_acc",
    "code": "acccod",
    "desc": "name",
    "inv_acc": "stock_acc",
    "inv_acc_name": "stock_acc_name",
}

_MAX_STOCK_ACC_GROUPS = 100

def _sanitize_stock_acc_groups(raw: Any) -> List[Dict[str, Any]]:
    """净化上报的存货科目组候选:只留已知键、限长限量,fit 归一布尔。

    acccod 必填 —— 它是「选哪个组」的唯一锚,没有它这条候选选不了也存不住。
    """
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:_MAX_STOCK_ACC_GROUPS]:
        if not isinstance(item, dict):
            continue
        src = {str(k).strip().lower(): v for k, v in item.items()}
        for alias, key in _ISACC_ALIASES.items():
            if alias in src and key not in src:
                src[key] = src[alias]
        clean: Dict[str, Any] = {"fit": bool(src.get("fit"))}
        for k in _STOCK_ACC_GROUP_KEYS:
            v = src.get(k)
            if v is not None and str(v).strip() != "":
                clean[k] = str(v).strip()[:120]
        try:  # 下拉上显示「N 个商品在用」,帮会计认出哪个才是这家真在用的组
            clean["used_by"] = max(0, int(src.get("used_by") or 0))
        except (TypeError, ValueError):
            clean["used_by"] = 0
        if clean.get("acccod"):
            out.append(clean)
    return out

_PRODUCT_KEYS = ("code", "name", "kind")

_CUSTOMER_KEYS = ("code", "name", "tax_id", "kind", "branch")

# 供应商档（APMAS）与部门（ISTAB 50）：复核时让会计选一次（2026-09-28 Express 压测，小助手 1.3 起上报）。
_SUPPLIER_KEYS = ("code", "name", "tax_id", "branch")

_DEPARTMENT_KEYS = ("code", "name")

_FINGERPRINT_INT_KEYS = ("stock_master_count", "stcrd_lines", "stcrd_lines_moving_stock")

_MAX_CATALOG = 20000

def _sanitize_catalog(raw: Any, keys: tuple) -> List[Dict[str, Any]]:
    """净化上报目录:只留已知键、限长限量;code 或 name 至少一个非空才收。"""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:_MAX_CATALOG]:
        if not isinstance(item, dict):
            continue
        clean: Dict[str, Any] = {}
        for k in keys:
            v = item.get(k)
            if v is not None and str(v).strip() != "":
                clean[k] = str(v).strip()[:200]
        if clean.get("code") or clean.get("name"):
            out.append(clean)
    return out

def _sanitize_fingerprint(raw: Any) -> Dict[str, int]:
    """净化记账指纹:只留三个计数键、归一非负整数(喂 posting_profile 推库存模式)。"""
    if not isinstance(raw, dict):
        return {}
    fp: Dict[str, int] = {}
    for k in _FINGERPRINT_INT_KEYS:
        v = raw.get(k)
        try:
            fp[k] = max(0, int(v))
        except (TypeError, ValueError):
            continue
    return fp

_SELECTED_ACCOUNT_KEYS = (
    "account_set",
    "account_dir",
    "account_company",
    "account_set_row",
    "express_root",
)

def _clean_selected(fields: Dict[str, Any]) -> Dict[str, Any]:
    """从上报 dict 取所选账套整组的非空字段(account_set_row 转 int)。"""
    sel: Dict[str, Any] = {}
    for k in _SELECTED_ACCOUNT_KEYS:
        v = (fields or {}).get(k)
        if k == "account_set_row":
            if v is not None and str(v).strip() != "":
                try:
                    sel[k] = int(v)
                except (TypeError, ValueError):
                    pass
        elif v is not None and str(v).strip() != "":
            sel[k] = str(v).strip()
    return sel

def selected_account_changed(current: Dict[str, Any], reported: Dict[str, Any]) -> bool:
    """上报的所选账套整组是否与已存 config 不同(防每拍无谓写库)。"""
    for k in _SELECTED_ACCOUNT_KEYS:
        if str((reported or {}).get(k) or "").strip() != str((current or {}).get(k) or "").strip():
            return True
    return False

_MAPPING_KEYS = (
    "revenue_acc",
    "ar_acc",
    "vat_output_acc",
    "fallback_acc",
    "ap_acc",
    "vat_input_acc",
)


# Fixed-source pure definitions: services/erp/express_target_projection.py


_COLLECTOR_KIND = "companion"

def _value(item: Mapping[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""

def _attributes(item: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        value = item.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (bool, int)):
            result[key] = value
        elif isinstance(value, Mapping):
            result[key] = {
                str(child_key).strip()[:100]: str(child_value).strip()[:500]
                for child_key, child_value in value.items()
                if str(child_key).strip() and child_value not in (None, "")
            }
        else:
            result[key] = str(value).strip()[:500]
    return result

def _options(
    raw: Any,
    *,
    source_keys: tuple[str, ...],
    label_keys: tuple[str, ...],
    attribute_keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        source_id = _value(item, source_keys)[:300]
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        rows.append(
            {
                "source_id": source_id,
                "label": (_value(item, label_keys) or source_id)[:500],
                "attributes": _attributes(item, attribute_keys),
            }
        )
    return rows

def _account_sets(raw: Any) -> list[dict[str, Any]]:
    rows = _options(
        raw,
        source_keys=("path", "code"),
        label_keys=("name", "company", "code", "path"),
        attribute_keys=(
            "code",
            "path",
            "company",
            "tax_id",
            "writable",
            "row",
            "root",
            "root_label",
            "mapping",
            # 账套会计期间与本公司分店号（小助手 1.2.2 起）：识别后按单据日期、本方分店挑账套。
            "period_start",
            "period_end",
            "next_end",
            "branch",
        ),
    )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        source_id = normalize_express_account_key(row["source_id"])
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        normalized.append({**row, "source_id": source_id})
    return normalized

def _selected_account_set(body: Mapping[str, Any]) -> str:
    return normalize_express_account_key(body.get("account_set") or body.get("account_dir"))

def _ensure_selected_choice(
    account_sets: list[dict[str, Any]], body: Mapping[str, Any], selected_key: str
) -> list[dict[str, Any]]:
    if not selected_key or any(row["source_id"] == selected_key for row in account_sets):
        return account_sets
    return [
        *account_sets,
        {
            "source_id": selected_key,
            "label": str(body.get("account_company") or selected_key).strip()[:500],
            "attributes": {
                "path": str(body.get("account_dir") or selected_key).strip()[:500],
                "writable": True,
            },
        },
    ]

def _masters(body: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    catalog = body.get("catalog") if isinstance(body.get("catalog"), Mapping) else {}
    return {
        "products": _options(
            catalog.get("products"),
            source_keys=("code",),
            label_keys=("name", "code"),
            attribute_keys=("kind",),
        ),
        "customers": _options(
            catalog.get("customers"),
            source_keys=("code",),
            label_keys=("name", "code"),
            attribute_keys=("tax_id", "kind", "branch"),
        ),
        "accounts": _options(
            body.get("accounts"),
            source_keys=("code",),
            label_keys=("name", "code"),
            attribute_keys=("type",),
        ),
    }

def _form_schema() -> dict[str, Any]:
    fields = [
        {
            "key": "product_id",
            "label": "Product",
            "type": "reference",
            "options_source": "products",
        },
        {
            "key": "customer_id",
            "label": "Customer",
            "type": "reference",
            "options_source": "customers",
        },
        {
            "key": "account_id",
            "label": "Account",
            "type": "reference",
            "options_source": "accounts",
        },
    ]
    fields.extend(
        {
            "key": f"{entity[:-1]}_id",
            "label": entity.title(),
            "type": "unsupported",
            "visible": False,
            "attributes": {"status": "collector_not_connected"},
        }
        for entity in ("suppliers", "units", "branches")
    )
    return {"fields": fields}

def _capabilities() -> dict[str, Any]:
    actions = [
        {"key": f"master.{entity}.read", "label": entity.title()}
        for entity in ("products", "customers", "accounts")
    ]
    actions.extend(
        {
            "key": f"master.{entity}.read",
            "label": entity.title(),
            "enabled": False,
            "block_reason": "erp.target_projection_collector_not_connected",
        }
        for entity in ("suppliers", "units", "branches")
    )
    return {"actions": actions}

def _collector(body: Mapping[str, Any]) -> dict[str, str]:
    version = str(body.get("companion_version") or "").strip()[:200]
    result = {"kind": _COLLECTOR_KIND}
    if version:
        result["adapter_version"] = version
    return result

def _endpoint_observation(
    body: Mapping[str, Any], account_sets: list[dict[str, Any]], observed_at: datetime
) -> dict[str, Any]:
    return {
        "adapter": "express",
        "observed_at": observed_at,
        "collector": _collector(body),
        "account_sets": account_sets,
        "masters": {},
        "form_schema": {"fields": []},
        "capabilities": {"actions": []},
    }

def _account_observation(
    body: Mapping[str, Any],
    account_sets: list[dict[str, Any]],
    selected_key: str,
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "adapter": "express",
        "account_set_key": selected_key,
        "observed_at": observed_at,
        "collector": _collector(body),
        "account_sets": account_sets,
        "masters": _masters(body),
        "form_schema": _form_schema(),
        "capabilities": _capabilities(),
    }


SELECTED_KEYS = frozenset(_SELECTED_ACCOUNT_KEYS)
MAPPING_KEYS = frozenset(_MAPPING_KEYS)
CACHE_KEYS = frozenset((
    "reported_account_sets", "account_sets_seen_at", "reported_accounts", "accounts_seen_at",
    "reported_products", "reported_customers", "reported_suppliers", "reported_departments",
    "catalog_seen_at", "catalog_fingerprint",
    "reported_stock_acc_groups", "stock_acc_groups_seen_at", "max_payload_version",
    "companion_version", "agent_device_name", "agent_last_seen_at",
))
PATCH_KEYS = SELECTED_KEYS | MAPPING_KEYS | CACHE_KEYS


class ReportError(ValueError):
    def __init__(self):
        self.code = "companion.report_invalid"
        super().__init__(self.code)


@dataclass(frozen=True)
class ReportPlan:
    offline: bool
    patch: dict
    observations: list[dict]
    projection_error: dict | None
    completion: dict | None
    response: dict
    lease_selection: str = ""
    # 这次心跳带的是哪份账套的主档（按单个账套刷新时）；空 = 小助手默认账套的例行上报。
    refreshed_account: str = ""


def plan_report(body: Mapping, previous: Mapping, observed_at: datetime) -> ReportPlan:
    """Plan a report using server-supplied previous values and an aware UTC time.

    This never grants identity, changes native Connection metadata, completes a
    refresh lease, or attests ERP preparation. Those are the caller's duties.
    """
    if (not isinstance(body, Mapping) or not isinstance(previous, Mapping)
            or not isinstance(observed_at, datetime) or observed_at.tzinfo is None
            or observed_at.utcoffset() != timedelta(0)):
        raise ReportError()
    if body.get("offline"):
        return ReportPlan(True, {"agent_last_seen_at": "1970-01-01 00:00:00+00"}, [], None, None,
                          {"connected": False})
    timestamp = observed_at.isoformat()
    patch = {"agent_last_seen_at": timestamp}
    device = str(body.get("device") or "")
    if device:
        patch["agent_device_name"] = device[:60]
    stored = accounts_stored = 0
    if body.get("account_sets") is not None:
        values = _sanitize_account_sets(body["account_sets"])
        patch.update(reported_account_sets=values, account_sets_seen_at=timestamp)
        stored = len(values)
    if body.get("accounts") is not None:
        values = _sanitize_accounts(body["accounts"])
        patch.update(reported_accounts=values, accounts_seen_at=timestamp)
        accounts_stored = len(values)
    mapping = body.get("mapping")
    if mapping is not None:
        # The old route raises AttributeError on nonempty non-object mapping.
        # Preserve rejection, with a stable transport-independent error code.
        if mapping and not isinstance(mapping, Mapping):
            raise ReportError()
        for key in _MAPPING_KEYS:
            value = str((mapping or {}).get(key) or "").strip()[:40]
            if value:
                patch[key] = value
    catalog = body.get("catalog")
    if isinstance(catalog, dict):
        patch.update(reported_products=_sanitize_catalog(catalog.get("products") or [], _PRODUCT_KEYS),
                     reported_customers=_sanitize_catalog(catalog.get("customers") or [], _CUSTOMER_KEYS),
                     catalog_seen_at=timestamp)
        # 旧版小助手不报这两份：不写空表冒充「这份账套没有供应商 / 部门」。
        for key, keys in (("suppliers", _SUPPLIER_KEYS), ("departments", _DEPARTMENT_KEYS)):
            if catalog.get(key) is not None:
                patch[f"reported_{key}"] = _sanitize_catalog(catalog.get(key) or [], keys)
        fingerprint = _sanitize_fingerprint(catalog.get("fingerprint"))
        if fingerprint:
            patch["catalog_fingerprint"] = fingerprint
        if catalog.get("isacc_candidates") is not None:
            patch.update(reported_stock_acc_groups=_sanitize_stock_acc_groups(catalog["isacc_candidates"]),
                         stock_acc_groups_seen_at=timestamp)
    selected = str(body.get("account_set") or "").strip() or None
    if selected and selected_account_changed(previous, body):
        patch.update(_clean_selected(body))
    if body.get("max_payload_version") is not None:
        try:
            version = int(body["max_payload_version"])
        except (TypeError, ValueError):
            version = 0
        if version >= 1:
            patch["max_payload_version"] = version
    if previous.get("companion_version") != body.get("companion_version"):
        version = str(body.get("companion_version") or "").strip()
        if version and len(version) <= 20:
            patch["companion_version"] = version

    account_sets = _account_sets(body.get("account_sets"))
    selected_key = _selected_account_set(body)
    reported_sets = body.get("account_sets") is not None
    if not reported_sets:
        account_sets = _ensure_selected_choice(account_sets, body, selected_key)
    scope = str(body.get("master_refresh_scope") or "").strip()
    request_id = body.get("master_refresh_request_id")
    error = str(body.get("master_refresh_error") or "").strip()
    refreshed = normalize_express_account_key(body.get("master_refresh_account_set"))
    projection_key = refreshed if scope == "account_set" and refreshed else selected_key
    observations, projection_error, completion = [], None, None
    if reported_sets and not account_sets:
        error = error or "ERR_ACCOUNT_SET_EMPTY"
        projection_error = {"scope": "account_set" if scope == "account_set" else "endpoint",
                            "account_set": projection_key if scope == "account_set" else None, "error_code": error}
        if request_id and scope in {"endpoint", "account_set"}:
            completion = {"request_id": request_id, "scope": scope, "account_set": projection_key, "error_code": error}
    elif account_sets or (isinstance(catalog, Mapping) and selected_key):
        if request_id and error and scope in {"endpoint", "account_set"}:
            completion = {"request_id": request_id, "scope": scope, "account_set": projection_key, "error_code": error}
        if account_sets:
            observations.append(_endpoint_observation(body, account_sets, observed_at))
            if request_id and scope == "endpoint" and not error:
                completion = {"request_id": request_id, "scope": scope, "account_set": selected_key, "error_code": None}
        if isinstance(catalog, Mapping) and projection_key and any(row["source_id"] == projection_key for row in account_sets):
            observations.append(_account_observation(body, account_sets, projection_key, observed_at))
            if request_id and scope == "account_set" and not error:
                completion = {"request_id": request_id, "scope": scope, "account_set": projection_key, "error_code": None}
    response = {"connected": True, "account_set": selected or previous.get("account_set"),
                "method": previous.get("method") or "rpa", "account_sets_received": stored,
                "accounts_received": accounts_stored}
    # The response may display the previous selection. Refresh leasing must use
    # only this heartbeat's choice; the legacy lease then canonicalizes its key.
    lease_selection = str(body.get("account_set") or body.get("account_dir") or "").strip()
    # 只有这次按单个账套读取成功、且确实带回了目录，这些主档才属于那份账套；读取失败时心跳里带的是
    # 小助手默认账套的缓存（例如重启后账套还没扫完），不能记到被刷新的账套名下。
    account_refreshed = scope == "account_set" and refreshed and not error and isinstance(catalog, Mapping)
    return ReportPlan(False, deepcopy(patch), deepcopy(observations), projection_error, completion,
                      response, lease_selection=lease_selection,
                      refreshed_account=refreshed if account_refreshed else "")
