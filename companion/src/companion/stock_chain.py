# -*- coding: utf-8 -*-
"""分仓计价账套（ISINFO.LOC_COST = Y）的批次链：按仓记在 STLOC 上（11-19 实录，69EXP 先进先出 + 分仓计价测试账套）。

- 这类账套每件存货 × 每个仓一条批次链：STLOC 的 ``MLOTNUM``（当前那一批的批号）、``MREMBAL`` / ``MREMVAL``（链上的结存与货值），
  ``LOCVAL`` 也随单增减（不分仓计价的账套这几栏几乎恒 0，``stock_location`` 不碰）。
- 原生在某个仓出 / 进：取价、明细的批次指针与结存都看**这个仓**的链；写后更新这个仓的链，再把这条链原样抄到主档 STMAS
  （``MLOTNUM`` / ``MREMBAL`` / ``MREMVAL``）。实录：613023026101 的 01 仓链 2418 / 80549.5（指 RR2400191），主档上是陈旧的
  0（指 35 仓那一批）；原生 OU 出 1 后 01 仓链与主档都是 2417 / 80516.2。
- CC 原先只读写主档那条陈旧的链：增加行指错批、明细结存记成负数，财务之后在 Express 存同日进货时 Express 照这些值重算，
  出库从 33.33 变成 29.73（11-18 先进先出一轮的已知差异）。

- 只读统计（11-19，服务器 15 本先进先出 + 分仓计价账套）：有数据的 7 本 STLOC 都记着链；其中 1 本大部分存货 STLOC 有结存却没批号、链在主档上。
  所以逐个 (存货, 仓) 判：这个仓记着链（``Chain.kept``）才按仓走，否则照旧用主档的链。

本模块只读写 STLOC 的这几栏与取价要用的库存卡，不动任何已有的单据行。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

import dbf

from . import account_rules
from . import dbf_schema as S
from . import stock_location

# 链上还没有批时原生明细与链记的批号（11-24 NLTTEST 原生 JU 实录：空链的仓写入行记 B/F、作废后仓链回到 B/F）
EMPTY_HEAD = "B/F"

# 先进先出的批：进货、调增 / 完工收货、盘盈、调入、退货回仓
RECEIPT_POSOPR = ("0", "1", ":", "3", "2")

Key = Tuple[str, str]  # (存货码, 仓库码)


@dataclass(frozen=True)
class Chain:
    head: str          # 当前那一批的批号（原样；被标成负的已还原，见 ``marked``）
    bal: Decimal       # 链上结存
    val: Decimal       # 链上货值
    marked: bool = False  # 原表上的批号首字符是「-」（链已为负）
    kept: bool = True     # 这个仓记着链：有批号，或这个仓还没有结存（新链从这里起）。有结存却没批号的（老账套部分存货，链在主档上）为 False


def applies(account_dir) -> bool:
    """这套账按仓计价（ISINFO.LOC_COST = Y）且有 STLOC。"""
    info = account_rules._first(account_dir, "ISINFO") or {}
    return str(info.get("LOC_COST") or "").strip().upper() == "Y" and \
        stock_location._file(account_dir, stock_location.TABLE) is not None


def _dec(value) -> Decimal:
    return stock_location._dec(value)


def unmark(raw: str) -> Tuple[str, bool]:
    """批号首字符「-」= 链已为负（原生）；还原成原样：「-/F」→「B/F」，日期打头的 →「2」（11-18 实录）。"""
    raw = (raw or "").rstrip()
    if raw.startswith("-"):
        return ("B" if raw[1:3] == "/F" else "2") + raw[1:], True
    return raw, False


def row_head(head: str, bal) -> str:
    """明细上记的写前批号：链是空的（没有批号、写前结存 0）时记 ``B/F``（原生）；有结存却没批号的老数据照旧记空。"""
    return head or (EMPTY_HEAD if _dec(bal) == 0 else "")


def mark(head: str) -> str:
    return "-" + head[1:] if head else head


def read(account_dir, keys: Iterable[Key]) -> Dict[Key, Chain]:
    """各 (存货, 仓) 写前的链；STLOC 没有这一行的从空链起（随单新建这一行）。"""
    keys = set(keys)
    out: Dict[Key, Chain] = {key: Chain("", Decimal(0), Decimal(0)) for key in keys}  # 这个仓还没有行：新链
    table = stock_location._open(account_dir, stock_location.TABLE, dbf.READ_ONLY)
    if table is None:
        return out
    try:
        seen = set()
        stocked = set()
        for rec in S.live_rows(table):
            key = (stock_location._text(rec, "STKCOD"), stock_location._text(rec, "LOCCOD"))
            stocked.add(key[0])
            if key not in keys or key in seen:
                continue
            seen.add(key)
            head, marked = unmark(str(stock_location._value(rec, "MLOTNUM") or ""))
            kept = bool(head) or _dec(stock_location._value(rec, "LOCBAL")) == 0
            out[key] = Chain(head, _dec(stock_location._value(rec, "MREMBAL")), _dec(stock_location._value(rec, "MREMVAL")),
                             marked, kept)
    finally:
        table.close()
    # STLOC 一行都没有、主档却有结存的存货（10-22 前 CC 留下的）：链在主档上，照主档走；写单时补行（stock_location._heal）。
    # 本单新建的存货（主档结存 0）照旧从这个仓的新链起
    legacy = {key[0] for key in keys - seen if key[0] not in stocked}
    if legacy:
        held = _master_held(account_dir, legacy)
        for key in keys - seen:
            if key[0] in held:
                out[key] = Chain("", Decimal(0), Decimal(0), kept=False)
    return out


def _master_held(account_dir, codes: set) -> set:
    table = stock_location._open(account_dir, "STMAS", dbf.READ_ONLY)
    if table is None:
        return set()
    try:
        return {stock_location._text(rec, "STKCOD") for rec in S.live_rows(table)
                if stock_location._text(rec, "STKCOD") in codes and _dec(stock_location._value(rec, "TOTBAL")) != 0}
    finally:
        table.close()


def fifo_lots(account_dir, chains: Dict[Key, Chain], before=None) -> Dict[Key, List[Tuple[Decimal, Decimal]]]:
    """各 (存货, 仓) 还有剩余的批 → ``[(剩余数量, 单价)]``（由早到晚）：只看这个仓的进货类批，从最新的往回数到链上结存为止，
    最早仍有剩余的那一批只剩差额（原生出库取这一批的价）。``before``：只看这一天之前的批（当天开日的批，见 ``day_firsts``）。"""
    out: Dict[Key, List[Tuple[Decimal, Decimal]]] = {key: [] for key in chains}
    if not chains:
        return out
    main = account_rules.main_location(account_dir)
    rows: Dict[Key, list] = {}
    table = dbf.Table(str(_stcrd(account_dir)), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(dbf.READ_ONLY)
    try:
        for rec in S.live_rows(table):
            key = ((rec.STKCOD or "").strip(), (rec.LOCCOD or "").strip() or main)
            if key not in chains or (rec.POSOPR or "").strip() not in RECEIPT_POSOPR:
                continue
            qty = _dec(rec.XTRNQTY)
            if qty > 0 and rec.DOCDAT and (before is None or rec.DOCDAT < before):
                price = _dec(rec.XUNITPR) or _dec(rec.XTRNVAL) / qty  # 库存单位单价（原生 4 位小数）；老行没有就按货值 / 数量
                rows.setdefault(key, []).append((rec.DOCDAT, (rec.DOCNUM or "").strip(), (rec.SEQNUM or "").strip(), qty, price))
    finally:
        table.close()
    for key, chain in chains.items():
        left, need = [], chain.bal
        for _day, _doc, _seq, qty, price in sorted(rows.get(key, []), key=lambda row: (row[0], row[1], row[2]), reverse=True):
            if need <= 0:
                break
            take = min(qty, need)
            left.append((take, price))
            need -= take
        out[key] = list(reversed(left))
    return out


# 原生存单即重算当天（11-20 69EXP 先进先出 + 分仓计价逐张实录 P0–P5）：每存一张单，Express 把这件存货在这个仓当天的全部明细
# 按「动作类别 → 单号 → 行号」重排（进货 / 调增 1、调入 3 在前，调出 4、出库 6、调减 8 在后），从排在最前那一行记的写前结存与批次指针
# （MREMBAL / MREMVAL / MLOTNUM）起整段重算，已存单的明细与凭证金额一起改。所以 CC 后写的行排到当天已有行前面时，这一行要记当天开日的
# 状态（= 原来排最前那一行记的值），金额也按开日的批算；之后原生存单或「计算」就与全程原生一致。


@dataclass(frozen=True)
class DayFirst:
    order: Tuple[str, str, int]  # 排序键：(动作类别, 单号, 行号)
    head: str  # 那一行记的批次指针（原样，含「-」）
    bal: Decimal
    val: Decimal


def order_of(posopr: str, docnum: str, seqnum) -> Tuple[str, str, int]:
    """原生当天重算的排序键。"""
    try:
        seq = int(str(seqnum).strip() or 0)
    except ValueError:
        seq = 0
    return (str(posopr or "").strip(), str(docnum or "").strip(), seq)


def day_firsts(account_dir, keys: Iterable[Key], docdat) -> Dict[Key, DayFirst]:
    """各 (存货, 仓) 这一天已有明细里排在最前的那一行（没有就不在结果里）。"""
    keys = set(keys)
    out: Dict[Key, DayFirst] = {}
    if not keys or docdat is None:
        return out
    main = account_rules.main_location(account_dir)
    table = dbf.Table(str(_stcrd(account_dir)), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(dbf.READ_ONLY)
    try:
        for rec in S.live_rows(table):
            if rec.DOCDAT != docdat:
                continue
            key = ((rec.STKCOD or "").strip(), (rec.LOCCOD or "").strip() or main)
            if key not in keys:
                continue
            order = order_of(rec.POSOPR, rec.DOCNUM, rec.SEQNUM)
            if key not in out or order < out[key].order:
                out[key] = DayFirst(order, (rec.MLOTNUM or "").rstrip(), _dec(rec.MREMBAL), _dec(rec.MREMVAL))
    finally:
        table.close()
    return out


def opening(first: DayFirst) -> Chain:
    """当天开日的链（取排最前那一行记的写前值）。"""
    head, marked = unmark(first.head)
    return Chain(head, first.bal, first.val, marked)


def ahead(first: Optional[DayFirst], order: Tuple[str, str, int]) -> bool:
    """这一行排在当天已有各行之前。"""
    return first is not None and order < first.order


def _stcrd(account_dir):
    path = stock_location._file(account_dir, "STCRD")
    if path is None:
        raise FileNotFoundError("STCRD")
    return path


def moved(chain: Chain, qty: Decimal, value: Decimal, new_head: str = "") -> Chain:
    """链上增减 ``qty`` / ``value``（带正负）；增加成新一批时 ``new_head`` 是它的批号。
    减少超过链上结存时链停在 0（原生：允许负库存的账套出超后这个仓的链是 0 / 0，不记负数，11-19 实录 OU2400001），
    批号标成负（首字符「-」，11-20 逐张实录 P1）；之后增加成新一批时批号指新批、不再标负（实录 P3）。"""
    bal, val = chain.bal + qty, chain.val + value
    marked = chain.marked
    if qty < 0 and bal <= 0:
        marked = marked or bal < 0
        bal, val = Decimal(0), Decimal(0)
    if new_head:
        marked = False
    return replace(chain, bal=bal, val=val, head=new_head or chain.head, marked=marked)


def stored_head(chain: Chain) -> str:
    """写回表里的批号：链为负（出超过）时首字符改「-」（原生）。"""
    return mark(chain.head) if chain.marked or chain.bal < 0 else chain.head


def write(account_dir, after: Dict[Key, Chain], value_moves: Dict[Key, Decimal]) -> Dict[Key, dict]:
    """把写后的链记进 STLOC 这几行（行已由 ``stock_location.apply`` 建好 / 改好结存），``LOCVAL`` 加上本单货值 → 读回要核的值。"""
    if not after:
        return {}
    expected: Dict[Key, dict] = {}
    table = stock_location._open(account_dir, stock_location.TABLE, dbf.READ_WRITE)
    if table is None:
        return expected
    try:
        fields = {name.upper() for name in table.field_names}
        seen = set()
        for rec in S.live_rows(table):
            key = (stock_location._text(rec, "STKCOD"), stock_location._text(rec, "LOCCOD"))
            if key not in after or key in seen:
                continue
            seen.add(key)
            chain = after[key]
            locval = _dec(stock_location._value(rec, "LOCVAL")) + value_moves.get(key, Decimal(0))
            values = {"MLOTNUM": stored_head(chain), "MREMBAL": float(chain.bal), "MREMVAL": float(chain.val),
                      "LOCVAL": float(locval)}
            locbal = _dec(stock_location._value(rec, "LOCBAL"))
            if locbal > 0:  # 原生：写后这个仓结存为正时 UNITPR = 货值 / 结存，≤ 0 不动（11-20 实录 P0 / P2 / P5）
                values["UNITPR"] = float((locval / locbal).quantize(Decimal("0.0001")))
            values = {name: value for name, value in values.items() if name in fields}
            with rec as row:
                for name, value in values.items():
                    row[name] = value
            expected[key] = values
    finally:
        table.close()
    return expected


def verify(account_dir, expected: Dict[Key, dict]) -> List[str]:
    """读回 STLOC 的链栏，回不符的说明（空 = 一致）。"""
    if not expected:
        return []
    problems: List[str] = []
    table = stock_location._open(account_dir, stock_location.TABLE, dbf.READ_ONLY)
    try:
        found = {}
        for rec in S.live_rows(table):
            key = (stock_location._text(rec, "STKCOD"), stock_location._text(rec, "LOCCOD"))
            if key in expected and key not in found:
                found[key] = {name: stock_location._value(rec, name) for name in expected[key]}
    finally:
        table.close()
    for key, values in expected.items():
        got = found.get(key)
        if got is None:
            problems.append(f"STLOC {key} missing")
            continue
        for name, value in values.items():
            have = got.get(name)
            if isinstance(value, float):
                if abs(_dec(have) - _dec(value)) > Decimal("0.000001"):
                    problems.append(f"STLOC {key} {name} {have!r} != {value!r}")
            elif str(have or "").rstrip() != str(value or "").rstrip():
                problems.append(f"STLOC {key} {name} {have!r} != {value!r}")
    return problems


__all__ = ["Chain", "DayFirst", "EMPTY_HEAD", "RECEIPT_POSOPR", "row_head", "ahead", "applies", "day_firsts", "fifo_lots", "opening", "order_of", "mark", "moved", "read", "stored_head", "unmark", "verify", "write"]
