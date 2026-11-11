# -*- coding: utf-8 -*-
"""按需请求 ``express_ar_open``（收款单 RE，载荷 20 的前一步；评估 ``docs/work/2026-11-11-collection-receipt-assessment.md``「读：新按需种类」）：
只读读出一本 Express 账套全部还没收清的发票，交回服务端做「未收清发票快照」给收款员勾选。Express 为准，ERP 不改它。

- 账套判据、复制到本机再读、不碰「等账套」、不拿写者锁、Express 开着照读：与 ``express_masters`` / ``express_read`` 同一套
  （``resolve`` / ``snapshot``），只复制 ARTRN；复制途中表变了整份重读，读满次数仍在变就照交，meta 报 ``snapshot_changed``。
- 参数：``{account_set, account_root}``（通用位置键），可选 ``customer``（只读这一个客户，给写前刷新用）；多一个键回 ``params_invalid``。
- 条件：ARTRN 未删、``RECTYP = '3'``（发票）、``DOCSTAT != 'C'``、``REMAMT > 0``（两位小数后）。
- 结果（gzip JSON）：``{"version", "account_set", "ar_mark", "read_at", "items": [{docnum, rectyp, docdat, customer, salesman, department,
  net, received, remaining}]}``；日期 ISO、金额两位小数的字符串。``ar_mark`` 同 ``express_marks`` 报的那一个（复制前取），服务端据此判快照
  新不新。超过 ``MAX_BYTES["express_ar_open"]`` 回 ``too_large``，不截断。
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import time
from pathlib import Path
from typing import Callable

from . import express_masters, express_read
from .logger import get_logger
from .request_contract import MAX_BYTES, RequestFailed, Result

log = get_logger(__name__)

KIND = "express_ar_open"
RESULT_VERSION = 1
TABLES = ("ARTRN",)
PARAM_KEYS = ("account_set", "account_root")
OPTIONAL_KEYS = ("customer",)
INVOICE_RECTYP = "3"
CANCELLED = "C"
CUSTOMER_MAX = 10


def _params(params: dict) -> tuple:
    if not isinstance(params, dict) or set(params) - set(PARAM_KEYS) - set(OPTIONAL_KEYS):
        raise RequestFailed("params_invalid")
    values = [params.get(key) for key in (*PARAM_KEYS, *OPTIONAL_KEYS)]
    if any(value is not None and not isinstance(value, str) for value in values):
        raise RequestFailed("params_invalid")
    customer = str(values[2] or "").strip()
    if len(customer.encode("cp874", "replace")) > CUSTOMER_MAX:
        raise RequestFailed("params_invalid")
    return values[0], values[1], customer


def _open_invoice(customer: str):
    get, money = express_read._get, express_masters.money
    code = express_masters._code

    def pick(rec, columns, _gone):
        if code(get(rec, columns, "RECTYP")) != INVOICE_RECTYP or code(get(rec, columns, "DOCSTAT")) == CANCELLED:
            return None
        who = code(get(rec, columns, "CUSCOD"))
        if customer and who != customer:
            return None
        remaining = money(get(rec, columns, "REMAMT"))
        if remaining.startswith("-") or remaining == "0.00":
            return None
        return {"docnum": code(get(rec, columns, "DOCNUM")), "rectyp": INVOICE_RECTYP,
                "docdat": express_read._iso(get(rec, columns, "DOCDAT")), "customer": who,
                "salesman": code(get(rec, columns, "SLMCOD")), "department": code(get(rec, columns, "DEPCOD")),
                "net": money(get(rec, columns, "NETAMT")), "received": money(get(rec, columns, "RCVAMT")),
                "remaining": remaining}

    return pick


def collect(folder: Path, customer: str = "") -> tuple:
    """从复制出来的 ARTRN 读出未收清的发票 → (行, 诊断)。没有 ARTRN 回 ``account_tables_missing``。"""
    if not (folder / "ARTRN.DBF").is_file():
        raise RequestFailed("account_tables_missing")
    diag = {"rows": {}, "bad_rows": {}}
    return express_read._rows(folder, "ARTRN", diag, _open_invoice(customer)), diag


def read_ar_open(cfg, params: dict, *, sleep: Callable[[float], None] = time.sleep) -> Result:
    """{account_set, account_root[, customer]} → 未收清发票（gzip JSON）。"""
    started = time.monotonic()
    account_set, account_root, customer = _params(params)
    folder = express_read.resolve(cfg, account_set, account_root)
    read, changed, stamp = None, False, ""
    for attempt in range(express_read.READS):
        stamp = express_masters.mark(folder, express_masters.AR_MARK_TABLES)  # 复制前取：复制途中又改了，下一次标记不同
        try:
            with express_read.snapshot(folder, tables=TABLES, sleep=sleep) as (copy, moved):
                read, changed = collect(copy, customer), moved
        except express_read._Unreadable as error:
            log.info("账套副本读不全，稍后重读: %s", error)
        else:
            if not changed:
                break
            log.info("复制账套途中单据表变了，稍后重读")
        if attempt < express_read.READS - 1:
            sleep(express_read.REREAD_WAIT)
    if read is None:
        raise RequestFailed("account_unreadable")
    items, diag = read
    data = {"version": RESULT_VERSION, "account_set": express_read._norm(folder)[:500], "ar_mark": stamp,
            "read_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": items}
    if customer:
        data["customer"] = customer
    content = gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if len(content) > MAX_BYTES[KIND]:
        raise RequestFailed("too_large")
    meta = {"format": "json.gz", "version": RESULT_VERSION, "counts": {"items": len(items)},
            "read_seconds": round(time.monotonic() - started, 2)}
    if diag["bad_rows"]:
        meta["bad_rows"] = diag["bad_rows"]
    if changed:
        meta["snapshot_changed"] = True
    return Result(content, meta)
