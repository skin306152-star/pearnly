# -*- coding: utf-8 -*-
"""按需请求 ``express_read``（2026-10 CC 闭环 L1）：只读读出一本 Express 账套某一个月的账，交回服务端（``readback`` 域）
出总账、税报和「推送以后改过哪些」的差异表。

- 只读这台电脑能推送的同一组账套：根目录必须是 CC 桌面端里设的 Express 数据根或授权根（``express_root`` /
  ``authorized_roots``），账套必须登记在那个根的公司登记表（SCCOMP）里；只读，不要求可写。不满足回 account_not_allowed，
  登记表或账套文件夹读不到回 account_unreachable。
- 不碰「等账套」：不探 ISRUN、不拿写者锁、不等推送（``account_lock`` 只给写入用）。要读的表先用普通的共享读打开（Express
  开着账套照样能读）整份复制到本机临时目录，再只读打开副本；复制时文件被独占（Express 在整理、压缩）就隔几秒再试，仍不行
  回 account_busy。读完即删副本，账套里的文件一个字节都不动。
- 当月有借贷不平的凭证（多半是复制那一刻 Express 或推送正写到一半）：整份隔几秒重读一次，仍不平照报在 diagnostics。
- 只照表读、不算账：期初公式、总账与税报版式、与推送记录的比对都在服务端，改口径不用重发 CC 桌面版。金额一律两位小数的
  字符串（DBF 浮点 → str → Decimal 四舍五入），日期 ISO；结果是 gzip 后的 JSON（``RESULT_VERSION``）。

读出的内容（字段名见 ``collect``）：
- company：ISINFO 公司名、税号、分店号；settings：ISINFO 里推送写分录时会换用的几个默认科目（现金 ACCNUM00、存货 ACCNUM04、
  购货 ACCNUM18）与是不是期末盘存制（ISPERPETUA = 'N'），服务端据此认出推送时换过的科目（``dbf_writer`` / ``dbf_sales``）；
  calendar：ISPRD 24 期（读不到为空）；
- accounts：GLACC；balances：GLBAL 每个科目 BEG* / DEBIT* / CREDIT* 各列的合计（同一科目多行相加，只给非 0 的列）；
- movements：到本月为止、没作废的凭证的 GLJNLIT 行按（科目, 日历月, TRNTYP）相加——服务端据此算本月的期初；
- journal：行的 VOUDAT 在当月、或单据号被点名的凭证，带凭证头（作废的也给，看 docstat）；
- vat：ISVAT 里 VATPRD、DOCDAT 或 VATDAT 落在当月的行，以及被点名的单据号（不论月份）；软删的只给被点名的；
- documents：APTRN / ARTRN 里 DOCDAT 在当月的单据，以及被点名的单据号（不论日期，软删的标 deleted），带对方名称、税号与
  对方档上的科目（赊购 / 赊销的应付 / 应收行记它）。
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import ntpath
import os
import re
import shutil
import tempfile
import time
from collections import defaultdict
from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Iterator, Optional

import dbf

from . import dbf_schema as S
from . import fiscal_period
from .account_probe import probe_account_sets
from .logger import get_logger
from .request_contract import MAX_BYTES, RequestFailed, Result

log = get_logger(__name__)

RESULT_VERSION = 1
TABLES = ("GLACC", "GLBAL", "GLJNL", "GLJNLIT", "ISVAT", "ISINFO", "ISPRD", "APTRN", "ARTRN", "APMAS", "ARMAS")
REQUIRED = ("GLACC", "GLJNL", "GLJNLIT")
# 复制时文件被独占（Express 整理 / 压缩账套）：隔这么久再试，都不行回 account_busy。
COPY_WAITS = (3.0, 10.0, 20.0)
# 读出借贷不平的凭证、或副本读到一半坏了：隔这么久整份重读。
REREAD_WAIT = 5.0
DOCNUMS_MAX = 2000
DOCNUM_MAX = 20
JOURNAL_LINES_MAX = 200_000
UNBALANCED_SHOWN = 50
CANCELLED = "C"
DEBIT = (S.TRNTYP_DEBIT, "D")
CREDIT = (S.TRNTYP_CREDIT, "C")
CENT = Decimal("0.01")
_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
# GLBAL 的余额列（BEGLY、DEBIT1..12 / CREDIT1..12 及其 LY、DEBITCLS / CREDITCLS……）：列名按前缀认，不写死个数。
_BALANCE_COLUMN = re.compile(r"^(BEG|DEBIT|CREDIT)[A-Z0-9]*$")
# ISVAT 的金额列：写入器只写 AMT01 / VAT01；别的税率组（AMT02……）有值就另外照报。
_VAT_COLUMN = re.compile(r"^(AMT|VAT)\d{2}$")


class _Unreadable(Exception):
    """副本读到一半坏了（复制那一刻 Express 正在追加）：整份重读。"""


# ── 参数 ────────────────────────────────────────────────────────────────────


def _period(value) -> tuple:
    found = _PERIOD.fullmatch(str(value or ""))
    if not found:
        raise RequestFailed("params_invalid")
    year, month = int(found.group(1)), int(found.group(2))
    first = dt.date(year, month, 1)
    last = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return f"{year:04d}-{month:02d}", first, last


def _docnums(value) -> frozenset:
    """被点名的单据号（推送过的单）：不论日期都要读，好看出被改了日期、作废或删掉的。"""
    if value in (None, ""):
        return frozenset()
    if not isinstance(value, list) or len(value) > DOCNUMS_MAX:
        raise RequestFailed("params_invalid")
    out = set()
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > DOCNUM_MAX:
            raise RequestFailed("params_invalid")
        out.add(item.strip().upper())
    return frozenset(out)


def _norm(value) -> str:
    path = str(value or "").strip().replace("/", "\\").rstrip("\\")
    return ntpath.normcase(ntpath.normpath(path)) if path else ""


def resolve(cfg, account_set, account_root) -> Path:
    """云端点名的账套 → 本机账套文件夹（与推送同一组判据，只是不要求可写）。"""
    wanted = _norm(account_set)
    if not wanted:
        raise RequestFailed("account_not_allowed")
    root = _norm(account_root)
    if not root:
        # 没给根目录：只认 CC 桌面端里选定的那一本（同推送 `worker._task_settings`）
        chosen = [getattr(cfg, "account_set", ""), getattr(cfg, "account_dir", "")]
        if wanted not in {_norm(item) for item in chosen if item}:
            raise RequestFailed("account_not_allowed")
        folder = Path(str(getattr(cfg, "account_dir", "") or account_set))
    else:
        roots = [getattr(cfg, "express_root", "") or "", *(getattr(cfg, "authorized_roots", None) or ())]
        allowed = next((item for item in roots if item and _norm(item) == root), None)
        if not allowed:
            raise RequestFailed("account_not_allowed")
        try:
            registered = probe_account_sets(allowed, include_details=False)
        except Exception:
            raise RequestFailed("account_unreachable") from None
        selected = next((item for item in registered if _norm(item.path) == wanted), None)
        if selected is None:
            raise RequestFailed("account_not_allowed")
        folder = Path(str(selected.path))
    if not folder.is_dir():
        raise RequestFailed("account_unreachable")
    return folder


# ── 复制到本机再读 ──────────────────────────────────────────────────────────


def _copy(source: Path, target: Path, waits, sleep) -> None:
    for wait in (*waits, None):
        try:
            with open(source, "rb") as reader, open(target, "wb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
            return
        except FileNotFoundError:
            target.unlink(missing_ok=True)
            return  # 这一刻表没了：当这张表缺，照缺表处理
        except PermissionError:
            code = "account_busy"  # 被独占（Express 整理 / 压缩账套）
        except OSError:
            code = "account_unreachable"
        target.unlink(missing_ok=True)
        if wait is None:
            raise RequestFailed(code)
        sleep(wait)


@contextmanager
def snapshot(account_dir, *, waits=COPY_WAITS, sleep: Callable[[float], None] = time.sleep) -> Iterator[Path]:
    """要读的表（含备注文件）复制到本机临时目录；出了 with 即删。文件名一律大写（读副本时按大写找）。"""
    target = Path(tempfile.mkdtemp(prefix="pearnly-readback-"))
    try:
        try:
            found = {}
            for entry in os.listdir(account_dir):
                found.setdefault(entry.upper(), Path(account_dir) / entry)
        except OSError:
            raise RequestFailed("account_unreachable") from None
        for table in TABLES:
            for extension in (".DBF", ".FPT"):
                source = found.get(table + extension)
                if source is not None:
                    _copy(source, target / (table + extension), waits, sleep)
        yield target
    finally:
        shutil.rmtree(target, ignore_errors=True)


@contextmanager
def _table(folder: Path, name: str):
    path = folder / f"{name}.DBF"
    if not path.is_file():
        yield None
        return
    try:
        table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
        table.open(dbf.READ_ONLY)
    except Exception as error:
        raise _Unreadable(f"{name}: {type(error).__name__}") from error
    try:
        yield table
    finally:
        table.close()


def _rows(folder: Path, name: str, diag: dict, pick, *, deleted: bool = False):
    """逐行交给 ``pick(rec, columns, is_deleted)``，回它给的非 None 结果。读不出的单行（坏字节）跳过并计数；整张表读到
    一半坏了（副本不完整）抛 ``_Unreadable``。"""
    out = []
    with _table(folder, name) as table:
        if table is None:
            return out
        columns = {str(item).upper() for item in table.field_names}
        count = bad = 0
        try:
            for rec in table:
                gone = dbf.is_deleted(rec)
                if gone and not deleted:
                    continue
                count += 1
                try:
                    item = pick(rec, columns, gone)
                except RequestFailed:
                    raise
                except Exception:
                    bad += 1
                    continue
                if item is not None:
                    out.append(item)
        except (RequestFailed, _Unreadable):
            raise
        except Exception as error:
            raise _Unreadable(f"{name}: {type(error).__name__}") from error
        diag["rows"][name] = count
        if bad:
            diag["bad_rows"][name] = bad
    return out


def _get(rec, columns, name):
    return rec[name] if name in columns else None


def _text(value) -> str:
    return "" if value is None else str(value).replace("\xa0", " ").strip()


def _day(value) -> Optional[dt.date]:
    if isinstance(value, dt.datetime):
        return value.date()
    return value if isinstance(value, dt.date) else None


def _iso(value) -> str:
    day = _day(value)
    return day.isoformat() if day else ""


def _money(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("amount")
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def _shown(amount: Decimal) -> str:
    return f"{amount:.2f}"


def _branch(rec, columns) -> str:
    return S.orgnum_to_branch5(rec["ORGNUM"]) if "ORGNUM" in columns else ""


# ── 读一份副本 ──────────────────────────────────────────────────────────────


def collect(folder: Path, period: str, first: dt.date, last: dt.date, docnums: frozenset = frozenset()) -> dict:
    """从复制出来的账套表读出这个月要交回的内容（口径见文件头）。"""
    started = time.monotonic()
    diag = {"rows": {}, "bad_rows": {}, "missing_tables": [], "orphan_lines": 0, "unbalanced": [],
            "trntyp_unknown": 0}
    present = {name for name in TABLES if (folder / f"{name}.DBF").is_file()}
    if any(name not in present for name in REQUIRED):
        raise RequestFailed("account_tables_missing")
    diag["missing_tables"] = [name for name in TABLES if name not in present]

    def month(day) -> bool:
        return bool(day) and first <= day <= last

    def named(docnum) -> bool:
        return docnum.upper() in docnums

    infos = _rows(folder, "ISINFO", diag, lambda r, c, _g: (
        {"name": _text(_get(r, c, "THINAM")), "tax_id": _text(_get(r, c, "TAXID")), "branch": _branch(r, c)},
        {"cash": _text(_get(r, c, "ACCNUM00")), "stock": _text(_get(r, c, "ACCNUM04")),
         "purchases": _text(_get(r, c, "ACCNUM18")), "periodic": _text(_get(r, c, "ISPERPETUA")).upper() == "N"}))
    company, settings = infos[0] if infos else (
        {"name": "", "tax_id": "", "branch": ""}, {"cash": "", "stock": "", "purchases": "", "periodic": False})

    try:
        calendar = fiscal_period.load(folder)
    except Exception:
        calendar = None
    periods = [{"index": p.index, "slot": p.slot, "beg": p.beg.isoformat(), "end": p.end.isoformat()}
               for p in (calendar.periods if calendar else ())]

    accounts = _rows(folder, "GLACC", diag, lambda r, c, _g: {
        "code": _text(_get(r, c, "ACCNUM")), "name": _text(_get(r, c, "ACCNAM")),
        "name2": _text(_get(r, c, "ACCNAM2")), "group": _text(_get(r, c, "GROUP")),
        "level": _text(_get(r, c, "LEVEL")), "parent": _text(_get(r, c, "PARENT"))})
    accounts = [row for row in accounts if row["code"]]

    sums: dict = defaultdict(lambda: defaultdict(Decimal))

    def balance(rec, columns, _gone):
        code = _text(_get(rec, columns, "ACCNUM"))
        if not code:
            return None
        values = {name: _money(rec[name]) for name in sorted(columns) if _BALANCE_COLUMN.fullmatch(name)}
        for name, value in values.items():
            sums[code][name] += value
        return code

    _rows(folder, "GLBAL", diag, balance)
    balances = [{"code": code, "values": {name: _shown(value) for name, value in sorted(values.items()) if value}}
                for code, values in sorted(sums.items())]

    heads = {}

    def head(rec, columns, _gone):
        voucher = _text(_get(rec, columns, "VOUCHER"))
        if voucher:
            heads[voucher] = {"voucher": voucher, "book": _text(_get(rec, columns, "JNLTYP")),
                              "date": _iso(_get(rec, columns, "VOUDAT")), "descrp": _text(_get(rec, columns, "DESCRP")),
                              "docstat": _text(_get(rec, columns, "DOCSTAT")).upper(),
                              "trnstat": _text(_get(rec, columns, "TRNSTAT")),
                              "creby": _text(_get(rec, columns, "CREBY"))}
        return None

    _rows(folder, "GLJNL", diag, head)

    moved: dict = defaultdict(Decimal)
    picked: dict = defaultdict(list)
    shown = {"lines": 0}

    def line(rec, columns, _gone):
        voucher = _text(_get(rec, columns, "VOUCHER"))
        header = heads.get(voucher)
        if header is None:
            diag["orphan_lines"] += 1  # 没有凭证头的行（Express 里看不到）：不算
            return None
        day = _day(_get(rec, columns, "VOUDAT"))
        if day is None:
            raise ValueError("line without a date")
        account = _text(_get(rec, columns, "ACCNUM"))
        trntyp = _text(_get(rec, columns, "TRNTYP")).upper()
        amount = _money(_get(rec, columns, "AMOUNT"))
        if header["docstat"] != CANCELLED and day <= last:
            moved[(account, f"{day.year:04d}-{day.month:02d}", trntyp)] += amount
        if month(day) or named(voucher):
            shown["lines"] += 1
            if shown["lines"] > JOURNAL_LINES_MAX:
                raise RequestFailed("too_large")
            picked[voucher].append({"seq": _text(_get(rec, columns, "SEQIT")), "account": account,
                                    "date": day.isoformat(), "descrp": _text(_get(rec, columns, "DESCRP")),
                                    "trntyp": trntyp, "amount": _shown(amount),
                                    "dept": _text(_get(rec, columns, "DEPCOD"))})
        return None

    _rows(folder, "GLJNLIT", diag, line)
    movements = [{"code": code, "month": when, "trntyp": trntyp, "amount": _shown(amount)}
                 for (code, when, trntyp), amount in sorted(moved.items()) if amount]
    journal = []
    for voucher in sorted(picked):
        lines = picked[voucher]
        journal.append({**heads[voucher], "lines": lines})
        if heads[voucher]["docstat"] == CANCELLED:
            continue
        debit = sum((Decimal(item["amount"]) for item in lines if item["trntyp"] in DEBIT), Decimal(0))
        credit = sum((Decimal(item["amount"]) for item in lines if item["trntyp"] in CREDIT), Decimal(0))
        unknown = [item for item in lines if item["trntyp"] not in DEBIT + CREDIT]
        diag["trntyp_unknown"] += len(unknown)
        if debit != credit or unknown:
            diag["unbalanced"].append(voucher)
    diag["unbalanced"] = diag["unbalanced"][:UNBALANCED_SHOWN] + (
        ["..."] if len(diag["unbalanced"]) > UNBALANCED_SHOWN else [])

    def vat(rec, columns, gone):
        docnum = _text(_get(rec, columns, "DOCNUM"))
        dates = [_day(_get(rec, columns, name)) for name in ("VATPRD", "VATDAT", "DOCDAT")]
        if not (named(docnum) or (not gone and any(month(day) for day in dates))):
            return None
        extra = {name: _shown(_money(rec[name])) for name in sorted(columns)
                 if _VAT_COLUMN.fullmatch(name) and name not in ("AMT01", "VAT01") and _money(rec[name])}
        return {"vatrec": _text(_get(rec, columns, "VATREC")).upper(), "rectyp": _text(_get(rec, columns, "RECTYP")),
                "vatprd": _iso(dates[0]), "vatdat": _iso(dates[1]), "docdat": _iso(dates[2]), "docnum": docnum,
                "refnum": _text(_get(rec, columns, "REFNUM")), "descrp": _text(_get(rec, columns, "DESCRP")),
                "prenam": _text(_get(rec, columns, "PRENAM")), "taxid": _text(_get(rec, columns, "TAXID")),
                "branch": _branch(rec, columns), "docstat": _text(_get(rec, columns, "DOCSTAT")).upper(),
                "amt01": _shown(_money(_get(rec, columns, "AMT01"))),
                "vat01": _shown(_money(_get(rec, columns, "VAT01"))), "extra": extra, "deleted": bool(gone)}

    vat_rows = _rows(folder, "ISVAT", diag, vat, deleted=True)

    parties = {}
    for table, code_column, name_column in (("APMAS", "SUPCOD", "SUPNAM"), ("ARMAS", "CUSCOD", "CUSNAM")):
        found = {}
        for row in _rows(folder, table, diag, lambda r, c, _g, code_column=code_column, name_column=name_column: (
                _text(_get(r, c, code_column)), _text(_get(r, c, name_column)), _text(_get(r, c, "TAXID")),
                _text(_get(r, c, "ACCNUM")))):
            if row[0]:
                found.setdefault(row[0], row)
        parties[table] = found

    documents = []
    for table, side, code_column, ref_column, masters in (("APTRN", "AP", "SUPCOD", "REFNUM", "APMAS"),
                                                          ("ARTRN", "AR", "CUSCOD", "YOUREF", "ARMAS")):
        def document(rec, columns, gone, side=side, code_column=code_column, ref_column=ref_column, masters=masters):
            docnum = _text(_get(rec, columns, "DOCNUM"))
            docdat = _day(_get(rec, columns, "DOCDAT"))
            if not (named(docnum) or (not gone and month(docdat))):
                return None
            party = _text(_get(rec, columns, code_column))
            _code, name, tax_id, account = parties[masters].get(party, (party, "", "", ""))
            return {"table": side, "rectyp": _text(_get(rec, columns, "RECTYP")), "docnum": docnum,
                    "docdat": _iso(docdat), "ref": _text(_get(rec, columns, ref_column)), "party": party,
                    "party_name": name, "party_taxid": tax_id, "party_account": account,
                    "flgvat": _text(_get(rec, columns, "FLGVAT")),
                    "amount": _shown(_money(_get(rec, columns, "AMOUNT"))),
                    "netval": _shown(_money(_get(rec, columns, "NETVAL"))),
                    "vatamt": _shown(_money(_get(rec, columns, "VATAMT"))),
                    "netamt": _shown(_money(_get(rec, columns, "NETAMT"))),
                    "docstat": _text(_get(rec, columns, "DOCSTAT")).upper(),
                    "userid": _text(_get(rec, columns, "USERID")), "deleted": bool(gone)}

        documents.extend(_rows(folder, table, diag, document, deleted=True))

    diag["read_seconds"] = round(time.monotonic() - started, 2)
    return {"version": RESULT_VERSION, "period": period, "company": company, "settings": settings, "calendar": periods,
            "accounts": accounts, "balances": balances, "movements": movements, "journal": journal,
            "vat": vat_rows, "documents": documents, "diagnostics": diag}


# ── 处理函数（request_worker.HANDLERS["express_read"]）────────────────────────


def read_books(cfg, params: dict, *, sleep: Callable[[float], None] = time.sleep) -> Result:
    """读一本账套的一个月：{account_set, account_root, period: "YYYY-MM", docnums: [...]}。"""
    period, first, last = _period(params.get("period"))
    docnums = _docnums(params.get("docnums"))
    folder = resolve(cfg, params.get("account_set"), params.get("account_root"))
    data = None
    for attempt in range(3):
        try:
            with snapshot(folder, sleep=sleep) as copy:
                data = collect(copy, period, first, last, docnums)
        except _Unreadable as error:
            log.info("账套副本读不全，稍后重读: %s", error)
            data = None
        if data is not None and (not data["diagnostics"]["unbalanced"] or attempt >= 1):
            break
        if attempt == 2:
            break
        sleep(REREAD_WAIT)
    if data is None:
        raise RequestFailed("account_unreadable")
    data["account_set"] = folder.name
    data["read_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if len(content) > MAX_BYTES["express_read"]:
        raise RequestFailed("too_large")
    return Result(content, {"format": "json.gz", "version": RESULT_VERSION, "vouchers": len(data["journal"]),
                            "vat": len(data["vat"]), "documents": len(data["documents"])})
