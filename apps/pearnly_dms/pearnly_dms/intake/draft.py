# -*- coding: utf-8 -*-
"""DMS LINE 建档流的**取值模型** —— 纯函数，零副作用。

把"身份证识别结果 + DMS 查重结果（geo/prefixes）"整成三样东西：

| 产出 | 给谁用 |
|---|---|
| `draft` | 写库字段值（键名与网页确认页同一词表） |
| `summary` | 卡片复述用的可读五要素 |
| `display_diffs` | 差异卡逐条「字段名：旧 → 新」（地址 id 映成可读名，写入仍用原始值） |

老产品把这块从 `flow.py` 抽出来，理由是"单一职责 + 控 flow 行数"——时序/状态归 flow，
值映射集中在这里。照搬。

⚠️ 键名**绝不自造**：身份键取自 `_IDENTITY_MAP`、地址键取自 `_ADDR_MAP`，
与网页确认页同一个词表，否则同一条数据在两条入口会写成两个键。

⚠️ 称谓（คำนำหน้า）**不再回退主档首项**（线上 2026-09 改的）：
回退会把人写成错的称谓（นาย ↔ นางสาว 是两个人称），而这是写进客户档的字段。
现在的口径是：**只在唯一命中（或安全别名唯一命中）时才写；不唯一就留空**，
由编辑器里的人确认。`prefix_name` 仍然带上原始身份证称谓 —— 编辑器要用它。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Tuple

from pearnly_dms.line import cards as dms_cards

_GEO_LISTS = {
    # prefix_id 也在主档里查可读名（它和地址一样是"id → 名字"的映射）
    "prefix_id": "prefixes",
    "province_id": "provinces",
    "district_id": "districts",
    "subdistrict_id": "subdistricts",
    "zipcode_id": "zipcodes",
}

_MASTER_DRAFT_KEYS = (
    "people_id",
    "prefix_id",
    "prefix_name",
    "name",
    "birthday_be",
    "phone",
    "house_no",
    "building",
    "floor",
    "room",
    "village",
    "moo",
    "soi",
    "road",
    "province_id",
    "province_name",
    "district_id",
    "district_name",
    "subdistrict_id",
    "subdistrict_name",
    "zipcode_id",
)

# 称谓的安全别名组：同一组的写法可以互相认（泰文/英文/缩写）。
# 组**之间**绝不互认 —— นาย 和 นางสาว 永远不是同一个称谓。
_PREFIX_ALIASES = (
    frozenset({"mr", "นาย"}),
    frozenset({"mrs", "นาง"}),
    frozenset({"miss", "ms", "นางสาว", "นส"}),
    frozenset({"ดช", "เด็กชาย"}),
    frozenset({"ดญ", "เด็กหญิง"}),
)


def _norm_prefix(value: str) -> str:
    """称谓归一：NFKC + casefold + 去掉空白与常见分隔符。

    主档里的写法带点、带空格（"นาง สาว"）跟身份证上的不同，不归一就比不上。
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\s.．。、,，/\\|｜·・:：\-]+", "", text)


def _prefix_id(prefix_name: str, prefixes: List[list]) -> str:
    """身份证称谓 → 当前 DMS 主档的 prefix_id。**只接受唯一命中**。

    命中两个以上（主档里有重复写法）也不写 —— 宁可让人确认，也不赌一个。
    """
    target = _norm_prefix(prefix_name)
    if not target:
        return ""
    alias_group = next((group for group in _PREFIX_ALIASES if target in group), None)
    matches = set()
    for row in prefixes or ():
        if not row or len(row) < 2 or row[0] is None:
            continue
        label = _norm_prefix(row[1])
        if label == target or (alias_group is not None and label in alias_group):
            matches.add(str(row[0]))
    return next(iter(matches)) if len(matches) == 1 else ""


def build_draft(id_card: dict, geo: dict, prefixes: List[list], phone: str) -> Dict[str, str]:
    """OCR 身份证 + 实时地址/称谓 → 写库字段；未唯一匹配的称谓留给用户确认。"""
    addr = id_card.get("address") or {}
    sel = geo.get("selected") or {}
    txt = geo.get("text") or {}
    pn = id_card.get("prefix_name") or ""
    return {
        "prefix_id": _prefix_id(pn, prefixes),
        # 编辑器需要原始身份证称谓，在主档暂时未命中时也不能把它丢掉。
        "prefix_name": pn,
        "name": id_card.get("name") or "",
        "people_id": id_card.get("people_id") or "",
        "tax_id": id_card.get("people_id") or "",
        "birthday_be": id_card.get("birthday_be") or "",
        "phone": phone or "",
        "house_no": txt.get("house_no") or addr.get("house_no") or "",
        "moo": txt.get("moo") or addr.get("moo") or "",
        "soi": txt.get("soi") or addr.get("soi") or "",
        "road": txt.get("road") or addr.get("road") or "",
        "province_id": sel.get("province_id") or "",
        "district_id": sel.get("district_id") or "",
        "subdistrict_id": sel.get("subdistrict_id") or "",
        "zipcode_id": sel.get("zipcode_id") or "",
        # 预览卡读 draft.zipcode 显示；zipcode_id 是主档 ID，可读名要单独解析
        "zipcode": geo_name(geo, "zipcode_id", sel.get("zipcode_id") or ""),
    }


def geo_name(geo: dict, field: str, value: str) -> str:
    """把地址 id 映成可读名（在对应级联选项里查）；非地址字段/查不到 → 原值。"""
    key = _GEO_LISTS.get(field)
    if not key or not value:
        return value or ""
    for opt in geo.get(key) or []:
        if opt and str(opt[0]) == str(value):
            return opt[1]
    return value


def build_summary(draft: Dict[str, str], geo: dict) -> Dict[str, str]:
    """新建卡复述的五要素（证号/姓名/生日/地址/电话）；地址拼成一行可读串。"""
    bits: List[str] = []
    if draft.get("house_no"):
        bits.append(draft["house_no"])
    if draft.get("moo"):
        bits.append("หมู่ " + draft["moo"])
    if draft.get("soi"):
        bits.append("ซ." + draft["soi"])
    if draft.get("road"):
        bits.append("ถ." + draft["road"])
    for field, pfx in (("subdistrict_id", "ต."), ("district_id", "อ."), ("province_id", "จ.")):
        name = geo_name(geo, field, draft.get(field, ""))
        if name:
            bits.append(pfx + name)
    zc = geo_name(geo, "zipcode_id", draft.get("zipcode_id", ""))
    if zc:
        bits.append(zc)
    return {
        "people_id": draft.get("people_id", ""),
        "name": draft.get("name", ""),
        "birthday_be": draft.get("birthday_be", ""),
        "address": " ".join(bits),
        "zipcode": zc,
        "phone": draft.get("phone", ""),
    }


def master_snapshot(fields: dict) -> Tuple[Dict[str, str], Dict[str, str]]:
    """"保留 DMS 旧资料"时，生成后续订车逐问要用的主档快照。"""
    current = {key: str(fields.get(key) or "") for key in _MASTER_DRAFT_KEYS}
    current["zipcode"] = str(fields.get("zipcode_name") or fields.get("zipcode") or "")
    bits = [
        current.get("house_no"),
        f"หมู่ {current['moo']}" if current.get("moo") else "",
        f"ซ.{current['soi']}" if current.get("soi") else "",
        f"ถ.{current['road']}" if current.get("road") else "",
        f"ต.{current['subdistrict_name']}" if current.get("subdistrict_name") else "",
        f"อ.{current['district_name']}" if current.get("district_name") else "",
        f"จ.{current['province_name']}" if current.get("province_name") else "",
        current.get("zipcode"),
    ]
    summary = {
        "people_id": current.get("people_id", ""),
        "name": current.get("name", ""),
        "birthday_be": current.get("birthday_be", ""),
        "address": " ".join(part for part in bits if part),
        "zipcode": current.get("zipcode", ""),
        "phone": current.get("phone", ""),
    }
    return current, summary


def display_diffs(field_diffs: List[dict], geo: dict) -> List[Dict[str, str]]:
    """差异卡逐条：地址 id 值映成可读名（**写入仍用原始 diff.new**）。"""
    rows: List[Dict[str, str]] = []
    for d in field_diffs:
        f = d.get("field", "")
        rows.append(
            {
                "label": dms_cards.FIELD_LABELS_TH.get(f, f),
                "old": geo_name(geo, f, d.get("old", "")),
                "new": geo_name(geo, f, d.get("new", "")),
            }
        )
    return rows


def has_admin_creds(endpoint: Any = None) -> bool:
    """端点是否配了"有修改权限"的凭据组 —— 决定差异卡给不给 [อัปเดตข้อมูล] 按钮。

    ⚠️ 没配就**不给按钮**：让用户点了才发现"要管理员"是最差的体验，而且他会以为系统坏了。
    """
    import frappe

    if not endpoint:
        return False
    name = endpoint if isinstance(endpoint, str) else str(getattr(endpoint, "name", "") or "")
    if not name:
        return False
    try:
        return bool(frappe.db.get_value("Pearnly ERP Endpoint", name, "admin_username"))
    except Exception:
        return False
