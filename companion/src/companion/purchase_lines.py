# -*- coding: utf-8 -*-
"""采购单的行:GLJNLIT 分录行构造 + 明细行路由 + 走库存时的存货重分类分录。

一张采购票的明细按 item_mode 逐行走两条路:
- stock_item → stock_purchase.write_stock_in_lines:建/复用 STKTYP=0 库存品 + 真入库 + 真成本;
- 其余(non_stock_item / direct_account)→ dbf_detail.write_stcrd_detail,不动库存。
同一张单两种行都有(混行,载荷 19,ERP 按行选入库 / 只记账,11-06)时照载荷顺序分段写,SEQNUM 用整张单的行号(`dbf_detail.runs`)。

库存行照 Express 原生直接记物料科目组的科目(11-07,S 盘 95 本取证,``docs/work/2026-11-07-cc-purchase-gl-native-评估.md``):
永续盘存借科目组的存货科目 ``ISACC.ACCNUM01``、定期盘存借科目组的购货科目 ``ACCNUM02``,同一科目合成一条、SEQIT 0,从采购那条借方里
扣出来(:func:`stock_journal`)。以前是借购货再追加一对「借 存货 / 贷 购货」重分类(SEQIT 7 / 8)——原生一张都没有;定期账套全记进账套默认
购货,按科目分的购货金额不对。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from . import account_rules
from . import dbf_detail
from . import dbf_schema as S
from . import stock_purchase

GlRow = Tuple[str, str, str, Decimal]  # (SEQIT, ACCNUM, TRNTYP, 金额)


class StockLineError(Exception):
    """采购入库路失败(混行 / 数量成本非法 / 建档失败 / 重分类超额)→ 整单回滚交会计。"""


class GroupAccountMissing(StockLineError):
    """物料科目组的存货 / 购货科目不在这套账的科目表里:原生 Express 存档报 1155「过账科目设置不对」、整张拒存
    (11-08 TEST 原生实录),CC 照原生停单,写明哪个科目组的哪个科目,要人在 Express 改科目组。"""


def journal_rows(entry) -> List[GlRow]:
    """entry.lines → [(SEQIT, ACCNUM, TRNTYP, amount)]。

    lines 由 mapper 按 [采购(D), 进项税(D·若有), 应付(C)] 顺序产出;据此定角色/SEQIT。
    带 ``role`` 的载荷(ERP 前台的明细按行科目拆,采购可以有几条,10-24)按角色定:purchase 几条都是采购 SEQIT,
    input_vat / ap 各一条;没有 role 的旧载荷照旧按位置。
    """
    if any(ln.get("role") for ln in entry.lines):
        seqit = {"purchase": S.SEQIT["purchase"], "input_vat": S.SEQIT["input_vat"], "ap": S.SEQIT["ap"]}
        rows: List[GlRow] = []
        for ln in entry.lines:
            role = str(ln.get("role") or "")
            if role in seqit:
                side = S.TRNTYP_CREDIT if role == "ap" else S.TRNTYP_DEBIT
                rows.append((seqit[role], str(ln["acc"]), side, _d(ln["amount"])))
        return rows
    debits = [ln for ln in entry.lines if str(ln.get("side", "")).upper() == "D"]
    credits = [ln for ln in entry.lines if str(ln.get("side", "")).upper() == "C"]
    rows: List[GlRow] = []
    if debits:
        p = debits[0]
        rows.append(
            (S.SEQIT["purchase"], str(p["acc"]), S.TRNTYP_DEBIT, _d(p["amount"]))
        )
    if len(debits) > 1:
        v = debits[1]
        rows.append(
            (S.SEQIT["input_vat"], str(v["acc"]), S.TRNTYP_DEBIT, _d(v["amount"]))
        )
    if credits:
        a = credits[0]
        rows.append((S.SEQIT["ap"], str(a["acc"]), S.TRNTYP_CREDIT, _d(a["amount"])))
    return rows


def write_detail(
    account_dir,
    docnum: str,
    docdat,
    entry,
    gl_rows: List[GlRow],
    *,
    depcod: str = "",
    userid: str = S.PEARNLY_USERID,
    locations: Optional[List[Optional[str]]] = None,
    moves: Optional[list] = None,
    items: Optional[list] = None,
) -> Tuple[List[dict], Dict[Tuple[str, str], Decimal]]:
    """写本单明细行,返回 (逐行 mode 结果, 库存行按物料科目组累计的入库额 {(ACCNUM01, ACCNUM02): 金额},交 :func:`stock_journal`)。

    混行(库存 + 非库存)照载荷顺序分段写,每行 SEQNUM = 它在整张单里的行号(以前两条路各从 1 起会撞,V1 整单拒收);
    只有库存行入库、进重分类。``items``：调用方按单据价格口径备好的明细（价含税的带含税额），不给用 ``entry.items``。
    """
    items = entry.items if items is None else items
    segments = dbf_detail.runs(items, lambda it: _mode(it) == dbf_detail.ITEM_MODE_STOCK)
    whole = len(segments) == 1  # 只有一种行:调用与以前一字不差(不带 seqs)
    line_modes: List[dict] = []
    groups: Dict[Tuple[str, str], Decimal] = {}
    for stock, seqs, part in segments:
        numbered = {} if whole else {"seqs": seqs}
        if not stock:
            line_modes += dbf_detail.write_stcrd_detail(
                account_dir,
                docnum,
                docdat,
                part,
                entry.supplier_code,
                entry.items_account,
                depcod=depcod,
                userid=userid,
                ref_no=entry.ref_no,
                posopr=S.STCRD_POSOPR_PURCHASE,
                **({"locations": locations} if locations is not None else {}),
                **numbered,
            )
            continue
        try:
            modes, _by_inventory = stock_purchase.write_stock_in_lines(
                account_dir,
                docnum,
                docdat,
                part,
                entry.supplier_code,
                depcod=depcod,
                acccod=getattr(entry, "stock_acccod", ""),
                userid=userid,
                ref_no=entry.ref_no,
                groups=groups,
                **({"locations": locations, "moves": moves} if locations is not None else {}),
                **numbered,
            )
        except Exception as e:  # 入库任一步失败 → 统一交会计(整单回滚·不留半截库存)
            raise StockLineError(str(e)) from e
        line_modes += modes
    return line_modes, groups


def has_stock_lines(line_modes: List[dict]) -> bool:
    """本单是否真写了库存行 —— 决定读回校验放不放行"库存签名"。不能用"有没有重分类分录"
    代答:赠品行(净额 0)动库存却不产分录,那样会被读回闸误判成不该动库存。"""
    return any(m.get("mode") == dbf_detail.ITEM_MODE_STOCK for m in line_modes)


def imbalance(gl_rows: List[GlRow]) -> str:
    """借贷不平 → 返回描述(调用方据此整单失败);平 → 空串。写盘前的最后一道自检 ——
    载荷闸只看 payload.lines 平不平,这里看的是真要写进 GLJNLIT 的行(含重分类)。"""
    dr = sum((a for _s, _acc, t, a in gl_rows if t == S.TRNTYP_DEBIT), Decimal("0"))
    cr = sum((a for _s, _acc, t, a in gl_rows if t == S.TRNTYP_CREDIT), Decimal("0"))
    return f"GL 分录不平(Dr={dr} Cr={cr})" if dr != cr else ""


def _mode(item: dict) -> str:
    return str(item.get("item_mode") or "").strip()


def _d(v) -> Decimal:
    return Decimal(str(v))


def stock_journal(account_dir, gl_rows: List[GlRow], groups: Dict[Tuple[str, str], Decimal], items_account: str = "",
                  *, periodic: bool = False) -> List[GlRow]:
    """库存行照原生直接记物料科目组科目:从采购那条借方(记在 ``items_account`` 的那条,没有用第一条)扣出入库额,
    逐科目一条借方(SEQIT 0,同科目合并),余下的留在原采购科目(为 0 就不写)。

    - 永续盘存借 ``ACCNUM01``(存货),定期盘存(``periodic``)借 ``ACCNUM02``(购货;S 盘 30 本定期账套 29,395 张如此);
    - 科目组的科目在科目表里但不能记账、账套默认(永续 ``ISINFO.ACCNUM04``、定期 ``ACCNUM18``)能记账时用默认(69POL 原生 259 / 296 张如此);
      都不能就仍记科目组的科目(同以前重分类,不核);
    - 科目组的科目**不在科目表里** → 抛 :class:`GroupAccountMissing`(原生 1155 整张拒存,11-08 TEST 实录);读不到科目表不拦;
    - 扣出的钱超过本单这条购货额 → 抛 :class:`StockLineError`(行净额与税前额对不上,交会计)。
    """
    if not groups:
        return gl_rows
    fallback = account_rules.default_account(account_dir, "ACCNUM18" if periodic else "ACCNUM04")
    debit: Dict[str, Decimal] = {}
    for (inventory, purchases), raw in groups.items():
        amount = Decimal(str(raw))
        if amount <= 0:
            continue
        group = str((purchases if periodic else inventory) or "").strip()
        if group and account_rules.chart_account(account_dir, group) is False:
            slot = "ACCNUM02" if periodic else "ACCNUM01"
            used_by = ", ".join(account_rules.groups_using(account_dir, slot, group)) or "?"
            raise GroupAccountMissing(f"item group {used_by} {slot} {group} not in GLACC")
        acc = group
        if not (group and account_rules.postable_account(account_dir, group)) and fallback \
                and account_rules.postable_account(account_dir, fallback):
            acc = fallback
        if acc:
            debit[acc] = debit.get(acc, Decimal("0")) + amount
    if not debit:
        return gl_rows
    purchase_acc, purchase_amt = _purchase_line(gl_rows, items_account)
    moved = sum(debit.values(), Decimal("0"))
    if moved > purchase_amt:
        raise StockLineError(f"入库额 {moved} 超出本单购货额 {purchase_amt}")
    out: List[GlRow] = []
    placed = False
    for row in gl_rows:
        seqit, acc, side, amt = row
        if not placed and seqit == S.SEQIT["purchase"] and acc == purchase_acc and side == S.TRNTYP_DEBIT:
            placed = True
            debit[purchase_acc] = debit.get(purchase_acc, Decimal("0")) + (Decimal(str(amt)) - moved)
            out += [(seqit, account, side, value) for account, value in debit.items() if value != 0]
            continue
        out.append(row)
    return _merged(out)


def _merged(rows: List[GlRow]) -> List[GlRow]:
    """同一 (SEQIT, 科目, 借贷) 合成一条、按第一次出现的顺序(原生同一科目的几行库存品合成一条借方)。"""
    merged: Dict[Tuple[str, str, str], Decimal] = {}
    for seqit, acc, side, amt in rows:
        key = (seqit, acc, side)
        merged[key] = merged.get(key, Decimal("0")) + Decimal(str(amt))
    return [(seqit, acc, side, amt) for (seqit, acc, side), amt in merged.items()]


def _purchase_line(gl_rows: List[GlRow], items_account: str = "") -> Tuple[str, Decimal]:
    """库存行的购货分录:有记在 ``items_account`` 的采购分录用它,没有用第一条采购分录(以前的口径)。"""
    purchases = [(accnum, amt) for seqit, accnum, _trntyp, amt in gl_rows if seqit == S.SEQIT["purchase"]]
    if not purchases:
        raise StockLineError("本单缺购货分录,记不了库存行")
    wanted = str(items_account or "").strip()
    return next((row for row in purchases if wanted and str(row[0]).strip() == wanted), purchases[0])
