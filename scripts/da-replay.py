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

    python3 da-replay.py inventory --corpus /mnt/c/PearnlyStress/语料/06-录入推送 --gold /mnt/c/PearnlyStress/语料/金标-2026-10 --out OUT \
        [--express 70EXP] [--isprd ISPRD导出] [--registry SCCOMP导出]
    python3 da-replay.py judge --corpus ... --out OUT [--company 名称] [--express 70EXP] [--isprd ...] [--registry ...] \
        [--periods strict|estimated] [--tiebreak none,recent_entry,recent_push,ask_once] \
        [--lines none|answer] [--line-names stkdes|opaque] [--company-choice prior|full]
    PYTHONPATH=<含 pypdf 的目录> python3 da-replay.py gate --gold ... --originals /mnt/c/PearnlyStress/语料 \
        --manifest /mnt/c/PearnlyStress/语料/06-录入推送/清单.tsv --out OUT
    python3 da-replay.py report --out OUT

会计期间（10-17）：与 CC 桌面版同口径读 ISPRD（`isprd_window`），来源依次是答案自带、``--isprd`` 只读导出、70EXP 测试账套；
都没有时 judge 默认 ``--periods strict``（同线上读不到 ISPRD：不报期间），``estimated`` 按单据日期估作对照。每张单记下按日期选账套
的情形（`book_pick`），C 类主因按它拆开真两份同期与估算重叠；两份同期时各挑法的选对率见 `tiebreak`（模拟，不改判档）。

商品单与上传日（10-17 第三段，只在回放里量，线上判档不变）：答案的「走库存」只认库存品（STMAS.STKTYP=0）或有库存影响的明细行
（STCRD 按单号分组、按 SEQNUM 排、PEOPLE 防撞号，进销同号再按明细日期分），另记有没有明细；``--lines answer`` 把答案明细造进票面
（``--line-names`` ``stkdes`` 品名照 Express 是乐观上界、``opaque`` 品名打码是「同名查找落空、记忆理想命中」的估计），默认 ``none``
不造；入账方式不造明细时按声明值计分（同第四轮），造了明细按载荷实际会写什么计分（明细过对账闸才写 STCRD），存货逐行计分、逐行
学记忆；每张单记这家供应商之前的 Express 存货历史（`stock_history` + 与线上共用的 `history_rules.posting_settled`）按线上同一判据
`automation.posting_by_history` 定的档（主判定不带这一层：上界；把之前判成 A / B 的单当 CC 推的剔掉另算一次是下界）；模拟的人替公司选入账方式按「第一次交人之前、会因入账方式交人
的单里的多数」（``--company-choice prior``，不到 10 张退到全期多数；全期多数 ``full`` 作对照）。上传日另按「凭证录入日与税期 ภ.พ.30
截止日取早」估一次（`upload_proxy`），只重判日期类核对（``class_alt``），报表在 C 类主因后加一行对照。

账套记忆的证据（10-17 审查处理）：回读按整张记录比（``--readback-scope record``，``account`` 只比科目作对照）；假定每期都在申报
截止日后一天按时做了合格回读。证据窗口同线上（10-17 改取法，`automation.memory_window`）：先跳过凭记忆推出去、还没合格回读核过的
推送，再在剩下能当证据的里取最近的，往回最多看 ``--recent-max`` 张推送（默认同线上 `automation.RECENT_SCAN_MAX` 1000，0 不设上限
作对照）。第五轮及以前的 ``--recent-max 30`` 是旧取法（截最新 30 张再跳过），存档按 ``evidence_window`` 区分。

与第四轮不能直接比：judge 默认 ``--periods strict``、答案的走库存口径、替公司选入账方式的口径、证据的上限与整张记录回读都变了。
第四轮口径的对照组合是 ``--periods estimated --company-choice full --recent-max 0 --readback-scope account``（`--lines none`），
仍不同的只有这一轮的规则本身（记忆三类推广、票面写明的现 / 赊不算证据、对方档取写入码）与答案的走库存口径。

金额一律 Decimal；日期按单据所属会计期间（不看墙上时间）。脚本拒绝 ``\\\\`` 与 ``S:`` 开头的路径（不碰局域网真账），读写都拒。
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import math
import re
import struct
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple

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
# 存货明细（STCRD）读的列：按 DOCNUM 挂单、SEQNUM 排序、PEOPLE（对方码）防撞号；金额取 NETVAL，空取 TRNVAL
STCRD_FIELDS = ("DOCNUM", "SEQNUM", "DOCDAT", "STKCOD", "STKDES", "TRNQTY", "UNITPR", "TRNVAL", "NETVAL", "DISCAMT",
                "PEOPLE")
# 同一天录入（GLJNL.CREDAT）这么多张凭证以上：疑似账套导入或重建（CREDAT 是导入日、不是录入日），盘点与报表单列
BATCH_DAY_DOCS = 200


class Line(NamedTuple):
    """答案一张单的一行存货明细（STCRD）：存货码、品名（STKDES，空取主档名）、数量、单价、行净额（字符串，Decimal 写法）、
    主档类别（``stock`` 库存品 STKTYP=0 / ``non_stock`` / ``unknown`` 主档里没有）、有没有库存影响（`STOCK_IMPACT` 任一非 0）。"""
    code: str
    name: str
    qty: str
    price: str
    amount: str
    kind: str
    moving: bool


def stock_line(line: Line) -> bool:
    """这一行算不算「走库存」（10-17 答案口径）：库存品（STKTYP=0）或有库存影响；非库存品的明细（服务费等挂 STKTYP 3 / 4 / 5）
    不算 —— 与 Pearnly 的 ``stock_item`` 同义。"""
    return line.kind == "stock" or line.moving


def _seq(value: Any) -> int:
    """STCRD.SEQNUM → 行序：字符列（C(3)，``"1"``）取数字；数值列（N 型带小数位，``Decimal("1.00")``）取整，不能拼成 100。"""
    if isinstance(value, Decimal):
        try:
            return int(value)
        except (ValueError, ArithmeticError):
            return 0
    number = re.sub(r"\D", "", str(value or "").split(".")[0])
    return int(number) if number else 0


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


PERIOD_FIELDS = [f"{edge}{index}{slot}" for slot in ("", "NY") for index in range(1, 13) for edge in ("BEG", "END")]
# 账套会计期间从哪来（10-17，按优先级）：答案自带的 ISPRD（与答案同一时刻导出）> ``--isprd`` 只读导出 > 测试机 70EXP 测试账套
# > 按单据日期估（只在 ``--periods estimated`` 时用；strict 同线上读不到 ISPRD：不报期间）
ORIGINS = ("answer", "export", "testcopy", "estimated", "none")


def isprd_window(path: Path) -> Optional[Tuple[Optional[dt.date], Optional[dt.date], Optional[dt.date]]]:
    """一张 ISPRD.DBF → (本年起, 本年止, 次年止)，与 CC 桌面版 `account_probe._fiscal_window` 同口径：BEG / END × 12 期 × 本年、
    次年两段逐期容错读（起止缺一就跳过该期，`fiscal_period._read_periods`），本年起取本年各期起始最小、本年止取最大，次年止取
    次年各期止最大；没有次年段次年止留空（线上没报就是空，不再按「本年止 + 1 年」补）。表不在、一期都读不出回 None。"""
    row = next(dbf_rows(path, PERIOD_FIELDS), None)
    if not row:
        return None
    current, following = [], []
    for slot, bucket in (("", current), ("NY", following)):
        for index in range(1, 13):
            beg, end = row.get(f"BEG{index}{slot}"), row.get(f"END{index}{slot}")
            if isinstance(beg, dt.date) and isinstance(end, dt.date):
                bucket.append((beg, end))
    if not current and not following:
        return None
    return (min(beg for beg, _end in current) if current else None,
            max(end for _beg, end in current) if current else None,
            max(end for _beg, end in following) if following else None)


class Calendars:
    """答案以外的会计期间来源（``--isprd`` 只读导出、70EXP 测试账套），按账套目录名找；同名账套（不同公司）按本方税号分开。
    `find` 回 ((起, 止, 次年止), 来源) 或 None。"""

    def __init__(self):
        self.found: Dict[str, Dict[str, List[Tuple[str, Tuple]]]] = {"export": {}, "testcopy": {}}
        self.refused: set = set()

    def add(self, origin: str, name: str, tax: str, window: Optional[Tuple]) -> None:
        if window:
            self.found[origin].setdefault(name.strip().upper(), []).append((digits(tax), window))

    def find(self, name: str, own_tax: str) -> Optional[Tuple[Tuple, str]]:
        """同税号的优先；没有时只认没写税号、且同名各份期间一样的。写了别的税号的一律不认（``68EXP`` 这类通用目录名各家都有，
        审查处理 10-17：原先没有同税号、也没有空税号时退到全部行，会把别家的期间当成这家的真 ISPRD）；本方税号也不知道时，只在
        同名各份期间一样时认。不认的记进 `conflicts`。"""
        own = digits(own_tax)
        key = str(name or "").strip().upper()
        for origin in ("export", "testcopy"):
            rows = self.found[origin].get(key) or []
            if not rows:
                continue
            mine = {window for tax, window in rows if tax and tax == own}
            if own:
                windows = mine or {window for tax, window in rows if not tax}
            else:
                windows = {window for _tax, window in rows}
            if len(windows) == 1:
                return windows.pop(), origin
            self.refused.add(f"{origin}:{key}")
        return None

    def conflicts(self) -> List[str]:
        """不认这一来源的账套目录名：同名、税号也分不开、期间又不同的，以及 `find` 时只有别家税号、或分不开的（盘点里列出）。"""
        found = {f"{origin}:{name}" for origin, rows in self.found.items() for name, entries in rows.items()
                 if len({window for _tax, window in entries}) > 1}
        return sorted(found | self.refused)


def fiscal_calendars(express: Optional[Path], sets: Sequence[Path], isprd: Optional[Path] = None) -> Calendars:
    """答案以外的会计期间（`isprd_window` 口径）：

    - ``isprd``：用户批准的 S 盘只读导出（先复制到 C:，`refuse_lan` 不读 S:），目录下任意深度的 ``<账套目录>/ISPRD.DBF``，
      按上一层目录名认；同目录有 ISINFO.DBF 时取它的税号分开同名账套；
    - ``express``：测试机本地 Express 测试账套（``70EXP/T*``，各公司最新账套的副本），对照表是语料脚本的 ``expsets*.tsv``
      （第 1 列税号、第 3 列来源账套、第 5 列测试账套）。
    读不到的账套用答案自带的 ISPRD，再没有按 ``--periods`` 估或不报。"""
    found = Calendars()
    if isprd:
        for path in sorted(refuse_lan(isprd).rglob("*")):
            if path.name.upper() != "ISPRD.DBF" or not path.is_file():
                continue
            info = next((item for item in path.parent.iterdir() if item.name.upper() == "ISINFO.DBF"), None)
            tax = (next(dbf_rows(info, ["TAXID"]), None) or {}).get("TAXID") if info else ""
            found.add("export", path.parent.name, str(tax or ""), isprd_window(path))
    if express:
        for table in sets:
            if not refuse_lan(table).is_file():
                continue
            for line in table.read_text(encoding="utf-8-sig").splitlines():
                parts = line.split("\t")
                if len(parts) < 5 or not parts[2].strip() or not parts[4].strip():
                    continue
                found.add("testcopy", parts[2], parts[0],
                          isprd_window(refuse_lan(express) / parts[4].strip() / "ISPRD.DBF"))
    return found


def registered_books(registry: Optional[Path]) -> Optional[Dict[str, set]]:
    """Express 登记表 SCCOMP.DBF（``--registry``，只读导出，文件或含若干 SCCOMP.DBF 的目录）登记的账套 → {目录名（PATH 最后一节，
    大写）: {本方税号}}。线上目录只来自登记表（`account_probe.probe_account_sets`），没登记的备份副本、弃用账套线上不在目录里。
    SCCOMP 没有税号列：导出里登记表旁边有那份账套的 ISINFO.DBF（``<根>/<PATH>`` 或 ``<SCCOMP 所在目录>/<PATH>``，Express 的
    登记表在 ``<根>/secure/``）时取它的 TAXID，取不到记空串（只按目录名认，`is_registered` 放行）。审查处理 10-17：原先只按
    目录名认，``68EXP`` 这类通用目录名会把别家登记的账套当成这家的。没给回 None。"""
    if not registry:
        return None
    root = refuse_lan(registry)
    tables = [root] if root.is_file() else [path for path in sorted(root.rglob("*"))
                                            if path.name.upper() == "SCCOMP.DBF" and path.is_file()]
    names: Dict[str, set] = collections.defaultdict(set)
    for table in tables:
        for row in dbf_rows(table, ["COMPCOD", "PATH"]):
            parts = [part for part in re.split(r"[\\/]", str(row.get("PATH") or "").strip()) if part]
            if not str(row.get("COMPCOD") or "").strip() or not parts:
                continue
            tax = ""
            for base in (table.parent.parent, table.parent):
                info = base.joinpath(parts[-1], "ISINFO.DBF")
                if not re.match(r"^[A-Za-z]:", parts[0]) and len(parts) > 1:
                    info = base.joinpath(*parts, "ISINFO.DBF")
                if info.is_file():
                    tax = digits((next(dbf_rows(info, ["TAXID"]), None) or {}).get("TAXID"))
                    break
            names[parts[-1].upper()].add(tax)
    return dict(names)


def load_book(folder: Path, *, company: str, own_tax: str, key: str, calendar: Optional[Tuple] = None,
              origin: str = "testcopy", periods: str = "strict") -> Dict[str, Any]:
    """一份账套答案（``答案/<账套>/原表``）→ 判定层要的全部事实。只读。会计期间：答案自带 ``原表/ISPRD.DBF`` 优先，再用
    ``calendar``（(起, 止, 次年止)，来源 ``origin``：export / testcopy，见 `fiscal_calendars`）；都没有时 ``periods`` 为
    ``estimated`` 按单据日期估（`book_period`，次年止按本年止 + 1 年），``strict``（默认，同 ``judge`` 的默认与线上）不报期间
    （同线上读不到 ISPRD：该账套的单出 ``period_unknown``，同组旧账套退到次年段时核不了新年度账套、选不出）。"""
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
    products, stock_types, product_names = [], {}, {}
    for row in dbf_rows(tables / "STMAS.DBF", ["STKCOD", "STKDES", "STKTYP"]):
        code = str(row.get("STKCOD") or "").strip()
        if code:
            kind = "stock" if str(row.get("STKTYP") or "").strip() == "0" else "non_stock"
            products.append({"code": code, "name": str(row.get("STKDES") or "").strip(), "kind": kind})
            stock_types[code] = kind
            product_names[code] = products[-1]["name"]
    departments = [{"code": str(row.get("TYPCOD") or "").strip(), "name": str(row.get("TYPDES") or "").strip()}
                   for row in dbf_rows(tables / "ISTAB.DBF", ["TABTYP", "TYPCOD", "TYPDES"])
                   if str(row.get("TABTYP") or "").strip() == "50" and str(row.get("TYPCOD") or "").strip()]
    lines = moving = 0
    # 存货明细按单号分组（10-17）：(SEQNUM, 文件顺序, PEOPLE, DOCDAT, Line)；同样的品名 / 码只留一份字符串（大账套几十万行）
    detail: Dict[str, List[Tuple[int, int, str, Optional[dt.date], Line]]] = collections.defaultdict(list)
    shared: Dict[str, str] = {}

    def text(value: Any) -> str:
        value = str(value or "").strip()
        return shared.setdefault(value, value)

    for index, row in enumerate(dbf_rows(tables / "STCRD.DBF", [*STCRD_FIELDS, *STOCK_IMPACT])):
        lines += 1
        impact = any(row.get(field) not in (None, "", Decimal(0)) for field in STOCK_IMPACT)
        moving += int(impact)
        code = text(row.get("STKCOD"))
        amount = row.get("NETVAL") if row.get("NETVAL") is not None else row.get("TRNVAL")
        detail[str(row.get("DOCNUM") or "").strip().upper()].append(
            (_seq(row.get("SEQNUM")), index, text(row.get("PEOPLE")),
             row.get("DOCDAT") if isinstance(row.get("DOCDAT"), dt.date) else None,
             Line(code, text(row.get("STKDES")) or text(product_names.get(code)),
                  "" if row.get("TRNQTY") is None else str(row["TRNQTY"]),
                  "" if row.get("UNITPR") is None else str(row["UNITPR"]),
                  "" if amount is None else str(money(amount)), stock_types.get(code, "unknown"), impact)))
    fingerprint = {"stcrd_lines": lines, "stcrd_lines_moving_stock": moving,
                   "stock_master_count": sum(1 for kind in stock_types.values() if kind == "stock")}
    # 明细挂单的统计（盘点用，核 PEOPLE 是否总有值、与对方码一致）：按挂单次数数（同一行可能被几张同号单各看一次）——挂上的、
    # PEOPLE 空的、PEOPLE 是别家（丢掉）的、明细日期与单头入账日不同（丢掉）的；``shared_docnum`` 挂到两张以上单的单号数
    stcrd = collections.Counter(lines=lines)
    attached_to: Dict[str, int] = collections.Counter()

    def doc_lines(docnum: str, party: str, posted: Optional[dt.date], clash: bool) -> Tuple[Line, ...]:
        found = []
        for _seq_no, _index, people, day, line in sorted(detail.get(docnum, ()), key=lambda item: item[:2]):
            if people and party and people != party:
                stcrd["people_other"] += 1  # 单号撞上别家的单：不挂到这张
                continue
            if clash and day and isinstance(posted, dt.date) and day != posted:
                # 进项与销项同一个单号（``clash``）、PEOPLE 又空或进销同一个码：明细日期跟着单头写，不同就不是这张（审查处理
                # 10-17；只在撞号时看日期，不撞号的单头改过日期也照挂）
                stcrd["date_other"] += 1
                continue
            stcrd["attached"] += 1
            stcrd["people_blank"] += int(not people)
            found.append(line)
        if found:
            attached_to[docnum] += 1
            stcrd["shared_docnum"] += int(attached_to[docnum] == 2)
        return tuple(found)

    vouchers: Dict[str, Dict[str, Any]] = {}
    entry_days = []  # 这份账套里每张未作废凭证的录入日（GLJNL.CREDAT），`--tiebreak recent_entry` 与盘点 last_entered 用
    for row in dbf_rows(tables / "GLJNL.DBF", ["VOUCHER", "DOCSTAT", "CREDAT", "CREBY"]):
        voucher = str(row.get("VOUCHER") or "").strip().upper()
        if isinstance(row.get("CREDAT"), dt.date) and str(row.get("DOCSTAT") or "").strip().upper() != CANCELLED:
            entry_days.append(row["CREDAT"])
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
                                             "VAT01", "AMTRAT0", "DOCSTAT", "VATPRD", "VATDAT"]):
        if str(row.get("DOCSTAT") or "").strip().upper() == CANCELLED:
            continue
        registers.setdefault((str(row.get("VATREC") or "").strip().upper(),
                              str(row.get("DOCNUM") or "").strip().upper()), row)

    docs = []
    for direction, table, code_field, master_rows, record in (
            ("purchase", "APTRN", "SUPCOD", suppliers, "P"), ("sales", "ARTRN", "CUSCOD", customers, "S")):
        by_code = {row["code"]: row for row in master_rows}
        for row in dbf_rows(tables / f"{table}.DBF", ["RECTYP", "DOCNUM", "DOCDAT", "REFNUM", "YOUREF", code_field,
                                                     "TOTAL", "VATAMT", "NETAMT", "DOCSTAT", "USERID", "DEPCOD",
                                                     "VATPRD"]):
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
            # 税期（10-17，估上传日用）：税登记的 VATPRD（自然月首日），没有再取单据表头的（销项表头真单恒空，税期记在 ISVAT）
            vat_period = (register or {}).get("VATPRD") or row.get("VATPRD")
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
                "vat_booked": bool(vat_lines),
                "vat_period": vat_period if isinstance(vat_period, dt.date) else None,
                "vat_day": (register or {}).get("VATDAT") if isinstance((register or {}).get("VATDAT"), dt.date)
                else None,
                "pearnly": str(row.get("USERID") or "").strip().upper() == "PEARNLY",
            })
    # 存货明细挂单（10-17）：两张表都读完再挂，进项与销项同号的单才按明细日期分开（`doc_lines`）。答案口径：只有库存品或有库存
    # 影响的明细才算走库存（`stock_line`）；``detail`` 有没有任何明细行
    numbers = {way: {doc["docnum"] for doc in docs if doc["direction"] == way} for way in ("purchase", "sales")}
    clashes = numbers["purchase"] & numbers["sales"]
    for doc in docs:
        detail_lines = doc_lines(doc["docnum"], doc["party_code"], doc["posted"], doc["docnum"] in clashes)
        doc.update(stock=any(stock_line(line) for line in detail_lines), detail=bool(detail_lines), lines=detail_lines)
    own = isprd_window(tables / "ISPRD.DBF")
    if own:
        calendar, origin = own, "answer"
    if calendar:
        period, following = (calendar[0], calendar[1]), calendar[2] if len(calendar) > 2 else None
    elif periods == "strict":
        period, following, origin = (None, None), None, "none"
    else:
        period = book_period([doc["date"] for doc in docs if doc["date"] and not doc["cancelled"]])
        following, origin = next_year_end(period[1]), "estimated"
    return {"key": key, "folder": str(folder), "company": company, "own_tax": own_tax,
            "branch": branch5(info.get("ORGNUM")), "defaults": defaults, "accounts": list(accounts.values()),
            "suppliers": suppliers, "customers": customers, "products": products, "departments": departments,
            "fingerprint": fingerprint, "perpetual": str(info.get("ISPERPETUA") or "").strip().upper() == "Y",
            "period": period, "next_end": following, "period_source": "ISPRD" if calendar else origin,
            "period_origin": origin, "estimated": origin == "estimated", "entry_days": sorted(entry_days),
            "batch_days": {day: count for day, count in collections.Counter(entry_days).items()
                           if count >= BATCH_DAY_DOCS},
            "stcrd": dict(stcrd), "docs": docs}


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


def upload_proxy(m, doc: Mapping[str, Any]) -> Tuple[dt.date, str]:
    """估这张单上传到 Pearnly 的日子（10-17，只给 ``class_alt`` 用，记忆、历史、回读的时钟仍按 `entered_on`）：申报截止前单据
    一定已经在会计手上，取凭证录入日与这张单税期（VATPRD）ภ.พ.30 截止日（`vat_deadline`）里早的那个，且不早于单据日期 →
    (日子, 来源)：截止日更早 ``vat_deadline``，否则 ``entered``；没有税期照 `entered_on`。仍是上界（偏悲观）。"""
    entered = entered_on(doc)
    period = doc.get("vat_period")
    if not isinstance(period, dt.date):
        return entered, "entered"
    deadline = vat_deadline(m, period.year, period.month)
    if deadline < entered:
        return max(doc["date"], deadline), "vat_deadline"
    return entered, "entered"


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


LINE_MODES = ("none", "answer")
LINE_NAMES = ("stkdes", "opaque")


def invoice_name(line: Line, names: str = "stkdes") -> str:
    """票面上这一行的品名：``stkdes`` 照会计建档的 STKDES（Express 同名查找全中，乐观上界）；``opaque`` 打码成
    ``L-`` + STKDES 的 sha1 前 10 位：同名查找永远落空，但同一种货每张票写法一模一样、账套记忆次次命中 —— 是「同名查找落空、
    记忆理想命中」的估计，不是悲观下界（真实发票上同一件货的写法常常不同，记忆实际命中更少，审查处理 10-17）。"""
    if names == "opaque":
        return "L-" + hashlib.sha1((line.name or line.code).encode("utf-8")).hexdigest()[:10]
    return line.name or line.code


def invoice_items(doc: Mapping[str, Any], names: str = "stkdes") -> List[Dict[str, str]]:
    """答案的存货明细 → 票面明细行 ``[{name, qty, price, subtotal}]``（`common.extract_line_items` 收的形状，金额是 Decimal
    写法的字符串）。"""
    return [{"name": invoice_name(line, names), "qty": line.qty, "price": line.price, "subtotal": line.amount}
            for line in doc.get("lines") or ()]


def perfect_fields(doc: Mapping[str, Any], book: Mapping[str, Any], lines: str = "none",
                   names: str = "stkdes") -> Dict[str, Any]:
    """答案这张单的「识别全对」票面：单号、日期、对方名称 / 税号 / 分店、本方、三项金额；有税额的是正式税票，
    没税额的按收据。``lines`` 为 ``none``（默认，同第四轮）不造明细行（识别全对的票面也不知道原件上的品名），入账方式按声明值
    计分（`payload_values` 的 ``declared``）；``answer`` 把答案的存货明细造进票面（`invoice_items`，品名按 ``names``）。"""
    party, own = ("buyer", "seller") if doc["direction"] == "sales" else ("seller", "buyer")
    vat = Decimal(doc["vat"])
    fields = {
        "document_type": "tax_invoice" if vat > 0 else "receipt", "direction": doc["direction"],
        "invoice_number": doc["ref"], "date": doc["date"].isoformat() if doc["date"] else "",
        f"{party}_name": doc["party_name"], f"{party}_tax": doc["party_tax"], f"{party}_branch": doc["party_branch"],
        f"{own}_name": book["company"], f"{own}_tax": book["own_tax"], f"{own}_branch": book["branch"],
        "subtotal": str(doc["net"]), "vat": str(doc["vat"]), "total_amount": str(doc["total"]),
    }
    if lines == "answer" and doc.get("lines"):
        fields["items"] = invoice_items(doc, names)
    return fields


def synthetic_text(fields: Mapping[str, Any]) -> str:
    """判定层不测识别：原文核对（含税额、日期、票种标题在文字层里）按「原件上印着」造一段文字层，够 `automation.text_layer`
    的字数。票种标题照造的票种印（正式税票「ใบกำกับภาษี」、收据「ใบเสร็จรับเงิน」）：识别读错票种的比例归识别闸层量。"""
    day = str(fields.get("date") or "")
    printed = f"{day[8:10]}/{day[5:7]}/{day[:4]}" if len(day) >= 10 else ""
    taxes = " ".join(str(fields.get(key) or "") for key in ("seller_tax", "buyer_tax"))
    title = "ใบกำกับภาษี" if fields.get("document_type") == "tax_invoice" else "ใบเสร็จรับเงิน"
    body = f"{title} {fields.get('invoice_number') or ''} {printed} {fields.get('total_amount') or ''} {taxes}"
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


_PRODUCT_INDEXES: Dict[int, Dict[str, Any]] = {}


def _product_index(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """存货主档按归一品名（`choices.norm_name`，库存品在前）与码的索引，按 ``id(rows)`` 缓存（同 `_master_index`）。"""
    found = _PRODUCT_INDEXES.get(id(rows))
    if found is None or found["rows"] is not rows:
        from pearnly_cowork.push.express.choices import norm_name

        by_name: Dict[str, List[Mapping[str, Any]]] = collections.defaultdict(list)
        for row in sorted(rows, key=lambda item: item.get("kind") != "stock"):
            name = norm_name(row.get("name"))
            if name:
                by_name[name].append(row)
        found = _PRODUCT_INDEXES[id(rows)] = {"rows": rows, "name": by_name,
                                              "code": {row["code"]: row for row in rows}}
    return found


def _product_rows(rows: Sequence[Mapping[str, Any]], names: Iterable[str], keep: Iterable[str]) -> List[Dict[str, Any]]:
    """这张单用得着的存货主档行（10-17）：与票面明细归一品名相同的、账套记忆里这家记过的码，再留一行别的（「主档报上来了」）。
    `choices.resolve` 与 `automation._items_known` 只按同名与记过的码查，结果与全表一样（同 `_party_rows` 的做法）。"""
    index = _product_index(rows)
    found: List[Mapping[str, Any]] = []
    for name in names:
        found += [row for row in index["name"].get(name, ()) if row not in found]
    found += [index["code"][code] for code in keep if code and code in index["code"]
              and index["code"][code] not in found]
    other = next((row for row in rows if row not in found), None)
    if other is not None:
        found.append(other)
    return [dict(row) for row in found]


def book_config(book: Mapping[str, Any], *, party_tax: str = "", keep: Iterable[str] = (),
                full: bool = False, item_names: Iterable[str] = (), item_keep: Iterable[str] = ()) -> Dict[str, Any]:
    """推送准备的账套配置（CC 桌面版上报的主档 `reported_*`、ISINFO 默认科目、记账指纹）。``full`` 为假时对方主档只给
    这张单用得着的行（见 `_party_rows`），存货主档只给票面明细同名的、记过的码与另一行（`_product_rows`；``item_names`` 是
    票面明细的归一品名，``item_keep`` 是账套记忆里这家记过的存货码）。"""
    defaults = book["defaults"]
    config = {key: value for key, value in defaults.items() if value and key != "cash_acc"}
    suppliers, customers, products = book["suppliers"], book["customers"], list(book["products"])
    if full:
        suppliers = [{k: row[k] for k in ("code", "name", "tax_id", "branch")} for row in suppliers]
        customers = [{k: row[k] for k in ("code", "name", "tax_id", "branch")} for row in customers]
    else:
        suppliers, customers = _party_rows(suppliers, party_tax, keep), _party_rows(customers, party_tax, keep)
        products = _product_rows(book["products"], item_names, item_keep)
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
    """这家公司各份账套给按日期选账套用的样子（`companies.identity.book`，带 ``next_end``）；另带回放自己的 ``origin``
    （期间来源，`ORIGINS`），选账套不看它。"""
    return [dict(m["identity"].book("express", "replay", account_row(item)), origin=item.get("period_origin") or "")
            for item in books]


def book_key(m, book: Mapping[str, Any]) -> str:
    """一份账套在目录里的键（与 `shaped_books` 同样规范化）。"""
    return m["identity"].book("express", "replay", account_row(book))["key"]


def day_books(m, shaped: Sequence[Mapping[str, Any]], book: Mapping[str, Any], day: dt.date) -> List[str]:
    """按单据日期这张单该推往的账套键（生产 `companies.automation.day_books` 同一判据：这家公司这个分店的账套，
    `companies.identity.for_day` —— 先本年期间覆盖这天的，没有再用次年段含这天的旧账套）。"""
    identity = m["identity"]
    return [item["key"] for item in identity.for_day(identity.for_branch(shaped, book["branch"]), day.isoformat())]


def outside(book: Mapping[str, Any], day: Optional[dt.date]) -> str:
    """这一天落在这份账套期间的哪一段之外：晚于次年段 ``after``、次年段 ``next_year``、早于本年 ``before``、本年里或期间
    没报全回空串。"""
    start, end = book["period"]
    if not (start and end and isinstance(day, dt.date)):
        return ""
    following = book.get("next_end")
    return "after" if following and day > following else "next_year" if day > end else "before" if day < start else ""


def fiscal_year(start: Optional[dt.date], day: dt.date) -> int:
    """这一天在以 ``start`` 起算的第几个会计年（期间没报按公历年）。"""
    if not start:
        return day.year
    months = (day.year - start.year) * 12 + day.month - start.month - (1 if day.day < start.day else 0)
    return months // 12


def book_pick(m, shaped: Optional[Sequence[Mapping[str, Any]]], book: Mapping[str, Any],
              picked: Sequence[str], day: dt.date) -> Dict[str, Any]:
    """按日期选出的账套（``picked``，`day_books`）对这份账套是哪种情形（10-17，拆「两份同期」是真是估）：``self`` 正好是这份、
    ``many`` 多份（``many_keys`` 候选、``many_origins`` 各候选的期间来源（`ORIGINS`）、``many_estimated`` 候选里有按单据估期间的、
    ``many_parts`` 各候选这一天落在本年覆盖 ``current`` 还是次年段 ``next``，``many_part`` 都是本年 ``current`` / 都是次年段
    ``next`` / 不一样 ``mixed``——`identity.for_day` 先认本年覆盖的、没有才认次年段，同一次选出的候选总在同一段，``mixed``
    只是防御）、``none`` 一份都选不出（``none_unknown`` 同分店有期间没报全的账套）、``other``
    另一份（``other_newer`` 那份本年起始晚于这份本年止，即新年度账套已建）。"""
    mine = book_key(m, book)
    keys = sorted(set(picked))
    found: Dict[str, Any] = {"book_pick": "self" if keys == [mine] else "none" if not keys else
                             "many" if len(keys) > 1 else "other"}
    group = m["identity"].for_branch(shaped, book["branch"]) if shaped is not None else []
    by_key = {item["key"]: item for item in group}
    if found["book_pick"] == "many":
        found["many_keys"] = keys
        found["many_origins"] = [(by_key.get(key) or {}).get("origin") or "" for key in keys]
        found["many_estimated"] = "estimated" in found["many_origins"]
        # 逐份候选看这一天在它的本年还是次年段（审查处理 10-17：原先只看排序后第一份）
        parts = tuple("current" if str((by_key.get(key) or {}).get("period_start") or "") <= day.isoformat()
                      <= str((by_key.get(key) or {}).get("period_end") or "") else "next" for key in keys)
        found["many_parts"] = list(parts)
        found["many_part"] = parts[0] if len(set(parts)) == 1 else "mixed"
    elif found["book_pick"] == "none":
        found["none_unknown"] = any(not (item.get("period_start") and item.get("period_end"))
                                    for item in group if item["key"] != mine)
    elif found["book_pick"] == "other":
        end = book["period"][1]
        start = str((by_key.get(keys[0]) or {}).get("period_start") or "")
        found["other_newer"] = bool(end and start and start > end.isoformat())
    return found


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


def stock_history(entries: Sequence[Mapping[str, Any]], since: dt.date, until: dt.date) -> Dict[str, Dict[str, Any]]:
    """同一家供应商（``entries`` 已按供应商挑好，且只含录入早于这张单的）``since`` ≤ 单据日期 < ``until`` 的进项答案单，按入账方式
    汇总成 `history_rules.posting_settled` 收的形状 ``{"stock": {docs, last}, "service": {docs, last}}``：有库存明细（`stock_line`）
    记 stock，没有记 service。不含作废的、不含 Pearnly 写的（会计亲手录的才算，用户 10-17）。防泄漏同 `history_data`。"""
    out = {kind: {"docs": 0, "last": ""} for kind in ("stock", "service")}
    for doc in entries:
        day = doc["date"]
        if not day or not since <= day < until or doc["cancelled"] or doc["pearnly"]:
            continue
        found = out["stock" if doc["stock"] else "service"]
        found["docs"] += 1
        found["last"] = max(found["last"], day.isoformat())
    return out


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


STOCK_ITEM_MODES = ("stock_item", "stock_sale")


def written_items(payload: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """载荷里 CC 桌面版真会写进 STCRD 的库存行：明细过了对账闸（``items_status == ok``，`dbf_writer` 只在这时写明细）且写入模式是
    库存（``stock_item`` / ``stock_sale``）。"""
    if str(payload.get("items_status") or "") != "ok":
        return []
    return [item for item in payload.get("items") or []
            if isinstance(item, Mapping) and str(item.get("item_mode") or "") in STOCK_ITEM_MODES]


def payload_values(payload: Mapping[str, Any], book: Mapping[str, Any], fields: Mapping[str, Any],
                   declared: Optional[str] = None) -> Dict[str, Any]:
    """推出去会是什么：对方档（载荷里的码，没有就按 CC 桌面版找档）、科目、现 / 赊、抵不抵进项；入账方式按载荷实际会写什么
    （有写得进 STCRD 的库存行是 ``stock``，否则 ``service``：声明走库存、明细没过闸的单只写表头，10-17）；存货是这些库存行的码
    （载荷写定的，没有按同名在全量存货主档里找，再没有记 ``new``），按行序成元组。

    ``declared``：票面没造明细（``--lines none``）时这张单用的入账类型（``stock`` / ``service``），入账方式照它计分。没造明细的
    载荷本来就写不出库存行，按载荷计会把每张「公司选走库存」的单都算成推错（回放造票面的伪影，真实票面识别得出明细，审查处理
    10-17），这时只量「公司选的值与会计的记法」这一层，明细过不过对账闸看造了明细的那两遍。"""
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
    stock = written_items(payload)
    out["posting"] = ("stock" if declared == "stock" else "service") if declared is not None else \
        "stock" if stock else "service"
    if stock:
        from pearnly_cowork.push.express.choices import norm_name

        index = _product_index(book["products"])["name"]
        out["item"] = tuple(str(item.get("code") or "").strip()
                            or next((row["code"] for row in index.get(norm_name(item.get("name")), ())), "new")
                            for item in stock)
    else:
        out["item"] = ()
    return out


def answer_items(doc: Mapping[str, Any]) -> Tuple[str, ...]:
    """答案这张单的库存行存货码（`stock_line`，按 SEQNUM），与 `payload_values` 的 ``item`` 比。"""
    return tuple(line.code for line in doc.get("lines") or () if stock_line(line))


SCORED = ("party", "account", "payment", "vat", "posting", "item")
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
            got = pushed.get(item)
            shown = "+".join(got) if isinstance(got, tuple) else got
            out[item] = f"wrong:{shown or 'none'}"
    return out


class Memory:
    """一份账套的「账套记忆」（`push.choices.remembered` 的形状）：只记模拟的人在 C 类里选的项。"""

    def __init__(self):
        self.kinds: Dict[str, Dict[str, str]] = {kind: {} for kind in
                                                 ("party", "item", "account", "payment", "vat", "department", "receipt")}

    def learn(self, needed: Sequence[Mapping[str, Any]], answer: Mapping[str, Any],
              items: Optional[Mapping[str, str]] = None) -> None:
        """``items``：存货逐行学（10-17）——票面每一行的归类键（`choices.item_subject`）→ 答案那一行的存货码；模拟的人按会计
        记的码选。"""
        for item in needed or ():
            kind, subject = item.get("kind"), item.get("subject")
            value = (items or {}).get(subject) if kind == "item" else answer.get(kind)
            if kind in self.kinds and subject and value and value != "*multi":
                self.kinds[kind][subject] = value


COMPANY_CHOICE_MODES = ("prior", "full")
# 替公司选入账方式时「此前已录单的多数」至少要这么多张会交人的单，不够退到全期多数（审查处理 10-17：一两张就定，第四轮初跑
# NEWLINK 那种第一张恰好是服务的会整家选反）
PRIOR_MIN = 10


def posting_population(m, book: Mapping[str, Any], doc: Mapping[str, Any], entered: dt.date,
                       cache: Optional[Dict[str, bool]] = None) -> bool:
    """这张单会不会因「永续 / 混合库存账套、没声明入账类型」交人（`goods_posting.blocked` 同一判据：画像拦的账套里，销项全部单、
    进项不是费用车道的单）：替公司选入账方式时只在这类单里数多数。车道按「识别全对」的票面判，不看这家记过的小票抵进项
    （那要先回放到那一刻，近似）。画像按账套缓存在 ``cache``（键是账套键）。"""
    cache = cache if cache is not None else {}
    blocks = cache.get(book["key"])
    if blocks is None:
        blocks = cache[book["key"]] = m["goods_posting"].blocked({}, direction="sales", config=book_config(book),
                                                                  posting_kind=None, today=entered)
    if not blocks:
        return False
    if doc["direction"] == "sales":
        return True
    from pearnly_cowork.push.express.field_rules import lane_verdict

    is_expense, _src = lane_verdict(perfect_fields(doc, book), today=entered)
    return not is_expense


class CompanyChoice:
    """一家公司按公司记的选择（工作空间 ``document_policy`` 的 ``goods_posting|<公司>``，`push.express.goods_posting`）：
    商品单入账方式。模拟的人在第一张因此交人的单上替这家公司选一次，之后这家照此。回放同线上：单都不预先声明入账类型，会交人的
    全按公司选的走（审查处理 10-16）。人选哪个（`pick`）：``before``（10-17 主口径）这家第一次交人那一刻之前已录进 Express、会因
    入账方式交人的单里的多数（会计知道这家记不记库存，但不知道以后），不到 `PRIOR_MIN` 张退到全期多数（``full_fallback``）；
    ``preferred`` 全期多数（第四轮口径，带了未来信息，``--company-choice full`` 作对照）；都没有按这张单的答案。第四轮初跑先按第一张
    单的答案选，NEWLINK 等几家第一张恰好是服务、此后十几万张商品单全被记成费用。"""

    def __init__(self, preferred: Optional[str] = None, before=None, full: Optional[str] = None):
        self.goods_posting: Optional[str] = None
        self.preferred = preferred
        self.before = before
        self.full = full if full is not None else preferred  # 全期多数（prior 口径下是不够张数时的退路）
        self.picked_by = ""
        self.prior: Optional[str] = None
        self.prior_docs = 0

    def pick(self, entered: dt.date, doc: Mapping[str, Any]) -> None:
        """在这张交人的单上选一次（记下选的值、凭什么选、那一刻之前的多数与张数）。"""
        if self.before:
            self.prior, self.prior_docs = self.before(entered)
        if self.prior:
            self.goods_posting, self.picked_by = self.prior, "prior"
        elif self.preferred:
            self.goods_posting, self.picked_by = self.preferred, "full"
        elif self.before and self.full:
            self.goods_posting, self.picked_by = self.full, "full_fallback"
        else:
            self.goods_posting, self.picked_by = ("stock" if doc["stock"] else "service"), "answer"

    def summary(self) -> Dict[str, Any]:
        return {"used": self.goods_posting, "by": self.picked_by, "prior": self.prior, "prior_docs": self.prior_docs,
                "full": self.full}


_READBACK_DAYS: Dict[str, dt.date] = {}
# 证据窗口往回最多看这家这么多张推送（同线上 `automation.RECENT_SCAN_MAX`；回放不导入 assess：它连 Frappe，常量与窗口函数都在
# 纯函数的 automation 里，测试锁住两边相同）。``--recent-max 0`` 不设上限（对照）
RECENT_MAX = 1000
# 证据窗口的取法（存档用）：先跳过没回读核过的凭记忆推送、再取最近的（10-17）；没有这一项的存档是旧取法 newest（截最新几张再跳过）
EVIDENCE_WINDOW = "evidence_first"
READBACK_SCOPES = ("record", "account")


def pushed_evidence(m, log: Sequence[Mapping[str, Any]], entered: dt.date, kind: str = "account",
                    branch: Optional[str] = None, limit: int = RECENT_MAX) -> List[str]:
    """回放里这家供应商在这份账套里之前推出去的单（``log`` 按推的先后，``{account, source, payment, payment_source, party,
    party_source, branch, stock, ok, period, entered}``）→ 证据序列（新的在前），与线上 `assess._recent` 同一个窗口函数
    `automation.memory_window`（10-17 改取法：先跳过凭记忆推出去、还没回读核过的，再在能当证据的里取最近的；往回最多看 ``limit``
    张推送，先截、再按分店筛，0 不设上限）。证据判定同线上 `automation.memory_evidence`：推的时候由人或规则定的照记；凭账套记忆推
    出去的，要这期的合格回读（截止日后一天）在这张单录入之前、而且在那张推出去之后，整张记录与会计最终记的都相同才算（``ok``，
    见 `pushed_record`；任一不同就是回读里被改，三类一起断开，同线上 `readback_marks` 按整张记录，10-17）；还没回读核过的不算证据。
    ``branch``：对方档只数同一分店的。值为 None 的是回放推不出这一类（C 类重出载荷也没出，`judge_document` 的近似），跳过。回放
    假定每一期都在申报截止日后一天按时做了合格回读，线上要有人真发起回读才有证据。"""
    recent = list(reversed(log or ()))
    if limit and limit > 0:
        recent = recent[:limit]  # 同 `memory_window` 的上限（先截）；这里先截只为少造几条
    entries = []
    for record in recent:
        mark = ""
        if record.get(kind) is not None and (branch is None or record.get("branch") == branch):
            day = _READBACK_DAYS.get(record["period"])
            if day is None:
                day = _READBACK_DAYS[record["period"]] = readback_day(m, record["period"])
            if record["entered"] <= day <= entered:
                mark = "confirmed" if record.get("ok") else "changed"
        entries.append({"account": record.get("account"), "source": record.get("source") or "",
                        "payment": record.get("payment"), "payment_source": record.get("payment_source") or "",
                        "party": record.get("party"), "party_source": record.get("party_source") or "",
                        "branch": record.get("branch"), "stock": bool(record.get("stock")), "readback": mark})
    return m["automation"].memory_window(entries, kind, branch=branch, scan=limit)


PUSHED_KINDS = ("party", "account", "payment", "vat", "posting", "item")


def pushed_record(answer: Mapping[str, Any], payload: Optional[Mapping[str, Any]], values: Mapping[str, Any],
                  needed: Iterable[str], scope: str = "record") -> Dict[str, Any]:
    """这张推出去的单记进 ``pushed`` 的样子（`pushed_evidence` 用）：值与来源取推出去的载荷，对方档取 CC 桌面版实际写的码（载荷
    写定的码，没写按 CC 桌面版找档 `payload_values`；找不到要新建 / 定不出的记空串，同线上回执 ``meta.party_code``，审查处理
    10-17）；这次要人选的类按答案（模拟的人选的）——科目来源记 ``chosen``（推送服务按上传后有人选细分），现 / 赊记 ``remembered``
    （线上人选的先进账套记忆、载荷 ``doctype_src`` 也是 remembered），对方档来源不在载荷里。``stock``：真写了库存行（回读不比分录，
    科目的 confirmed 不算证据）。``ok``：推出去的对方档、科目、现 / 赊、抵进项、入账方式、存货都与答案相同（答案没有这一项不比，
    一单多科目算不同）——合格回读里整张没被改；入账方式与存货只在计分的单上比（审查处理 10-17：原先不比，比线上整张记录宽）。
    ``scope`` 为 ``account`` 时只比科目（第四轮口径，``--readback-scope account`` 对照）。没载荷时（推不出）各类记 None，``ok``
    只比要人选的几类（乐观近似）。"""
    payload = payload if isinstance(payload, Mapping) else None
    needed = set(needed or ())
    doctype = str((payload or {}).get("doctype") or "").strip().upper()
    out = {"account": None, "source": "", "payment": None, "payment_source": "", "party": None, "party_source": "",
           "stock": False}
    if payload is not None:
        party = str(values.get("party") or "").strip()
        out.update(account=str(payload.get("items_account") or "").strip(),
                   source=str(payload.get("account_source") or ""), payment=doctype,
                   payment_source=str(payload.get("doctype_src") or ""),
                   party="" if party in ("new", "ambiguous") else party, stock=bool(written_items(payload)))
    pushed = {kind: values.get(kind) for kind in PUSHED_KINDS} if payload is not None else {}
    if "account" in needed:
        out.update(account=str(answer.get("account") or ""), source="chosen")
        pushed["account"] = answer.get("account")
    if "payment" in needed:
        out.update(payment={"cash": "HP", "credit": "RR"}.get(answer.get("payment"), ""), payment_source="remembered")
        pushed["payment"] = answer.get("payment")
    if "party" in needed:
        out.update(party=str(answer.get("party") or ""), party_source="")
        pushed["party"] = answer.get("party")
    for kind in ("vat", "item"):
        if kind in needed:
            pushed[kind] = answer.get(kind)
    kinds = PUSHED_KINDS if scope == "record" else ("account",)
    out["ok"] = all(answer.get(kind) is None or (kind not in pushed and payload is None)
                    or (answer[kind] != "*multi" and pushed.get(kind) == answer[kind]) for kind in kinds)
    return out


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
                   company: Optional[CompanyChoice] = None, shaped: Optional[Sequence[Mapping[str, Any]]] = None,
                   pushed: Optional[Dict[str, List[Dict[str, Any]]]] = None, lines: str = "none",
                   names: str = "stkdes", recent_max: int = RECENT_MAX, readback_scope: str = "record",
                   auto: Optional[set] = None):
    """一张答案单走一遍推送判定 → 判定结果（类别、原因、逐项计分）。C 类由模拟的人按答案选（记进账套记忆）；商品单入账方式
    因没选过交人时，模拟的人按这张单的答案替这家公司选一次（``company``）。``shaped``：这家公司各份账套（`shaped_books`），
    按日期选账套用；没给只认这一份。``pushed``：这份账套里各家供应商（13 位税号）之前推出去的单（科目、现 / 赊、对方档与整张
    记录对不对，`pushed_record` / `pushed_evidence`），判完把这张也记进去（C 类没出载荷的学完记忆后再出一次，结果里
    ``pushed_from`` 记 payload / replanned / approx）。``lines`` / ``names``：票面造不造明细行、品名怎么写（`perfect_fields`，
    10-17）；造了明细时存货逐行计分（`answer_items`）、逐行学记忆。每张单另记 ``posting_history``（这家供应商之前的 Express 存货
    历史定下的入账方式，`stock_history` + `history_rules.posting_settled`）与 ``posting_tier_if_history``（按公司选入账方式的单
    加上这一层的档，与线上同一判据 `automation.posting_by_history`：一致 A、相反 conflict、定成走库存而载荷写不出库存行 no_lines、
    存货没着落 items、定成记费用却有库存行或与库存品同名 named、没定下 B；``auto`` 是之前判成 A / B 的单（``id``），
    ``posting_history_manual`` / ``posting_tier_if_history_manual`` 把它们当 CC 推的、从历史里剔掉再算一次：用户 10-17 定的口径不含
    CC 推的单，回放里能被自动推的单线上会是 USERID=PEARNLY、不进历史，所以不剔是上界、全剔是下界），以及按「录入日与税期截止日
    取早」估上传日重判日期核对的 ``class_alt``。``recent_max`` / ``readback_scope`` 见 `pushed_evidence` / `pushed_record`；
    ``memory_cap_lost`` 记账套记忆定的哪几类只因证据窗口往回看的上限（``recent_max``）没到 A（不设上限会到）。"""
    company = company if company is not None else CompanyChoice()
    pushed = pushed if pushed is not None else {}
    automation = m["automation"]
    norm = m["choices"].norm_name
    fields = perfect_fields(doc, book, lines, names)
    direction = doc["direction"]
    day = doc["date"]
    entered = entered_on(doc)
    party = "buyer" if direction == "sales" else "seller"
    subject = m["choices"].party_subject(direction, fields.get(f"{party}_tax"), fields.get(f"{party}_branch"),
                                         fields.get(f"{party}_name"))
    # 票面明细的归类键（`choices.item_subject`，品名截到 50 字同载荷）→ 答案那一行的存货码；存货主档只给同名与这家记过的码
    item_codes: Dict[str, str] = {}
    for item, line in zip(fields.get("items") or (), doc.get("lines") or ()):
        item_codes.setdefault(m["choices"].item_subject(direction, fields.get(f"{party}_tax"),
                                                        fields.get(f"{party}_name"), str(item["name"])[:50]), line.code)
    item_names = {norm(str(item["name"])[:50]) for item in fields.get("items") or ()}

    def configure():
        # `choices.resolve` 只查这张单各行归类键记过的码：只留这些（不扫整份记忆，大账套几万个存货）
        return book_config(book, party_tax=digits(doc["party_tax"]), keep=[memory.kinds["party"].get(subject)],
                           item_names=item_names, item_keep=[memory.kinds["item"].get(key) for key in item_codes])

    config = configure()
    tax = digits(fields.get("seller_tax"))
    mine = history_entries.get(tax if len(tax) == 13 else "name:" + norm(fields.get("seller_name")),
                               []) if direction == "purchase" else []
    # 防泄漏（审查处理 10-16）：Express 历史只含这张单录入之前已录进去的单（按录入日，不按单据日期），不含它自己
    earlier = [item for item in mine if item is not doc and item["date"] and entered_on(item) < entered]
    data = history_data(earlier, entered - dt.timedelta(days=365), entered, entered)
    # 这家供应商之前的入账方式（10-17，主判定不带、另列）：会计亲手录、近 12 个月、只有一种记法且 ≥3 张（与线上共用同一个纯函数）
    settled = m["history_rules"].posting_settled(stock_history(earlier, entered - dt.timedelta(days=365), entered),
                                                 entered, entered) if direction == "purchase" else None
    posting_history = (settled or {}).get("kind") or ""
    manual = m["history_rules"].posting_settled(stock_history([item for item in earlier if id(item) not in (auto or ())],
                                                              entered - dt.timedelta(days=365), entered),
                                                entered, entered) if direction == "purchase" else None
    posting_manual = (manual or {}).get("kind") or ""
    remembered = {kind: dict(values) for kind, values in memory.kinds.items()}
    # 入账类型：同线上，单都不预先声明（审查处理 10-16：原先答案里没有存货明细的单按「服务」声明，「公司选走库存、单是服务」
    # 这种错推在回放里一张都出不来）；会交人的这家公司选过入账方式就照选的（`goods_posting.resolve`，与推送服务同一份），
    # 没选过照生产交人。这家记过的小票抵进项（``applied["vat"]``）改了车道时按它再定一次（`service._posting_decision` 同口径）
    def decide_with(kind):
        return m["decide"].decide(fields, direction=direction, posting_kind=kind, config=config,
                                  remembered=remembered, branch=book["branch"],
                                  settle=_settle(m, data, fields, config, entered) if direction == "purchase" else None)

    def plan():
        """按此刻的账套记忆、公司选的入账方式定这张单、出载荷 → (入账类型, 哪来的, 决定, 载荷, 准备好, 原因)。"""
        kind, origin = m["goods_posting"].resolve(
            fields, direction=direction, config=config, posting_kind=None, chosen=company.goods_posting, today=entered)
        found, _sources = decide_with(kind)
        vat_choice = found["applied"].get("vat")
        if vat_choice:
            again = m["goods_posting"].resolve(fields, direction=direction, config=config, posting_kind=None,
                                               chosen=company.goods_posting, today=entered, vat_choice=vat_choice)
            if again != (kind, origin):
                (kind, origin), (found, _sources) = again, decide_with(again[0])
        if found.get("history") and found["applied"].get("account"):
            # 同推送服务 `_account_from`：历史已定的科目来源记 express_history（不报成账套记忆）
            found["applied"]["account_from"] = "express_history"
        out, good, why = None, False, ""
        if not found["needed"]:
            config["choices"] = found["applied"]
            history = {"id": doc["docnum"], "workspace_client_id": "replay", "tenant_id": "replay",
                       "invoice_no": fields["invoice_number"], "invoice_date": fields["date"], "direction": direction,
                       "total_amount": fields["total_amount"], "confidence": "high", "pages": [{"fields": fields}]}
            context = m["ExpressContext"](enabled=True, today=entered, own_tax_id=book["own_tax"], tenant_id="replay",
                                          mappings={}, account_tax_id=book["own_tax"])
            try:
                prepared = m["prepare_express"]({"id": "replay", "config": config}, history, context=context,
                                                posting_kind=kind)
                out = prepared.payload or None
                good = prepared.disposition == "ready"
                why = "" if good else (prepared.reason or "preflight")
            except Exception as error:  # noqa: BLE001 — 一张单准备失败就当没准备好（C），不中断回放
                why = f"prepare_error:{type(error).__name__}"
        return kind, origin, found, out, good, why

    posting_kind, posting_from, decision, payload, ready, reason = plan()
    if reason.startswith(m["goods_posting"].NEEDED_PREFIX) and company.goods_posting is None:
        # 模拟的人在复核页点「走库存 / 记费用」：替这家公司选一次（`CompanyChoice.pick`：此前已录单的多数，没有按这张答案）
        company.pick(entered, doc)
    # 账套记忆定的科目、现 / 赊、对方档都要最近连续 3 张有证据的同值（`automation.memory_settled` / `memory_evidence`）：同线上，
    # 数的是这家供应商（13 位税号）在这份账套里之前推出去的单（A / B 是载荷的、C 是模拟的人推的），凭记忆推的要合格回读核过、
    # 整张没被改才算；对方档只数同一分店的（审查处理 10-16 科目；10-17 推广到现 / 赊与对方档）。销项不查（同线上），按 0 张算
    supplier = digits(doc["party_tax"]) if direction == "purchase" else ""
    branch = branch5(fields.get("seller_branch"))
    log = pushed.get(supplier, []) if len(supplier) == 13 else []

    def evidence(limit):
        return {"account": pushed_evidence(m, log, entered, limit=limit),
                "payment": pushed_evidence(m, log, entered, "payment", limit=limit),
                "party": pushed_evidence(m, log, entered, "party", branch, limit=limit)}

    recent = evidence(recent_max) if direction == "purchase" else {"account": [], "payment": None, "party": None}
    pages = [{"fields": fields, "_layer_chain": ["direct"]}]
    picked = day_books(m, shaped, book, day) if shaped is not None else [book_key(m, book)]
    facts = automation.Facts(
        direction=direction, fields=fields, decision=decision, payload=payload, config=config, today=entered,
        ready=ready, blocked=reason, pages=pages, review_reasons=(), text=synthetic_text(fields),
        page_texts=[synthetic_text(fields)],
        account=account_row(book), catalog=catalog,
        day_books=picked, uploaded=entered,
        source_total=1, posting_from=posting_from, account_recent=recent["account"],
        payment_recent=recent["payment"], party_recent=recent["party"],
        prior_pushes=prior.get(_ref_key(m, fields["invoice_number"]), []), history_read=entered,
        layers=automation.layers(fields, direction=direction, remembered=remembered,
                                 history=(decision.get("history") or {}).get("account")))
    verdict = automation.classify(facts)

    def history_tier(kind: str) -> Optional[str]:
        """按公司选入账方式的单加上供应商存货历史这一层的档：调线上同一个判据 `automation.posting_by_history`（用户 10-17 批准接
        线上，回放与线上同口径）——没定下 B；与公司选的相反 conflict（线上交人）；一致但定成走库存、载荷写不出库存行 no_lines、
        库存行的存货没着落 items、定成记费用而载荷有库存行或明细与库存品同名 named（线上都不给 A）；一致 A。不是按公司选的回
        None。回放的主判定不带这一层（回放分不清哪些单线上会是 CC 写的、不进历史），上下界分别看 ``posting_tier_if_history`` 与
        ``posting_tier_if_history_manual``。"""
        if posting_from != m["goods_posting"].COMPANY_CHOICE:
            return None
        judged = dataclasses.replace(facts, posting_kind=posting_kind or "", posting_history=kind)
        found = automation.posting_by_history(judged)
        if found == "A" and automation.tiers(judged).get("posting") != "A":
            return "keyed"  # 对方没有 13 位税号、历史按名字对上：线上封顶 B（`automation.tiers`，第五轮后审查）
        return found or None

    # 证据窗口往回看的上限丢掉的 A（审查处理 10-17；10-17 改取法后上限是 1000 张推送）：记忆定的项判成 B，不设上限的证据却能定下的类
    cap_lost = []
    if direction == "purchase" and recent_max and len(log) > recent_max and payload:
        sources = {"account": payload.get("account_source"), "payment": payload.get("doctype_src"),
                   "party": (decision.get("decided_by") or {}).get("party")}
        wanted = {"account": payload.get("items_account"), "payment": str(payload.get("doctype") or "").upper(),
                  "party": str(((payload.get("supplier") or {}) if isinstance(payload.get("supplier"), Mapping)
                                else {}).get("code") or "")}
        loose = None
        for kind in ("account", "payment", "party"):
            if sources[kind] != "remembered" or verdict.items.get(kind) != "B" or not wanted[kind]:
                continue
            loose = loose or evidence(0)
            if automation.memory_settled(loose[kind], wanted[kind]):
                cap_lost.append(kind)

    # 上传日另估一次（10-17，`upload_proxy`）：只重判日期类核对（date_stale / day_month / date_future），其余时钟不变
    uploaded_alt, uploaded_src = upload_proxy(m, doc)
    alt = verdict if uploaded_src == "entered" else \
        automation.classify(dataclasses.replace(facts, uploaded=uploaded_alt, today=max(uploaded_alt, day)))
    answer = answer_values(doc)
    if posting_from == m["goods_posting"].COMPANY_CHOICE:
        # 入账方式按公司选的：与答案比（答案有库存品或库存影响的明细 = 走库存，没有 = 记费用；10-17 口径，`stock_line`）
        answer["posting"] = "stock" if doc["stock"] else "service"
    declared = None if lines == "answer" else posting_kind or "service"
    values = payload_values(payload, book, fields, declared) if payload else {}
    if lines == "answer" and payload and (posting_from == m["goods_posting"].COMPANY_CHOICE or values.get("item")):
        # 存货逐行计分（10-17）：只计入账方式也计分的单（按公司选的）与载荷写了库存行的单（同入账方式只在按公司选时计分，
        # 不交人的周期制账套不计）；答案的库存行与载荷真会写进 STCRD 的库存行都没有时不计
        answer["item"] = answer_items(doc) if (answer_items(doc) or values.get("item")) else None
    scored = score(answer, values) if payload else {}
    if verdict.cls == "C":
        memory.learn(decision["needed"], answer, item_codes)
    pushed_from = ""
    if len(supplier) == 13:
        # 这张推出去的单（A / B 照载荷；C 由模拟的人推：要人选的按答案选，没出载荷的学完记忆、公司选过入账方式后照线上再出一次，
        # 拿到人真正会推出去的载荷；还是出不来的按 `pushed_record` 的乐观近似）
        pushed_payload, pushed_from, _kind = payload, "payload", posting_kind
        if payload is None:
            config = configure()
            remembered = {kind: dict(values) for kind, values in memory.kinds.items()}
            _kind, _origin, _decision, pushed_payload, _ready, _reason = plan()
            pushed_from = "replanned" if pushed_payload else "approx"
        record = pushed_record(answer, pushed_payload,
                               payload_values(pushed_payload, book, fields,
                                              None if lines == "answer" else _kind or "service")
                               if pushed_payload else {},
                               [item.get("kind") for item in decision["needed"]], readback_scope)
        pushed.setdefault(supplier, []).append(dict(record, branch=branch, period=day.strftime("%Y-%m"),
                                                    entered=entered))
    prior.setdefault(_ref_key(m, fields["invoice_number"]), []).append(
        {"direction": direction, "party_tax_id": doc["party_tax"], "ref_no": fields["invoice_number"],
         "total": str(doc["total"])})
    period = day.strftime("%Y-%m")
    return {"company": book["company"], "book": book["key"], "docnum": doc["docnum"], "direction": direction,
            "period": period, "entered": entered.isoformat(), "class": verdict.cls, "reasons": list(verdict.reasons),
            "items": dict(verdict.items), "needed": [item.get("kind") for item in decision["needed"]],
            # 期间与选账套（10-17）：``outside`` 按单据（发票）日期、``posted_part`` 按入账日（APTRN / ARTRN.DOCDAT）比这份
            # 账套的期间（同 `outside` 的取值）；``crossyear`` 两个日子落在不同会计年（跨年补抵）；``book_pick`` 等见 `book_pick`
            "outside": outside(book, day), "posted_part": outside(book, doc.get("posted")),
            "crossyear": isinstance(doc.get("posted"), dt.date) and
            fiscal_year(book["period"][0], day) != fiscal_year(book["period"][0], doc["posted"]),
            **book_pick(m, shaped, book, picked, day), "period_origin": book.get("period_origin") or "",
            "posting_from": posting_from,
            "prepare": reason, "sources": {**{key: (payload or {}).get(key) for key in ("doctype_src", "account_source",
                                                                                         "item_src", "doctype")},
                                           "party_source": (decision.get("decided_by") or {}).get("party")},
            "pushed_from": pushed_from, "party_tax": doc["party_tax"],
            # 商品单（10-17）：答案走不走库存、有没有明细、用的入账类型、载荷明细过没过闸、存货历史与假如按它定档的档
            "answer_stock": bool(doc["stock"]), "detail": bool(doc.get("detail")), "posting_kind": posting_kind or "",
            "items_status": str((payload or {}).get("items_status") or ""), "posting_history": posting_history,
            "posting_tier_if_history": history_tier(posting_history),
            "posting_history_manual": posting_manual, "posting_tier_if_history_manual": history_tier(posting_manual),
            "memory_cap_lost": cap_lost,
            # 估上传日（10-17）：日子、来源（entered / vat_deadline）、按它重判的类别与原因（原因与现行一样时不重复存）
            "uploaded_alt": uploaded_alt.isoformat(), "uploaded_src": uploaded_src, "class_alt": alt.cls,
            **({} if list(alt.reasons) == list(verdict.reasons) else {"reasons_alt": list(alt.reasons)}),
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


def is_registered(book: Mapping[str, Any], registered: Optional[Mapping[str, set]]) -> bool:
    """这份账套在不在 Express 登记表里（`registered_books`；没给登记表都算在）：目录名登记了，且登记的那份税号就是这家（或取不到
    税号、只能按目录名认）。"""
    if registered is None:
        return True
    taxes = registered.get(Path(str(book.get("folder") or book["key"])).name.upper())
    if not taxes:
        return False
    return "" in taxes or digits(book.get("own_tax")) in taxes


def owners(books: Sequence[Mapping[str, Any]], registered: Optional[set] = None) -> Dict[Tuple, str]:
    """同一张单在几份账套里都有（Express 新年度账套是上一年复制过来的，带着往年的单；还有备份副本）：只在一份里回放。
    键 (方向, 单号, 日期, 对方码, 含税额) → 回放它的账套：本年期间盖住单据日期的那份（登记了的优先，再 ISPRD 的、单多的）；
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
        pool = sorted(pool, key=lambda book: (is_registered(book, registered), book.get("period_source") == "ISPRD",
                                              len(book["docs"]),
                                              book["period"][0] or dt.date.min, book["key"]), reverse=True)
        out[key] = pool[0]["key"]
    return out


def doc_key(doc: Mapping[str, Any]) -> Tuple:
    return (doc["direction"], doc["docnum"], doc["date"], doc["party_code"], str(doc["total"]))


def holders(books: Sequence[Mapping[str, Any]]) -> collections.Counter:
    """每张单（`doc_key`）在几份账套里有（复制来的新年度账套、备份副本）。"""
    found = collections.Counter()
    for book in books:
        for key in {doc_key(doc) for doc in book["docs"] if doc["date"]}:
            found[key] += 1
    return found


def judge_company(m, books: Sequence[Mapping[str, Any]], registered: Optional[Mapping[str, set]] = None, *,
                  lines: str = "none", names: str = "stkdes", choice_mode: str = "prior",
                  choice: Optional[Dict[str, Any]] = None, recent_max: int = RECENT_MAX,
                  readback_scope: str = "record") -> Tuple[List[Dict[str, Any]], collections.Counter]:
    """一家公司（各年度账套）按单据录入日顺序判；每份账套各自一份账套记忆与已推记录。同一张单只在一份账套里回放
    （`owners`）。``registered``（`registered_books`）：给了时按日期选账套与目录只认登记了的账套（线上目录 = 登记表），
    没登记的账套里的单照样判（选出的就不是它）。每行另记 ``unique``（这张单只在一份账套里有，`tiebreak` 只拿这种计分）、
    ``registered`` 与 ``entered_batch``（录入日是这份账套成批录入的日子，`BATCH_DAY_DOCS`）。``lines`` / ``names`` 见
    `perfect_fields`；``choice_mode``：模拟的人替公司选入账方式按 ``prior``（此前已录单的多数，10-17 主口径）或 ``full``（全期
    多数，对照）；两种「多数」都只在会因入账方式交人的单里数（`posting_population`，审查处理 10-17：原先整家全部单都算，费用车道的
    单偏向 service），此前的不到 `PRIOR_MIN` 张时退到全期多数（``full_fallback``）；给了 ``choice`` 时把这家选了什么、凭什么
    （`CompanyChoice.summary`）写进去。``recent_max`` / ``readback_scope`` 见 `pushed_evidence` / `pushed_record`。"""
    listed = [book for book in books if is_registered(book, registered)]
    catalog = [account_row(book) for book in listed]
    out, skipped = [], collections.Counter()
    queue = []
    owner = owners(books, registered)
    held = holders(books)
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
    profiles: Dict[str, bool] = {}
    population = [item for item in queue if posting_population(m, item[3], item[4], item[0], profiles)] or queue
    stock = sum(1 for item in population if item[4]["stock"])
    preferred = "stock" if stock * 2 >= len(population) else "service"
    # 此前已录单的多数（10-17）：会交人的那类单按录入日排好，录入日严格早于这一天的前缀里走库存的占不少于一半就是 stock；
    # 不到 `PRIOR_MIN` 张回 (None, 张数)
    days = [item[0] for item in population]
    running = [0]
    for item in population:
        running.append(running[-1] + int(bool(item[4]["stock"])))

    def before(entered: dt.date) -> Tuple[Optional[str], int]:
        count = bisect.bisect_left(days, entered)
        if count < PRIOR_MIN:
            return None, count
        return ("stock" if running[count] * 2 >= count else "service"), count

    # 语料一家公司一个目录：按公司选的入账方式一家一份
    company = CompanyChoice(preferred, full=preferred) if choice_mode == "full" else \
        CompanyChoice(None, before=before, full=preferred)
    shaped = shaped_books(m, listed)
    pushed = collections.defaultdict(dict)  # 每份账套：供应商 → 推出去的单（科目、现 / 赊、对方档，`pushed_evidence`）
    auto: set = set()  # 判成 A / B 的单（``id``）：存货历史「不含 CC 推的」下界口径从历史里剔掉（`judge_document`）
    for entered, _day, _number, book, doc in queue:
        row = judge_document(m, doc, book, memories[book["key"]], priors[book["key"]], catalog,
                             purchases[book["key"]], company, shaped, pushed[book["key"]], lines=lines, names=names,
                             recent_max=recent_max, readback_scope=readback_scope, auto=auto)
        if row["class"] in ("A", "B"):
            auto.add(id(doc))
        row.update(unique=held[doc_key(doc)] == 1, registered=is_registered(book, registered),
                   entered_batch=isinstance(doc.get("entered"), dt.date) and entered == doc["entered"]
                   and entered in (book.get("batch_days") or {}))
        out.append(row)
    if choice is not None:
        choice.update(company.summary())
    return out, skipped


# 两份以上同期账套时怎么挑（10-17，零费用模拟，不改判档）：none 照线上停下；recent_entry 认这张单录入前 Express 里最近有录入
# （GLJNL.CREDAT）的那份；recent_push 认之前模拟推进去的那份；ask_once 用户已定的口径：同一家、同一分店、本年或次年段、
# 同一组候选第一次停下时问人一次（模拟的人按答案选），之后同组照选，候选组合变了重新问
TIEBREAKS = ("none", "recent_entry", "recent_push", "ask_once")


def _last_before(days: Sequence[dt.date], day: dt.date) -> Optional[dt.date]:
    index = bisect.bisect_left(days, day)
    return days[index - 1] if index else None


def tiebreak(m, rows: Sequence[Mapping[str, Any]], books: Sequence[Mapping[str, Any]],
             strategies: Sequence[str] = TIEBREAKS) -> Dict[str, Dict[str, int]]:
    """一家公司的判定行（`judge_company` 的顺序，即录入日顺序）里按日期选出多份账套（``book_pick == many``）的单，各口径会挑
    哪份。只拿只在一份账套里有的单（``unique``）与答案账套（这张单所在的那份）比：``right`` 选对、``wrong`` 选错、``stopped``
    仍停（ask_once 问人那张也算停，``asked`` 另计；问到几份都有的单不记选择、下一张再问）；``shared`` 几份都有、答案账套
    不唯一的不计分；``freed`` 选对且 C 类原因只剩 ``book_many`` 的（挑对就不再是 C）。recent_push 各挑法各记各的「最近推进去的
    那份」：挑法选了哪份就记哪份（选错了之后也跟着错），停下由人推的记答案账套（审查处理 10-17：原先一律记答案账套，等于每张都
    自动纠正，偏乐观）。"""
    by_raw = {book["key"]: book for book in books}
    by_norm = {book_key(m, book): book for book in books}
    out = {name: collections.Counter() for name in strategies}
    asked: Dict[Tuple, str] = {}
    last_push: Dict[str, Dict[str, int]] = {name: {} for name in strategies}
    for index, row in enumerate(rows):
        book = by_raw[row["book"]]
        own = book_key(m, book)
        if row.get("book_pick") == "many":
            candidates = list(row.get("many_keys") or ())
            entered = dt.date.fromisoformat(row["entered"])
            for name in strategies:
                choice = None
                if name == "recent_entry":
                    latest = {key: _last_before(by_norm[key].get("entry_days") or [], entered)
                              for key in candidates if key in by_norm}
                    best = max((day for day in latest.values() if day), default=None)
                    winners = [key for key, day in latest.items() if best and day == best]
                    choice = winners[0] if len(winners) == 1 else None
                elif name == "recent_push":
                    pushed = last_push[name]
                    seen = {key: pushed[key] for key in candidates if key in pushed}
                    choice = max(seen, key=seen.get) if seen else None
                elif name == "ask_once":
                    combo = (book["branch"], tuple(row.get("many_parts") or [row.get("many_part")]), tuple(candidates))
                    choice = asked.get(combo)
                    if choice is None:
                        out[name]["asked"] += 1
                        if own in candidates and row.get("unique"):
                            # 模拟的人按答案选这份，记下这组候选；几份都有的单答案账套说不准，不记、下一张再问
                            asked[combo] = own
                counter = out[name]
                if not row.get("unique"):
                    counter["shared"] += 1
                elif choice is None:
                    counter["stopped"] += 1
                elif choice == own:
                    counter["right"] += 1
                    if row["class"] == "C" and list(row["reasons"]) == ["book_many"]:
                        counter["freed"] += 1
                else:
                    counter["wrong"] += 1
                # 这张单推进了哪份：挑法选了的就是它选的，停下由人推进答案账套
                last_push[name][choice or own] = index
        else:
            for name in strategies:
                last_push[name][own] = index  # 只选出一份（或选不出、由人推）：推进它所在的账套
    return {name: dict(counter) for name, counter in out.items()}


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


_DEADLINES: Dict[Tuple[int, int], dt.date] = {}


def vat_deadline(m, year: int, month: int) -> dt.date:
    """这一税期 ภ.พ.30 的截止日（电子申报有延期取电子的，`companies.automation._deadline` 同口径）。"""
    found = _DEADLINES.get((year, month))
    if found is None:
        found = m["tax_periods"].deadlines("pp30", year, month)
        found = _DEADLINES[(year, month)] = found.get("efiling") or found["paper"]
    return found


def readback_day(m, period: str) -> dt.date:
    """合格回读：这一期 ภ.พ.30 截止日（`vat_deadline`）之后一天发起。"""
    return vat_deadline(m, int(period[:4]), int(period[5:7])) + dt.timedelta(days=1)


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


# Express 答案作金标（生产机导出那组，`gold_source = express_answer`）：答案是靠识别结果配上的（`eval-invoices.match`，单号 /
# 对方税号 / 金额至少两项与识别一致），配对用到的项对识别来说是「自证」，不计分（审查处理 10-16）
EXPRESS_GOLD = "express_answer"
SELF_FIELDS = {"doc_no": ("invoice_number",), "party_tax": ("party_tax",), "amount": ("subtotal", "vat", "total")}
# 金标票种（`doc_kind`，看原件定的）→ 能不能抵进项：完整税票类按「印了税额、写明买方」，收据类不抵，其余（水电、海关、油票……）
# 票种本身定不了，不计分
CLAIM_KINDS = ("tax_invoice", "receipt_tax_invoice")
NO_CLAIM_KINDS = ("receipt", "bill_invoice", "ecommerce_order", "credit_note")


def compare_gold(fields: Mapping[str, Any], gold: Mapping[str, Any], direction: str,
                 source: str = "") -> Dict[str, str]:
    """识别出的一张票与金标逐字段：ok / wrong / prefix_only（单号只差字母前缀，不算错）/ n/a（金标里没印）/ self（Express 答案
    作金标时配对用到的项，自证、不计分）。"""
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
    if source == EXPRESS_GOLD:
        for hit in gold.get("matched_on") or ():
            for key in SELF_FIELDS.get(hit, ()):
                out[key] = "self"
    return out


def claim_gold(gold: Mapping[str, Any], kind: str, source: str) -> Optional[bool]:
    """这张票该不该抵进项（金标口径）：独立金标按看原件定的票种（`CLAIM_KINDS` 印了税额、写明买方才抵；`NO_CLAIM_KINDS` 不抵；
    其余定不了回 None）；Express 答案作金标按会计实际记了进项税没有（税额 > 0）。"""
    try:
        vat = Decimal(str(gold.get("vat") or "0"))
    except (InvalidOperation, ValueError):
        vat = Decimal(0)
    if source == EXPRESS_GOLD:
        return vat > 0
    if kind in CLAIM_KINDS:
        return vat > 0 and bool(digits(gold.get("buyer_tax")) or str(gold.get("buyer_name") or "").strip())
    if kind in NO_CLAIM_KINDS:
        return False
    return None


def vat_claim(m, fields: Mapping[str, Any], text: Optional[str], gold: Mapping[str, Any], kind: str,
              source: str) -> Dict[str, Any]:
    """进项、识别出税额的票：识别结果按方向推断抵不抵（`field_rules.judge_direction`），文字层核票种后的档（`automation.tiers`
    同一规则：判抵且原文核得到完整税票与买方税号 A、核不到 B；判不抵且识别票种与原文标题都正是简式税票 / 收据 A、否则 B），与金标口径比。"""
    from pearnly_cowork.push.express.field_rules import judge_direction

    kind_ocr, _route = judge_direction(dict(fields), today=dt.date.today())
    claims = kind_ocr == "purchase_invoice"
    if claims:
        tier = "A" if m["automation"].claim_in_text("purchase", fields, text) else "B"
    else:
        tier = "A" if m["automation"].no_claim_in_text(fields, text) else "B"
    want = claim_gold(gold, kind, source)
    return {"claims": claims, "tier": tier, "gold": want,
            "result": "n/a" if want is None else "ok" if want == claims else "wrong"}


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


def load_books(args, corpus: Path, periods: str) -> Tuple[Dict[str, List[Tuple[Path, Dict[str, Any]]]], Calendars]:
    """按 ``--company`` 读答案账套（`answer_books`）：{公司: [(目录, `load_book`)]}；会计期间来源见 `fiscal_calendars`。"""
    calendars = fiscal_calendars(Path(args.express) if args.express else None,
                                 [corpus / "_脚本" / name for name in ("expsets.tsv", "expsets7.tsv")],
                                 Path(args.isprd) if getattr(args, "isprd", None) else None)
    loaded = collections.defaultdict(list)
    for company, tax, folder in answer_books(corpus, args.company):
        found = calendars.find(folder.name, tax)
        loaded[company].append((folder, load_book(folder, company=company, own_tax=tax, key=folder.name,
                                                  calendar=found[0] if found else None,
                                                  origin=found[1] if found else "testcopy", periods=periods)))
    return loaded, calendars


def cmd_inventory(args) -> int:
    """盘点按 estimated 读期间（没有 ISPRD 的按单据估，``period_origin`` 标出），估出来的重叠也列出来。"""
    corpus, out = refuse_lan(args.corpus), refuse_lan(args.out)
    books, coverage, overlaps = [], collections.Counter(), {}
    loaded, calendars = load_books(args, corpus, "estimated")
    registered = registered_books(Path(args.registry) if args.registry else None)
    for company, pairs in sorted(loaded.items()):
        owner = owners([book for _folder, book in pairs], registered)
        keys = {book["key"]: {doc_key(doc) for doc in book["docs"] if doc["date"] and not doc["cancelled"]}
                for _folder, book in pairs}
        for folder, book in pairs:
            others = set().union(*[found for key, found in keys.items() if key != book["key"]])
            _inventory_book(company, folder, book, owner, books, coverage,
                            shared=len(keys[book["key"]] & others),
                            registered=None if registered is None else is_registered(book, registered))
        overlaps[company] = book_overlaps([book for _folder, book in pairs], keys)
    _inventory_real(args, books, coverage, out, overlaps, calendars.conflicts())
    return 0


def _span_overlap(one: Tuple, two: Tuple) -> Optional[Tuple[dt.date, dt.date]]:
    if not all(one) or not all(two):
        return None
    start, end = max(one[0], two[0]), min(one[1], two[1])
    return (start, end) if start <= end else None


def book_overlaps(books: Sequence[Mapping[str, Any]], keys: Mapping[str, set]) -> List[Dict[str, Any]]:
    """同一家同一分店两两账套的期间重叠（`identity.for_day` 会两份都选出的那种）：本年段与本年段、次年段与次年段各自重叠的
    起止、两边期间来源，以及重叠段里只在这一份里有的单数（``only``，按 `doc_key`；复制来的往年单两份都有，不算）。"""
    out = []
    for index, one in enumerate(books):
        for two in books[index + 1:]:
            if one["branch"] != two["branch"]:
                continue
            spans = {"current": _span_overlap(one["period"], two["period"]),
                     "next": _span_overlap((one["period"][1] and one["period"][1] + dt.timedelta(days=1),
                                            one.get("next_end")),
                                           (two["period"][1] and two["period"][1] + dt.timedelta(days=1),
                                            two.get("next_end")))}
            if not any(spans.values()):
                continue

            def only(book, other):
                found = collections.Counter()
                for doc in book["docs"]:
                    if doc["cancelled"] or not doc["date"] or doc_key(doc) in keys[other["key"]]:
                        continue
                    for part, span in spans.items():
                        if span and span[0] <= doc["date"] <= span[1]:
                            found[part] += 1
                return dict(found)

            out.append({"books": [one["key"], two["key"]], "branch": one["branch"],
                        "origins": [one.get("period_origin"), two.get("period_origin")],
                        **{part: [str(day) for day in span] if span else None for part, span in spans.items()},
                        "only": [only(one, two), only(two, one)]})
    return out


def _inventory_book(company, folder, book, owner, books, coverage, *, shared: int = 0,
                    registered: Optional[bool] = None) -> None:
    """一份账套的盘点行（只数这份账套「拥有」的单，见 `owners`）。期间检验（10-17）：``posted_outside`` 入账日
    （APTRN / ARTRN.DOCDAT）落在本年起～次年止之外的单数（Express 只在 24 期里记账，真 ISPRD 应约等于 0；不为 0 说明期间快照与
    答案不同期）；``last_entered`` 最后一张凭证录入日；``shared_docs`` 与同公司别的账套相同的单数（复制 / 备份）；
    ``registered`` 在不在登记表（给了 ``--registry`` 才有）。"""
    tables = folder / "原表"
    present = [name for name in ANSWER_TABLES if (tables / f"{name}.DBF").is_file()]
    docs = book["docs"]
    usable = [doc for doc in docs if not eligible(doc) and owner.get(doc_key(doc)) == book["key"]]
    purchases = [doc for doc in usable if doc["direction"] == "purchase"]
    rectyp = collections.Counter(f"{doc['direction']}:{doc['rectyp'] or '-'}" for doc in docs if not doc["cancelled"])
    for doc in usable:
        coverage[(company, doc["date"].strftime("%Y-%m"))] += 1
    live = [doc for doc in docs if not doc["cancelled"] and isinstance(doc.get("posted"), dt.date)]
    books.append({
        "company": company, "book": folder.name, "tables": present,
        "fields": {name: [field for field in ("ACCNUM", "SUPCOD", "CUSCOD", "RECTYP", "CREDAT", "USERID")
                          if field in dbf_fields(tables / f"{name}.DBF")]
                   for name in ("APTRN", "ARTRN", "GLJNL", "GLJNLIT")},
        "period": [str(day) for day in book["period"]], "period_source": book["period_source"],
        "next_end": str(book.get("next_end") or ""), "period_origin": book.get("period_origin") or "",
        "branch": book["branch"],
        "posted_outside": sum(1 for doc in live if outside(book, doc["posted"]) in ("after", "before")
                              or (outside(book, doc["posted"]) == "next_year" and not book.get("next_end"))),
        "posted_checked": len(live) if all(book["period"]) else 0,
        "last_entered": str((book.get("entry_days") or [""])[-1]),
        "shared_docs": shared, "registered": registered,
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
        # 商品单与上传日（10-17 第三段）：几张表的全部列名（核有没有 CHGDAT / CRETIM 一类时间列、VATDAT 的意思）；凭证录入日
        # 晚于单据日几天、进项税期晚于单据月几个月、税登记 VATDAT 晚于单据日几天的分桶；同一天录入 ≥200 张的日子（疑似导入 /
        # 重建）；存货明细挂单统计（PEOPLE 空 / 是别家）与答案走库存 / 只有非库存明细的张数
        "columns": {name: dbf_fields(tables / f"{name}.DBF") for name in ("APTRN", "ARTRN", "GLJNL", "ISVAT", "STCRD")},
        "entry_lag": dict(collections.Counter(_lag_bucket((doc["entered"] - doc["date"]).days)
                                              if isinstance(doc["entered"], dt.date) else "none" for doc in usable)),
        "vat_lag": dict(collections.Counter(
            _month_bucket((doc["vat_period"].year - doc["date"].year) * 12 + doc["vat_period"].month - doc["date"].month)
            if isinstance(doc.get("vat_period"), dt.date) else "none" for doc in purchases)),
        "vatdat_lag": dict(collections.Counter(_lag_bucket((doc["vat_day"] - doc["date"]).days)
                                               if isinstance(doc.get("vat_day"), dt.date) else "none"
                                               for doc in purchases)),
        "batch_days": [{"day": str(day), "vouchers": count} for day, count in
                       sorted((book.get("batch_days") or {}).items())],
        "batch_docs": sum(1 for doc in usable if doc["entered"] in (book.get("batch_days") or {})),
        "stcrd": dict(book.get("stcrd") or {}),
        "stock_answers": sum(1 for doc in usable if doc.get("stock")),
        "detail_only": sum(1 for doc in usable if doc.get("detail") and not doc.get("stock")),
    })
    print(f"{company} {folder.name}: {len(usable)}/{len(docs)}", flush=True)


def _lag_bucket(days: int) -> str:
    """滞后天数分桶（录入日、VATDAT 比单据日期）。"""
    if days < 0:
        return "<0"
    return next((f"<={edge}" for edge in (7, 30, 60, 90) if days <= edge), ">90")


def _month_bucket(months: int) -> str:
    """进项税期比单据月份晚几个月的分桶（泰国进项最多晚 6 个月抵）。"""
    if months < 0:
        return "<0"
    return str(months) if months <= 3 else "4-6" if months <= 6 else ">6"


def _inventory_real(args, books, coverage, out, overlaps=None, conflicts=()) -> None:
    real = collections.Counter()
    if args.gold:
        gold, sample = _gold(Path(args.gold))
        for number, item in gold.items():
            row = sample.get(number) or {}
            for invoice in item.get("invoices") or []:
                real[(row.get("公司") or "?", str(invoice.get("date") or "")[:7] or "?", row.get("形态") or "?",
                      row.get("有答案") or "?")] += 1
    _write_json(out / "inventory.json", {
        "books": books, "overlaps": overlaps or {}, "calendar_conflicts": list(conflicts), "periods": [
            {"company": company, "period": period, "docs": count} for (company, period), count in sorted(coverage.items())],
        "real_results": [{"company": c, "period": p, "form": f, "answers": a, "invoices": n}
                         for (c, p, f, a), n in sorted(real.items())]})
    print(f"books {len(books)}", flush=True)


def cmd_judge(args) -> int:
    m = _modules()
    corpus, out = refuse_lan(args.corpus), refuse_lan(args.out)
    loaded, _calendars = load_books(args, corpus, args.periods)
    registered = registered_books(Path(args.registry) if args.registry else None)
    strategies = [name.strip() for name in str(args.tiebreak or "").split(",") if name.strip()]
    unknown = sorted(set(strategies) - set(TIEBREAKS))
    if unknown:
        raise SystemExit(f"unknown --tiebreak {','.join(unknown)} (choose from {','.join(TIEBREAKS)})")
    lines = args.lines if args.lines == "none" else f"{args.lines}:{args.line_names}"
    for company, pairs in sorted(loaded.items()):
        books = [book for _folder, book in pairs]
        choice: Dict[str, Any] = {}
        judged, why = judge_company(m, books, registered, lines=args.lines, names=args.line_names,
                                    choice_mode=args.company_choice, choice=choice, recent_max=args.recent_max,
                                    readback_scope=args.readback_scope)
        base = m["automation_rules"].THRESHOLDS
        tiers = {name: timeline(m, judged, value or base) for name, value in THRESHOLD_SETS.items()}
        tiers["A_task"] = timeline(m, judged, base, trigger="task")
        # 一家一个文件（可以分两三路并行跑），`report` 合并；期间口径与选账套模拟一并记下
        _write_json(out / "judge" / f"{company.replace('/', '_')}.json",
                    {"rows": judged, "skipped": {company: dict(why)}, "tiers": {company: tiers},
                     "tiebreak": {company: tiebreak(m, judged, books, strategies)},
                     "origins": {company: dict(collections.Counter(book["period_origin"] for book in books))},
                     "periods": args.periods, "registry": bool(registered is not None),
                     # 商品单（10-17）：票面明细口径、替公司选入账方式的口径与这家选了什么
                     "lines": lines, "company_choice": args.company_choice, "goods_posting": {company: choice},
                     # 账套记忆的证据口径（审查处理 10-17）：最新几张（0 不设上限）、回读按整张记录还是只比科目
                     "recent_max": args.recent_max, "readback_scope": args.readback_scope,
                     "evidence_window": EVIDENCE_WINDOW})
        print(f"{company}: {len(judged)} docs, " + ", ".join(
            f"{key} {sum(1 for row in judged if row['class'] == key)}" for key in "ABC"), flush=True)
    return 0


def load_judge(out: Path) -> Optional[Dict[str, Any]]:
    files = sorted((out / "judge").glob("*.json")) if (out / "judge").is_dir() else []
    if not files:
        return None
    merged: Dict[str, Any] = {"rows": [], "skipped": {}, "tiers": {}, "tiebreak": {}, "origins": {}, "periods": set(),
                              "lines": set(), "company_choice": set(), "goods_posting": {}, "recent_max": set(),
                              "readback_scope": set(), "evidence_window": set()}
    for path in files:
        part = json.loads(path.read_text(encoding="utf-8"))
        merged["rows"].extend(part["rows"])
        merged["skipped"].update(part["skipped"])
        merged["tiers"].update(part["tiers"])
        merged["tiebreak"].update(part.get("tiebreak") or {})
        merged["origins"].update(part.get("origins") or {})
        merged["periods"].add(part.get("periods") or "estimated")  # 第四轮及以前的存档没有这一项：都是估的
        merged["lines"].add(part.get("lines") or "none")  # 第五轮第二段及以前都不造明细
        merged["company_choice"].add(part.get("company_choice") or "full")  # 第四轮按全期多数
        merged["goods_posting"].update(part.get("goods_posting") or {})
        merged["recent_max"].add(str(part.get("recent_max", 0)))  # 第五轮第一段及以前不设上限
        merged["readback_scope"].add(part.get("readback_scope") or "record")
        merged["evidence_window"].add(part.get("evidence_window") or "newest")  # 第五轮及以前：截最新几张再跳过
    for key in ("periods", "lines", "company_choice", "recent_max", "readback_scope", "evidence_window"):
        merged[key] = sorted(merged[key])
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
            source = str(truth.get("gold_source") or "")
            result = compare_gold(invoice.get("fields") or {}, golds[index], direction, source) \
                if index is not None else {}
            fields = invoice.get("fields") or {}
            claim = None
            if direction == "purchase" and index is not None and money(fields.get("vat")) > 0:
                number_page = int(invoice.get("page") or 1)
                count = max(1, int(merged.get("pages") or invoice.get("pages") or 1))
                text = "\n".join(texts[number_page - 1:number_page - 1 + count]) if texts else None
                claim = vat_claim(m, fields, text, golds[index], str(truth.get("doc_kind") or ""), source)
            uncertain = [path for path in truth.get("uncertain") or [] if path.startswith(f"invoices[{index}]")] \
                if index is not None else []
            rows.append({"id": number, "form": row.get("形态"), "company": row.get("公司"), "direction": direction,
                         "page": invoice.get("page"), "text_pdf": "image" not in reasons, "reasons": reasons,
                         "passed": not reasons, "entry": truth.get("entry"), "paired": index is not None,
                         "result": result, "uncertain": uncertain, "gold_source": source or "independent",
                         "vat_claim": claim,
                         "wrong": [key for key, value in result.items() if value == "wrong"]})
    _write_json(out / "gate.json", {"rows": rows, "records": args.records, "flags": args.flags,
                                    "manifest": str(args.manifest)})
    print(f"gate rows {len(rows)}", flush=True)
    return 0


BOOK_REASONS = {"book", "book_many", "book_none"}


def _picked(row: Mapping[str, Any], pick: str) -> bool:
    """这张 C 单因按日期选账套没选到这份停下，且选法是 ``pick``（`book_pick`）。"""
    return row.get("book_pick") == pick and bool(BOOK_REASONS & set(row["reasons"]))


# C 类按「主要原因」归一类（按这个顺序取第一个命中的），方便按类修。期间与选账套（10-17）按 `book_pick` 拆开「真两份同期 /
# 估算重叠 / 测试账套期间 / 新年度已建 / 跨年补抵」（70EXP 测试账套是测试窗口可随意改写的副本，它的 ISPRD 不等于线上读到的真
# ISPRD，单列，审查处理 10-17）；期间外的再按入账日拆：入账日也在期间外是期间快照与答案不同期（回放偏差），只有发票日
# 在外是补抵的真问题
CAUSES = (
    ("posting", "商品单没声明入账类型（永续 / 混合库存账套交人）", lambda row: str(row.get("prepare") or "").startswith("posting_needs_review")),
    ("needed", "第一次见、要人选（对方档 / 科目 / 现赊）", lambda row: bool(row.get("needed"))),
    ("period_unknown", "推往账套读不到会计期间（没有 ISPRD；strict 同线上不报期间）",
     lambda row: "period_unknown" in row["reasons"]),
    ("book_many_estimated", "按日期选出两份以上同期账套，候选里有按单据估的期间（回放偏差）",
     lambda row: _picked(row, "many") and bool(row.get("many_estimated"))),
    ("book_many_testcopy", "按日期选出两份以上同期账套，候选里有 70EXP 测试账套的期间（测试账套可能被改过，待核）",
     lambda row: _picked(row, "many") and "testcopy" in (row.get("many_origins") or ())),
    ("book_many_real", "按日期选出两份以上同期账套，候选期间都是答案自带或只读导出的 ISPRD（真问题）",
     lambda row: _picked(row, "many")),
    ("book_none_unknown", "一份都选不出：同分店有账套期间没报全，核不了新年度账套",
     lambda row: _picked(row, "none") and bool(row.get("none_unknown"))),
    ("book_other_newer", "单据在账套次年段、新年度账套已建（该推往新账套）",
     lambda row: _picked(row, "other") and bool(row.get("other_newer"))),
    ("book_other_crossyear", "选出另一份：发票日与入账日跨会计年（跨年补抵）",
     lambda row: _picked(row, "other") and bool(row.get("crossyear"))),
    ("book_other", "选出另一份（其他）", lambda row: _picked(row, "other")),
    # 第四轮及以前的存档没有 book_pick，照旧口径归类
    ("next_year", "单据在账套次年段、新年度账套已建（旧存档口径）",
     lambda row: "book_pick" not in row and row.get("outside") == "next_year"
     and bool({"period", "book"} & set(row["reasons"]))),
    ("after_snapshot", "单据晚于账套次年段，入账日也在期间外（期间快照与答案不同期，回放偏差）",
     lambda row: row.get("outside") == "after" and row.get("posted_part") in ("after", "before")),
    ("after", "单据晚于账套次年段（入账日在期间内：补抵的真问题）", lambda row: row.get("outside") == "after"),
    ("before_snapshot", "单据早于账套本年，入账日也在期间外（期间快照与答案不同期，回放偏差）",
     lambda row: row.get("outside") == "before" and row.get("posted_part") in ("after", "before")),
    ("before", "单据早于账套本年（入账日在期间内：补抵的真问题）", lambda row: row.get("outside") == "before"),
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


def _posting_answer(row: Mapping[str, Any]) -> str:
    return "stock" if row.get("answer_stock") else "service"


def goods_summary(rows: Sequence[Mapping[str, Any]], choices: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """商品单（10-17 第三段）：按公司选入账方式的单（``posting_from == company_choice``）。

    - ``companies``：每家选了什么（``used``、凭什么 ``by``、那一刻之前的多数 ``prior`` 与张数 ``prior_docs``、全期多数 ``full``）、
      这类单张数、答案走库存的、只有非库存明细的（``detail_only``：旧口径算走库存、新口径不算）、与选的值不同的（``disagree_used``）、
      与全期多数不同的（``disagree_full``）——替换第四轮「0–15% 与会计记法不同」的口径；
    - ``history``（`posting_tier_if_history`，线上同一判据 `automation.posting_by_history`，回放主判定不带）：加上「这家供应商之前的
      Express 存货历史」一层，A（与公司选的一致、载荷正好按它写）/ no_lines（一致但定成走库存、载荷写不出库存行）/ items（存货没
      着落）/ named（定成记费用却有库存行或与库存品同名）/ keyed（判据成立但对方没有 13 位税号、历史按名字对上，线上封顶 B）/
      B（没定下）/ conflict（相反，线上交人）各几张（no_lines、items、named、keyed 线上不给 A）；A 里入账方式
      计分错的（``A_wrong``，按 ``scored["posting"]``，同报表计分口径）；现在只因入账方式是 B、会升 A 的（``extra_A``，其中整张推错
      ``extra_A_wrong``、入账方式本身判错 ``extra_A_posting_wrong``）；相反会多出的 C（``conflict_C``，其中真拦住了入账方式错的
      ``conflict_caught``）。``history_manual`` 同上，只是把之前判成 A / B 的单当 CC 推的剔掉（不含 CC 推的下界；``history`` 是
      上界）。``--lines none`` 那两遍票面没有明细，定成走库存的都落 no_lines，只看 answer 两遍的 ``extra_A``（审查处理 10-17）。
      按家另列一份。"""
    chosen = [row for row in rows if row.get("posting_from") == "company_choice"]

    def posting_wrong(row) -> bool:
        return (row.get("scored") or {}).get("posting", "n/a") not in ("ok", "n/a")

    def history(selected, key="posting_tier_if_history") -> Dict[str, int]:
        found = collections.Counter()
        for row in selected:
            tier = row.get(key) or "B"
            wrong = posting_wrong(row)
            found[tier] += 1
            if tier == "A":
                found["A_wrong"] += int(wrong)
                if row["class"] == "B" and list(row["reasons"]) == ["judged:posting"]:
                    found["extra_A"] += 1
                    found["extra_A_wrong"] += int(not _row_ok(row))
                    found["extra_A_posting_wrong"] += int(wrong)
            elif tier == "conflict":
                found["conflict_C"] += int(row["class"] in ("A", "B"))
                found["conflict_caught"] += int(wrong)
        return dict(found)

    companies = {}
    for company in sorted({row["company"] for row in chosen} | set(choices)):
        mine = [row for row in chosen if row["company"] == company]
        picked = dict(choices.get(company) or {})
        companies[company] = {
            **picked, "docs": len(mine), "stock": sum(1 for row in mine if row.get("answer_stock")),
            "detail_only": sum(1 for row in mine if row.get("detail") and not row.get("answer_stock")),
            "disagree_used": sum(1 for row in mine if _posting_answer(row) != picked.get("used")),
            "disagree_full": sum(1 for row in mine if picked.get("full") and _posting_answer(row) != picked["full"]),
            "history": history(mine), "history_manual": history(mine, "posting_tier_if_history_manual")}
    return {"docs": len(chosen), "history": history(chosen),
            "history_manual": history(chosen, "posting_tier_if_history_manual"),
            "items_status": dict(collections.Counter(row.get("items_status") or "-" for row in rows
                                                     if row.get("items_status"))),
            "companies": {company: value for company, value in companies.items() if value["docs"] or value.get("used")}}


def upload_summary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """录入晚 60 天（10-17 第三段）：现行（上传日 = 凭证录入日）与估上传日（`upload_proxy`，只重判日期核对）两种假设下 date_stale
    的张数（按方向；估上传日另按来源），估上传日下的 A / B / C 与 A 类准确率（计分不变），C 变成不是 C 的张数，以及录入日是账套成批
    录入日子的（``entered_batch``，疑似导入 / 重建，单列）。"""
    def alt(row):
        return row.get("reasons_alt", row["reasons"])

    stale = [row for row in rows if "date_stale" in row["reasons"]]
    stale_alt = [row for row in rows if "date_stale" in alt(row)]
    return {"stale_cause": sum(1 for row in rows if row["class"] == "C" and primary_cause(row) == "stale"),
            "stale": dict(collections.Counter(row["direction"] for row in stale)),
            "stale_alt": dict(collections.Counter(row["direction"] for row in stale_alt)),
            "stale_alt_sources": dict(collections.Counter(f"{row['direction']}|{row.get('uploaded_src') or '-'}"
                                                          for row in stale_alt)),
            "sources": dict(collections.Counter(row.get("uploaded_src") or "-" for row in rows)),
            "stale_batch": sum(1 for row in stale if row.get("entered_batch")),
            "classes_alt": dict(collections.Counter(row.get("class_alt") or row["class"] for row in rows)),
            "freed": dict(collections.Counter(row["direction"] for row in rows
                                              if row["class"] == "C" and (row.get("class_alt") or "C") != "C")),
            "A_alt": _accuracy([row for row in rows if (row.get("class_alt") or row["class"]) == "A"], "correct_eval")}


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
    sources = {"account": "account_source", "payment": "doctype_src", "vat": "item_src", "party": "party_source"}
    items: Dict[str, Dict[str, int]] = collections.defaultdict(lambda: collections.Counter())
    for row in a_rows + b_rows:
        for item, value in row["scored"].items():
            if value == "n/a":
                continue
            source = (row.get("posting_from") if item == "posting" else
                      (row.get("sources") or {}).get(sources.get(item, ""))) or "-"
            key = f"{row['direction']}|{item}|{source}"
            items[key]["n"] += 1
            items[key]["wrong"] += int(value != "ok")
    # 账套记忆定的项（10-17 推广「连续 3 张有证据」）：各家 A / B 张数与 A 里错的（第四轮 ASIA-FA 现 / 赊 260、对方档 65）。
    # vat 是记过的小票抵进项（``item_src == choice``），还是「记一次就 A」，只量不改（审查处理 10-17，设计文档登记为例外）
    memory: Dict[str, Dict[str, Dict[str, int]]] = {kind: collections.defaultdict(collections.Counter)
                                                    for kind in ("account", "payment", "party", "vat")}
    for row in a_rows + b_rows:
        for kind in memory:
            remembered = (row.get("sources") or {}).get(sources[kind]) == ("choice" if kind == "vat" else "remembered")
            if not remembered or row["items"].get(kind) not in ("A", "B"):
                continue
            tier = row["items"][kind]
            counter = memory[kind][row["company"]]
            counter[tier] += 1
            if row["class"] == "A" and row["scored"].get(kind, "n/a") not in ("ok", "n/a"):
                counter["A_wrong"] += 1
    without_payment = [dict(row, correct=all(value in ("ok", "n/a") for item, value in row["scored"].items()
                                             if item != "payment") and bool(row["scored"])) for row in a_rows]
    sales_payment = [row for row in a_rows + b_rows if row["direction"] == "sales"
                     and row["scored"].get("payment", "n/a") != "n/a"]
    # 期间与选账套（10-17）：期间口径、各家账套期间来源、按日期选账套的情形（全部 / C 类）、选多份时各挑法的模拟
    picks = {"all": dict(collections.Counter(row.get("book_pick") or "-" for row in rows)),
             "C": dict(collections.Counter(
                 (row.get("book_pick") or "-") + (":estimated" if row.get("many_estimated") else
                                                  ":testcopy" if "testcopy" in (row.get("many_origins") or ()) else "")
                 for row in rows if row["class"] == "C"))}
    origins = collections.Counter()
    for found in (data.get("origins") or {}).values():
        origins.update(found)
    tiebreaks: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for found in (data.get("tiebreak") or {}).values():
        for name, counter in found.items():
            tiebreaks[name].update(counter)
    return {"docs": len(rows), "classes": dict(by_class), "A": _accuracy(a_rows), "B": _accuracy(b_rows),
            "periods": list(data.get("periods") or []), "origins": dict(origins), "book_picks": picks,
            "lines": list(data.get("lines") or []), "company_choice": list(data.get("company_choice") or []),
            "recent_max": list(data.get("recent_max") or []), "readback_scope": list(data.get("readback_scope") or []),
            "evidence_window": list(data.get("evidence_window") or []),
            "goods": goods_summary(rows, data.get("goods_posting") or {}), "upload_alt": upload_summary(rows),
            "tiebreak": {name: dict(counter) for name, counter in tiebreaks.items()},
            "tiebreak_companies": {company: found for company, found in sorted((data.get("tiebreak") or {}).items())
                                   if any(sum(counter.values()) for counter in found.values())},
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
            "memory_sources": {kind: {company: dict(counter) for company, counter in sorted(found.items())}
                               for kind, found in memory.items()},
            # 证据窗口往回最多看 `RECENT_MAX` 张推送：记忆定的项因此是 B、不设上限会是 A 的张数（按类、按家）
            "memory_cap_lost": {kind: dict(collections.Counter(row["company"] for row in rows
                                                               if kind in (row.get("memory_cap_lost") or ())))
                                for kind in ("account", "payment", "party")},
            # 回放里「推出去的单」从哪来：payload 照载荷、replanned C 类学完记忆再出的载荷、approx 推不出（乐观近似）
            "pushed_from": dict(collections.Counter(row.get("pushed_from") or "-" for row in rows)),
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
    independent = [row for row in text_rows if row.get("gold_source", "independent") == "independent"]
    express = [row for row in text_rows if row.get("gold_source") == EXPRESS_GOLD]
    claims = [row for row in text_rows if row["passed"] and row.get("vat_claim")]
    return {"all": part(rows), "text_pdf": part(text_rows), "image": part(image_rows),
            # 只有独立金标（看原件定的）进准确率与合并估计；Express 答案作金标的单列，只比配对没用到的项（审查处理 10-16）
            "text_pdf_independent": part(independent), "text_pdf_express": part(express),
            "express_fields": dict(collections.Counter(
                f"{key}:{value}" for row in express if row["passed"] for key, value in row["result"].items())),
            "vat_claim": {"n": len(claims),
                          "by": dict(collections.Counter(
                              f"{row['vat_claim']['tier']}:{'抵' if row['vat_claim']['claims'] else '不抵'}:"
                              f"{row['vat_claim']['result']}" for row in claims)),
                          "a_wrong": sum(1 for row in claims if row["vat_claim"]["tier"] == "A"
                                         and row["vat_claim"]["result"] == "wrong")},
            "by_form": {form: part([row for row in rows if row["form"] == form]) for form in sorted({row["form"] or "?" for row in rows})}}


def _rate(counter: Mapping[str, int]) -> str:
    scored = sum(counter.get(key, 0) for key in ("right", "wrong", "stopped"))
    return pct(counter.get("right", 0) / scored if scored else None)


def _render_books(s: Mapping[str, Any]) -> List[str]:
    """报表里「期间与选账套」一节（10-17）：期间口径与来源、按日期选账套的情形、选多份时各挑法的模拟（只拿独有单计分）。"""
    lines = [f"期间口径 {'/'.join(s.get('periods') or []) or '—'}；账套期间来源（份）："
             + "、".join(f"{key} {value}" for key, value in sorted((s.get("origins") or {}).items())) + "。", "",
             "按日期选账套（self 正好是推往的这份 / many 多份同期 / none 选不出 / other 另一份）：全部 "
             + "、".join(f"{key} {value}" for key, value in sorted(((s.get("book_picks") or {}).get("all") or {}).items()))
             + "；C 类 " + "、".join(f"{key} {value}" for key, value in
                                    sorted(((s.get("book_picks") or {}).get("C") or {}).items())) + "。", ""]
    found = s.get("tiebreak") or {}
    if found:
        lines += ["选出多份同期账套时各挑法（模拟，只拿只在一份账套里有的单与答案账套比；ask_once = 按公司、分店、本年 / 次年段、"
                  "候选组合问一次）：", "",
                  "| 挑法 | 选对 | 选错 | 仍停 | 其中问人 | 选对率 | 选对后不再是 C | 几份都有、不计分 |", "|---|---|---|---|---|---|---|---|",
                  *[f"| {name} | {value.get('right', 0)} | {value.get('wrong', 0)} | {value.get('stopped', 0)} | "
                    f"{value.get('asked', 0)} | {_rate(value)} | {value.get('freed', 0)} | {value.get('shared', 0)} |"
                    for name, value in found.items()], ""]
        per = s.get("tiebreak_companies") or {}
        names = list(found)
        if per:
            lines += ["按家（选对 / 选错 / 仍停）：", "", "| 公司 | " + " | ".join(names) + " |",
                      "|---|" + "---|" * len(names),
                      *[f"| {company} | " + " | ".join(
                          f"{(value.get(name) or {}).get('right', 0)} / {(value.get(name) or {}).get('wrong', 0)} / "
                          f"{(value.get(name) or {}).get('stopped', 0)}" for name in names) + " |"
                        for company, value in per.items()], ""]
    return lines


def _render_upload(s: Mapping[str, Any]) -> List[str]:
    """C 类主因表后的「录入晚 60 天」对照一行（10-17）。"""
    found = s.get("upload_alt")
    if not found:
        return []
    stale, alt = found.get("stale") or {}, found.get("stale_alt") or {}
    by_source = found.get("stale_alt_sources") or {}
    classes = found.get("classes_alt") or {}
    a_alt = found.get("A_alt") or {}
    return [f"录入晚 60 天：上传日按凭证录入日 {sum(stale.values())} 张（进项 {stale.get('purchase', 0)}、销项 "
            f"{stale.get('sales', 0)}；作主因 {found.get('stale_cause', 0)} 张，其中录入日是账套成批录入日子的 "
            f"{found.get('stale_batch', 0)} 张）；按「录入日与税期 ภ.พ.30 申报截止日取早」{sum(alt.values())} 张（进项 "
            f"{alt.get('purchase', 0)}、销项 {alt.get('sales', 0)}；上传日取了截止日的共 "
            f"{(found.get('sources') or {}).get('vat_deadline', 0)} 张，其中仍录入晚 "
            f"{sum(v for k, v in by_source.items() if k.endswith('|vat_deadline'))} 张）；按后者 A / B / C = "
            f"{classes.get('A', 0)} / {classes.get('B', 0)} / {classes.get('C', 0)}，A 类准确率 {pct(a_alt.get('point'))}"
            f"（下限 {pct(a_alt.get('lower'))}，n={a_alt.get('n', 0)}）。只重判日期核对（录入晚、日月对调、晚于今天），"
            "记忆、Express 历史与回读仍按录入日计时，是混合时钟的估计，不作新的总准确率。", ""]


def _render_goods(s: Mapping[str, Any]) -> List[str]:
    """「商品单」一节（10-17 第三段）：答案口径、选入账方式与会计不同的比例、存货历史若用来定档、明细过闸情况。"""
    goods = s.get("goods")
    if not goods:
        return []
    history = goods.get("history") or {}
    manual = goods.get("history_manual") or {}
    lines = ["### 商品单（按公司选入账方式的单）", "",
             f"票面明细 {'/'.join(s.get('lines') or []) or 'none'}；替公司选入账方式 "
             f"{'/'.join(s.get('company_choice') or []) or '—'}（prior = 第一次交人之前、会因入账方式交人的单里的多数，不到 "
             f"{PRIOR_MIN} 张退到全期多数 full_fallback；full = 全期多数）。不造明细（none）时入账方式按声明值计分，造了明细按载荷"
             "实际写什么计分；answer:opaque 是「同名查找落空、记忆理想命中」的估计，不是悲观下界。答案口径："
             "只有库存品（STKTYP=0）或有库存影响的明细才算走库存，只挂非库存品明细的不算（与第四轮不能直接比）。"
             f"这类单 {goods.get('docs', 0)} 张；载荷明细过闸情况 "
             + "、".join(f"{k} {v}" for k, v in sorted((goods.get("items_status") or {}).items())) + "。", "",
             f"加上「这家供应商之前的 Express 存货历史」一层（线上同一判据，回放主判定不带；近 12 个月会计亲手录、只有一种记法且 ≥3 张）："
             f"与公司选的一致 {history.get('A', 0)}（其中入账方式计错 {history.get('A_wrong', 0)}）、一致但定成走库存而载荷写不出库存行 "
             f"{history.get('no_lines', 0)}（不造明细的那两遍都落这里）、存货没着落 {history.get('items', 0)}、定成记费用却有库存行"
             f"或与库存品同名 {history.get('named', 0)}（这三种线上不给 A）、没定下 {history.get('B', 0)}、相反 "
             f"{history.get('conflict', 0)}；现在只因入账方式是 B、会升 A 的 {history.get('extra_A', 0)} 张（整张推错 "
             f"{history.get('extra_A_wrong', 0)}、入账方式本身判错 {history.get('extra_A_posting_wrong', 0)}）；相反改交人会多出 C "
             f"{history.get('conflict_C', 0)} 张（其中真拦住入账方式错的 {history.get('conflict_caught', 0)}）。这是上界：回放里"
             "能被自动推的单线上会是 CC 写的、不进历史；把之前判成 A / B 的单都当 CC 推的剔掉（下界）时会升 A 的 "
             f"{manual.get('extra_A', 0)} 张（整张推错 {manual.get('extra_A_wrong', 0)}）、相反 {manual.get('conflict', 0)}。", "",
             "| 公司 | 选的 | 凭 | 此前多数 | 全期多数 | 张数 | 答案走库存 | 只有非库存明细 | 与选的不同 | 与全期多数不同 | 历史 A / 错 | "
             "历史相反 | 会升 A（推错） |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for company, row in (goods.get("companies") or {}).items():
        found = row.get("history") or {}
        docs = row.get("docs") or 0

        def rate(value):
            return f"{value}（{pct(value / docs if docs else None, 1)}）"

        lines.append(f"| {company} | {row.get('used') or '—'} | {row.get('by') or '—'} | {row.get('prior') or '—'} | "
                     f"{row.get('full') or '—'} | {docs} | {row.get('stock', 0)} | {row.get('detail_only', 0)} | "
                     f"{rate(row.get('disagree_used', 0))} | {rate(row.get('disagree_full', 0))} | "
                     f"{found.get('A', 0)} / {found.get('A_wrong', 0)} | {found.get('conflict', 0)} | "
                     f"{found.get('extra_A', 0)}（{found.get('extra_A_wrong', 0)}） |")
    return lines + [""]


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
        origins = collections.Counter(book.get("period_origin") or book.get("period_source") or "?" for book in books)
        drift = [book for book in books if book.get("period_origin") in ("answer", "export", "testcopy")
                 and book.get("posted_outside")]
        overlaps = [item for found in (inventory.get("overlaps") or {}).values() for item in found]
        lines += ["- 会计期间来源（份）：" + "、".join(f"{key} {value}" for key, value in sorted(origins.items()))
                  + f"；真 ISPRD 的账套里入账日落在期间外的有 {len(drift)} 份（共 "
                  f"{sum(book['posted_outside'] for book in drift)} 张，应约等于 0，不为 0 是期间快照与答案不同期）。",
                  f"- 同分店两两期间重叠 {len(overlaps)} 对（两边都是真 ISPRD "
                  f"{sum(1 for item in overlaps if 'estimated' not in item['origins'])} 对）；"
                  + ("登记表里没有的账套 " + str(sum(1 for book in books if book.get("registered") is False)) + " 份。"
                     if any(book.get("registered") is not None for book in books) else "没给登记表。"), ""]
        lags, vat_lags = collections.Counter(), collections.Counter()
        for book in books:
            lags.update(book.get("entry_lag") or {})
            vat_lags.update(book.get("vat_lag") or {})
        batched = [book for book in books if book.get("batch_days")]
        if lags:
            lines += ["- 凭证录入日晚于单据日期（天）：" + "、".join(f"{k} {v}" for k, v in sorted(lags.items()))
                      + "；进项税期晚于单据月份（月）：" + "、".join(f"{k} {v}" for k, v in sorted(vat_lags.items()))
                      + f"；同一天录入 ≥{BATCH_DAY_DOCS} 张凭证的账套 {len(batched)} 份（这些日子录入的单 "
                      f"{sum(book.get('batch_docs') or 0 for book in batched)} 张，疑似导入或重建）；答案走库存 "
                      f"{sum(book.get('stock_answers') or 0 for book in books)} 张、只有非库存明细 "
                      f"{sum(book.get('detail_only') or 0 for book in books)} 张。", ""]
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
                  "账套记忆定的项（科目、现 / 赊、对方档要最近连续 3 张有证据，10-17 推广；vat 是记过的小票抵进项，仍是记一次就 A，"
                  "只量不改；按家 A / B / A 里错）。回放假定每期都在申报截止日后一天做了合格回读，线上要有人真发起回读才有证据；"
                  "证据窗口往回看的上限因此丢掉的 A：" + "、".join(
                      f"{kind} {sum(found.values())}" for kind, found in (s.get("memory_cap_lost") or {}).items()) + "。",
                  "",
                  "| 项 | 公司 | A | B | A 里错 |", "|---|---|---|---|---|",
                  *[f"| {kind} | {company} | {value.get('A', 0)} | {value.get('B', 0)} | {value.get('A_wrong', 0)} |"
                    for kind, found in (s.get("memory_sources") or {}).items()
                    for company, value in sorted(found.items(), key=lambda kv: -kv[1].get("A_wrong", 0))[:8]], "",
                  "推送记录来源（payload 照载荷 / replanned C 类学完记忆再出 / approx 推不出的乐观近似）："
                  + "、".join(f"{key} {value}" for key, value in (s.get("pushed_from") or {}).items()), "",
                  "C 类按主要原因（每张只算一类）：", "", "| 主要原因 | 张数 |", "|---|---|",
                  *[f"| {dict((k, l) for k, l, _t in CAUSES).get(key, '其他')} | {value} |"
                    for key, value in s["c_causes"].items()], "",
                  *_render_upload(s),
                  *_render_books(s),
                  *_render_goods(s),
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
        for label, key in (("文字层 PDF（独立金标，计入合并）", "text_pdf_independent"),
                           ("文字层 PDF（Express 答案作金标，只比配对没用到的项，单列）", "text_pdf_express"),
                           ("文字层 PDF 合计", "text_pdf"), ("图片 / 扫描", "image"), ("合计", "all")):
            row = g[key]
            lines.append(f"| {label} | {row['invoices']} | {row['passed']} | {row['scored']} | {row['wrong']} | "
                         f"{row['disputed']} | {row['not_invoice_passed']} | {pct(row['point'])}（{pct(row['lower'])}） |")
        lines += ["", "闸后仍错的字段：" + ("、".join(f"{k} {v}" for k, v in g["all"]["fields"].items()) or "无"), "",
                  "Express 答案作金标那组过闸票逐项（self = 配对用到的项、不计分）：" + "、".join(
                      f"{k} {v}" for k, v in sorted((g.get("express_fields") or {}).items())), "",
                  f"抵不抵进项（过闸的进项、识别出税额的票 {(g.get('vat_claim') or {}).get('n', 0)} 张；档:识别判:与金标比）："
                  + "、".join(f"{k} {v}" for k, v in sorted(((g.get("vat_claim") or {}).get("by") or {}).items()))
                  + f"；判 A 却与金标不符 {(g.get('vat_claim') or {}).get('a_wrong', 0)} 张。", "",
                  "拦下的原因：" + "、".join(f"{k} {v}" for k, v in g["all"]["stopped_by"].items()), ""]
    if judge and gate:
        a, raw, t = judge["A_eval"], judge["A"], gate.get("text_pdf_independent") or gate["text_pdf"]
        merged = combined(a["ok"], a["n"], t["ok"], t["n"])
        original = combined(raw["ok"], raw["n"], t["ok"], t["n"])
        lines += ["## 合并估计（A 类 × 文字层 PDF 闸后，只用独立金标）", "",
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
            writer.writerow(["层", "公司", "账套", "单号", "方向", "期间", "录入日", "类别", "原因", "要选", "计分", "全对",
                             "估上传日", "来源", "类别（估上传日）"])
            for row in judge["rows"]:
                writer.writerow(["判定", row["company"], row["book"], row["docnum"], row["direction"], row["period"],
                                 row["entered"], row["class"], ",".join(row["reasons"]), ",".join(row["needed"]),
                                 json.dumps(row["scored"], ensure_ascii=False), int(row["correct"]),
                                 row.get("uploaded_alt") or "", row.get("uploaded_src") or "",
                                 row.get("class_alt") or ""])
            for row in (gate or {}).get("rows") or []:
                writer.writerow(["识别闸", row["company"], row["form"], row["id"], row["direction"], "", "",
                                 "过闸" if row["passed"] else "拦下", ",".join(row["reasons"]), "",
                                 json.dumps(row["result"], ensure_ascii=False), int(not row["wrong"]), "", "", ""])
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
    inventory.add_argument("--isprd")
    inventory.add_argument("--registry")
    inventory.add_argument("--out", required=True)
    judge = sub.add_parser("judge")
    judge.add_argument("--corpus", required=True)
    judge.add_argument("--company")
    judge.add_argument("--express", help="测试机本地 Express 测试账套根（70EXP），取会计期间 ISPRD")
    judge.add_argument("--isprd", help="各账套 ISPRD.DBF 的只读导出目录（先复制到 C:），优先于 70EXP")
    judge.add_argument("--registry", help="Express 登记表 SCCOMP.DBF 的只读导出（文件或目录）：选账套只认登记了的")
    judge.add_argument("--periods", choices=("strict", "estimated"), default="strict",
                       help="没有 ISPRD 的账套：strict 不报期间（同线上），estimated 按单据日期估（对照）")
    judge.add_argument("--tiebreak", default=",".join(TIEBREAKS),
                       help="选出多份同期账套时模拟的挑法，逗号隔开：" + " / ".join(TIEBREAKS))
    judge.add_argument("--lines", choices=LINE_MODES, default="none",
                       help="票面明细：none 不造（默认，同第四轮；入账方式按声明值计分）、answer 按答案的存货明细造")
    judge.add_argument("--line-names", choices=LINE_NAMES, default="stkdes",
                       help="--lines answer 时品名：stkdes 照 Express（乐观上界）、opaque 打码（同名查找落空、记忆理想命中）")
    judge.add_argument("--company-choice", choices=COMPANY_CHOICE_MODES, default="prior",
                       help="模拟的人替公司选入账方式：prior 第一次交人之前会交人那类单的多数（默认，不到 10 张退到全期多数）、"
                            "full 全期多数（第四轮，对照）")
    judge.add_argument("--recent-max", type=int, default=RECENT_MAX,
                       help="账套记忆的证据窗口往回最多看几张推送（同线上 1000；先跳过没回读核过的凭记忆推送、再取最近的有证据的，"
                            "先截再按分店筛）；0 不设上限（对照）")
    judge.add_argument("--readback-scope", choices=READBACK_SCOPES, default="record",
                       help="回读里改没改：record 按整张记录（同线上，默认）、account 只比科目（第四轮，对照）")
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
