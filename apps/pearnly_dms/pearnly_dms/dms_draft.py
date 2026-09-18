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
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from pearnly_dms import dms_cards

_GEO_LISTS = {
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


def build_draft(id_card: dict, geo: dict, prefixes: List[list], phone: str) -> Dict[str, str]:
    """OCR 身份证 + 地址级联 → 写库字段值。

    称谓**以 OCR 为准**，识别不出（或主档里没有这个称谓）才回退主档第一项 ——
    回退是为了不让建档卡在"称谓空"这种小问题上（DMS 空的必填 select 会报误导性错误）。
    """
    addr = id_card.get("address") or {}
    sel = geo.get("selected") or {}
    txt = geo.get("text") or {}
    pn = id_card.get("prefix_name") or ""
    prefix = next(
        (opt for opt in prefixes or [] if opt and len(opt) > 1 and opt[1] == pn),
        None,
    )
    if prefix is None:
        prefix = next((opt for opt in prefixes or [] if opt and opt[0] is not None), None)
    prefix_id = str(prefix[0]) if prefix else ""
    return {
        "prefix_id": prefix_id,
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
