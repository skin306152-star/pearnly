#!/usr/bin/env python3
"""数字会计员第 3 期第 15 条：两层回放（零费用，设计见 docs/work/2026-10-16-da-phase3-design.md）。

不连站点、不调识别与模型、不写任何账套：只读语料答案（Express 原表的只读副本）与已有的真识别结果，导入与推送服务同一套纯函数
（`push.express.decide` / `automation` / `prepare`、`readback.history_rules`、`companies.automation_rules`、
`push.automation.rules`、`history.review`、`tax.periods`）。语料只在测试机，本工具在测试机 WSL 里跑（源码复制到 WSL /tmp，跑完删）。

四个子命令（都只读）：

- ``inventory`` 第 0 步盘点：每家每份账套答案有哪些表、单据有没有科目（GLJNLIT）、对方码、RECTYP、税登记；每家每期有现成真识别
  结果的张数（回放库从哪里来）。
- ``judge`` 判定层：答案每一张单造成「识别全对」的票面，按公司、按单据录入日顺序模拟：账套记忆从空开始，C 类由「模拟的人」按答案
  选，人的选择记进账套记忆；Express 历史只用同一份账套里这张单之前 12 个月的答案分录（防泄漏）；主档用这份账套原表（含模拟期之后
  才建的档，偏乐观）。反事实计分：所有判成 A / B 的单都当被推，比对方档、费用 / 收入科目、现 / 赊、抵不抵进项。三套阈值的档位
  时间线并排。
- ``gate`` 识别闸层：只用有真识别结果的件（金标 215 份，当前生产读法 3.8），过第 2 条识别侧与原文核对之后，字段仍错的比例，按票种
  （文字层 PDF / 图片）分开；对照的是独立金标（原件上印的）。
- ``report``：合并两层出 ``报表.md`` 与 ``明细.tsv``（明细带单据级信息，只放测试机，不进仓库）。

用法（测试机 WSL）::

    python3 da-replay.py inventory --corpus /mnt/c/PearnlyStress/语料/06-录入推送 --gold /mnt/c/PearnlyStress/语料/金标-2026-10 --out OUT
    python3 da-replay.py judge --corpus ... --out OUT [--company 名称]
    PYTHONPATH=<含 pypdf 的目录> python3 da-replay.py gate --gold ... --originals /mnt/c/PearnlyStress/语料 \
        --manifest /mnt/c/PearnlyStress/语料/06-录入推送/清单.tsv --out OUT
    python3 da-replay.py report --out OUT

金额一律 Decimal；日期按单据所属会计期间（不看墙上时间）。脚本拒绝 ``\\\\`` 与 ``S:`` 开头的路径（不碰局域网真账），读写都拒。
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import math
import re
import struct
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "apps/pearnly_cowork", ROOT / "apps/pearnly_core"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# ── 统计 ────────────────────────────────────────────────────────────────────────

Z95 = 1.959963984540054
# 第 15 条达标线：点估计 ≥99.5% 且 95% 下限（Wilson）≥99.0%（0 错要 ≥381 张、1 错 ≥563、2 错 ≥726）
BAR_POINT = 0.995
BAR_LOWER = 0.990


def wilson(ok: int, n: int, z: float = Z95) -> Tuple[Optional[float], Optional[float]]:
    """正确率的 Wilson 区间 (下限, 上限)；n = 0 回 (None, None)。"""
    if n <= 0:
        return None, None
    p = ok / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denominator = 1 + z * z / n
    return max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator)


def passes_bar(ok: int, n: int) -> bool:
    lower, _ = wilson(ok, n)
    return bool(n) and ok / n >= BAR_POINT and lower is not None and lower >= BAR_LOWER


def combined(judge_ok: int, judge_n: int, gate_ok: int, gate_n: int) -> Dict[str, Optional[float]]:
    """合并估计：自动推准确率 ≈ (1 − 判定错率) × (1 − 闸后识别错率)；区间取两层同侧界相乘（保守）。"""
    if not judge_n or not gate_n:
        return {"point": None, "lower": None, "upper": None}
    j_low, j_up = wilson(judge_ok, judge_n)
    g_low, g_up = wilson(gate_ok, gate_n)
    return {"point": (judge_ok / judge_n) * (gate_ok / gate_n), "lower": j_low * g_low, "upper": j_up * g_up}


def pct(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value * 100:.{digits}f}%"


# ── 只读 DBF（Express 原表；cp874） ─────────────────────────────────────────────


def refuse_lan(path: Any) -> Path:
    """局域网 Accserver / S 盘一律不读不写（设计第 15 条）：拒绝 ``\\\\`` 与 ``S:`` 开头的路径。"""
    text = str(path or "")
    if text.startswith("\\\\") or text.startswith("//") or re.match(r"^[sS]:", text) or text.lower().startswith("/mnt/s/"):
        raise SystemExit(f"refused: {text} is on the LAN share")
    return Path(text)


def _value(kind: str, raw: bytes, encoding: str) -> Any:
    if kind in ("C", "V"):
        return raw.decode(encoding, "replace").replace("\x00", "").strip()
    if kind == "D":
        text = raw.decode("ascii", "replace").strip()
        try:
            return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8])) if len(text) == 8 else None
        except ValueError:
            return None
    if kind in ("N", "F"):
        text = raw.decode("ascii", "replace").strip()
        try:
            return Decimal(text) if text else None
        except InvalidOperation:
            return None
    if kind == "B" and len(raw) == 8:
        number = struct.unpack("<d", raw)[0]
        return Decimal(repr(number)) if math.isfinite(number) else None
    if kind == "I" and len(raw) == 4:
        return Decimal(struct.unpack("<i", raw)[0])
    if kind == "Y" and len(raw) == 8:
        return Decimal(struct.unpack("<q", raw)[0]) / Decimal(10000)
    if kind == "L":
        return raw[:1] in (b"T", b"t", b"Y", b"y")
    return None


def dbf_rows(path: Path, wanted: Optional[Iterable[str]] = None, encoding: str = "cp874"):
    """逐行读一张 dBase / FoxPro 表（只读、跳过删除行）：``{字段: 值}``，只取 ``wanted`` 里的字段。表不在回空。"""
    path = refuse_lan(path)
    if not path.is_file():
        return
    data = path.read_bytes()
    if len(data) < 32:
        return
    count, header, size = struct.unpack("<IHH", data[4:12])
    fields, position, offset = [], 32, 1
    while position + 32 <= header and data[position] != 0x0D:
        name = data[position:position + 11].split(b"\x00")[0].decode("ascii", "replace").strip().upper()
        fields.append((name, chr(data[position + 11]), offset, data[position + 16]))
        offset += data[position + 16]
        position += 32
    keep = {name.upper() for name in wanted} if wanted is not None else None
    chosen = [field for field in fields if keep is None or field[0] in keep]
    view = memoryview(data)
    for index in range(count):
        start = header + index * size
        record = view[start:start + size]
        if len(record) < size:
            break
        if record[0] == 0x2A:  # '*' 删除行
            continue
        yield {name: _value(kind, bytes(record[off:off + length]), encoding) for name, kind, off, length in chosen}


def dbf_fields(path: Path) -> List[str]:
    path = refuse_lan(path)
    if not path.is_file():
        return []
    with open(path, "rb") as handle:
        head = handle.read(32)
        header = struct.unpack("<H", head[8:10])[0]
        body = handle.read(max(0, header - 32))
    names, position = [], 0
    while position + 32 <= len(body) and body[position] != 0x0D:
        names.append(body[position:position + 11].split(b"\x00")[0].decode("ascii", "replace").strip().upper())
        position += 32
    return names


# ── 答案：一份 Express 账套 ─────────────────────────────────────────────────────

ANSWER_TABLES = ("ISINFO", "APTRN", "ARTRN", "ISVAT", "GLJNL", "GLJNLIT", "APMAS", "ARMAS", "GLACC", "STMAS", "STCRD",
                 "ISTAB")
CANCELLED = "C"
DEBIT, CREDIT = "0", "1"
# 现 / 赊：进项 HP=1 现购、RR=3 赊购；销项 HS=1 现销、IV=3 赊销（CC 桌面版 dbf_schema 同口径）
RECTYP_PAYMENT = {"1": "cash", "3": "credit"}
# ISINFO 默认科目槽（CC 桌面版 `account_probe._ISINFO_DEFAULT_SLOTS` 同口径）
ISINFO_SLOTS = {"revenue_acc": "ACCNUM11", "ar_acc": "ACCNUM02", "vat_output_acc": "ACCNUM10",
                "fallback_acc": "ACCNUM04", "ap_acc": "ACCNUM07", "vat_input_acc": "ACCNUM06", "cash_acc": "ACCNUM00"}
INPUT_VAT_WORDS = ("ภาษีซื้อ",)
OUTPUT_VAT_WORDS = ("ภาษีขาย",)
STOCK_IMPACT = ("PHYBAL", "MREMBAL", "MREMVAL", "LOTBAL", "LOTVAL", "LUNITPR")
# 录入日读不到（凭证没有 CREDAT）时按单据日期后这么多天当上传日
ENTRY_LAG_DAYS = 7


def digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def branch5(value: Any) -> str:
    number = digits(str(value or "").split(".")[0])
    return (number[-5:] if number else "0").zfill(5)


def money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    try:
        return Decimal(str(value or "0").replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _clean_name(prefix: Any, name: Any) -> str:
    return " ".join(f"{prefix or ''} {name or ''}".replace("\xa0", " ").split())


def book_period(dates: Sequence[dt.date]) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    """账套的会计年（起止）：原表没有 ISPRD，取装下最多单据的连续 12 个月（并列取早的）。"""
    days = sorted(day for day in dates if day)
    if not days:
        return None, None
    months = collections.Counter((day.year, day.month) for day in days)

    def window(year, month):
        return sum(months.get(((year * 12 + month - 1 + step) // 12, (month - 1 + step) % 12 + 1), 0)
                   for step in range(12))

    year, month = max(sorted(months), key=lambda key: window(*key))
    start = dt.date(year, month, 1)
    end = dt.date(year + (month + 11) // 12, (month + 11) % 12 + 1, 1) - dt.timedelta(days=1)
    return start, end


def next_year_end(end: Optional[dt.date]) -> Optional[dt.date]:
    """次年段止的估计（ISPRD 读不到 NY 第 12 期时）：本年止再过一年（Express 每份账套都带下一年 12 期）。"""
    if not end:
        return None
    try:
        return end.replace(year=end.year + 1)
    except ValueError:  # 2 月 29 日
        return end.replace(year=end.year + 1, day=28)


def fiscal_calendars(express: Optional[Path], sets: Sequence[Path]) -> Dict[str, Tuple[dt.date, dt.date, Optional[dt.date]]]:
    """测试机本地 Express 测试账套（``70EXP/T*``，各公司最新账套的副本）的会计期间（ISPRD 本年 CY：BEG1 ～ END12，次年段
    NY 止 END12NY，与 CC 桌面版上报的 ``period_start`` / ``period_end`` / ``next_end`` 同一来源）：``{来源账套名: (起, 止,
    次年止)}``。对照表是语料脚本的 ``expsets*.tsv``（税号、年、来源账套、来源路径、测试账套……）。读不到的账套回放时按
    单据日期估。"""
    found: Dict[str, Tuple[dt.date, dt.date, Optional[dt.date]]] = {}
    if not express:
        return found
    for table in sets:
        if not refuse_lan(table).is_file():
            continue
        for line in table.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split("\t")
            if len(parts) < 5 or not parts[2].strip() or not parts[4].strip():
                continue
            row = next(dbf_rows(refuse_lan(express) / parts[4].strip() / "ISPRD.DBF", ["BEG1", "END12", "END12NY"]),
                       None)
            if row and row.get("BEG1") and row.get("END12"):
                found[parts[2].strip().upper()] = (row["BEG1"], row["END12"], row.get("END12NY") or None)
    return found


def load_book(folder: Path, *, company: str, own_tax: str, key: str,
              calendar: Optional[Tuple] = None) -> Dict[str, Any]:
    """一份账套答案（``答案/<账套>/原表``）→ 判定层要的全部事实。只读。"""
    tables = folder / "原表"
    info = next(dbf_rows(tables / "ISINFO.DBF", [*ISINFO_SLOTS.values(), "ORGNUM", "TAXID", "ISPERPETUA"]), {}) or {}
    defaults = {name: str(info.get(slot) or "").strip() for name, slot in ISINFO_SLOTS.items()}
    accounts = {}
    for row in dbf_rows(tables / "GLACC.DBF", ["ACCNUM", "ACCNAM", "GROUP", "ACCTYP", "STATUS"]):
        code = str(row.get("ACCNUM") or "").strip()
        if code:
            accounts[code] = {"code": code, "name": row.get("ACCNAM") or "", "group": row.get("GROUP") or "",
                              "type": row.get("ACCTYP") or ""}
    input_vat = {code for code, row in accounts.items() if any(word in row["name"] for word in INPUT_VAT_WORDS)}
    output_vat = {code for code, row in accounts.items() if any(word in row["name"] for word in OUTPUT_VAT_WORDS)}
    input_vat |= {defaults["vat_input_acc"]} - {""}
    output_vat |= {defaults["vat_output_acc"]} - {""}

    def masters(table, code_field, name_field):
        out = []
        for row in dbf_rows(tables / f"{table}.DBF", [code_field, name_field, "PRENAM", "TAXID", "ORGNUM", "ACCNUM"]):
            code = str(row.get(code_field) or "").strip()
            if code:
                out.append({"code": code, "name": str(row.get(name_field) or "").strip(),
                            "prefix": str(row.get("PRENAM") or "").strip(), "tax_id": digits(row.get("TAXID")),
                            "branch": branch5(row.get("ORGNUM")), "account": str(row.get("ACCNUM") or "").strip()})
        return out

    suppliers, customers = masters("APMAS", "SUPCOD", "SUPNAM"), masters("ARMAS", "CUSCOD", "CUSNAM")
    products, stock_types = [], {}
    for row in dbf_rows(tables / "STMAS.DBF", ["STKCOD", "STKDES", "STKTYP"]):
        code = str(row.get("STKCOD") or "").strip()
        if code:
            kind = "stock" if str(row.get("STKTYP") or "").strip() == "0" else "non_stock"
            products.append({"code": code, "name": str(row.get("STKDES") or "").strip(), "kind": kind})
            stock_types[code] = kind
    departments = [{"code": str(row.get("TYPCOD") or "").strip(), "name": str(row.get("TYPDES") or "").strip()}
                   for row in dbf_rows(tables / "ISTAB.DBF", ["TABTYP", "TYPCOD", "TYPDES"])
                   if str(row.get("TABTYP") or "").strip() == "50" and str(row.get("TYPCOD") or "").strip()]
    lines = moving = 0
    stock_docs = set()
    for row in dbf_rows(tables / "STCRD.DBF", ["DOCNUM", "STKCOD", *STOCK_IMPACT]):
        lines += 1
        if any(row.get(field) not in (None, "", Decimal(0)) for field in STOCK_IMPACT):
            moving += 1
        # 有存货明细（库存或非库存品）的单：会计按货记（分存货 / 费用科目），回放不声明入账类型
        stock_docs.add(str(row.get("DOCNUM") or "").strip().upper())
    fingerprint = {"stcrd_lines": lines, "stcrd_lines_moving_stock": moving,
                   "stock_master_count": sum(1 for kind in stock_types.values() if kind == "stock")}

    vouchers: Dict[str, Dict[str, Any]] = {}
    for row in dbf_rows(tables / "GLJNL.DBF", ["VOUCHER", "DOCSTAT", "CREDAT", "CREBY"]):
        voucher = str(row.get("VOUCHER") or "").strip().upper()
        if voucher:
            vouchers[voucher] = {"cancelled": str(row.get("DOCSTAT") or "").strip().upper() == CANCELLED,
                                 "created": row.get("CREDAT"), "debit": collections.Counter(),
                                 "credit": collections.Counter()}
    for row in dbf_rows(tables / "GLJNLIT.DBF", ["VOUCHER", "ACCNUM", "TRNTYP", "AMOUNT"]):
        found = vouchers.get(str(row.get("VOUCHER") or "").strip().upper())
        account = str(row.get("ACCNUM") or "").strip()
        if found is None or not account:
            continue
        side = "debit" if str(row.get("TRNTYP") or "").strip() in (DEBIT, "D") else "credit"
        found[side][account] += money(row.get("AMOUNT"))
    registers: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in dbf_rows(tables / "ISVAT.DBF", ["VATREC", "DOCNUM", "DOCDAT", "REFNUM", "TAXID", "ORGNUM", "AMT01",
                                             "VAT01", "AMTRAT0", "DOCSTAT"]):
        if str(row.get("DOCSTAT") or "").strip().upper() == CANCELLED:
            continue
        registers.setdefault((str(row.get("VATREC") or "").strip().upper(),
                              str(row.get("DOCNUM") or "").strip().upper()), row)

    docs = []
    for direction, table, code_field, master_rows, record in (
            ("purchase", "APTRN", "SUPCOD", suppliers, "P"), ("sales", "ARTRN", "CUSCOD", customers, "S")):
        by_code = {row["code"]: row for row in master_rows}
        for row in dbf_rows(tables / f"{table}.DBF", ["RECTYP", "DOCNUM", "DOCDAT", "REFNUM", "YOUREF", code_field,
                                                     "TOTAL", "VATAMT", "NETAMT", "DOCSTAT", "USERID", "DEPCOD"]):
            docnum = str(row.get("DOCNUM") or "").strip().upper()
            if not docnum:
                continue
            party_code = str(row.get(code_field) or "").strip()
            master = by_code.get(party_code) or {}
            register = registers.get((record, docnum))
            voucher = vouchers.get(docnum)
            # 对方单号：税登记的、单据的「对方单号」，没有再取「对方参考」（YOUREF，不少账套把票号记在这里；同 eval 口径）
            ref = str((register or {}).get("REFNUM") or row.get("REFNUM") or row.get("YOUREF") or "").strip()
            if direction == "sales" and not ref:
                ref = docnum
            debit = dict(voucher["debit"]) if voucher else {}
            credit = dict(voucher["credit"]) if voucher else {}
            if direction == "purchase":
                booked = {code: amount for code, amount in debit.items() if code not in input_vat}
                vat_lines = [code for code in debit if code in input_vat]
            else:
                booked = {code: amount for code, amount in credit.items() if code not in output_vat}
                vat_lines = [code for code in credit if code in output_vat]
            docs.append({
                "direction": direction, "book": key, "company": company, "docnum": docnum,
                "rectyp": str(row.get("RECTYP") or "").strip(),
                "cancelled": str(row.get("DOCSTAT") or "").strip().upper() == CANCELLED,
                "date": (register or {}).get("DOCDAT") or row.get("DOCDAT"), "posted": row.get("DOCDAT"),
                "entered": (voucher or {}).get("created"), "ref": ref,
                "party_code": party_code, "party_name": _clean_name(master.get("prefix"), master.get("name")),
                "party_tax": digits((register or {}).get("TAXID")) or master.get("tax_id", ""),
                "party_branch": branch5((register or {}).get("ORGNUM")) if register else master.get("branch", "00000"),
                # 含税额 NETAMT；未税额 = 含税 − 税额（FLGVAT=1 价内含税时 TOTAL 是含税价，不能当未税额）
                "net": money(row.get("NETAMT")) - money(row.get("VATAMT")), "vat": money(row.get("VATAMT")),
                "total": money(row.get("NETAMT")),
                "register": bool(register), "voucher": bool(voucher) and not (voucher or {}).get("cancelled"),
                "accounts": {code: str(amount) for code, amount in booked.items()},
                "vat_booked": bool(vat_lines), "stock": docnum in stock_docs,
                "pearnly": str(row.get("USERID") or "").strip().upper() == "PEARNLY",
            })
    period = tuple(calendar[:2]) if calendar else \
        book_period([doc["date"] for doc in docs if doc["date"] and not doc["cancelled"]])
    following = (calendar[2] if calendar and len(calendar) > 2 else None) or next_year_end(period[1])
    return {"key": key, "folder": str(folder), "company": company, "own_tax": own_tax,
            "branch": branch5(info.get("ORGNUM")), "defaults": defaults, "accounts": list(accounts.values()),
            "suppliers": suppliers, "customers": customers, "products": products, "departments": departments,
            "fingerprint": fingerprint, "perpetual": str(info.get("ISPERPETUA") or "").strip().upper() == "Y",
            "period": period, "next_end": following, "period_source": "ISPRD" if calendar else "estimated",
            "docs": docs}


def answer_books(corpus: Path, only: Optional[str] = None) -> List[Tuple[str, str, Path]]:
    """``有答案/<公司>_<税号>/答案/<账套>`` → [(公司, 税号, 账套目录)]，按公司、账套名排序；``only`` 是 ``|`` 隔开的公司名。"""
    out = []
    for company_dir in sorted(refuse_lan(corpus).joinpath("有答案").iterdir()):
        if not company_dir.is_dir() or "_" not in company_dir.name:
            continue
        company, tax = company_dir.name.rsplit("_", 1)
        if only and company not in only.split("|"):
            continue
        for book in sorted((company_dir / "答案").glob("*/")):
            if (book / "原表").is_dir():
                out.append((company, tax, book))
    return out


# ── 判定层：造「识别全对」的票面，走推送同一套判定 ─────────────────────────────────


def entered_on(doc: Mapping[str, Any]) -> dt.date:
    """这张答案单录进 Express 的日子：凭证录入日（GLJNL.CREDAT，不早于单据日期才认），没有取单据日期后 ``ENTRY_LAG_DAYS`` 天。"""
    entered = doc.get("entered")
    return entered if isinstance(entered, dt.date) and entered >= doc["date"] else \
        doc["date"] + dt.timedelta(days=ENTRY_LAG_DAYS)


def _modules():
    from pearnly_cowork.companies import automation_rules
    from pearnly_cowork.companies import identity
    from pearnly_cowork.push.automation import rules as tier_rules
    from pearnly_cowork.push.express import automation, decide, goods_posting
    from pearnly_cowork.push.express import choices as choice_rules
    from pearnly_cowork.push.express.preflight import ExpressContext
    from pearnly_cowork.push.express.prepare import prepare_express
    from pearnly_cowork.readback import history_rules
    from pearnly_cowork.tax import periods as tax_periods

    return {"automation": automation, "decide": decide, "choices": choice_rules, "ExpressContext": ExpressContext,
            "prepare_express": prepare_express, "history_rules": history_rules, "automation_rules": automation_rules,
            "tier_rules": tier_rules, "tax_periods": tax_periods, "identity": identity, "goods_posting": goods_posting}


def perfect_fields(doc: Mapping[str, Any], book: Mapping[str, Any]) -> Dict[str, Any]:
    """答案这张单的「识别全对」票面：单号、日期、对方名称 / 税号 / 分店、本方、三项金额；有税额的是正式税票，
    没税额的按收据。不造明细行（识别全对的票面也不知道原件上的品名；上传入口不声明库存时商品行按科目直记）。"""
    party, own = ("buyer", "seller") if doc["direction"] == "sales" else ("seller", "buyer")
    vat = Decimal(doc["vat"])
    fields = {
        "document_type": "tax_invoice" if vat > 0 else "receipt", "direction": doc["direction"],
        "invoice_number": doc["ref"], "date": doc["date"].isoformat() if doc["date"] else "",
        f"{party}_name": doc["party_name"], f"{party}_tax": doc["party_tax"], f"{party}_branch": doc["party_branch"],
        f"{own}_name": book["company"], f"{own}_tax": book["own_tax"], f"{own}_branch": book["branch"],
        "subtotal": str(doc["net"]), "vat": str(doc["vat"]), "total_amount": str(doc["total"]),
    }
    return fields


def synthetic_text(fields: Mapping[str, Any]) -> str:
    """判定层不测识别：原文核对（含税额、日期在文字层里）按「原件上印着」造一段文字层，够 `automation.text_layer` 的字数。"""
    day = str(fields.get("date") or "")
    printed = f"{day[8:10]}/{day[5:7]}/{day[:4]}" if len(day) >= 10 else ""
    taxes = " ".join(str(fields.get(key) or "") for key in ("seller_tax", "buyer_tax"))
    body = f"{fields.get('invoice_number') or ''} {printed} {fields.get('total_amount') or ''} {taxes}"
    return (body + " ") * 20


def _party_rows(rows: Sequence[Mapping[str, Any]], tax: str, keep: Iterable[str]) -> List[Dict[str, Any]]:
    """这张单用得着的对方主档行：同税号的、账套记忆里记着的码；都没有时留一行别家的（「主档报上来了」与全表同义）。
    判定只看同税号的行与记着的码在不在（`choices.resolve`、`automation._party_tier`），按这张单挑出来结果与全表一样，
    只是省掉几十万张单 × 几千行的逐行比与深拷贝。"""
    keep = {code for code in keep if code}
    index = _master_index(rows)
    found = list(index["tax"].get(tax, [])) if tax else []
    found += [index["code"][code] for code in keep if code in index["code"] and index["code"][code] not in found]
    if not found and rows:
        found = [next((row for row in rows if row["tax_id"] != tax), rows[0])]
    return [{key: row[key] for key in ("code", "name", "tax_id", "branch")} for row in found]


_INDEXES: Dict[int, Dict[str, Any]] = {}


def _master_index(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    found = _INDEXES.get(id(rows))
    if found is None or found["rows"] is not rows:
        by_tax: Dict[str, List[Mapping[str, Any]]] = collections.defaultdict(list)
        for row in rows:
            if row["tax_id"]:
                by_tax[row["tax_id"]].append(row)
        found = _INDEXES[id(rows)] = {"rows": rows, "tax": by_tax, "code": {row["code"]: row for row in rows}}
    return found


def book_config(book: Mapping[str, Any], *, party_tax: str = "", keep: Iterable[str] = (),
                full: bool = False) -> Dict[str, Any]:
    """推送准备的账套配置（CC 桌面版上报的主档 `reported_*`、ISINFO 默认科目、记账指纹）。``full`` 为假时对方主档只给
    这张单用得着的行（见 `_party_rows`），存货主档只留一行（不造明细行，存货不判；只保住「主档报上来了」）。"""
    defaults = book["defaults"]
    config = {key: value for key, value in defaults.items() if value and key != "cash_acc"}
    suppliers, customers, products = book["suppliers"], book["customers"], list(book["products"])
    if full:
        suppliers = [{k: row[k] for k in ("code", "name", "tax_id", "branch")} for row in suppliers]
        customers = [{k: row[k] for k in ("code", "name", "tax_id", "branch")} for row in customers]
    else:
        suppliers, customers = _party_rows(suppliers, party_tax, keep), _party_rows(customers, party_tax, keep)
        products = products[:1]
    config.update(account_set=book["key"], account_dir=book["key"],
                  reported_account_sets=[{"path": book["key"], "writable": True}],
                  reported_suppliers=suppliers, reported_customers=customers, reported_products=products,
                  reported_departments=list(book["departments"]),
                  reported_accounts=[{"code": row["code"], "name": row["name"]} for row in book["accounts"]],
                  catalog_fingerprint=dict(book["fingerprint"]))
    return config


def account_row(book: Mapping[str, Any]) -> Dict[str, Any]:
    start, end = book["period"]
    following = book.get("next_end")
    return {"source_id": book["key"], "attributes": {"tax_id": book["own_tax"], "branch": book["branch"],
                                                      "period_start": start.isoformat() if start else "",
                                                      "period_end": end.isoformat() if end else "",
                                                      "next_end": following.isoformat() if following else ""}}


def shaped_books(m, books: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """这家公司各份账套给按日期选账套用的样子（`companies.identity.book`，带 ``next_end``）。"""
    return [m["identity"].book("express", "replay", account_row(item)) for item in books]


def day_books(m, shaped: Sequence[Mapping[str, Any]], book: Mapping[str, Any], day: dt.date) -> List[str]:
    """按单据日期这张单该推往的账套键（生产 `companies.automation.day_books` 同一判据：这家公司这个分店的账套，
    `companies.identity.for_day` —— 先本年期间覆盖这天的，没有再用次年段含这天的旧账套）。"""
    identity = m["identity"]
    return [item["key"] for item in identity.for_day(identity.for_branch(shaped, book["branch"]), day.isoformat())]


def history_data(entries: Sequence[Mapping[str, Any]], since: dt.date, until: dt.date, read_at: dt.date
                 ) -> Dict[str, Any]:
    """同一份账套里 ``since`` ≤ 单据日期 < ``until`` 的进项答案分录汇总成 CC 桌面版 `account_history` v2 的形状
    （供应商 → 科目 → 几张单、最后一次）。防泄漏：只用这张单之前的分录。"""
    used: Dict[Tuple[str, str], set] = collections.defaultdict(set)
    last: Dict[Tuple[str, str], dt.date] = {}
    parties: Dict[str, Dict[str, str]] = {}
    for doc in entries:
        day = doc["date"]
        if not day or not since <= day < until or doc["cancelled"] or doc["pearnly"]:
            continue
        parties.setdefault(doc["party_code"], {"name": doc["party_name"], "tax_id": doc["party_tax"],
                                               "branch": doc["party_branch"]})
        for account in doc["accounts"]:
            key = (doc["party_code"], account)
            used[key].add(doc["docnum"])
            last[key] = max(last.get(key, day), day)
    suppliers = collections.defaultdict(list)
    for (party, account), docs in used.items():
        suppliers[party].append({"code": account, "docs": len(docs), "last": last[(party, account)].isoformat()})
    rows = []
    for party, accounts in sorted(suppliers.items()):
        accounts.sort(key=lambda item: (-item["docs"], item["code"]))
        rows.append({**parties[party], "code": party, "accounts": accounts[:5]})
    return {"version": 2, "since": since.isoformat(), "read_at": f"{read_at.isoformat()}T00:00:00Z", "suppliers": rows}


def cc_party(masters: Sequence[Mapping[str, Any]], tax: str, name: str, branch: str) -> str:
    """CC 桌面版写入时按载荷找对方档（`master_create._find_code` 同一级联）：税号唯一命中（分店号明确且不符当未命中）；
    同税号多档按分店号、再按名称取唯一；税号零命中按名称精确。找不到回 ``new``，定不出回 ``ambiguous``。"""
    def norm(value):
        return " ".join(str(value or "").replace("\xa0", " ").split())

    hits = [row for row in masters if tax and row.get("tax_id") == tax]
    wanted = branch5(branch) if str(branch or "").strip() else ""
    if not hits:
        found = [row["code"] for row in masters if norm(row.get("name")) and norm(row.get("name")) == norm(name)]
        return found[0] if found else "new"
    if len(hits) == 1:
        return hits[0]["code"] if not wanted or hits[0]["branch"] == wanted else "new"
    if wanted:
        same = [row for row in hits if row["branch"] == wanted]
        if len(same) == 1:
            return same[0]["code"]
        if not same:
            return "new"
        hits = same
    named = [row["code"] for row in hits if norm(row.get("name")) == norm(name)]
    return named[0] if len(named) == 1 else "ambiguous"


def answer_values(doc: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    """答案（会计在 Express 里实际的记法）：对方档、科目（只有一个科目才有值；几个科目记 ``*multi``）、现 / 赊、抵不抵进项。"""
    accounts = sorted(doc["accounts"], key=lambda code: -Decimal(doc["accounts"][code]))
    out = {"party": doc["party_code"] or None,
           "account": None if not doc["voucher"] or not accounts else accounts[0] if len(accounts) == 1 else "*multi",
           "payment": RECTYP_PAYMENT.get(doc["rectyp"])}
    if doc["direction"] == "purchase" and Decimal(doc["vat"]) > 0:
        out["vat"] = "claim" if (doc["register"] or doc["vat_booked"]) else "expense"
    return out


def payload_values(payload: Mapping[str, Any], book: Mapping[str, Any], fields: Mapping[str, Any]) -> Dict[str, Any]:
    """推出去会是什么：对方档（载荷里的码，没有就按 CC 桌面版找档）、科目、现 / 赊、抵不抵进项。"""
    direction = payload.get("direction")
    side, masters = ("customer", book["customers"]) if direction == "sales" else ("supplier", book["suppliers"])
    block = payload.get(side) if isinstance(payload.get(side), Mapping) else {}
    party_side = "buyer" if direction == "sales" else "seller"
    code = str(block.get("code") or "").strip()
    if not code:
        code = "new" if block.get("confirmed_new") else cc_party(masters, digits(fields.get(f"{party_side}_tax")),
                                                                 str(fields.get(f"{party_side}_name") or ""),
                                                                 str(fields.get(f"{party_side}_branch") or ""))
    doctype = str(payload.get("doctype") or "").upper()
    out = {"party": code, "account": str(payload.get("items_account") or "").strip() or None,
           "payment": "cash" if doctype in ("HP", "HS") else "credit" if doctype in ("RR", "IV") else None}
    if direction == "purchase":
        out["vat"] = "claim" if Decimal(str(payload.get("vat_amount") or "0")) > 0 else "expense"
    return out


SCORED = ("party", "account", "payment", "vat")
# 本层核不了的项：销项现 / 赊（票面单据类型按「有税额 = 正式税票」造，现销的税票 / 收据合一单在真实识别里可能是
# receipt_tax_invoice，判法不同），单列、不计入准确率（审查处理 10-16）
NOT_EVALUABLE = {("sales", "payment")}


def evaluable_correct(direction: str, scored: Mapping[str, str]) -> bool:
    """去掉本层核不了的项后全对（`NOT_EVALUABLE`）。"""
    kept = {item: value for item, value in (scored or {}).items() if (direction, item) not in NOT_EVALUABLE}
    return bool(scored) and all(value in ("ok", "n/a") for value in kept.values())


def score(answer: Mapping[str, Any], pushed: Mapping[str, Any]) -> Dict[str, str]:
    """逐项：ok / wrong（带类）/ n/a（答案里没有这一项不计分、单列）。"""
    out = {}
    for item in SCORED:
        want = answer.get(item)
        if item not in answer or want is None:
            out[item] = "n/a"
        elif want == "*multi":
            out[item] = "wrong:multi_account"
        elif pushed.get(item) == want:
            out[item] = "ok"
        else:
            out[item] = f"wrong:{pushed.get(item) or 'none'}"
    return out


class Memory:
    """一份账套的「账套记忆」（`push.choices.remembered` 的形状）：只记模拟的人在 C 类里选的项。"""

    def __init__(self):
        self.kinds: Dict[str, Dict[str, str]] = {kind: {} for kind in
                                                 ("party", "item", "account", "payment", "vat", "department", "receipt")}

    def learn(self, needed: Sequence[Mapping[str, Any]], answer: Mapping[str, Any]) -> None:
        for item in needed or ():
            kind, subject = item.get("kind"), item.get("subject")
            value = answer.get(kind)
            if kind in self.kinds and subject and value and value != "*multi":
                self.kinds[kind][subject] = value


class CompanyChoice:
    """一家公司按公司记的选择（工作空间 ``document_policy`` 的 ``goods_posting|<公司>``，`push.express.goods_posting`）：
    商品单入账方式。模拟的人在第一张因此交人的单上按答案选一次（有存货明细 = 走库存，没有 = 记费用），之后这家照此。"""

    def __init__(self):
        self.goods_posting: Optional[str] = None


def _settle(m, data, fields, config, today):
    """要人选费用科目、这家从没人选过时，Express 历史很一致的科目（`push.application.service._settled_account` 同口径）。"""
    rules = m["history_rules"]
    chart = {str(row.get("code") or "") for row in config.get("reported_accounts") or []}

    def settle(needed):
        wanted = [item for item in needed or () if item.get("kind") == "account"]
        if not wanted or wanted[0].get("current"):
            return None
        accounts = rules.supplier_accounts(data, fields.get("seller_tax"), fields.get("seller_name"))
        found = rules.settled(accounts, today, data.get("read_at"))
        found = rules.stamp(found, data)
        if found and (not chart or found["code"] in chart):
            return found
        return None

    return settle


def history_index(docs: Sequence[Mapping[str, Any]], norm) -> Dict[str, List[Mapping[str, Any]]]:
    """进项答案按供应商键分组（13 位税号，否则 ``name:`` + 归一名；同 `history_rules.supplier_accounts` 的对法）。"""
    index: Dict[str, List[Mapping[str, Any]]] = collections.defaultdict(list)
    for doc in docs:
        if doc["direction"] != "purchase":
            continue
        tax = digits(doc["party_tax"])
        index[tax if len(tax) == 13 else "name:" + norm(doc["party_name"])].append(doc)
    return index


def judge_document(m, doc, book, memory: Memory, prior: Dict[str, List[Dict[str, Any]]], catalog, history_entries,
                   company: Optional[CompanyChoice] = None, shaped: Optional[Sequence[Mapping[str, Any]]] = None):
    """一张答案单走一遍推送判定 → 判定结果（类别、原因、逐项计分）。C 类由模拟的人按答案选（记进账套记忆）；商品单入账方式
    因没选过交人时，模拟的人按这张单的答案替这家公司选一次（``company``）。``shaped``：这家公司各份账套（`shaped_books`），
    按日期选账套用；没给只认这一份。"""
    company = company if company is not None else CompanyChoice()
    automation = m["automation"]
    fields = perfect_fields(doc, book)
    direction = doc["direction"]
    day = doc["date"]
    entered = entered_on(doc)
    party = "buyer" if direction == "sales" else "seller"
    subject = m["choices"].party_subject(direction, fields.get(f"{party}_tax"), fields.get(f"{party}_branch"),
                                         fields.get(f"{party}_name"))
    config = book_config(book, party_tax=digits(doc["party_tax"]), keep=[memory.kinds["party"].get(subject)])
    tax = digits(fields.get("seller_tax"))
    mine = history_entries.get(tax if len(tax) == 13 else "name:" + m["choices"].norm_name(fields.get("seller_name")),
                               []) if direction == "purchase" else []
    # 防泄漏（审查处理 10-16）：Express 历史只含这张单录入之前已录进去的单（按录入日，不按单据日期），不含它自己
    earlier = [item for item in mine if item is not doc and item["date"] and entered_on(item) < entered]
    data = history_data(earlier, entered - dt.timedelta(days=365), entered, entered)
    remembered = {kind: dict(values) for kind, values in memory.kinds.items()}
    # 入账类型：答案里没有存货明细（STCRD）的单按「服务」（录入第一步每批的选择；人这样选不改任何计分项），有存货明细的
    # 不声明；没声明的商品单这家公司选过入账方式就照选的（`goods_posting.resolve`，与推送服务同一份），没选过照生产交人
    posting_kind, posting_from = m["goods_posting"].resolve(
        fields, direction=direction, config=config, posting_kind=None if doc["stock"] else "service",
        chosen=company.goods_posting, today=entered)
    decision, _sources = m["decide"].decide(fields, direction=direction, posting_kind=posting_kind, config=config,
                                            remembered=remembered, branch=book["branch"],
                                            settle=_settle(m, data, fields, config, entered)
                                            if direction == "purchase" else None)
    if decision.get("history") and decision["applied"].get("account"):
        # 同推送服务 `_account_from`：历史已定的科目来源记 express_history（不报成账套记忆）
        decision["applied"]["account_from"] = "express_history"
    payload, ready, reason = None, False, ""
    if not decision["needed"]:
        config["choices"] = decision["applied"]
        history = {"id": doc["docnum"], "workspace_client_id": "replay", "tenant_id": "replay",
                   "invoice_no": fields["invoice_number"], "invoice_date": fields["date"], "direction": direction,
                   "total_amount": fields["total_amount"], "confidence": "high", "pages": [{"fields": fields}]}
        context = m["ExpressContext"](enabled=True, today=entered, own_tax_id=book["own_tax"], tenant_id="replay",
                                      mappings={}, account_tax_id=book["own_tax"])
        try:
            prepared = m["prepare_express"]({"id": "replay", "config": config}, history, context=context,
                                            posting_kind=posting_kind)
            payload = prepared.payload or None
            ready = prepared.disposition == "ready"
            reason = "" if ready else (prepared.reason or "preflight")
        except Exception as error:  # noqa: BLE001 — 一张单准备失败就当没准备好（C），不中断回放
            reason = f"prepare_error:{type(error).__name__}"
    if reason.startswith(m["goods_posting"].NEEDED_PREFIX) and company.goods_posting is None:
        # 模拟的人在复核页点「走库存 / 记费用」：按这张单的答案（有存货明细 = 走库存）替这家公司选一次
        company.goods_posting = "stock" if doc["stock"] else "service"
    # 账套记忆定的科目要最近连续 3 张同科目（`automation.memory_settled`）：这家供应商在这份账套里录入早于这张的单，
    # 新的在前，记的科目取答案（推出去后 Express 里最终的样子；一单多科目记 *multi，断开连续）
    recent = [answer_values(item).get("account") or "" for item in sorted(
        (item for item in earlier if not item["cancelled"]),
        key=lambda item: (entered_on(item), item["date"], item["docnum"]), reverse=True)[:12]]
    pages = [{"fields": fields, "_layer_chain": ["direct"]}]
    facts = automation.Facts(
        direction=direction, fields=fields, decision=decision, payload=payload, config=config, today=entered,
        ready=ready, blocked=reason, pages=pages, review_reasons=(), text=synthetic_text(fields),
        page_texts=[synthetic_text(fields)],
        account=account_row(book), catalog=catalog,
        day_books=day_books(m, shaped, book, day) if shaped is not None else [book["key"]], uploaded=entered,
        source_total=1, posting_from=posting_from, account_recent=recent,
        prior_pushes=prior.get(_ref_key(m, fields["invoice_number"]), []), history_read=entered,
        layers=automation.layers(fields, direction=direction, remembered=remembered,
                                 history=(decision.get("history") or {}).get("account")))
    verdict = automation.classify(facts)
    answer = answer_values(doc)
    scored = score(answer, payload_values(payload, book, fields)) if payload else {}
    if verdict.cls == "C":
        memory.learn(decision["needed"], answer)
    prior.setdefault(_ref_key(m, fields["invoice_number"]), []).append(
        {"direction": direction, "party_tax_id": doc["party_tax"], "ref_no": fields["invoice_number"],
         "total": str(doc["total"])})
    period = day.strftime("%Y-%m")
    return {"company": book["company"], "book": book["key"], "docnum": doc["docnum"], "direction": direction,
            "period": period, "entered": entered.isoformat(), "class": verdict.cls, "reasons": list(verdict.reasons),
            "items": dict(verdict.items), "needed": [item.get("kind") for item in decision["needed"]],
            "outside": "" if not (book["period"][0] and book["period"][1]) else
            "after" if book.get("next_end") and day > book["next_end"] else
            "next_year" if day > book["period"][1] else "before" if day < book["period"][0] else "",
            "posting_from": posting_from,
            "prepare": reason, "sources": {key: (payload or {}).get(key) for key in ("doctype_src", "account_source",
                                                                                      "item_src", "doctype")},
            "scored": scored, "correct": bool(scored) and all(value in ("ok", "n/a") for value in scored.values()),
            "correct_eval": evaluable_correct(direction, scored),
            "vat_direction_only": verdict.cls == "B" and list(verdict.reasons) == ["judged:vat"]}


def _ref_key(m, number: Any) -> str:
    """已推记录按单号取（生产 `push.application.assess._prior` 按同单号查推送记录）。"""
    return m["automation"].fit_ref_no(number).upper()


def eligible(doc: Mapping[str, Any]) -> Optional[str]:
    """这张答案单能不能拿来回放：不能的回原因（作废、不是现 / 赊单据、没有日期、金额为 0、Pearnly 写的）。"""
    if doc["cancelled"]:
        return "cancelled"
    if doc["rectyp"] not in RECTYP_PAYMENT:
        return f"rectyp_{doc['rectyp'] or 'none'}"
    if not doc["date"]:
        return "no_date"
    if Decimal(doc["total"]) <= 0:
        return "zero_amount"
    if doc["pearnly"]:
        return "pearnly"
    return None


def _covers(book: Mapping[str, Any], day: dt.date) -> bool:
    start, end = book["period"]
    return bool(start and end) and start <= day <= end


def _covers_next(book: Mapping[str, Any], day: dt.date) -> bool:
    start, end = book["period"]
    following = book.get("next_end")
    return bool(start and end and following) and end < day <= following


def owners(books: Sequence[Mapping[str, Any]]) -> Dict[Tuple, str]:
    """同一张单在几份账套里都有（Express 新年度账套是上一年复制过来的，带着往年的单；还有备份副本）：只在一份里回放。
    键 (方向, 单号, 日期, 对方码, 含税额) → 回放它的账套：本年期间盖住单据日期的那份（ISPRD 的优先、再取单多的）；
    都盖不住（次年段、往年段）取期间最晚的那份。"""
    found: Dict[Tuple, List[Mapping[str, Any]]] = collections.defaultdict(list)
    for book in books:
        for doc in book["docs"]:
            if doc["date"]:
                found[doc_key(doc)].append(book)
    out = {}
    for key, holders in found.items():
        day = key[2]
        # 同推送按日期选账套（`identity.for_day`）：本年期间盖住的优先，没有再取次年段含这天的旧账套
        covering = [book for book in holders if _covers(book, day)] or \
            [book for book in holders if _covers_next(book, day)]
        pool = covering or holders
        pool = sorted(pool, key=lambda book: (book.get("period_source") == "ISPRD", len(book["docs"]),
                                              book["period"][0] or dt.date.min, book["key"]), reverse=True)
        out[key] = pool[0]["key"]
    return out


def doc_key(doc: Mapping[str, Any]) -> Tuple:
    return (doc["direction"], doc["docnum"], doc["date"], doc["party_code"], str(doc["total"]))


def judge_company(m, books: Sequence[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], collections.Counter]:
    """一家公司（各年度账套）按单据录入日顺序判；每份账套各自一份账套记忆与已推记录。同一张单只在一份账套里回放
    （`owners`）。"""
    catalog = [account_row(book) for book in books]
    out, skipped = [], collections.Counter()
    queue = []
    owner = owners(books)
    for book in books:
        for doc in book["docs"]:
            why = eligible(doc)
            if not why and owner.get(doc_key(doc)) != book["key"]:
                why = "copy_in_other_book"
            if why:
                skipped[why] += 1
                continue
            queue.append((entered_on(doc), doc["date"], doc["docnum"], book, doc))
    queue.sort(key=lambda item: (item[0], item[1], item[2]))
    memories = collections.defaultdict(Memory)
    priors = collections.defaultdict(dict)
    purchases = {book["key"]: history_index(book["docs"], m["choices"].norm_name) for book in books}
    company, shaped = CompanyChoice(), shaped_books(m, books)  # 语料一家公司一个目录：按公司选的入账方式一家一份
    for _entered, _day, _number, book, doc in queue:
        out.append(judge_document(m, doc, book, memories[book["key"]], priors[book["key"]], catalog,
                                  purchases[book["key"]], company, shaped))
    return out, skipped


# ── 档位时间线（三套阈值并排） ───────────────────────────────────────────────────

THRESHOLD_SETS = {
    "A": None,  # 建议 A = `companies.automation_rules.THRESHOLDS`（用户 10-16 定）
    "B": {"up_l1": {"periods": 2, "a_min": 20, "a_rate": "0.01"},
          "up_l2": {"periods": 3, "auto_rate": "0.005", "b_min": 20, "b_rate": "0.01"},
          "down": {"auto_changed_min": 2, "auto_rate": "0.01"}},
    "C": {"up_l1": {"periods": 1, "a_min": 10, "a_rate": "0.02"},
          "up_l2": {"periods": 1, "auto_rate": "0.01", "b_min": 0, "b_rate": "1.0"},
          "down": {"auto_changed_min": 3, "auto_rate": "0.02"}},
}


def _row_ok(row: Mapping[str, Any]) -> bool:
    """推出去算不算对（回读里不被改）：本层能核的项全对（旧存档没有 ``correct_eval`` 时按全部项）。"""
    return bool(row.get("correct_eval", row.get("correct")))


def readback_day(m, period: str) -> dt.date:
    """合格回读：这一期 ภ.พ.30 截止日（电子申报有延期取电子的）之后一天发起（`companies.automation._deadline` 同口径）。"""
    found = m["tax_periods"].deadlines("pp30", int(period[:4]), int(period[5:7]))
    return (found.get("efiling") or found["paper"]) + dt.timedelta(days=1)


def timeline(m, rows: Sequence[Mapping[str, Any]], thresholds: Mapping[str, Any], *, trigger: str = "upload",
             b_at_l2: Optional[bool] = None) -> Dict[str, Any]:
    """一家公司按录入日走一遍档位：起点 L0；每期回读（截止日后一天）用 `automation_rules.tally` / `evaluate` 评一次；
    推错的单（反事实计分不全对）在回读里就是「被改」。每张单按 `push.automation.rules.action` 定怎么办：回读不旧按最近一次
    评过的期算；定时任务 A 类在 L0 的基线按 `automation_rules.baseline`（新客户照推、之后看证据）。回 ``{"changes": [...],
    "final", "auto", "proposals", "stopped", "auto_wrong"}``。"""
    tier_rules, rules = m["tier_rules"], m["automation_rules"]
    b_at_l2 = tier_rules.B_AT_L2 if b_at_l2 is None else b_at_l2
    tier, since, reason_now, last_read = tier_rules.L0, None, "", ""
    periods = sorted({row["period"] for row in rows})
    evaluations = sorted((readback_day(m, period), period) for period in periods)
    stats: Dict[str, Dict[str, int]] = {}
    covered: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    changes, counts = [], collections.Counter()
    ordered = sorted(rows, key=lambda row: (row["entered"], row["period"], row["docnum"]))
    pending = list(evaluations)
    cursor = 0

    def evaluate_until(day: dt.date):
        nonlocal tier, since, reason_now, last_read
        while pending and pending[0][0] <= day:
            at, period = pending.pop(0)
            stats[period] = rules.tally(covered.get(period, []))
            last_read = max(last_read, period)
            new, reason = rules.evaluate(tier, since, stats, thresholds=thresholds, b_at_l2=b_at_l2)
            if new != tier:
                changes.append({"period": period, "at": at.isoformat(), "from": tier, "to": new, "reason": reason})
                tier, since, reason_now = new, at, reason

    while cursor < len(ordered):
        row = ordered[cursor]
        entered = dt.date.fromisoformat(row["entered"])
        evaluate_until(entered)
        baseline = rules.baseline(tier, since, stats, reason_now, thresholds=thresholds)
        action, _why = tier_rules.action(row["class"], tier, trigger, baseline=baseline,
                                         readback=tier_rules.readback_ok(last_read, row["period"]))
        if action == tier_rules.PUSH:
            counts["auto"] += 1
            counts["auto_wrong"] += int(not _row_ok(row))
        elif action == tier_rules.PROPOSE:
            counts["proposals"] += 1
        else:
            counts["stopped"] += 1
        if row["class"] in ("A", "B") and action != tier_rules.STOP:
            covered[row["period"]].append({"automated": action == tier_rules.PUSH, "proposal": action == tier_rules.PROPOSE,
                                           "cls": row["class"], "covered": entered < readback_day(m, row["period"]),
                                           "readback": "" if _row_ok(row) else "changed", "verify": "ok"})
        cursor += 1
    evaluate_until(dt.date.max)
    return {"changes": changes, "final": tier, **{key: counts[key] for key in ("auto", "auto_wrong", "proposals", "stopped")}}


# ── 识别闸层：真识别结果 × 独立金标 ─────────────────────────────────────────────

# 第 2 条里属于识别侧与原文核对的原因码（日期、账套、分店、重复等属判定层，这一层不判）
GATE_REASONS = ("review_flag", "review_reasons", "image", "text_missing", "text_total", "text_date", "text_tax",
                "amount_inferred", "amount_math", "party_tax", "void", "split", "evidence")
GATE_FIELDS = ("invoice_number", "date", "party_tax", "party_branch", "subtotal", "vat", "total")
SALES_CATEGORIES = ("销项发票",)


def _alnum(value: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def _number_digits(value: Any) -> str:
    text = _alnum(value)
    return text[len(re.match(r"^[A-Z]*", text).group(0)):].lstrip("0")


def compare_gold(fields: Mapping[str, Any], gold: Mapping[str, Any], direction: str) -> Dict[str, str]:
    """识别出的一张票与金标逐字段：ok / wrong / prefix_only（单号只差字母前缀，不算错）/ n/a（金标里没印）。"""
    party = "buyer" if direction == "sales" else "seller"
    out = {}
    want, got = gold.get("invoice_number"), fields.get("invoice_number")
    if not want:
        out["invoice_number"] = "n/a"
    elif _alnum(want) == _alnum(got):
        out["invoice_number"] = "ok"
    elif _number_digits(want) and _number_digits(want) == _number_digits(got):
        out["invoice_number"] = "prefix_only"
    else:
        out["invoice_number"] = "wrong"
    want = gold.get("date")
    out["date"] = "n/a" if not want else "ok" if str(fields.get("date") or "")[:10] == want else "wrong"
    want = digits(gold.get(f"{party}_tax"))
    out["party_tax"] = "n/a" if not want else "ok" if digits(fields.get(f"{party}_tax")) == want else "wrong"
    want = gold.get(f"{party}_branch")
    out["party_branch"] = "n/a" if not want else "ok" if branch5(fields.get(f"{party}_branch")) == branch5(want) \
        else "wrong"
    for key, gold_key in (("subtotal", "subtotal"), ("vat", "vat"), ("total", "total")):
        want = gold.get(gold_key)
        got = fields.get("total_amount" if key == "total" else key)
        if want in (None, ""):
            out[key] = "n/a"
        else:
            try:
                same = abs(Decimal(str(want)) - Decimal(str(got if got not in (None, "") else "nan"))) <= Decimal("0.01")
            except (InvalidOperation, ValueError):
                same = False
            out[key] = "ok" if same else "wrong"
    return out


def pair_gold(invoice: Mapping[str, Any], golds: Sequence[Mapping[str, Any]]) -> Optional[int]:
    """识别出的一张票配金标里的哪一张：同页优先，再按含税额、单号；配不上回 None。"""
    fields = invoice.get("fields") or {}
    best, best_score = None, 0
    for index, gold in enumerate(golds):
        points = 0
        if gold.get("page") == invoice.get("page"):
            points += 1
        try:
            if gold.get("total") and abs(Decimal(str(gold["total"])) - Decimal(str(fields.get("total_amount")))) <= Decimal("0.01"):
                points += 2
        except (InvalidOperation, ValueError):
            pass
        if gold.get("invoice_number") and _number_digits(gold["invoice_number"]) == _number_digits(fields.get("invoice_number")):
            points += 2
        if points > best_score:
            best, best_score = index, points
    return best if best_score >= 2 else None


def pdf_texts(path: Path) -> Optional[List[str]]:
    """原件 PDF 每页的文字层（与生产 `history.text_layer` 同样用 pypdf）；不是 PDF、读不出回 None。"""
    path = refuse_lan(path)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        return None
    try:
        import pypdf

        from pearnly_cowork.history.pdf_text import page_text  # 同生产：带隐形识别文字层的页给空串

        reader = pypdf.PdfReader(str(path))
        return [page_text(page) for page in reader.pages]
    except Exception:  # noqa: BLE001 — 读不出就当没有文字层
        return None


def _review_module():
    """`history.review`（复核原因，纯函数）。它经 `ocr.validation.accounting` 导入；那个包的 ``__init__`` 要 pydantic，
    测试机没装时只把包目录挂上、不跑 ``__init__``（用到的 accounting / dates / math 三个文件都不要 pydantic）。"""
    try:
        from pearnly_cowork.history import review
    except ImportError:
        import types

        import pearnly_cowork.ocr as ocr_package

        package = types.ModuleType("pearnly_cowork.ocr.validation")
        package.__path__ = [str(Path(ocr_package.__file__).parent / "validation")]
        sys.modules["pearnly_cowork.ocr.validation"] = package
        for name in [name for name in sys.modules if name.startswith("pearnly_cowork.history.review")]:
            del sys.modules[name]
        from pearnly_cowork.history import review
    return review


def gate_invoice(m, invoice: Mapping[str, Any], *, direction: str, own_tax: str, texts: Optional[List[str]],
                 source_total: int) -> List[str]:
    """一张识别出的票过第 2 条识别侧与原文核对（`automation.checks` 原样调用，只取识别侧的原因码）。"""
    review = _review_module()
    automation = m["automation"]
    fields = dict(invoice.get("fields") or {})
    fields["direction"] = direction
    page = {"fields": fields, "_validation_warnings": list(invoice.get("warnings") or []),
            "_needs_manual_review": bool(invoice.get("review")), "_layer_chain": ["direct"]}
    count = max(1, int(invoice.get("pages") or 1))
    # 跨页的票按原件页数造同样多的页（生产按原件这几页逐页判文字层，审查处理 10-16），后几页只占位
    pages = [page, *({"fields": {}, "_layer_chain": ["direct"]} for _ in range(count - 1))]
    reasons = review.reasons(pages[:1], direction=direction, own_tax=own_tax or None)
    number = int(invoice.get("page") or 1)
    text, page_texts = None, None
    if texts:
        page_texts = [texts[index - 1] for index in range(number, number + count) if 1 <= index <= len(texts)]
        text = "\n".join(page_texts).strip() or None
    facts = automation.Facts(direction=direction, fields=fields, decision={}, payload={"total_amount": fields.get("total_amount")},
                             config={}, today=dt.date.today(), ready=True, pages=pages, review_reasons=reasons, text=text,
                             page_texts=page_texts, source_total=source_total)
    return [code for code in automation.checks(facts) if code in GATE_REASONS]


# ── 子命令 ──────────────────────────────────────────────────────────────────────


def _write_json(path: Path, value: Any) -> None:
    path = refuse_lan(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _read_tsv(path: Path) -> List[Dict[str, str]]:
    with open(refuse_lan(path), encoding="utf-8-sig", newline="") as handle:
        return [{key.strip(): (value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(handle, delimiter="\t")]


def _gold(gold_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Dict[str, str]]]:
    gold = json.loads(refuse_lan(gold_dir / "金标" / "gold-final.json").read_text(encoding="utf-8"))
    sample = {row["编号"]: row for row in _read_tsv(gold_dir / "样本.tsv")}
    return gold, sample


def cmd_inventory(args) -> int:
    corpus, out = refuse_lan(args.corpus), refuse_lan(args.out)
    books, coverage = [], collections.Counter()
    calendars = fiscal_calendars(Path(args.express) if args.express else None,
                                 [corpus / "_脚本" / name for name in ("expsets.tsv", "expsets7.tsv")])
    loaded = collections.defaultdict(list)
    for company, tax, folder in answer_books(corpus, args.company):
        loaded[company].append((folder, load_book(folder, company=company, own_tax=tax, key=folder.name,
                                                  calendar=calendars.get(folder.name.upper()))))
    for company, pairs in sorted(loaded.items()):
        owner = owners([book for _folder, book in pairs])
        for folder, book in pairs:
            _inventory_book(company, folder, book, owner, books, coverage)
    _inventory_real(args, books, coverage, out)
    return 0


def _inventory_book(company, folder, book, owner, books, coverage) -> None:
    """一份账套的盘点行（只数这份账套「拥有」的单，见 `owners`）。"""
    tables = folder / "原表"
    present = [name for name in ANSWER_TABLES if (tables / f"{name}.DBF").is_file()]
    docs = book["docs"]
    usable = [doc for doc in docs if not eligible(doc) and owner.get(doc_key(doc)) == book["key"]]
    rectyp = collections.Counter(f"{doc['direction']}:{doc['rectyp'] or '-'}" for doc in docs if not doc["cancelled"])
    for doc in usable:
        coverage[(company, doc["date"].strftime("%Y-%m"))] += 1
    books.append({
        "company": company, "book": folder.name, "tables": present,
        "fields": {name: [field for field in ("ACCNUM", "SUPCOD", "CUSCOD", "RECTYP", "CREDAT", "USERID")
                          if field in dbf_fields(tables / f"{name}.DBF")]
                   for name in ("APTRN", "ARTRN", "GLJNL", "GLJNLIT")},
        "period": [str(day) for day in book["period"]], "period_source": book["period_source"],
        "in_period": sum(1 for doc in usable if book["period"][0] and book["period"][0] <= doc["date"] <= book["period"][1]),
        "docs": len(docs), "usable": len(usable),
        "purchase": sum(1 for doc in usable if doc["direction"] == "purchase"),
        "sales": sum(1 for doc in usable if doc["direction"] == "sales"),
        "with_voucher": sum(1 for doc in usable if doc["voucher"]),
        "with_account": sum(1 for doc in usable if doc["accounts"]),
        "single_account": sum(1 for doc in usable if len(doc["accounts"]) == 1),
        "with_party_code": sum(1 for doc in usable if doc["party_code"]),
        "with_party_tax13": sum(1 for doc in usable if len(doc["party_tax"]) == 13),
        "with_register": sum(1 for doc in usable if doc["register"]),
        "with_entered": sum(1 for doc in usable if isinstance(doc["entered"], dt.date)),
        "rectyp": dict(rectyp), "perpetual": book["perpetual"], "fingerprint": book["fingerprint"],
        "suppliers": len(book["suppliers"]), "customers": len(book["customers"]), "accounts": len(book["accounts"]),
    })
    print(f"{company} {folder.name}: {len(usable)}/{len(docs)}", flush=True)


def _inventory_real(args, books, coverage, out) -> None:
    real = collections.Counter()
    if args.gold:
        gold, sample = _gold(Path(args.gold))
        for number, item in gold.items():
            row = sample.get(number) or {}
            for invoice in item.get("invoices") or []:
                real[(row.get("公司") or "?", str(invoice.get("date") or "")[:7] or "?", row.get("形态") or "?",
                      row.get("有答案") or "?")] += 1
    _write_json(out / "inventory.json", {"books": books, "periods": [
        {"company": company, "period": period, "docs": count} for (company, period), count in sorted(coverage.items())],
        "real_results": [{"company": c, "period": p, "form": f, "answers": a, "invoices": n}
                         for (c, p, f, a), n in sorted(real.items())]})
    print(f"books {len(books)}", flush=True)


def cmd_judge(args) -> int:
    m = _modules()
    corpus, out = refuse_lan(args.corpus), refuse_lan(args.out)
    by_company = collections.defaultdict(list)
    calendars = fiscal_calendars(Path(args.express) if args.express else None,
                                 [corpus / "_脚本" / name for name in ("expsets.tsv", "expsets7.tsv")])
    for company, tax, folder in answer_books(corpus, args.company):
        by_company[company].append(load_book(folder, company=company, own_tax=tax, key=folder.name,
                                             calendar=calendars.get(folder.name.upper())))
    for company, books in sorted(by_company.items()):
        judged, why = judge_company(m, books)
        base = m["automation_rules"].THRESHOLDS
        tiers = {name: timeline(m, judged, value or base) for name, value in THRESHOLD_SETS.items()}
        tiers["A_task"] = timeline(m, judged, base, trigger="task")
        # 一家一个文件（可以分两三路并行跑），`report` 合并
        _write_json(out / "judge" / f"{company.replace('/', '_')}.json",
                    {"rows": judged, "skipped": {company: dict(why)}, "tiers": {company: tiers}})
        print(f"{company}: {len(judged)} docs, " + ", ".join(
            f"{key} {sum(1 for row in judged if row['class'] == key)}" for key in "ABC"), flush=True)
    return 0


def load_judge(out: Path) -> Optional[Dict[str, Any]]:
    files = sorted((out / "judge").glob("*.json")) if (out / "judge").is_dir() else []
    if not files:
        return None
    merged: Dict[str, Any] = {"rows": [], "skipped": {}, "tiers": {}}
    for path in files:
        part = json.loads(path.read_text(encoding="utf-8"))
        merged["rows"].extend(part["rows"])
        merged["skipped"].update(part["skipped"])
        merged["tiers"].update(part["tiers"])
    return merged


def cmd_gate(args) -> int:
    m = _modules()
    gold_dir, out = refuse_lan(args.gold), refuse_lan(args.out)
    gold, sample = _gold(gold_dir)
    corpus_root = refuse_lan(args.originals)
    taxes = {}
    # 本方税号（复核原因要比本方）：必填，否则复核原因比生产少、复跑出不来存档结果（审查处理 10-16）
    for row in _read_tsv(Path(args.manifest)):
        if row.get("税号"):
            taxes.setdefault(row.get("公司"), row["税号"])
    flags = {}
    for line in open(refuse_lan(gold_dir / "金标" / args.flags), encoding="utf-8"):
        record = json.loads(line)
        for invoice in record.get("invoices") or []:
            flags[(record["id"], invoice.get("page"))] = invoice
    rows = []
    for line in open(refuse_lan(gold_dir / "金标" / args.records), encoding="utf-8"):
        record = json.loads(line)
        number = record["id"]
        row, truth = sample.get(number) or {}, gold.get(number) or {}
        direction = "sales" if row.get("分类") in SALES_CATEGORIES else "purchase"
        texts = pdf_texts(corpus_root / row.get("路径", "")) if row.get("路径") else None
        golds = list(truth.get("invoices") or [])
        invoices = list(record.get("invoices") or [])
        for invoice in invoices:
            flagged = flags.get((number, invoice.get("page"))) or {}
            merged = {**invoice, "review": flagged.get("review", record.get("review")),
                      "warnings": flagged.get("warnings") or []}
            reasons = gate_invoice(m, merged, direction=direction, own_tax=taxes.get(row.get("公司"), ""), texts=texts,
                                   source_total=len(invoices))
            index = pair_gold(invoice, golds) if truth.get("entry") == "enter" else None
            result = compare_gold(invoice.get("fields") or {}, golds[index], direction) if index is not None else {}
            uncertain = [path for path in truth.get("uncertain") or [] if path.startswith(f"invoices[{index}]")] \
                if index is not None else []
            rows.append({"id": number, "form": row.get("形态"), "company": row.get("公司"), "direction": direction,
                         "page": invoice.get("page"), "text_pdf": "image" not in reasons, "reasons": reasons,
                         "passed": not reasons, "entry": truth.get("entry"), "paired": index is not None,
                         "result": result, "uncertain": uncertain,
                         "wrong": [key for key, value in result.items() if value == "wrong"]})
    _write_json(out / "gate.json", {"rows": rows, "records": args.records, "flags": args.flags,
                                    "manifest": str(args.manifest)})
    print(f"gate rows {len(rows)}", flush=True)
    return 0


# C 类按「主要原因」归一类（按这个顺序取第一个命中的），方便按类修
CAUSES = (
    ("posting", "商品单没声明入账类型（永续 / 混合库存账套交人）", lambda row: str(row.get("prepare") or "").startswith("posting_needs_review")),
    ("needed", "第一次见、要人选（对方档 / 科目 / 现赊）", lambda row: bool(row.get("needed"))),
    ("next_year", "单据在账套次年段、新年度账套已建（该推往新账套）",
     lambda row: row.get("outside") == "next_year" and bool({"period", "book"} & set(row["reasons"]))),
    ("after", "单据晚于账套次年段", lambda row: row.get("outside") == "after"),
    ("before", "单据早于账套本年", lambda row: row.get("outside") == "before"),
    ("stale", "录入晚于单据日期 60 天以上", lambda row: "date_stale" in row["reasons"]),
    ("number", "答案没有对方单号", lambda row: "invoice_no_missing" in str(row.get("prepare") or "")),
    ("party", "对方档定不下（无 13 位税号 / 同税号多档 / 分店不符）", lambda row: "item:party" in row["reasons"]),
    ("party_tax", "对方税号校验位不对", lambda row: "party_tax" in row["reasons"]),
    ("own_tax", "本方税号对不上推往账套", lambda row: "own_tax" in row["reasons"]),
    ("day_month", "日、月都 ≤12 读法两可", lambda row: "day_month" in row["reasons"]),
    ("duplicate", "同账套已推过同对方同单号同金额", lambda row: "duplicate_number" in row["reasons"]),
    ("preflight", "推送预检不过（其他）", lambda row: "not_ready" in row["reasons"] or "blocked" in row["reasons"]),
)


def primary_cause(row: Mapping[str, Any]) -> str:
    for key, _label, test in CAUSES:
        if test(row):
            return key
    return "other"


def _accuracy(rows: Sequence[Mapping[str, Any]], key: str = "correct") -> Dict[str, Any]:
    n = len(rows)
    ok = sum(1 for row in rows if (_row_ok(row) if key == "correct_eval" else row[key]))
    lower, upper = wilson(ok, n)
    return {"n": n, "ok": ok, "point": ok / n if n else None, "lower": lower, "upper": upper, "bar": passes_bar(ok, n)}


def summarize_judge(data: Mapping[str, Any]) -> Dict[str, Any]:
    rows = data["rows"]
    by_class = collections.Counter(row["class"] for row in rows)
    a_rows = [row for row in rows if row["class"] == "A"]
    b_rows = [row for row in rows if row["class"] == "B"]
    wrong_items = collections.Counter(f"{item}:{value.split(':', 1)[1]}" if ":" in value else item
                                      for row in a_rows + b_rows for item, value in row["scored"].items()
                                      if value.startswith("wrong"))
    reasons = collections.Counter(reason for row in rows if row["class"] == "C" for reason in row["reasons"])
    causes = collections.Counter(primary_cause(row) for row in rows if row["class"] == "C")
    judged = collections.Counter(reason for row in b_rows for reason in row["reasons"])
    companies = {}
    for company in sorted({row["company"] for row in rows}):
        mine = [row for row in rows if row["company"] == company]
        tiers = data["tiers"].get(company) or {}
        companies[company] = {
            "docs": len(mine), "A": sum(1 for row in mine if row["class"] == "A"),
            "B": sum(1 for row in mine if row["class"] == "B"), "C": sum(1 for row in mine if row["class"] == "C"),
            "periods": len({row["period"] for row in mine}),
            "cause": (collections.Counter(primary_cause(row) for row in mine if row["class"] == "C").most_common(1)
                      or [("", 0)])[0][0],
            "A_acc": _accuracy([row for row in mine if row["class"] == "A"], "correct_eval"),
            "B_acc": _accuracy([row for row in mine if row["class"] == "B"], "correct_eval"),
            "tiers": {name: {"changes": value["changes"], "final": value["final"], "auto": value["auto"],
                             "auto_wrong": value["auto_wrong"], "proposals": value["proposals"],
                             "stopped": value["stopped"]} for name, value in tiers.items()},
        }
    sources = {"account": "account_source", "payment": "doctype_src", "vat": "item_src"}
    items: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: collections.Counter())
    for row in a_rows + b_rows:
        for item, value in row["scored"].items():
            if value == "n/a":
                continue
            source = (row.get("sources") or {}).get(sources.get(item, "")) or "-"
            key = f"{row['direction']}|{item}|{source}"
            items[key]["n"] += 1
            items[key]["wrong"] += int(value != "ok")
    without_payment = [dict(row, correct=all(value in ("ok", "n/a") for item, value in row["scored"].items()
                                             if item != "payment") and bool(row["scored"])) for row in a_rows]
    sales_payment = [row for row in a_rows + b_rows if row["direction"] == "sales"
                     and row["scored"].get("payment", "n/a") != "n/a"]
    return {"docs": len(rows), "classes": dict(by_class), "A": _accuracy(a_rows), "B": _accuracy(b_rows),
            "A_eval": _accuracy(a_rows, "correct_eval"), "B_eval": _accuracy(b_rows, "correct_eval"),
            "AB_eval": _accuracy(a_rows + b_rows, "correct_eval"),
            "sales_payment": {"n": len(sales_payment),
                              "wrong": sum(1 for row in sales_payment if row["scored"]["payment"] != "ok"),
                              "by_doctype": dict(collections.Counter(
                                  f"{row['class']}:{(row.get('sources') or {}).get('doctype') or '-'}:"
                                  f"{'ok' if row['scored']['payment'] == 'ok' else 'wrong'}" for row in sales_payment))},
            "A_without_payment": _accuracy(without_payment),
            "item_errors": {key: dict(value) for key, value in sorted(items.items(), key=lambda kv: -kv[1]["wrong"])},
            "AB": _accuracy(a_rows + b_rows), "wrong_items": dict(wrong_items.most_common()),
            "c_reasons": dict(reasons.most_common(30)), "c_causes": dict(causes.most_common()),
            "b_judged": dict(judged.most_common()),
            "b_vat_direction_only": sum(1 for row in b_rows if row.get("vat_direction_only")),
            "by_direction": {way: {"docs": sum(1 for row in rows if row["direction"] == way),
                                   **{key: sum(1 for row in rows if row["direction"] == way and row["class"] == key)
                                      for key in "ABC"},
                                   "A_acc": _accuracy([row for row in a_rows if row["direction"] == way]),
                                   "B_acc": _accuracy([row for row in b_rows if row["direction"] == way]),
                                   "A_eval": _accuracy([row for row in a_rows if row["direction"] == way],
                                                       "correct_eval"),
                                   "B_eval": _accuracy([row for row in b_rows if row["direction"] == way],
                                                       "correct_eval")}
                             for way in ("purchase", "sales")},
            "skipped": {key: sum(value.get(key, 0) for value in data["skipped"].values())
                        for key in sorted({k for value in data["skipped"].values() for k in value})},
            "companies": companies}


def summarize_gate(data: Mapping[str, Any]) -> Dict[str, Any]:
    rows = data["rows"]

    def part(selected):
        passed = [row for row in selected if row["passed"]]
        scored = [row for row in passed if row["paired"]]
        wrong = [row for row in scored if row["wrong"]]
        disputed = [row for row in wrong if row["uncertain"]]
        not_invoice = [row for row in passed if row["entry"] == "not_invoice"]
        unpaired = [row for row in passed if row["entry"] == "enter" and not row["paired"]]
        # 过了闸却配不上金标的：识别错到对不上也会这样，并进分母按错算（保守，审查处理 10-16）
        n = len(scored) + len(not_invoice) + len(unpaired)
        ok = len(scored) - len(wrong)
        lower, upper = wilson(ok, n)
        return {"invoices": len(selected), "passed": len(passed), "scored": len(scored), "wrong": len(wrong),
                "disputed": len(disputed), "not_invoice_passed": len(not_invoice), "unpaired_passed": len(unpaired),
                "n": n, "ok": ok, "point": ok / n if n else None, "lower": lower, "upper": upper,
                "fields": dict(collections.Counter(field for row in wrong for field in row["wrong"]).most_common()),
                "stopped_by": dict(collections.Counter(reason for row in selected for reason in row["reasons"]).most_common())}

    text_rows = [row for row in rows if row["text_pdf"]]
    image_rows = [row for row in rows if not row["text_pdf"]]
    return {"all": part(rows), "text_pdf": part(text_rows), "image": part(image_rows),
            "by_form": {form: part([row for row in rows if row["form"] == form]) for form in sorted({row["form"] or "?" for row in rows})}}


def render(judge: Optional[Mapping[str, Any]], gate: Optional[Mapping[str, Any]],
           inventory: Optional[Mapping[str, Any]]) -> str:
    lines = ["# 第 3 期两层回放报表", ""]
    if inventory:
        books = inventory["books"]
        usable = sum(book["usable"] for book in books)
        lines += ["## 第 0 步 盘点", "",
                  f"- 有答案的公司 {len({book['company'] for book in books})} 家、账套 {len(books)} 份；可回放单据 {usable} 张"
                  f"（进项 {sum(b['purchase'] for b in books)}、销项 {sum(b['sales'] for b in books)}）。",
                  f"- 有凭证 {sum(b['with_voucher'] for b in books)}、有科目 {sum(b['with_account'] for b in books)}"
                  f"（只一个科目 {sum(b['single_account'] for b in books)}）、有对方码 {sum(b['with_party_code'] for b in books)}、"
                  f"对方 13 位税号 {sum(b['with_party_tax13'] for b in books)}、有税登记 {sum(b['with_register'] for b in books)}、"
                  f"凭证有录入日 {sum(b['with_entered'] for b in books)}。",
                  f"- 每份账套都有原表：{sum(1 for b in books if set(ANSWER_TABLES) - {'STMAS', 'STCRD', 'ISTAB'} <= set(b['tables']))}"
                  f"/{len(books)} 份齐 ISINFO / APTRN / ARTRN / ISVAT / GLJNL / GLJNLIT / APMAS / ARMAS / GLACC。", ""]
        real = inventory.get("real_results") or []
        with_answer = [row for row in real if row["answers"] == "是"]
        cells = collections.Counter()
        for row in with_answer:
            cells[(row["company"], row["period"])] += row["invoices"]
        lines += [f"- 现成真识别结果（金标 3.8 一轮）：{sum(r['invoices'] for r in real)} 张票，其中有答案的公司 "
                  f"{sum(r['invoices'] for r in with_answer)} 张（电子 PDF "
                  f"{sum(r['invoices'] for r in with_answer if r['form'] == '电子PDF')} 张），分在 {len(cells)} 个「公司 × 期间」；"
                  f"同一家同一期最多 {max(cells.values(), default=0)} 张。", ""]
    if judge:
        s = judge
        lines += ["## 判定层（识别全对的票面，反事实计分）", "",
                  f"- 回放 {s['docs']} 张：A {s['classes'].get('A', 0)}、B {s['classes'].get('B', 0)}、C {s['classes'].get('C', 0)}"
                  f"（能自动的占比 A {pct(s['classes'].get('A', 0) / s['docs'] if s['docs'] else None)}、A+B "
                  f"{pct((s['classes'].get('A', 0) + s['classes'].get('B', 0)) / s['docs'] if s['docs'] else None)}）。",
                  f"- 准确率（不计本层核不了的销项现 / 赊）：A 类 {pct(s['A_eval']['point'])}（95% 下限 "
                  f"{pct(s['A_eval']['lower'])}，n={s['A_eval']['n']}，{'达标' if s['A_eval']['bar'] else '未达标'}）；B 类 "
                  f"{pct(s['B_eval']['point'])}（下限 {pct(s['B_eval']['lower'])}，n={s['B_eval']['n']}，"
                  f"{'达标' if s['B_eval']['bar'] else '未达标'}）。",
                  f"- 原值（四项全计）：A 类 {pct(s['A']['point'])}（下限 {pct(s['A']['lower'])}）；B 类 {pct(s['B']['point'])}"
                  f"（下限 {pct(s['B']['lower'])}）。",
                  f"- 销项现 / 赊（本层核不了，单列）：计分 {s['sales_payment']['n']}、与答案不同 {s['sales_payment']['wrong']}；"
                  "按 类别:单据类型:结果 " + "、".join(f"{k} {v}" for k, v in sorted(s['sales_payment']['by_doctype'].items())) + "。",
                  f"- B 类里只因「按方向判抵进项」（`judge_direction`）成 B 的 {s['b_vat_direction_only']} 张。", "",
                  "| 方向 | 张数 | A | B | C | A 准确率（下限） | B 准确率（下限） | A 原值 | B 原值 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for way, row in s["by_direction"].items():
            lines.append(f"| {way} | {row['docs']} | {row['A']} | {row['B']} | {row['C']} | {pct(row['A_eval']['point'])}"
                         f"（{pct(row['A_eval']['lower'])}） | {pct(row['B_eval']['point'])}（{pct(row['B_eval']['lower'])}） | "
                         f"{pct(row['A_acc']['point'])} | {pct(row['B_acc']['point'])} |")
        lines += ["", f"不算现 / 赊这一项时 A 类准确率 {pct(s['A_without_payment']['point'])}（下限 "
                      f"{pct(s['A_without_payment']['lower'])}）。", "",
                  "A / B 各项按来源的错率（方向 | 项 | 来源）：", "", "| 方向 | 项 | 来源 | 计分 | 错 | 错率 |",
                  "|---|---|---|---|---|---|",
                  *[f"| {key.split('|')[0]} | {key.split('|')[1]} | {key.split('|')[2]} | {value['n']} | {value['wrong']} | "
                    f"{pct(value['wrong'] / value['n'] if value['n'] else None)} |"
                    for key, value in list(s["item_errors"].items())[:16]], "",
                  "A / B 推错的项：" + "、".join(f"{key} {value}" for key, value in list(s["wrong_items"].items())[:12]), "",
                  "C 类按主要原因（每张只算一类）：", "", "| 主要原因 | 张数 |", "|---|---|",
                  *[f"| {dict((k, l) for k, l, _t in CAUSES).get(key, '其他')} | {value} |"
                    for key, value in s["c_causes"].items()], "",
                  "C 类全部原因码（一张可多个，前 15）：" + "、".join(f"{key} {value}" for key, value in
                                                         list(s["c_reasons"].items())[:15]), "",
                  "B 类判断项：" + "、".join(f"{key} {value}" for key, value in s["b_judged"].items()), "",
                  "不回放的答案行：" + "、".join(f"{key} {value}" for key, value in s["skipped"].items()), "",
                  "### 按家（A / B / C、准确率、三套阈值的档位）", "",
                  "| 公司 | 张数 | 期数 | A | B | C | C 主因 | A 准确率（下限） | 建议 A 升档 | B 稳 升档 | C 快 升档 | 建议 A 自动推（错） |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for company, row in s["companies"].items():
            def ups(name):
                tier = row["tiers"].get(name) or {}
                changes = tier.get("changes") or []
                return " ".join(f"{c['period']}→{c['to']}" for c in changes) or "一直 L0"
            auto = row["tiers"].get("A") or {}
            lines.append(f"| {company} | {row['docs']} | {row['periods']} | {row['A']} | {row['B']} | {row['C']} | "
                         f"{row['cause']} | "
                         f"{pct(row['A_acc']['point'])}（{pct(row['A_acc']['lower'])}） | {ups('A')} | {ups('B')} | {ups('C')} | "
                         f"{auto.get('auto', 0)}（{auto.get('auto_wrong', 0)}） |")
        totals = {name: {key: sum((row["tiers"].get(name) or {}).get(key, 0) for row in s["companies"].values())
                         for key in ("auto", "auto_wrong", "proposals", "stopped")} for name in THRESHOLD_SETS}
        reached = {name: sum(1 for row in s["companies"].values()
                             if any(c["to"] in ("L1", "L2") for c in (row["tiers"].get(name) or {}).get("changes") or []))
                   for name in THRESHOLD_SETS}
        downs = {name: sum(1 for row in s["companies"].values() for c in (row["tiers"].get(name) or {}).get("changes") or []
                           if c["reason"] == "changed") for name in THRESHOLD_SETS}
        lines += ["", "### 三套阈值并排（网页上传 / LINE 入口）", "",
                  "| 阈值 | 升到 L1 的公司 | 降档次数 | 自动推 | 其中推错 | 提议 | 停下 |", "|---|---|---|---|---|---|---|"]
        for name in THRESHOLD_SETS:
            label = {"A": "建议 A（已定）", "B": "B 稳", "C": "C 快"}[name]
            total = totals[name]
            lines.append(f"| {label} | {reached[name]} | {downs[name]} | {total['auto']} | {total['auto_wrong']} | "
                         f"{total['proposals']} | {total['stopped']} |")
        task = {key: sum((row["tiers"].get("A_task") or {}).get(key, 0) for row in s["companies"].values())
                for key in ("auto", "auto_wrong", "proposals", "stopped")}
        lines += ["", f"定时任务入口（A 类在 L0 按基线：新客户照推、之后看证据）按建议 A：自动推 {task['auto']}"
                      f"（推错 {task['auto_wrong']}）、提议 {task['proposals']}、停下 {task['stopped']}。"
                      "没有模拟任务默认值（任务里设的现 / 赊、科目），只换了触发方，这一行不作定时任务安不安全的结论。", ""]
    if gate:
        g = gate
        lines += ["## 识别闸层（金标 215 份 × 生产读法 3.8 的真识别结果）", "",
                  "| 票种 | 识别出的票 | 过闸 | 计分 | 闸后仍错 | 其中金标存疑 | 非票据过闸 | 准确率（下限） |",
                  "|---|---|---|---|---|---|---|---|"]
        for label, key in (("文字层 PDF", "text_pdf"), ("图片 / 扫描", "image"), ("合计", "all")):
            row = g[key]
            lines.append(f"| {label} | {row['invoices']} | {row['passed']} | {row['scored']} | {row['wrong']} | "
                         f"{row['disputed']} | {row['not_invoice_passed']} | {pct(row['point'])}（{pct(row['lower'])}） |")
        lines += ["", "闸后仍错的字段：" + ("、".join(f"{k} {v}" for k, v in g["all"]["fields"].items()) or "无"), "",
                  "拦下的原因：" + "、".join(f"{k} {v}" for k, v in g["all"]["stopped_by"].items()), ""]
    if judge and gate:
        a, raw, t = judge["A_eval"], judge["A"], gate["text_pdf"]
        merged = combined(a["ok"], a["n"], t["ok"], t["n"])
        original = combined(raw["ok"], raw["n"], t["ok"], t["n"])
        lines += ["## 合并估计（A 类 × 文字层 PDF 闸后）", "",
                  f"自动推准确率 ≈ {pct(merged['point'])}（95% 区间 {pct(merged['lower'])} ～ {pct(merged['upper'])}；"
                  f"不计销项现 / 赊）；四项全计 ≈ {pct(original['point'])}。", ""]
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    out = refuse_lan(args.out)
    judge = load_judge(out)
    gate = json.loads((out / "gate.json").read_text(encoding="utf-8")) if (out / "gate.json").exists() else None
    inventory = json.loads((out / "inventory.json").read_text(encoding="utf-8")) if (out / "inventory.json").exists() else None
    judge_summary = summarize_judge(judge) if judge else None
    gate_summary = summarize_gate(gate) if gate else None
    _write_json(out / "summary.json", {"judge": judge_summary, "gate": gate_summary})
    (out / "报表.md").write_text(render(judge_summary, gate_summary, inventory), encoding="utf-8")
    if judge:
        with open(out / "明细.tsv", "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["层", "公司", "账套", "单号", "方向", "期间", "录入日", "类别", "原因", "要选", "计分", "全对"])
            for row in judge["rows"]:
                writer.writerow(["判定", row["company"], row["book"], row["docnum"], row["direction"], row["period"],
                                 row["entered"], row["class"], ",".join(row["reasons"]), ",".join(row["needed"]),
                                 json.dumps(row["scored"], ensure_ascii=False), int(row["correct"])])
            for row in (gate or {}).get("rows") or []:
                writer.writerow(["识别闸", row["company"], row["form"], row["id"], row["direction"], "", "",
                                 "过闸" if row["passed"] else "拦下", ",".join(row["reasons"]), "",
                                 json.dumps(row["result"], ensure_ascii=False), int(not row["wrong"])])
    print((out / "报表.md").read_text(encoding="utf-8"))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="da-replay", description="数字会计员第 3 期两层回放（零费用、只读）")
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("--corpus", required=True)
    inventory.add_argument("--gold")
    inventory.add_argument("--company")
    inventory.add_argument("--express")
    inventory.add_argument("--out", required=True)
    judge = sub.add_parser("judge")
    judge.add_argument("--corpus", required=True)
    judge.add_argument("--company")
    judge.add_argument("--express", help="测试机本地 Express 测试账套根（70EXP），取会计期间 ISPRD")
    judge.add_argument("--out", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--gold", required=True)
    gate.add_argument("--originals", required=True, help="样本.tsv 里「路径」的根（语料目录）")
    gate.add_argument("--manifest", required=True, help="06-录入推送/清单.tsv（公司 → 本方税号）")
    gate.add_argument("--records", default="gold-gemini-code.jsonl", help="当前生产代码的识别结果（按记录）")
    gate.add_argument("--flags", default="gold-gemini-rec.jsonl", help="同一轮带复核标记与校验提示的记录")
    gate.add_argument("--out", required=True)
    report = sub.add_parser("report")
    report.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    return {"inventory": cmd_inventory, "judge": cmd_judge, "gate": cmd_gate, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
