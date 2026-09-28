"""MR.ERP 导入菜单：读导入页的选项、判定这次推哪个菜单、按账套记住人选的那个。纯函数，不碰框架与 IO。

为什么不能写死（2026-09-28 核实 MR.ERP 源码）：导入页 `<select name=selmenu>` 的选项按「公司 + 用户权限」
现生成（`imp*/formupload.php:128`），菜单号是各公司自己的（`menu/menuinsert.php:89` 取全局最大 +1）；
`data-chklac=1` 表示**当前年度**已在 `tbl_linkaccount` 关联日记账（关联按年度存）。`importpc.php` 不校验
selmenu，没关联的菜单照样回 "1"，单据进去了却没有前缀、不入账、网页列表看不到 —— 旧线上写死 67 / 453 /
118 / 125 就是这样假成功的。

规则（用户 2026-09-28）：只用已关联日记账的选项；某类单据只有一个就自动用；有多个时让人选一次，
按「连接 + 账套（公司 + 年度）+ 单据类型」记住；每次推送都按这次读到的导入页核对，所选的不在了或
不再关联就停下请人重选，**不自动换**。所选的存在连接的 `mrerp_menus`（{账套键: {单据类型: {idmenu, label,
chosen_by, chosen_at}}}），读到的选项存在 `mrerp_menu_options`（{账套键: {observed_at, pages: {导入页: [...]}}}）。
"""
from __future__ import annotations

from copy import deepcopy
import html as _html
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# 单据类型 → 读哪个导入页（采购的货品 / 费用在同一个导入页里选）。
PAGE_OF = {"purchase": "impaptran", "purchase_expense": "impaptran",
           "sales_credit": "impartran", "sales_cash": "imparse"}
# 导入页 → formupload 的路由号（MR.ERP 固定的程序页号，不是各公司的菜单号）。
PAGES = {"impaptran": 363, "impartran": 370, "imparse": 371}
KINDS = tuple(PAGE_OF)
# 按票面方向，推送时可能用到的单据类型（录入时只问会用到的）。
KINDS_FOR_DIRECTION = {"purchase": ("purchase", "purchase_expense"), "sales": ("sales_credit", "sales_cash")}

CHOICE_REQUIRED = "ERR_MRERP_MENU_CHOICE_REQUIRED"   # 有多个已关联的菜单，还没选
CHANGED = "ERR_MRERP_MENU_CHANGED"                   # 选过的菜单不在了或不再关联日记账
NOT_LINKED = "ERR_MRERP_MENU_NOT_LINKED"             # 这类单据没有一个已关联日记账的菜单
UNREADABLE = "ERR_MRERP_MENU_UNREADABLE"             # 导入页读不出菜单（未完成设置 / 权限）

_SELECT = re.compile(r"<select\b[^>]*\bname=[\"']selmenu[\"'][^>]*>(.*?)</select>", re.IGNORECASE | re.DOTALL)
_OPTION = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.IGNORECASE | re.DOTALL)
_ATTR = re.compile(r"([\w-]+)\s*=\s*[\"']([^\"']*)[\"']")
_TAG = re.compile(r"<[^>]+>")
_MAX_ACCOUNTS = 500
_MAX_OPTIONS = 100


def _text(value: Any) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", str(value or ""))).split())


def parse_options(page: str) -> Optional[List[Dict[str, Any]]]:
    """导入页 → `[{idmenu, label, linked}]`（页面顺序）；页上没有菜单下拉（未完成设置、被踢回登录页）返回 None。"""
    match = _SELECT.search(page or "")
    if not match:
        return None
    options = []
    for attrs, label in _OPTION.findall(match.group(1)):
        values = {k.lower(): v for k, v in _ATTR.findall(attrs)}
        idmenu = values.get("value", "").strip()
        if idmenu.isdigit():
            options.append({"idmenu": idmenu, "label": _text(label)[:120],
                            "linked": values.get("data-chklac", "").strip() == "1"})
    return options[:_MAX_OPTIONS]


def linked(options: Any) -> List[Dict[str, Any]]:
    return [o for o in options or [] if isinstance(o, dict) and o.get("linked") and o.get("idmenu")]


def resolve(options: Optional[List[Dict[str, Any]]], chosen: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """这次推哪个菜单 → (选项, None) 或 (None, 原因码)。选过的以选过的为准，失效就停下，不换。"""
    if options is None:
        return None, UNREADABLE
    usable = linked(options)
    want = str(chosen or "").strip()
    if want:
        hit = next((o for o in usable if o["idmenu"] == want), None)
        return (hit, None) if hit else (None, CHANGED)
    if len(usable) == 1:
        return usable[0], None
    return None, (NOT_LINKED if not usable else CHOICE_REQUIRED)


# ── 按账套记住所选 ─────────────────────────────────────────────────────────


def _load(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def choices(raw: Any) -> Dict[str, Dict[str, Dict[str, str]]]:
    """连接上存的各账套所选菜单；坏值当没选过。"""
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for account, kinds in _load(raw).items():
        if not isinstance(account, str) or not isinstance(kinds, dict):
            continue
        kept = {k: v for k, v in kinds.items()
                if k in PAGE_OF and isinstance(v, dict) and str(v.get("idmenu") or "").strip().isdigit()}
        if kept:
            out[account] = kept
    return out


def chosen_map(raw: Any, account: str) -> Dict[str, str]:
    """这份账套各单据类型选过的菜单号（推送时冻结进载荷）。"""
    return {kind: str(value["idmenu"]).strip() for kind, value in (choices(raw).get(account) or {}).items()}


def _entry(raw: Any, account: str) -> Dict[str, Any]:
    value = _load(raw).get(account)
    return value if isinstance(value, dict) and isinstance(value.get("pages"), dict) else {"pages": {}}


def options_for(raw: Any, account: str) -> Optional[Dict[str, Any]]:
    """这份账套最近一次读到的导入页选项 `{observed_at, pages: {导入页: [...] | None}}`；没读到过是 None。"""
    entry = _entry(raw, account)
    return entry if entry["pages"] else None


def observe(raw: Any, account: str, pages: Optional[Dict[str, Optional[List[Dict[str, Any]]]]], *, at: str,
            error: Optional[str] = None) -> Dict[str, Any]:
    """记下这份账套这次读到的选项（只覆盖这次读到的导入页）或这次读取失败的原因，返回新的整份记录。"""
    value = deepcopy(_load(raw))
    entry = _entry(value, account)
    if error:
        entry.update(error=str(error)[:80], error_at=at)
    else:
        entry["pages"] = {**entry["pages"], **{page: options for page, options in (pages or {}).items()
                                                if page in PAGES}}
        entry.update(observed_at=at, error=None, error_at=None)
    value[account] = entry
    if len(value) > _MAX_ACCOUNTS:  # 满了丢掉最久没读过的
        oldest = sorted((v.get("observed_at") or "", k) for k, v in value.items() if k != account)
        for _, key in oldest[:len(value) - _MAX_ACCOUNTS]:
            value.pop(key, None)
    return value


def choose(raw: Any, account: str, kind: str, idmenu: Any, observed: Any, *, actor: str,
           at: str) -> Optional[Dict[str, Any]]:
    """记下这份账套这类单据选的菜单；不是最近读到的已关联选项（或记录已满）返回 None。"""
    code = str(idmenu or "").strip()
    known = options_for(observed, account)
    page = PAGE_OF.get(kind)
    if not account or not page or not known:
        return None
    hit = next((o for o in linked(known["pages"].get(page)) if o["idmenu"] == code), None)
    if not hit:
        return None
    value = deepcopy(choices(raw))
    value.setdefault(account, {})[kind] = {"idmenu": code, "label": hit["label"], "chosen_by": actor, "chosen_at": at}
    return value if len(value) <= _MAX_ACCOUNTS else None


def view(raw: Any, observed: Any, account: str) -> Dict[str, Any]:
    """选择入口要的：每类单据的已关联选项、选过哪个、这次要不要请人选（读不到的算未知，不问）。"""
    known = options_for(observed, account)
    picked = chosen_map(raw, account)
    kinds = {}
    for kind, page in PAGE_OF.items():
        options = (known or {}).get("pages", {}).get(page) if known else None
        usable = linked(options)
        hit, reason = resolve(options, picked.get(kind)) if options is not None else (None, None)
        kinds[kind] = {"page": page, "options": [{"idmenu": o["idmenu"], "label": o["label"]} for o in usable],
                       "chosen": hit["idmenu"] if hit else None,
                       "reason": reason,
                       # 多个已关联选项且没选过，或选过的已失效还有别的可选：请人选一次。
                       "needed": reason in (CHOICE_REQUIRED, CHANGED) and bool(usable)}
    entry = _entry(observed, account)
    return {"account_set_key": account, "known": known is not None, "observed_at": entry.get("observed_at"),
            "error": entry.get("error"), "error_at": entry.get("error_at"), "kinds": kinds}


__all__ = ["CHANGED", "CHOICE_REQUIRED", "KINDS", "KINDS_FOR_DIRECTION", "NOT_LINKED", "PAGES", "PAGE_OF",
           "UNREADABLE", "chosen_map", "choices", "choose", "linked", "observe", "options_for", "parse_options",
           "resolve", "view"]
