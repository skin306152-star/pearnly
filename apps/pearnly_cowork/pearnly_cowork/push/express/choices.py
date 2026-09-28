"""Express 账套里「系统猜不准、要人选一次」的决定（2026-09-28 Express 压测，用户：「不确定的多选项，录入前 /
复核时让人先选定一次，系统记住后同类自动套用」）。纯计算，不连库；记在哪、谁能选见 `push/choices.py`。

每个决定按「账套 + 这一类」记一次：
- `party`：票面对方（进项供应商 / 销项客户）落 Express 哪个档。同税号有几个档（连锁分店、会计按名称区分）、
  或同税号只有一个档但分店号对不上（识别常把 00113 读成 00013）时要选；选「新建」就照票面新建，不再拦。
- `item`：票面品名（对方的叫法）落哪个 Express 存货。只管库存行；名字能对上 Express 存货名的不问。
- `account`：服务类进项记哪个费用科目（按供应商）。选了就按会计的记法写直接科目行，不建服务存货。
- `payment`：进项现购还是赊购（按供应商）。复核页改过付款方式就记下。
- `vat`：小票 / 简易税票上的税额抵不抵进项（按供应商）。
- `department`：部门码（整份账套一个）。

选过的值在 Express 主档里已经没有（被删 / 改了）→ 当作没选过、重新问，不静默换别的。
主档没报上来（旧版小助手）的那一类不拦，照旧由小助手按名匹配。
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional

NEW = "__new__"
KINDS = ("party", "item", "account", "payment", "vat", "department")
PAYMENT_VALUES = ("cash", "credit")
VAT_VALUES = ("claim", "expense")
# 票面名称里的法人前后缀：比名称时去掉（「บมจ.」上市公司缩写压测里就没去掉，匹配不上 Big C 的档）。
_LEGAL = ("บริษัทมหาชนจำกัด", "บริษัทจำกัด", "บริษัท", "บมจ.", "บมจ", "ห้างหุ้นส่วนจำกัด", "ห้างหุ้นส่วนสามัญ",
          "หจก.", "หจก", "หสน.", "จำกัด(มหาชน)", "จำกัดมหาชน", "(มหาชน)", "จำกัด", "co.,ltd.", "co.,ltd",
          "company limited", "co., ltd.", "co.ltd.", "limited", "ltd.", "public company limited", "pcl.")
_EXPENSE_DOC_TYPES = ("receipt", "simplified_tax_invoice")


def digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def norm_name(value) -> str:
    """名称归一（只用于比对 / 做键）：小写、去法人前后缀、去空白与标点；泰文声调符保留。"""
    text = unicodedata.normalize("NFC", str(value or "")).lower().replace("\xa0", " ")
    for word in sorted(_LEGAL, key=len, reverse=True):
        text = text.replace(word.lower(), " ")
    return re.sub(r"[\s.,()\-_/\\'\"&]+", "", text)


def branch5(value) -> str:
    """分店号归一成 5 位（空 / 0 = 总店 00000）。"""
    number = digits(value)
    return (number[-5:] if number else "0").zfill(5)


def party_subject(direction, tax, branch, name) -> str:
    tax = digits(tax)
    who = tax if len(tax) == 13 else f"name:{norm_name(name)}"
    return f"{direction}|{who}|{branch5(branch)}|{norm_name(name)}"


def supplier_subject(direction, tax, name) -> str:
    """按对方（不分店）记的决定：费用科目、付款方式、抵不抵进项。"""
    tax = digits(tax)
    return f"{direction}|{tax if len(tax) == 13 else 'name:' + norm_name(name)}"


def item_subject(direction, party_tax, party_name, item_name) -> str:
    return f"{supplier_subject(direction, party_tax, party_name)}|{norm_name(item_name)}"


def _money(value):
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def _rows(value) -> List[Dict[str, Any]]:
    return [row for row in value or [] if isinstance(row, Mapping) and str(row.get("code") or "").strip()]


def _codes(rows) -> set:
    return {str(row["code"]).strip() for row in rows}


def _party(fields, direction):
    side = "buyer" if direction == "sales" else "seller"
    return (fields.get(f"{side}_name") or "", fields.get(f"{side}_tax") or "", fields.get(f"{side}_branch") or "")


def _stock_lines(fields, posting_kind) -> List[Dict[str, Any]]:
    items = [item for item in fields.get("items") or [] if isinstance(item, Mapping)]
    kinds = [str(item.get("posting_kind") or "") for item in items]
    if any(kinds):
        return [item for item, kind in zip(items, kinds) if kind == "stock"]
    return items if posting_kind == "stock" else []


def _expense_receipt(fields) -> bool:
    return (str(fields.get("document_type") or "") in _EXPENSE_DOC_TYPES
            and (_money(fields.get("vat")) or Decimal(0)) > 0 and len(digits(fields.get("seller_tax"))) == 13)


def resolve(fields: Mapping[str, Any], *, direction: str, posting_kind: Optional[str],
            config: Mapping[str, Any], remembered: Mapping[str, Mapping[str, str]],
            account_resolved: bool = False) -> Dict[str, Any]:
    """这张票在这份账套里的决定 → `{"applied": {...}, "needed": [...]}`。

    `config`：推送准备用的账套配置（含小助手上报的 `reported_customers / reported_suppliers / reported_products /
    reported_accounts / reported_departments`）；`remembered`：`{kind: {subject: value}}`，这份账套里记过的；
    `account_resolved`：费用科目已由科目映射（分类 → 科目）定下，不用再问。
    `needed` 每条：`{kind, subject, label, options?, current?}`，页面据此弹一次框。
    """
    fields = fields if isinstance(fields, Mapping) else {}
    direction = "sales" if direction == "sales" else "purchase"
    name, tax, branch = _party(fields, direction)
    applied: Dict[str, Any] = {}
    needed: List[Dict[str, Any]] = []

    def chosen(kind, subject):
        return str((remembered.get(kind) or {}).get(subject) or "").strip()

    # ── 对方档 ──
    rows = _rows(config.get("reported_customers" if direction == "sales" else "reported_suppliers"))
    subject = party_subject(direction, tax, branch, name)
    value = chosen("party", subject)
    if value and (value == NEW or not rows or value in _codes(rows)):
        applied["party"] = value
    elif rows and len(digits(tax)) == 13:
        same_tax = [row for row in rows if digits(row.get("tax_id")) == digits(tax)]
        exact = [row for row in same_tax if branch5(row.get("branch")) == branch5(branch)]
        if len(same_tax) >= 2 or (same_tax and not exact) or value:
            needed.append({"kind": "party", "subject": subject, "label": name,
                           "options": [{"code": row["code"], "name": row.get("name") or "",
                                        "branch": branch5(row.get("branch"))} for row in same_tax[:50]],
                           "current": value or ""})

    # ── 部门（整份账套一个）──
    departments = _rows(config.get("reported_departments"))
    value = chosen("department", "*")
    if departments:
        if value and value in _codes(departments):
            applied["department"] = value
        else:
            needed.append({"kind": "department", "subject": "*", "label": "",
                           "options": [{"code": row["code"], "name": row.get("name") or ""} for row in departments],
                           "current": value or ""})

    party_key = supplier_subject(direction, tax, name)
    if direction == "purchase":
        # ── 付款方式：票面人工裁决 / 票面写明的优先；没有就按这家记过的；都没有要问 ──
        explicit = str(fields.get("posting_payment_manual") or "").strip()
        value = chosen("payment", party_key)
        if explicit in PAYMENT_VALUES:
            applied["remember_payment"] = explicit
        elif value in PAYMENT_VALUES:
            applied["payment"] = value
        elif not str(fields.get("payment_status") or "").strip():
            needed.append({"kind": "payment", "subject": party_key, "label": name,
                           "options": [{"code": code} for code in PAYMENT_VALUES], "current": ""})
        # ── 小票 / 简易税票的税额抵不抵进项 ──
        if _expense_receipt(fields):
            value = chosen("vat", party_key)
            if value in VAT_VALUES:
                applied["vat"] = value
            else:
                needed.append({"kind": "vat", "subject": party_key, "label": name,
                               "options": [{"code": code} for code in VAT_VALUES], "current": ""})

    # ── 存货对照（库存行）──
    products = [row for row in _rows(config.get("reported_products")) if str(row.get("kind") or "stock") == "stock"]
    lines = _stock_lines(fields, posting_kind)
    if lines and products:
        names = {norm_name(row.get("name")): row["code"] for row in products if norm_name(row.get("name"))}
        codes = _codes(products)
        items: Dict[str, str] = {}
        for line in lines:
            label = str(line.get("name") or "").strip()[:50]  # 载荷里的品名截到 50 字（`extract_line_items`）
            key = item_subject(direction, tax, name, label)
            value = chosen("item", key)
            if value and (value == NEW or value in codes):
                items[norm_name(label)] = value
            elif norm_name(label) in names and not value:
                continue  # Express 里有同名存货：小助手照名找得到，不用问
            else:
                needed.append({"kind": "item", "subject": key, "label": label, "current": value or ""})
        if items:
            applied["items"] = items

    # ── 服务类进项的费用科目 ──
    if direction == "purchase" and posting_kind != "stock" and not lines and not account_resolved:
        accounts = _rows(config.get("reported_accounts"))
        value = chosen("account", party_key)
        if value and (not accounts or value in _codes(accounts)):
            applied["account"] = value
        else:
            needed.append({"kind": "account", "subject": party_key, "label": name, "current": value or ""})
    return {"applied": applied, "needed": needed}


def apply_to_payload(payload: Dict[str, Any], applied: Mapping[str, Any]) -> Dict[str, Any]:
    """把选定的对方档 / 存货编码 / 部门码写进载荷（映射器出载荷之后、盖版本号之前）。

    带了存货编码或部门码 → 载荷版本 2：旧版小助手会静默忽略这两个键（按名新建存货、部门留空），
    所以用版本号让它诚实拒收（见 `preflight` 版本协商）。"""
    party = str(applied.get("party") or "")
    side = "customer" if payload.get("direction") == "sales" else "supplier"
    block = payload.get(side) if isinstance(payload.get(side), dict) else None
    if block is not None and party:
        if party == NEW:
            block.update(code="", confirmed_new=True, **{f"{side}_new": True})
        else:
            block.update(code=party, **{f"{side}_new": False})
    items = applied.get("items") or {}
    version = 1
    if items:
        for item in payload.get("items") or []:
            code = items.get(norm_name(item.get("name")))
            if code and code != NEW and str(item.get("item_mode") or "") in ("stock_item", "stock_sale"):
                item["code"] = code
                version = 2
    if applied.get("department"):
        payload["depcod"] = str(applied["department"])[:4]
        version = 2
    if version > int(payload.get("payload_version") or 1):
        payload["payload_version"] = version
    return payload


__all__ = ["KINDS", "NEW", "PAYMENT_VALUES", "VAT_VALUES", "apply_to_payload", "item_subject", "norm_name",
           "party_subject", "resolve", "supplier_subject"]
