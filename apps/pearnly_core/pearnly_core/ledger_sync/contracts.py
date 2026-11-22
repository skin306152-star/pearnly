"""账本变化的统一形状（多产品共用，纯函数）。

一次变化 = ``{ledger, book_key, kinds, source}``：

- ``ledger``：:data:`EXPRESS` / :data:`MRERP` / :data:`EXPRESS_CLOUD`（与单据上的 ``ledger_target`` 同一套小写码）；
- ``book_key``：产品认得的账套键（Express 是共用机器通道的 ``book_key``，MR.ERP 是 ``comidyear:seldb``）；
- ``kinds``：变了的类别（:data:`KINDS`）；说不清变了什么（通知缓冲溢出、监视重挂）报 :data:`ALL`，产品按全部处理；
- ``source``：怎么发现的（:data:`SOURCES`），只记在同步说明里，便于看即时监视在不在工作。

Express 表名 → 类别见 :data:`EXPRESS_TABLES`：CC 桌面版读主档的 ``MARK_TABLES``、应收的 ``AR_MARK_TABLES`` 都在里面
（`tests/test_companion_contract.py` 守住）。交易表（销货、进货、库存流水）变了结存跟着变，归 :data:`STOCK`。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Set

EXPRESS, MRERP, EXPRESS_CLOUD = "express", "mrerp", "express_cloud"
LEDGERS = (EXPRESS, MRERP, EXPRESS_CLOUD)

CUSTOMER, SUPPLIER, PRODUCT, WAREHOUSE, STOCK, SETTINGS, RECEIVABLE = (
    "customer", "supplier", "product", "warehouse", "stock", "settings", "receivable")
KINDS = (CUSTOMER, SUPPLIER, PRODUCT, WAREHOUSE, STOCK, SETTINGS, RECEIVABLE)
ALL = "*"
# 落进 ERP 主档的类别（整套主档读一次覆盖它们）；应收单走收款快照
MASTER_KINDS = frozenset((CUSTOMER, SUPPLIER, PRODUCT, WAREHOUSE, STOCK, SETTINGS))

WATCH, MARK, PROBE, MANUAL, AFTER_WRITE = "watch", "mark", "probe", "manual", "after_write"
SOURCES = (WATCH, MARK, PROBE, MANUAL, AFTER_WRITE)

EXPRESS_TABLES: Dict[str, str] = {
    # 主档（CC 桌面版 express_masters.MARK_TABLES）
    "ARMAS": CUSTOMER, "APMAS": SUPPLIER, "STMAS": PRODUCT, "STLOC": STOCK,
    "ISTAB": SETTINGS, "ISINFO": SETTINGS, "OESLM": SETTINGS, "BKMAS": SETTINGS,
    # 应收（AR_MARK_TABLES）
    "ARTRN": RECEIVABLE,
    # 单据与库存流水（CC 桌面版写账读写的同几张表）：单据开出 / 作废，结存跟着变
    "STCRD": STOCK, "STTRN": STOCK, "APTRN": STOCK,
}

MAX_BOOKS = 50      # 一次心跳最多报这么多本
MAX_TABLES = 64     # 一本最多报这么多张表名


def table_kind(name: Any) -> str:
    """Express 文件名 / 表名 → 类别；不认识的（索引、临时文件、我们的锁）回空串。``ARMAS.DBF`` / ``armas.cdx`` 都认作 ARMAS。"""
    text = str(name or "").strip().replace("\\", "/").rsplit("/", 1)[-1].upper()
    stem = text.split(".", 1)[0]
    return EXPRESS_TABLES.get(stem, "")


def kinds_of(values: Iterable[Any]) -> Set[str]:
    """一组类别（或 :data:`ALL`）→ 认识的类别集合；含 :data:`ALL` 回全部。"""
    found = set()
    for value in values or ():
        text = str(value or "").strip().lower()
        if text == ALL:
            return set(KINDS)
        if text in KINDS:
            found.add(text)
    return found


def express_changes(raw: Any, known_keys: Iterable[str]) -> Dict[str, Set[str]]:
    """CC 桌面版心跳里的 ``ledger_changes``（``[{key, tables: [...]} ]``，``tables`` 可含 :data:`ALL`）→
    ``{book_key: 类别集合}``。只认这台电脑报过的账套键（``known_keys``），表名只认 :data:`EXPRESS_TABLES`；
    形状不对、陌生键、全是不认识的表的条目丢掉。"""
    known = {str(key) for key in known_keys or () if key}
    if not isinstance(raw, list) or not known:
        return {}
    out: Dict[str, Set[str]] = {}
    for item in raw[:MAX_BOOKS]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        tables = item.get("tables")
        if key not in known or not isinstance(tables, list):
            continue
        kinds = set()
        for table in tables[:MAX_TABLES]:
            if str(table or "").strip() == ALL:
                kinds = set(KINDS)
                break
            kind = table_kind(table)
            if kind:
                kinds.add(kind)
        if kinds:
            out.setdefault(key, set()).update(kinds)
    return out


def change(ledger: str, book_key: str, kinds: Iterable[Any], source: str) -> Dict[str, Any]:
    """一条规范的变化记录；账本 / 来源不认识抛 ``ValueError``（调用方写错，不静默）。"""
    if ledger not in LEDGERS:
        raise ValueError(f"unknown ledger {ledger!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    return {"ledger": ledger, "book_key": str(book_key or ""), "kinds": sorted(kinds_of(kinds)), "source": source}


__all__ = [
    "ALL", "AFTER_WRITE", "CUSTOMER", "EXPRESS", "EXPRESS_CLOUD", "EXPRESS_TABLES", "KINDS", "LEDGERS", "MANUAL", "MARK",
    "MASTER_KINDS", "MRERP", "PROBE", "PRODUCT", "RECEIVABLE", "SETTINGS", "SOURCES", "STOCK", "SUPPLIER", "WAREHOUSE",
    "WATCH", "change", "express_changes", "kinds_of", "table_kind",
]
