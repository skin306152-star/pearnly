"""MR.ERP 主档默认值：读这家公司自己的主档、判定这次写哪个值、按账套记住人选的那个。纯函数，不碰框架与 IO。

为什么不能写死（2026-10-08 核实 MR.ERP 源码 `/Users/skin/mrerp-source/src`）：导入表头的分店 / 部门 / 工作 /
员工 / 销售区 / 运输，明细的部门 / 工作 / 仓库，自建客户 / 供应商 / 商品时的类型、科目、分类、单位，全部必填，
且由 `imp*/component/importpc.php` 与这家公司自己的主档逐条比对；对不上整张单据被拒，MR.ERP 不会自动建档。
旧线上写死的是 TEST2019 的值（`กร ทดสอบ` = Korn 测试），换任何一家公司都推不进去。

规则（用户 2026-10-08）：同 `targets/mrerp_menus.py` —— 某项只有一个可用值就用它；多个时请人选一次，按
「连接 + 账套」记住；每次推送按这次读到的主档核对，选过的没了就停下请人重选，**不自动换**。能用这家公司在
MR.ERP 里自己的设置就不问：商品 8 个科目与现金科目取「参考科目」（`acreffile/acreffileform.php`，MR.ERP 网页
新建时的默认同源）；销项的员工 / 销售区 / 运输优先用客户档里的（MR.ERP 网页选客户后同样改用客户档）；
称谓按票面名称的前缀对称谓表。

主档从各模块的 `component/bshlistboxdata.php`（登录并选好账套后 POST，不带参数，回 JSON）读；
`[id, 代码, 名称]` 一行，员工是 `[id, 代码, 称谓, "名 姓"]`。表为空时这个键整个不出现。
所选存在连接的 `mrerp_defaults`（{账套键: {字段: {code, name, chosen_by, chosen_at}}}），读到的选项存在
`mrerp_master_options`（{账套键: {observed_at, fields: {字段: [{code, name}]}, error, error_at}}）。
"""
from __future__ import annotations

from copy import deepcopy
import html as _html
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# 字段 → 从哪个 JSON 键读（多个模块的接口给的是同一张表，先到先用）。
SOURCES: Dict[str, Tuple[str, ...]] = {
    "branch": ("txtothcombrh",),
    "dept": ("txtothdept_h", "txtdept[]"),
    "job": ("txtothsuborder_h", "txtsuborderd[]"),
    "store": ("txtstore[]",),
    "employee": ("txtemp",),
    "area": ("txtararea",),
    "delivery": ("txtardelivery",),
    "customer_type": ("txtrectype",),
    "supplier_type": ("txtsuptype",),
    "product_group": ("txtstkgrp",),
    "unit": ("txtunit_usell",),
    # 客户 / 供应商分店（不是选择项：自建主档时按票面分店号取，见 push/mrerp/defaults.py）
    "customer_branch": ("txtothcombrhcus",),
}
CHART = "txtacfile"  # 科目表：几个科目字段共用
ACCOUNT_FIELDS = ("ar_account", "ap_account", "bank_account", "expense_account")
FIELDS = (*(k for k in SOURCES if k != "customer_branch"), *ACCOUNT_FIELDS)

# 录入提交前按票面方向问哪些（只问会用到的）。
FIELDS_FOR_DIRECTION = {
    "sales": ("branch", "dept", "job", "store", "employee", "area", "delivery", "customer_type", "ar_account",
              "bank_account", "product_group", "unit"),
    "purchase": ("branch", "dept", "job", "store", "employee", "supplier_type", "ap_account", "product_group",
                 "unit", "expense_account"),
}

# MR.ERP 导入对长度的硬要求（importpc.php）：不合的值导入必拒，不给人选。
_EXACT = {"branch": 5, "customer_branch": 5, "store": 4, "customer_type": 4, "supplier_type": 4}
_CODE_EXACT = {"area": 6, "delivery": 6}  # 自建客户主档写代码时
_RANGE = {"dept": (2, 20), "job": (2, 20), "product_group": (1, 6), "unit": (1, 6)}

# 参考科目（tbl_acreffile 的 id）：MR.ERP 网页新建商品 / 收款时的默认科目同源（stkmas/allform.php:20-80、
# farev/allform.php:65-104）。
REFERENCE = {"cash": 2, "acc_inv": 4, "acc_rev": 15, "acc_ret": 17, "acc_dis": 18, "acc_cost": 21, "acc_pur": 22,
             "acc_purret": 23, "acc_purdis": 24}

CHOICE_REQUIRED = "ERR_MRERP_DEFAULT_CHOICE_REQUIRED"  # 有多个可用值，还没选
CHANGED = "ERR_MRERP_DEFAULT_CHANGED"                  # 选过的值在 MR.ERP 主档里已经没有了
UNREADABLE = "ERR_MRERP_MASTER_UNREADABLE"             # 读不到主档 / 这家公司这张主档是空的

_MAX_ACCOUNTS = 500
_MAX_OPTIONS = 3000
_MAX_TEXT = 120
_TAG = re.compile(r"<[^>]+>")


def _text(value: Any) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", str(value or ""))).split())[:_MAX_TEXT]


# ── 解析 MR.ERP 的回应 ─────────────────────────────────────────────────────


def parse_listbox(body: Any) -> Optional[Dict[str, Any]]:
    """`bshlistboxdata.php` 的回应 → JSON 对象；不是 JSON 对象（掉线时回 200 + 弹窗脚本 + No database selected）→ None。"""
    try:
        value = json.loads(body) if isinstance(body, (str, bytes)) else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _usable(field: str, code: str, name: str) -> bool:
    if field in _EXACT and len(code) != _EXACT[field]:
        return False
    low, high = _RANGE.get(field, (1, 100))
    if not low <= len(code) <= high:
        return False
    if field == "employee":
        # 导入按 CONCAT(名, 姓) = 去掉全部空格的输入比对：名或姓本身带空格的员工永远对不上（importpc.php
        # impartran :324-348），不给人选。接口给的是「名 姓」，多于一个空格就是这种。
        return bool(name) and name.count(" ") <= 1
    if field in ("area", "delivery"):
        return bool(name) and len(code) == _CODE_EXACT[field]
    return True


def masters(payloads: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """几个接口的 JSON → 每个字段的可用值 `[{code, name}]`（按接口给的顺序，代码去重）。科目表放在 `chart`。"""
    out: Dict[str, List[Dict[str, str]]] = {}
    for field, keys in (*SOURCES.items(), ("chart", (CHART,))):
        rows = next((p[k] for p in payloads if isinstance(p, dict) for k in keys if isinstance(p.get(k), list)),
                    None)
        if rows is None:
            continue
        seen, values = set(), []
        for row in rows:
            if not isinstance(row, list) or len(row) < 3:
                continue
            code = str(row[1] if row[1] is not None else "").strip()
            name = _text(row[3] if field == "employee" and len(row) > 3 else row[2])
            if code and code not in seen and (field == "chart" or _usable(field, code, name)):
                seen.add(code)
                values.append({"code": code, "name": name})
        out[field] = values[:_MAX_OPTIONS]
    return out


def customer_defaults(payload: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """赊销 / 现金接口的 `txtarmas` → {客户码: {employee, area, delivery}}（客户档里的员工姓名 / 区域名 / 运输名，
    MR.ERP 网页选了客户就改用这几个，`artran/component/javascript/allform.js:1624-1645`）。"""
    out: Dict[str, Dict[str, str]] = {}
    for row in (payload or {}).get("txtarmas") or []:
        if isinstance(row, list) and len(row) >= 13 and row[1]:
            out[str(row[1]).strip()] = {"employee": _text(row[8]), "area": _text(row[10]), "delivery": _text(row[12])}
    return out


def known_codes(payload: Optional[Dict[str, Any]], key: str = "inpdupdata") -> Dict[str, str]:
    """主档接口带的全部客户 / 供应商 / 商品 `[id, 代码, 名称]` → {代码: 名称}。"""
    return {str(row[1]).strip(): _text(row[2]) for row in (payload or {}).get(key) or []
            if isinstance(row, list) and len(row) >= 3 and row[1]}


_REF_INPUT = re.compile(r"<input\b[^>]*\bname=[\"']txtacfile\[(\d+)\][\"'][^>]*>", re.IGNORECASE)
_VALUE = re.compile(r"\bvalue=[\"']([^\"']*)[\"']", re.IGNORECASE)


def parse_reference(page: str) -> Optional[Dict[int, str]]:
    """`acreffile/acreffileform.php` → {参考科目 id: 科目代码}（没配的是空串）；页上一个都没有 → None。"""
    found = {}
    for match in _REF_INPUT.finditer(page or ""):
        value = _VALUE.search(match.group(0))
        found[int(match.group(1))] = _text(value.group(1)) if value else ""
    return found or None


_PREFIX_SELECT = re.compile(r"<select\b[^>]*\bname=[\"']selprefix[\"'][^>]*>(.*?)</select>", re.IGNORECASE | re.DOTALL)
_OPTION = re.compile(r"<option\b[^>]*>(.*?)</option>", re.IGNORECASE | re.DOTALL)


def parse_prefixes(page: str) -> Optional[List[str]]:
    """`armas/allform.php` 的称谓下拉 → 称谓名（页面顺序）；没有这个下拉 → None。"""
    match = _PREFIX_SELECT.search(page or "")
    if not match:
        return None
    return [name for name in (_text(option) for option in _OPTION.findall(match.group(1))) if name][:200]


def prefix_for(name: Any, prefixes: List[str]) -> str:
    """票面名称以哪个称谓开头就用哪个（取最长的）；都不是就用称谓表第一项（MR.ERP 网页的默认）。"""
    text = " ".join(str(name or "").split())
    hits = [p for p in prefixes if p and text.startswith(p)]
    return max(hits, key=len) if hits else (prefixes[0] if prefixes else "")


# ── 判定这次写哪个值 ───────────────────────────────────────────────────────


def options(observed: Dict[str, List[Dict[str, str]]], field: str) -> Optional[List[Dict[str, str]]]:
    """这个字段这次可选的值；没读到这张主档 → None。"""
    return observed.get("chart" if field in ACCOUNT_FIELDS else field)


def resolve(values: Optional[List[Dict[str, str]]], chosen: Any, *,
            single: bool = True) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """这次写哪个值 → (值, None) 或 (None, 原因码)。选过的以选过的为准，失效就停下，不换。
    科目字段 `single=False`：科目表不会只有一个科目，永远请人选。"""
    if values is None:
        return None, UNREADABLE
    want = str(chosen or "").strip()
    if want:
        hit = next((v for v in values if v["code"] == want), None)
        return (hit, None) if hit else (None, CHANGED)
    if single and len(values) == 1:
        return values[0], None
    return None, (CHOICE_REQUIRED if values else UNREADABLE)


def resolve_field(observed: Dict[str, Any], chosen: Dict[str, str], field: str):
    return resolve(options(observed, field), chosen.get(field), single=field not in ACCOUNT_FIELDS)


# ── 按账套记住所选 ─────────────────────────────────────────────────────────


def _load(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def choices(raw: Any) -> Dict[str, Dict[str, Dict[str, str]]]:
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for account, fields in _load(raw).items():
        if not isinstance(account, str) or not isinstance(fields, dict):
            continue
        kept = {k: v for k, v in fields.items()
                if k in FIELDS and isinstance(v, dict) and str(v.get("code") or "").strip()}
        if kept:
            out[account] = kept
    return out


def chosen_map(raw: Any, account: str) -> Dict[str, str]:
    """这份账套各字段选过的代码（推送时冻结进载荷）。"""
    return {field: str(value["code"]).strip() for field, value in (choices(raw).get(account) or {}).items()}


def _entry(raw: Any, account: str) -> Dict[str, Any]:
    value = _load(raw).get(account)
    return value if isinstance(value, dict) and isinstance(value.get("fields"), dict) else {"fields": {}}


def observed_for(raw: Any, account: str) -> Optional[Dict[str, List[Dict[str, str]]]]:
    entry = _entry(raw, account)
    return entry["fields"] if entry["fields"] else None


def observe(raw: Any, account: str, fields: Optional[Dict[str, List[Dict[str, str]]]], *, at: str,
            error: Optional[str] = None) -> Dict[str, Any]:
    """记下这份账套这次读到的主档（只覆盖这次读到的字段）或读取失败的原因，返回新的整份记录。"""
    value = deepcopy(_load(raw))
    entry = _entry(value, account)
    if error:
        entry.update(error=str(error)[:80], error_at=at)
    else:
        entry["fields"] = {**entry["fields"], **{k: v for k, v in (fields or {}).items()
                                                 if k in (*SOURCES, "chart") and isinstance(v, list)}}
        entry.update(observed_at=at, error=None, error_at=None)
    value[account] = entry
    if len(value) > _MAX_ACCOUNTS:
        oldest = sorted((v.get("observed_at") or "", k) for k, v in value.items() if k != account)
        for _, key in oldest[:len(value) - _MAX_ACCOUNTS]:
            value.pop(key, None)
    return value


def choose(raw: Any, account: str, field: str, code: Any, observed: Any, *, actor: str,
           at: str) -> Optional[Dict[str, Any]]:
    """记下这份账套这个字段选的值；不是最近读到的可用值（或记录已满）返回 None。"""
    want = str(code or "").strip()
    known = observed_for(observed, account)
    if not account or field not in FIELDS or not known:
        return None
    hit = next((v for v in options(known, field) or [] if v["code"] == want), None)
    if not hit:
        return None
    value = deepcopy(choices(raw))
    value.setdefault(account, {})[field] = {"code": want, "name": hit["name"], "chosen_by": actor, "chosen_at": at}
    return value if len(value) <= _MAX_ACCOUNTS else None


def view(raw: Any, observed: Any, account: str) -> Dict[str, Any]:
    """选择入口要的：每个字段的可用值、选过哪个、这次要不要请人选（读不到的算未知，不问）。"""
    known = observed_for(observed, account)
    picked = chosen_map(raw, account)
    fields = {}
    for field in FIELDS:
        values = options(known, field) if known else None
        hit, reason = resolve_field(known, picked, field) if values is not None else (None, None)
        fields[field] = {"options": [{"code": v["code"], "name": v["name"]} for v in values or []],
                         "chosen": hit["code"] if hit else None, "reason": reason,
                         "needed": reason in (CHOICE_REQUIRED, CHANGED) and bool(values)}
    entry = _entry(observed, account)
    return {"known": known is not None, "observed_at": entry.get("observed_at"), "error": entry.get("error"),
            "error_at": entry.get("error_at"), "fields": fields}


__all__ = ["ACCOUNT_FIELDS", "CHANGED", "CHOICE_REQUIRED", "FIELDS", "FIELDS_FOR_DIRECTION", "REFERENCE",
           "UNREADABLE", "choices", "choose", "chosen_map", "customer_defaults", "known_codes", "masters",
           "observe", "observed_for", "options", "parse_listbox", "parse_prefixes", "parse_reference", "prefix_for",
           "resolve", "resolve_field", "view"]
