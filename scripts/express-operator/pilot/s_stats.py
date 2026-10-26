"""只读统计事务所 S 盘副本里各 Express 账套的单据量（「CC 代录 Express」试点客户分档用）。

在测试机上跑（只用标准库）::

    cd C:\\PearnlyStress\\_xp_pilot\\s_stats
    python -m pilot.s_stats "C:\\PearnlyStress\\S盘副本\\2568" --out stats-2568.json

口径
- 账套：年度目录下名字含 ``EXP`` 的目录（如 ``EXP68``）里，凡含 ``GLJNL.DBF`` 的目录算一本账套；
  年度目录里的备份、客户原件等其他目录不算（会重复计数）。可用 ``--books-under`` 改。
- 期间：默认按年度目录名换算公历整年（2568 → 2025-01-01..2025-12-31）；年度账套里常有下一年的
  数据（账套跨年继续用），一律按单据日期过滤。可用 ``--since`` / ``--until`` 改。
- 单据：每本账套内按单号去重，取 GLJNL 凭证号（VOUCHER，按 VOUDAT 记月）与 APTRN / ARTRN / STTRN
  单头单号（DOCNUM，按 DOCDAT 记月）的并集；删除标记行不算，任一来源 DOCSTAT='C'（作废）的单号不算。
  这样既算进过账的单据和手工日记账，也算进没过账的单头。PO / SO / QT / 报价等不在这四张表里的不算。
- 种类：单号按最长前缀匹配 ISRUN 的 PREFIX，先按该单据簿的 DOCTYP（Express 系统单据类型）定大类，
  再按 POSDES（泰文说明）关键词细分（见 ``classify``）；匹配不到前缀的记「其他凭证」。
  每本账套输出 ``prefix_map``（前缀 → 类别、DOCTYP、POSDES）方便核对。
- 只读：直接 ``open(..., 'rb')`` 读副本文件，不加锁、不写回。坏记录按字段容错（解析失败的值为空），
  整张表读不了记进 ``errors``，账套缺 GLJNL 等关键表时记 ``skipped`` 原因。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

try:
    from . import dbfread
except ImportError:  # 直接当脚本跑
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pilot import dbfread  # type: ignore

CATEGORIES = ["采购", "销售", "费用", "收款", "付款", "库存单", "其他凭证"]

# Express 系统单据类型（ISRUN.DOCTYP）→ 大类。ZP / ZR 是收付款方式代码、空是杂项代码，不是单据簿。
DOCTYP_CATEGORY = {
    "RR": "采购", "HP": "采购", "DN": "采购", "CP": "采购", "PO": "采购",
    "IV": "销售", "HS": "销售", "CN": "销售", "DR": "销售", "OI": "销售", "PX": "销售", "SO": "销售", "QT": "销售",
    "OE": "费用", "DP": "费用",
    "RE": "收款", "AI": "收款", "AJ": "收款",
    "PS": "付款", "AE": "付款", "AF": "付款", "VR": "付款", "QP": "付款",
    "JU": "库存单", "OU": "库存单", "RL": "库存单", "CA": "库存单",
    "GL": "其他凭证", "BD": "其他凭证", "BQ": "其他凭证", "BT": "其他凭证", "BU": "其他凭证",
    "BV": "其他凭证", "BW": "其他凭证", "BS": "其他凭证", "BR": "其他凭证",
}
NOT_BOOKS = {"", "ZP", "ZR"}

# POSDES 关键词细分（按顺序取第一个命中的）
EXPENSE_WORDS = ("ค่าใช้จ่าย", "คชจ", "เงินสดย่อย", "เงินทดรอง")
GL_JOURNAL_WORDS = (  # DOCTYP=GL（手工日记账）按日记账名归类
    (EXPENSE_WORDS, "费用"),
    (("ขาย",), "销售"),
    (("ซื้อ",), "采购"),
    (("รับ",), "收款"),
    (("จ่าย",), "付款"),
)


def classify(doctyp: str, posdes: str) -> str:
    """单据簿 → 类别。规则：DOCTYP 定大类；采购簿说明含费用关键词的（如 ซื้อเงินเชื่อ-ค่าใช้จ่าย）算费用；
    手工日记账（GL）按日记账名关键词：费用 > ขาย 销售 > ซื้อ 采购 > รับ 收款 > จ่าย 付款 > 其他凭证。"""
    doctyp = (doctyp or "").strip().upper()
    text = (posdes or "").replace("\xa0", " ")
    if doctyp == "GL":
        for words, cat in GL_JOURNAL_WORDS:
            if any(w in text for w in words):
                return cat
        return "其他凭证"
    cat = DOCTYP_CATEGORY.get(doctyp, "其他凭证")
    if cat == "采购" and any(w in text for w in EXPENSE_WORDS):
        return "费用"
    return cat


def _read(book: str, table: str) -> Optional[bytes]:
    for name in (table + ".DBF", table + ".dbf", table.lower() + ".dbf"):
        path = os.path.join(book, name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
    return None


def _rows(data: bytes, keep: set, errors: List[str], table: str):
    try:
        for row in dbfread.rows(data, keep=keep):
            yield row
    except Exception as exc:  # 表头坏、截断等：已读的行保留
        errors.append("%s: %s" % (table, exc))


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _month(value) -> Optional[str]:
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, dt.date):
        return value.isoformat()
    return None


def prefix_books(isrun: Optional[bytes], errors: List[str]) -> List[Tuple[str, str, str, str]]:
    """[(PREFIX, 类别, DOCTYP, POSDES)]，前缀长的在前。同一前缀多本时取非 ZP/ZR 的第一本。"""
    out: Dict[str, Tuple[str, str, str, str]] = {}
    if not isrun:
        return []
    for row in _rows(isrun, {"DOCTYP", "PREFIX", "POSDES"}, errors, "ISRUN"):
        if row["_del"]:
            continue
        doctyp = _text(row.get("DOCTYP")).upper()
        prefix = _text(row.get("PREFIX")).upper()
        if doctyp in NOT_BOOKS or not prefix:
            continue
        posdes = _text(row.get("POSDES")).replace("\xa0", " ")
        out.setdefault(prefix, (prefix, classify(doctyp, posdes), doctyp, posdes))
    return sorted(out.values(), key=lambda item: -len(item[0]))


def match(number: str, books: List[Tuple[str, str, str, str]]) -> Optional[Tuple[str, str, str, str]]:
    upper = number.upper()
    for book in books:
        if upper.startswith(book[0]):
            return book
    return None


def scan_book(path: str, since: dt.date, until: dt.date) -> Dict:
    errors: List[str] = []
    info: Dict = {"path": path, "book": os.path.basename(path.rstrip("\\/")), "errors": errors}
    gl = _read(path, "GLJNL")
    if gl is None:
        info["skipped"] = "没有 GLJNL.DBF"
        return info

    isinfo = _read(path, "ISINFO")
    if isinfo:
        for row in _rows(isinfo, {"THINAM", "ENGNAM", "TAXID", "ADDR01"}, errors, "ISINFO"):
            if not row["_del"]:
                info["company"] = _text(row.get("THINAM"))
                info["company_en"] = _text(row.get("ENGNAM"))
                info["taxid"] = re.sub(r"\D", "", _text(row.get("TAXID")))
                break
    books = prefix_books(_read(path, "ISRUN"), errors)

    docs: Dict[str, Tuple[str, str]] = {}  # 单号 → (月份, 来源)
    void = set()
    source_hits = Counter()
    bad_dates = 0
    rows_read = Counter()

    def take(number: str, when, source: str, status: str):
        nonlocal bad_dates
        if not number:
            return
        if status == "C":
            void.add(number)
            return
        day = when.date() if isinstance(when, dt.datetime) else when
        if not isinstance(day, dt.date):
            bad_dates += 1
            return
        if not (since <= day <= until):
            return
        if number in docs:
            if source not in docs[number][1]:
                docs[number] = (docs[number][0], docs[number][1] + "+" + source)
        else:
            docs[number] = (day.strftime("%Y-%m"), source)

    for row in _rows(gl, {"VOUCHER", "VOUDAT", "DOCSTAT", "JNLTYP"}, errors, "GLJNL"):
        rows_read["GLJNL"] += 1
        if row["_del"]:
            continue
        take(_text(row.get("VOUCHER")), row.get("VOUDAT"), "GL", _text(row.get("DOCSTAT")).upper())

    stock_headers = 0
    for table in ("APTRN", "ARTRN", "STTRN"):
        data = _read(path, table)
        if data is None:
            errors.append("%s: 缺表" % table)
            continue
        for row in _rows(data, {"DOCNUM", "DOCDAT", "DOCSTAT"}, errors, table):
            rows_read[table] += 1
            if row["_del"]:
                continue
            status = _text(row.get("DOCSTAT")).upper()
            take(_text(row.get("DOCNUM")), row.get("DOCDAT"), table[:2], status)
            day = row.get("DOCDAT")
            if table == "STTRN" and status != "C" and isinstance(day, dt.date) and since <= day <= until:
                stock_headers += 1

    by_month: Dict[str, Counter] = defaultdict(Counter)
    by_prefix = Counter()
    prefix_map: Dict[str, Dict] = {}
    for number, (month, source) in docs.items():
        if number in void:
            continue
        book = match(number, books)
        if book:
            prefix, cat, doctyp, posdes = book
            prefix_map.setdefault(prefix, {"category": cat, "doctyp": doctyp, "posdes": posdes})
        else:
            prefix, cat = "(未匹配:" + re.sub(r"[0-9].*", "", number)[:6] + ")", "其他凭证"
            prefix_map.setdefault(prefix, {"category": cat, "doctyp": "", "posdes": ""})
        by_month[month][cat] += 1
        by_prefix[prefix] += 1
        source_hits[source] += 1

    stock_items = 0
    items = 0
    stmas = _read(path, "STMAS")
    if stmas:
        for row in _rows(stmas, {"STKTYP"}, errors, "STMAS"):
            if row["_del"]:
                continue
            items += 1
            if _text(row.get("STKTYP")) == "0":
                stock_items += 1

    months = {m: dict(c, total=sum(c.values())) for m, c in sorted(by_month.items())}
    totals = Counter()
    for c in by_month.values():
        totals.update(c)
    info.update({
        "months": months,
        "active_months": len(months),
        "total": sum(totals.values()),
        "by_category": {cat: totals.get(cat, 0) for cat in CATEGORIES},
        "by_prefix": dict(by_prefix.most_common()),
        "prefix_map": prefix_map,
        "sources": dict(source_hits),  # GL=只在凭证、AP/AR/ST=只在单头、GL+AP 等=两边都有
        "void_numbers": len(void),
        "bad_dates": bad_dates,
        "rows_read": dict(rows_read),
        "items": items,
        "stock_items": stock_items,
        "stock_headers": stock_headers,
        "uses_stock_docs": stock_headers > 0 or totals.get("库存单", 0) > 0,
        # 每月单号集合的指纹，供同公司多本账套判断是不是拷贝（只存个数与哈希，不存单号本身）
        "month_keys": {m: sorted(hash_num(n) for n, (mm, _) in docs.items() if mm == m and n not in void)
                       for m in months},
    })
    return info


def hash_num(number: str) -> int:
    value = 0
    for ch in number.upper():
        value = (value * 131 + ord(ch)) & 0xFFFFFFFF
    return value


def find_books(root: str, under: str) -> List[str]:
    pattern = re.compile(under, re.I)
    tops = [os.path.join(root, d) for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d)) and pattern.search(d)]
    found = []
    for top in tops:
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames.sort()
            if any(f.upper() == "GLJNL.DBF" for f in filenames):
                found.append(dirpath)
    return found


def default_period(root: str) -> Tuple[dt.date, dt.date]:
    match_year = re.match(r"(25\d\d)", os.path.basename(root.rstrip("\\/")))
    year = int(match_year.group(1)) - 543 if match_year else dt.date.today().year
    return dt.date(year, 1, 1), dt.date(year, 12, 31)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", help="年度目录，如 C:\\PearnlyStress\\S盘副本\\2568")
    parser.add_argument("--out", required=True)
    parser.add_argument("--books-under", default=r"^EXP|EXP$", help="年度目录下哪些子目录里找账套（正则）")
    parser.add_argument("--since")
    parser.add_argument("--until")
    args = parser.parse_args(argv)

    since, until = default_period(args.root)
    if args.since:
        since = dt.date.fromisoformat(args.since)
    if args.until:
        until = dt.date.fromisoformat(args.until)
    started = time.time()
    books = find_books(args.root, args.books_under)
    result = {
        "root": args.root, "since": since.isoformat(), "until": until.isoformat(),
        "rules": {
            "doctyp_category": DOCTYP_CATEGORY,
            "expense_words": EXPENSE_WORDS,
            "gl_journal_words": [[list(w), c] for w, c in GL_JOURNAL_WORDS],
            "count_basis": "每本账套按单号去重：GLJNL 凭证号 ∪ APTRN/ARTRN/STTRN 单头单号；去删除行与作废(C)",
        },
        "books": [],
    }
    for path in books:
        t0 = time.time()
        try:
            info = scan_book(path, since, until)
        except Exception as exc:
            info = {"path": path, "book": os.path.basename(path), "skipped": "读不了：%r" % exc}
        info["seconds"] = round(time.time() - t0, 2)
        result["books"].append(info)
        print("%-60s %6s %5.1fs %s" % (path[-60:], info.get("total", "-"), info["seconds"],
                                         info.get("skipped", "")), flush=True)
    result["seconds"] = round(time.time() - started, 1)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
