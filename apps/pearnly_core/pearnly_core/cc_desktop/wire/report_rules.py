"""CC 桌面版上报内容的净化规则（多产品共用，纯函数；ADR-025）：科目表、存货科目组、主档目录、记账指纹、账套名单的行。

由 `pearnly_cowork.connections.reports` 原样搬来（2026-10-23），只把私有名改成公开名；Cowork 的心跳与共用机器通道
（ERP 用）对同一份上报得出同一份账套资料。不连库、不依赖框架。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from pearnly_core.express_rules.targets.identity import normalize_express_account_key


ACCOUNT_KEYS = ("code", "name", "type")

MAX_ACCOUNTS = 3000

def sanitize_accounts(raw: Any) -> List[Dict[str, Any]]:
    """净化 Agent 上报的科目表:只留 code/name/type、限长限量。code 必填(科目码=记账锚)。"""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:MAX_ACCOUNTS]:
        if not isinstance(item, dict):
            continue
        clean: Dict[str, Any] = {}
        for k in ACCOUNT_KEYS:
            v = item.get(k)
            if v is not None and str(v).strip() != "":
                clean[k] = str(v).strip()[:120]
        if clean.get("code"):
            out.append(clean)
    return out

STOCK_ACC_GROUP_KEYS = (
    "acccod",
    "name",
    "method",
    "stock_acc",
    "stock_acc_name",
    "cogs_acc",
    "cogs_acc_name",
)

ISACC_ALIASES = {
    "accdes": "name",
    "accnum01": "stock_acc",
    "accnum02": "cogs_acc",
    "code": "acccod",
    "desc": "name",
    "inv_acc": "stock_acc",
    "inv_acc_name": "stock_acc_name",
}

MAX_STOCK_ACC_GROUPS = 100

def sanitize_stock_acc_groups(raw: Any) -> List[Dict[str, Any]]:
    """净化上报的存货科目组候选:只留已知键、限长限量,fit 归一布尔。

    acccod 必填 —— 它是「选哪个组」的唯一锚,没有它这条候选选不了也存不住。
    """
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:MAX_STOCK_ACC_GROUPS]:
        if not isinstance(item, dict):
            continue
        src = {str(k).strip().lower(): v for k, v in item.items()}
        for alias, key in ISACC_ALIASES.items():
            if alias in src and key not in src:
                src[key] = src[alias]
        clean: Dict[str, Any] = {"fit": bool(src.get("fit"))}
        for k in STOCK_ACC_GROUP_KEYS:
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

PRODUCT_KEYS = ("code", "name", "kind")

CUSTOMER_KEYS = ("code", "name", "tax_id", "kind", "branch")

# 供应商档（APMAS）与部门（ISTAB 50）：复核时让会计选一次（2026-09-28 Express 压测，小助手 1.3 起上报）。
SUPPLIER_KEYS = ("code", "name", "tax_id", "branch")

DEPARTMENT_KEYS = ("code", "name")

FINGERPRINT_INT_KEYS = ("stock_master_count", "stcrd_lines", "stcrd_lines_moving_stock")

MAX_CATALOG = 20000

def sanitize_catalog(raw: Any, keys: tuple) -> List[Dict[str, Any]]:
    """净化上报目录:只留已知键、限长限量;code 或 name 至少一个非空才收。"""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw[:MAX_CATALOG]:
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

def sanitize_fingerprint(raw: Any) -> Dict[str, int]:
    """净化记账指纹:只留三个计数键、归一非负整数(喂 posting_profile 推库存模式)。"""
    if not isinstance(raw, dict):
        return {}
    fp: Dict[str, int] = {}
    for k in FINGERPRINT_INT_KEYS:
        v = raw.get(k)
        try:
            fp[k] = max(0, int(v))
        except (TypeError, ValueError):
            continue
    return fp

def value_of(item: Mapping[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""

def attributes(item: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
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

def options(
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
        source_id = value_of(item, source_keys)[:300]
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        rows.append(
            {
                "source_id": source_id,
                "label": (value_of(item, label_keys) or source_id)[:500],
                "attributes": attributes(item, attribute_keys),
            }
        )
    return rows

def account_sets(raw: Any) -> list[dict[str, Any]]:
    rows = options(
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
            # 与上面原始名单同一组新键（ADR-021）：认账套用，不进池目录（`connections.pool.catalog`）
            "uid",
            "share",
            # 录入标记（第 3 期 E3，1.9.1）：存进池登记、不进池目录，只给两份同期时的重问读
            "entry_mark",
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
