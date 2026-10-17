# -*- coding: utf-8 -*-
"""按需请求 ``account_history``（2026-10 科目建议 B）：只读读出一本 Express 账套近 12 个月的进项单据，汇总成
「供应商 → 常用科目」的次数，交回服务端（``readback.history``）给推送时要人选的费用科目排建议。

- 账套判据、复制到本机再读、不碰「等账套」、不拿写者锁、Express 开着照读：与 ``express_read`` 同一套（``resolve`` /
  ``snapshot``），只复制这里要读的几张表。
- 读什么：APTRN 里单据日期不早于 ``since``、没作废、没软删的单据（供应商代码 SUPCOD），对应凭证（GLJNL.VOUCHER 与
  单据号相同，Express 与 CC 桌面版写入都是这样）在 GLJNLIT 的借方行；凭证作废的整张不算。
- 结果版本 2（数字会计员第 3 期）：Pearnly 写的单（APTRN.USERID 或 GLJNL.CREBY = ``PEARNLY``）不算 —— CC 自己推的单
  不能攒成「已定」再拿来自动推（服务端只认 v2 的「已定」为规则定）。
- 不算的科目：账套设置 ISINFO 里的现金（ACCNUM00）、进项税（ACCNUM06）、应付（ACCNUM07），以及这家供应商档上挂的
  应付科目（APMAS.ACCNUM，16% 的供应商不用账套默认的应付）。现金、银行只出现在进项凭证的贷方，借方行本来就没有。
  是不是费用科目由服务端按科目表核（这里不判断，期末盘存制账套的「购货」照样交回）。
- 结果版本 3（数字会计员第 3 期，用户 10-17）：每家供应商另交 ``posting``，商品单走库存 / 记费用各几张、最后一张的单据日期 ——
  同上那批单（近 12 个月、没作废、不是 Pearnly 写的），在 STCRD 里（按 DOCNUM 挂单，PEOPLE 有值时须是这家供应商的码，防撞号）
  有一行是库存品（STMAS.STKTYP = 0）或有库存影响（``dbf_schema.stcrd_no_stock_impact`` 为假）的记走库存，其余（没有明细、只有
  非库存品的明细）记费用。
- ``posting`` 真可选（第五轮审查，10-17）：科目那几张表（``TABLES``）先照旧复制读完；STMAS、STCRD（``POSTING_TABLES``）另外
  单独复制、单独读，在整件请求剩下的时间里做（``LEASE_SECONDS`` 减去交回要留的 ``POSTING_RESERVE_SECONDS``，复制也算在内）。
  这两张表缺、被占、复制失败、读坏、有读不出的坏行、存货主档为空、时间不够，都只是整份不交 ``posting``（不猜：只扫了一半、
  漏了一行都可能把走库存的单算成记费用），原因记在 ``diagnostics.posting``，科目部分照常交回；复制与扫描的秒数记
  ``posting_copy_seconds`` / ``stcrd_seconds``。STCRD 要从头扫到尾（行按录入先后排，只读近 12 个月的那部分做不到）。
- 交回：每家供应商（代码、名称、税号、分店号）最多 ``ACCOUNTS_PER_SUPPLIER`` 个科目，每个科目记「几张单用过」与最后
  一次的单据日期，以及 ``posting``；按单据数取前 ``SUPPLIERS_MAX`` 家。只交次数，不交金额、不交单据明细。结果是 gzip 后的
  JSON（``RESULT_VERSION``，字段见 ``collect``）。
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import shutil
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

import dbf

from . import dbf_schema as S
from . import express_read
from .dbf_schema import PEARNLY_USERID
from .logger import get_logger
from .request_contract import LEASE_SECONDS, MAX_BYTES, RequestFailed, Result

log = get_logger(__name__)

RESULT_VERSION = 3
# 科目那部分要的表（一起复制，读坏整份重读）
TABLES = ("ISINFO", "APMAS", "APTRN", "GLJNL", "GLJNLIT")
REQUIRED = ("APTRN", "GLJNLIT")
# 商品单入账方式（v3）要的两张表：另外复制、另外读，出什么事都只是不交 ``posting``（见文件头）
POSTING_TABLES = ("STMAS", "STCRD")
# 库存品（STMAS.STKTYP）
STOCK_TYPE = "0"
# 整件请求的租约里给交回结果留的秒数：``posting`` 的复制与扫描要在租约到期前这么久做完，做不完就不交
POSTING_RESERVE_SECONDS = 120
COPY_CHUNK = 1024 * 1024
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


def _pearnly(user) -> bool:
    """这一行是不是 Pearnly（CC 桌面版）写的（写路的 ``USERID`` / ``CREBY``）。"""
    return str(user or "").strip().upper() == PEARNLY_USERID


def collect(folder: Path, since: dt.date, *, deadline: float | None = None) -> dict:
    """从复制出来的账套表汇总「供应商 → 常用科目」与入账方式（同一个文件夹里有 STMAS / STCRD 就读，口径见文件头）。
    ``deadline``（``time.monotonic()`` 的时刻）是 ``posting`` 的截止；没给按租约减去留给交回的时间算。"""
    started = time.monotonic()
    state = _accounts(folder, since)
    deadline = started + _posting_budget() if deadline is None else deadline
    return _assemble(state, _posting(folder, state["documents"], state["diag"], deadline), since, started)


def _posting_budget() -> float:
    return LEASE_SECONDS["account_history"] - POSTING_RESERVE_SECONDS


def _accounts(folder: Path, since: dt.date) -> dict:
    """科目那部分（``TABLES``）：读坏抛 ``express_read._Unreadable``（调用方整份重读）。回汇总要的中间结果。"""
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
        if docnum and party and when and when >= since and text(get(rec, columns, "DOCSTAT")).upper() != CANCELLED \
                and not _pearnly(text(get(rec, columns, "USERID"))):
            documents[docnum] = (party, when)
        return None

    express_read._rows(folder, "APTRN", diag, document)

    def head(rec, columns, _gone):
        voucher = _key(get(rec, columns, "VOUCHER"))
        if voucher in documents and (text(get(rec, columns, "DOCSTAT")).upper() == CANCELLED
                                     or _pearnly(text(get(rec, columns, "CREBY")))):
            documents.pop(voucher)  # 凭证作废、或是 Pearnly 写的：这张单不算
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
    return {"diag": diag, "excluded": excluded, "suppliers": suppliers, "documents": documents, "used": used,
            "last": last}


def _assemble(state: dict, posting, since: dt.date, started: float) -> dict:
    diag, suppliers, used, last = state["diag"], state["suppliers"], state["used"], state["last"]
    by_party: dict = defaultdict(list)
    party_docs: dict = defaultdict(set)
    for (party, account), docs in used.items():
        by_party[party].append({"code": account, "docs": len(docs), "last": last[(party, account)].isoformat()})
        party_docs[party] |= docs
    counted = {party: sum(row["docs"] for row in kinds.values()) for party, kinds in (posting or {}).items()}
    ranked = sorted(set(by_party) | set(counted),
                    key=lambda party: (-max(len(party_docs[party]), counted.get(party, 0)), party))[:SUPPLIERS_MAX]
    out = []
    for party in sorted(ranked):
        master = suppliers.get(party) or {}
        # 用过的单据多的在前；一样多时最近用过的在前，再按科目号
        accounts = sorted(by_party.get(party, []), key=lambda item: item["code"])
        accounts.sort(key=lambda item: item["last"], reverse=True)
        accounts.sort(key=lambda item: item["docs"], reverse=True)
        row = {"code": party, "name": master.get("name", ""), "tax_id": master.get("tax_id", ""),
               "branch": master.get("branch", ""), "documents": len(party_docs[party]),
               "accounts": accounts[:ACCOUNTS_PER_SUPPLIER]}
        if posting is not None:
            row["posting"] = posting.get(party) or _no_posting()
        out.append(row)
    diag["read_seconds"] = round(time.monotonic() - started, 2)
    return {"version": RESULT_VERSION, "since": since.isoformat(), "excluded": sorted(state["excluded"]),
            "suppliers": out, "documents": len({doc for docs in party_docs.values() for doc in docs}),
            "diagnostics": diag}


def _no_posting() -> dict:
    return {"stock": {"docs": 0, "last": ""}, "service": {"docs": 0, "last": ""}}


def _posting(folder: Path, documents: dict, diag: dict, deadline: float):
    """这批单（``documents``：单号 → (供应商码, 单据日期)）按供应商汇总走库存 / 记费用各几张（口径见文件头）→
    ``{供应商码: {"stock": {docs, last}, "service": {docs, last}}}``。判不清（表缺、读坏、有坏行、主档为空、过了
    ``deadline``）回 None，原因记 ``diag["posting"]``；从不往外抛（科目部分照常交回）。"""
    try:
        found, reason = _stocked(folder, documents, diag, deadline)
    except express_read._Unreadable as error:
        found, reason = None, f"{str(error).split(':', 1)[0].lower()}_unreadable"
    except Exception:  # noqa: BLE001 — 入账方式是可选的一项：任何意外都只是不交
        log.exception("入账方式读不出")
        found, reason = None, "failed"
    if found is None:
        diag["posting"] = reason
        return None
    out: dict = {}
    for docnum, (party, when) in documents.items():
        row = out.setdefault(party, _no_posting())["stock" if docnum in found else "service"]
        row["docs"] += 1
        row["last"] = max(row["last"], when.isoformat())
    diag["posting"] = "ok"
    return out


def _stocked(folder: Path, documents: dict, diag: dict, deadline: float):
    """走库存的单号集合与原因：(集合, "") 或 (None, 原因)。"""
    if any(not (folder / f"{name}.DBF").is_file() for name in POSTING_TABLES):
        return None, "tables_missing"
    get, text = express_read._get, express_read._text
    types = {}

    def master(rec, columns, _gone):
        code = text(get(rec, columns, "STKCOD"))
        if code:
            types[code] = text(get(rec, columns, "STKTYP"))
        return None

    try:
        express_read._rows(folder, "STMAS", diag, master)
    except express_read._Unreadable:
        return None, "stmas_unreadable"
    if diag["bad_rows"].get("STMAS"):
        return None, "stmas_bad_rows"  # 漏一件库存品就可能把走库存的单算成记费用
    if not types:
        return None, "no_stock_master"  # 存货主档读不出：库存品判不出，不猜
    stocked = set()
    started = time.monotonic()
    count = bad = 0
    try:
        with express_read._table(folder, "STCRD") as table:
            columns = {str(item).upper() for item in table.field_names}
            for rec in table:
                if time.monotonic() > deadline:
                    diag["stcrd_seconds"] = round(time.monotonic() - started, 2)
                    return None, "stcrd_too_slow"
                if dbf.is_deleted(rec):
                    continue
                count += 1
                try:
                    docnum = _key(get(rec, columns, "DOCNUM"))
                    found = documents.get(docnum)
                    if found is None or docnum in stocked:
                        continue
                    people = text(get(rec, columns, "PEOPLE"))
                    if people and people != found[0]:
                        continue  # 单号撞上别家（销项、别的供应商）的明细：不挂到这张
                    if types.get(text(get(rec, columns, "STKCOD"))) == STOCK_TYPE or not S.stcrd_no_stock_impact(rec):
                        stocked.add(docnum)
                except Exception:  # noqa: BLE001 — 坏字节的单行计数，下面整份不交
                    bad += 1
    except express_read._Unreadable:
        return None, "stcrd_unreadable"
    except Exception:  # noqa: BLE001 — 读到一半坏了（副本不完整）
        return None, "stcrd_unreadable"
    diag["rows"]["STCRD"] = count
    diag["stcrd_seconds"] = round(time.monotonic() - started, 2)
    if bad:
        diag["bad_rows"]["STCRD"] = bad
        return None, "stcrd_bad_rows"  # 坏行可能正是某张单唯一的库存行：不猜
    return stocked, ""


def _copy_until(source: Path, target: Path, deadline: float) -> str:
    """一块一块复制，过了 ``deadline`` 停；成功回空串，否则回原因（不重试、不抛）。"""
    try:
        with open(source, "rb") as reader, open(target, "wb") as writer:
            while True:
                if time.monotonic() > deadline:
                    return "copy_too_slow"
                piece = reader.read(COPY_CHUNK)
                if not piece:
                    return ""
                writer.write(piece)
    except FileNotFoundError:
        return "tables_missing"
    except PermissionError:
        return "copy_busy"  # 被独占（Express 整理 / 压缩账套）
    except OSError:
        return "copy_failed"


def _posting_from(account_dir: Path, documents: dict, diag: dict, deadline: float):
    """从账套文件夹单独复制 STMAS / STCRD（含备注文件）到本机再判入账方式；出了事只回 None（见 :func:`_posting`）。"""
    if not documents:
        diag["posting"] = "ok"
        return {}
    started = time.monotonic()
    target = Path(tempfile.mkdtemp(prefix="pearnly-posting-"))
    try:
        try:
            sources = express_read._sources(account_dir, POSTING_TABLES)
        except RequestFailed:
            diag["posting"] = "copy_failed"
            return None
        for name, (source, _stamped) in sources.items():
            reason = _copy_until(source, target / name, deadline)
            if reason:
                diag["posting"] = reason
                diag["posting_copy_seconds"] = round(time.monotonic() - started, 2)
                return None
        diag["posting_copy_seconds"] = round(time.monotonic() - started, 2)
        return _posting(target, documents, diag, deadline)
    finally:
        shutil.rmtree(target, ignore_errors=True)


# ── 处理函数（request_worker.HANDLERS["account_history"]）────────────────────


def read_history(cfg, params: dict, *, sleep: Callable[[float], None] = time.sleep) -> Result:
    """{account_set, account_root, since: "YYYY-MM-DD"} → 汇总好的「供应商 → 常用科目」与入账方式。"""
    started = time.monotonic()
    since = _since(params.get("since"))
    folder = express_read.resolve(cfg, params.get("account_set"), params.get("account_root"))
    state = None
    for attempt in range(express_read.READS):
        try:
            with express_read.snapshot(folder, tables=TABLES, sleep=sleep) as (copy, _changed):
                # 复制途中有人写了一张单：次数差一张不影响建议，不重读
                state = _accounts(copy, since)
            break
        except express_read._Unreadable as error:
            log.info("账套副本读不全，稍后重读: %s", error)
        if attempt < express_read.READS - 1:
            sleep(express_read.REREAD_WAIT)
    if state is None:
        raise RequestFailed("account_unreadable")
    # 入账方式另外复制、另外读，用整件请求剩下的时间（见文件头）；出了事只是不交 posting
    posting = _posting_from(folder, state["documents"], state["diag"], started + _posting_budget())
    data = _assemble(state, posting, since, started)
    data["account_set"] = folder.name
    data["read_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if len(content) > MAX_BYTES["account_history"]:
        raise RequestFailed("too_large")
    return Result(content, {"format": "json.gz", "version": RESULT_VERSION, "suppliers": len(data["suppliers"]),
                            "documents": data["documents"]})
