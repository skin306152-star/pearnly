# -*- coding: utf-8 -*-
"""按需请求 ``account_history``（2026-10 科目建议 B）：只读读出一本 Express 账套近 12 个月的进项单据，汇总成
「供应商 → 常用科目」的次数，交回服务端（``readback.history``）给推送时要人选的费用科目排建议。

- 账套判据、复制到本机再读、不碰「等账套」、不拿写者锁、Express 开着照读：与 ``express_read`` 同一套（``resolve`` /
  ``snapshot``），只复制这里要读的几张表。
- 读什么：APTRN 里单据日期不早于 ``since``、没作废、没软删的单据（供应商代码 SUPCOD），对应凭证（GLJNL.VOUCHER 与
  单据号相同，Express 与 CC 桌面版写入都是这样）在 GLJNLIT 的借方行；凭证作废的整张不算。
- 不算的科目：账套设置 ISINFO 里的现金（ACCNUM00）、进项税（ACCNUM06）、应付（ACCNUM07），以及这家供应商档上挂的
  应付科目（APMAS.ACCNUM，16% 的供应商不用账套默认的应付）。现金、银行只出现在进项凭证的贷方，借方行本来就没有。
  是不是费用科目由服务端按科目表核（这里不判断，期末盘存制账套的「购货」照样交回）。
- 交回：每家供应商（代码、名称、税号、分店号）最多 ``ACCOUNTS_PER_SUPPLIER`` 个科目，每个科目记「几张单用过」与最后
  一次的单据日期；按用过的单据数取前 ``SUPPLIERS_MAX`` 家。只交次数，不交金额、不交单据明细。结果是 gzip 后的 JSON
  （``RESULT_VERSION``，字段见 ``collect``）。
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from . import express_read
from .logger import get_logger
from .request_contract import MAX_BYTES, RequestFailed, Result

log = get_logger(__name__)

RESULT_VERSION = 1
TABLES = ("ISINFO", "APMAS", "APTRN", "GLJNL", "GLJNLIT")
REQUIRED = ("APTRN", "GLJNLIT")
# ISINFO 里不算「常用科目」的默认科目：现金、进项税、应付（索引含义见 ``account_probe._ISINFO_DEFAULT_SLOTS``）。
EXCLUDED_SLOTS = ("ACCNUM00", "ACCNUM06", "ACCNUM07")
SUPPLIERS_MAX = 5000
ACCOUNTS_PER_SUPPLIER = 5
# 服务端要「近 12 个月」：since 不能晚于今天，也不能早于这么多天（读的量有上限）。
SINCE_DAYS_MAX = 400
CANCELLED = "C"


def _since(value) -> dt.date:
    try:
        since = dt.date.fromisoformat(str(value or ""))
    except ValueError:
        raise RequestFailed("params_invalid") from None
    today = dt.date.today()
    if since > today or (today - since).days > SINCE_DAYS_MAX:
        raise RequestFailed("params_invalid")
    return since


def _key(value) -> str:
    return express_read._text(value).upper()


def collect(folder: Path, since: dt.date) -> dict:
    """从复制出来的账套表汇总「供应商 → 常用科目」（口径见文件头）。"""
    started = time.monotonic()
    diag = {"rows": {}, "bad_rows": {}, "missing_tables": []}
    present = {name for name in TABLES if (folder / f"{name}.DBF").is_file()}
    if any(name not in present for name in REQUIRED):
        raise RequestFailed("account_tables_missing")
    diag["missing_tables"] = [name for name in TABLES if name not in present]
    get, text, day = express_read._get, express_read._text, express_read._day

    infos = express_read._rows(folder, "ISINFO", diag, lambda r, c, _g: [text(get(r, c, slot))
                                                                         for slot in EXCLUDED_SLOTS])
    excluded = {code for code in (infos[0] if infos else []) if code}

    suppliers = {}

    def supplier(rec, columns, _gone):
        code = text(get(rec, columns, "SUPCOD"))
        if code and code not in suppliers:
            suppliers[code] = {"name": text(get(rec, columns, "SUPNAM")), "tax_id": text(get(rec, columns, "TAXID")),
                               "branch": express_read._branch(rec, columns),
                               "account": text(get(rec, columns, "ACCNUM"))}
        return None

    express_read._rows(folder, "APMAS", diag, supplier)

    documents = {}

    def document(rec, columns, _gone):
        docnum, party = _key(get(rec, columns, "DOCNUM")), text(get(rec, columns, "SUPCOD"))
        when = day(get(rec, columns, "DOCDAT"))
        if docnum and party and when and when >= since and text(get(rec, columns, "DOCSTAT")).upper() != CANCELLED:
            documents[docnum] = (party, when)
        return None

    express_read._rows(folder, "APTRN", diag, document)

    def head(rec, columns, _gone):
        voucher = _key(get(rec, columns, "VOUCHER"))
        if voucher in documents and text(get(rec, columns, "DOCSTAT")).upper() == CANCELLED:
            documents.pop(voucher)  # 凭证作废：这张单不算
        return None

    express_read._rows(folder, "GLJNL", diag, head)

    used: dict = defaultdict(set)
    last: dict = {}

    def line(rec, columns, _gone):
        found = documents.get(_key(get(rec, columns, "VOUCHER")))
        if found is None or text(get(rec, columns, "TRNTYP")).upper() not in express_read.DEBIT:
            return None
        party, when = found
        account = text(get(rec, columns, "ACCNUM"))
        control = (suppliers.get(party) or {}).get("account")
        if not account or account in excluded or account == control:
            return None
        used[(party, account)].add(_key(get(rec, columns, "VOUCHER")))
        last[(party, account)] = max(last.get((party, account), when), when)
        return None

    express_read._rows(folder, "GLJNLIT", diag, line)

    by_party: dict = defaultdict(list)
    party_docs: dict = defaultdict(set)
    for (party, account), docs in used.items():
        by_party[party].append({"code": account, "docs": len(docs), "last": last[(party, account)].isoformat()})
        party_docs[party] |= docs
    ranked = sorted(by_party, key=lambda party: (-len(party_docs[party]), party))[:SUPPLIERS_MAX]
    out = []
    for party in sorted(ranked):
        master = suppliers.get(party) or {}
        # 用过的单据多的在前；一样多时最近用过的在前，再按科目号
        accounts = sorted(by_party[party], key=lambda item: item["code"])
        accounts.sort(key=lambda item: item["last"], reverse=True)
        accounts.sort(key=lambda item: item["docs"], reverse=True)
        out.append({"code": party, "name": master.get("name", ""), "tax_id": master.get("tax_id", ""),
                    "branch": master.get("branch", ""), "documents": len(party_docs[party]),
                    "accounts": accounts[:ACCOUNTS_PER_SUPPLIER]})
    diag["read_seconds"] = round(time.monotonic() - started, 2)
    return {"version": RESULT_VERSION, "since": since.isoformat(), "excluded": sorted(excluded),
            "suppliers": out, "documents": len({doc for docs in party_docs.values() for doc in docs}),
            "diagnostics": diag}


# ── 处理函数（request_worker.HANDLERS["account_history"]）────────────────────


def read_history(cfg, params: dict, *, sleep: Callable[[float], None] = time.sleep) -> Result:
    """{account_set, account_root, since: "YYYY-MM-DD"} → 汇总好的「供应商 → 常用科目」。"""
    since = _since(params.get("since"))
    folder = express_read.resolve(cfg, params.get("account_set"), params.get("account_root"))
    data = None
    for attempt in range(express_read.READS):
        try:
            with express_read.snapshot(folder, tables=TABLES, sleep=sleep) as (copy, _changed):
                # 复制途中有人写了一张单：次数差一张不影响建议，不重读
                data = collect(copy, since)
            break
        except express_read._Unreadable as error:
            log.info("账套副本读不全，稍后重读: %s", error)
        if attempt < express_read.READS - 1:
            sleep(express_read.REREAD_WAIT)
    if data is None:
        raise RequestFailed("account_unreadable")
    data["account_set"] = folder.name
    data["read_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if len(content) > MAX_BYTES["account_history"]:
        raise RequestFailed("too_large")
    return Result(content, {"format": "json.gz", "version": RESULT_VERSION, "suppliers": len(data["suppliers"]),
                            "documents": data["documents"]})
