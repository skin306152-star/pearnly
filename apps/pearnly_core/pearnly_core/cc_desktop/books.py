"""CC 桌面版共用机器通道：每台电脑够得到哪些 Express 账套（多产品共用，ADR-025）。

心跳报整份账套名单时（与 Cowork 同一份上报，经共用的 `wire.report_rules` 净化）替换这台电脑的那几行：新的建、变了的改、
没再报的删；报「没变」就不动。行上记这台电脑本机的写法（路径、Express 根目录、登记行号、文件号）与账套资料（公司、税号、
分店、会计期间、能不能写、6 个默认科目）。产品按税号 + 分店 + 日期挑账套（`for_company`），领单时位置换成领取那台电脑的写法。
"""
import json

import frappe

from pearnly_core.cc_desktop import contracts, documents
from pearnly_core.express_rules.targets import account_match

BOOK = documents.BOOK
FIELDS = ("name", "device", "book_key", "identity", "path", "root", "row", "uid", "label", "company", "tax_id", "branch",
          "period_start", "period_end", "next_end", "writable", "mapping")


def replace(device, raw, moment):
    """这台电脑整份上报的账套名单 → 它的账套行（只动它自己的）。有变化（新建 / 改 / 删）时告诉产品（`products.books_changed`）。"""
    fresh = {row["book_key"]: row for row in contracts.books(raw)}
    stored = {row.book_key: row for row in frappe.get_all(BOOK, filters={"device": device}, fields=list(FIELDS))}
    changed = False
    for key, row in stored.items():
        if key not in fresh:
            documents.remove(BOOK, row.name)
            changed = True
    for key, row in fresh.items():
        current = stored.get(key)
        if current is not None and contracts.same_book(current, row):
            continue
        changed = True
        values = {**row, "writable": 1 if row["writable"] else 0, "seen_at": moment}
        if current is None:
            documents.new(BOOK, frappe.generate_hash(length=20), {"device": device, **values})
        else:
            doc = frappe.get_doc(BOOK, current.name)
            doc.update(values)
            documents.persist(doc)
    if changed:
        from pearnly_core.cc_desktop import products

        products.books_changed(device)


def drop_device(device):
    for name in frappe.get_all(BOOK, filters={"device": device}, pluck="name"):
        documents.remove(BOOK, name)


def writable(device) -> dict:
    """``{book_key: 行}``：这台电脑够得到、能写的账套。"""
    rows = frappe.get_all(BOOK, filters={"device": device, "writable": 1}, fields=list(FIELDS))
    return {row.book_key: row for row in rows}


def readable(device) -> dict:
    rows = frappe.get_all(BOOK, filters={"device": device}, fields=list(FIELDS))
    return {row.book_key: row for row in rows}


def reachers(keys, *, writable_only=True) -> dict:
    """``{book_key: [电脑]}``：够得到这几份账套（能写）的电脑。"""
    keys = sorted({key for key in keys if key})
    if not keys:
        return {}
    filters = {"book_key": ["in", keys]}
    if writable_only:
        filters["writable"] = 1
    found = {}
    for row in frappe.get_all(BOOK, filters=filters, fields=["book_key", "device"]):
        found.setdefault(row.book_key, []).append(row.device)
    return {key: sorted(set(devices)) for key, devices in found.items()}


def mapping(row) -> dict:
    try:
        value = json.loads(row.get("mapping") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _book(rows):
    """同一份账套（同一个键）几台电脑各报一行：合成一份给产品看，``devices`` 是够得到它的电脑，``writable`` 任一台能写。"""
    first = rows[0]
    return {"key": first.book_key, "label": first.label or first.company or first.path, "company": first.company,
            "tax_id": first.tax_id, "branch": first.branch,
            "period_start": str(first.period_start or "") or None, "period_end": str(first.period_end or "") or None,
            "next_end": str(first.next_end or "") or None,
            "writable": any(row.writable for row in rows), "devices": sorted({row.device for row in rows}),
            "writers": sorted({row.device for row in rows if row.writable}), "mapping": mapping(first),
            "path": first.path, "root": first.root}


def for_company(tax_id, branch=None, *, devices=None) -> list:
    """这家公司（税号 + 分店）的账套，一份一项；``devices`` 给了就只看这几台电脑报的。分店：只留这个分店的，公司只报过这一个
    分店号时没报分店号的（旧年度没税号的）也算（同 Cowork `companies.identity.for_branch`）。"""
    tax = "".join(ch for ch in str(tax_id or "") if ch.isdigit())
    if len(tax) != 13:
        return []
    filters = {"tax_id": tax}
    if devices is not None:
        if not devices:
            return []
        filters["device"] = ["in", sorted(devices)]
    grouped = {}
    for row in frappe.get_all(BOOK, filters=filters, fields=list(FIELDS), order_by="book_key, device"):
        grouped.setdefault(row.book_key, []).append(row)
    books = [_book(rows) for rows in grouped.values()]
    number = "".join(ch for ch in str(branch or "") if ch.isdigit())
    if number:
        number = number[-5:].zfill(5)
        known = {item["branch"] for item in books if item["branch"]}
        books = [item for item in books if item["branch"] == number or (not item["branch"] and known <= {number})]
    return books


def for_day(books, day) -> list:
    """按单据日期挑账套（与 Cowork 推送的期间硬闸、定时任务跟随公司当年账套同一条规则 `account_match.for_day`）：本年期间
    覆盖这一天的；没有就用期间含这一天的旧账套（次年段）。两份同期都回（调用方按「问一次、记住」处理）。"""
    return account_match.for_day(list(books), str(day or "")[:10],
                                 lambda item: (item.get("period_start"), item.get("period_end"), item.get("next_end")))


def spelling(device, key):
    """这台电脑对这份账套的本机写法（领单时把载荷里的位置换成它）。"""
    rows = frappe.get_all(BOOK, filters={"device": device, "book_key": key}, fields=list(FIELDS), limit=1)
    return rows[0] if rows else None
