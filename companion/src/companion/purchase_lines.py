# -*- coding: utf-8 -*-
"""采购单的行:GLJNLIT 分录行构造 + 明细行路由 + 走库存时的存货重分类分录。

一张采购票的明细按 item_mode 逐行走两条路:
- stock_item → stock_purchase.write_stock_in_lines:建/复用 STKTYP=0 库存品 + 真入库 + 真成本;
- 其余(non_stock_item / direct_account)→ dbf_detail.write_stcrd_detail,不动库存。
同一张单两种行都有(混行,载荷 19,ERP 按行选入库 / 只记账,11-06)时照载荷顺序分段写,SEQNUM 用整张单的行号(`dbf_detail.runs`)。

走库存的钱在 Express 里该挂存货而不是购货,故在同一凭证追加一对重分类分录:借 存货 / 贷 购货。
Pearnly 云端算好、本来就平的那张分录一个字不改 —— 净效果=购货科目对这些行归零、存货增加、
借贷仍平(与销售侧追加 COGS 分录同款机制,见 stock_sale)。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from . import dbf_detail
from . import dbf_schema as S
from . import stock_purchase

# GLJNLIT.SEQIT:本单已占 采购=0 / 进项税=3 / 应付=6,重分类接在其后(镜像销项 COGS 用 8/9)。
RECLASS_SEQIT_DR = "7"  # 借 存货
RECLASS_SEQIT_CR = "8"  # 贷 购货

GlRow = Tuple[str, str, str, Decimal]  # (SEQIT, ACCNUM, TRNTYP, 金额)


class StockLineError(Exception):
    """采购入库路失败(混行 / 数量成本非法 / 建档失败 / 重分类超额)→ 整单回滚交会计。"""


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
) -> Tuple[List[dict], List[GlRow]]:
    """写本单明细行,返回 (逐行 mode 结果, 要追加的重分类分录行)。

    混行(库存 + 非库存)照载荷顺序分段写,每行 SEQNUM = 它在整张单里的行号(以前两条路各从 1 起会撞,V1 整单拒收);
    只有库存行入库、进重分类。``items``：调用方按单据价格口径备好的明细（价含税的带含税额），不给用 ``entry.items``。
    """
    items = entry.items if items is None else items
    segments = dbf_detail.runs(items, lambda it: _mode(it) == dbf_detail.ITEM_MODE_STOCK)
    whole = len(segments) == 1  # 只有一种行:调用与以前一字不差(不带 seqs)
    line_modes: List[dict] = []
    inv_amounts: Dict[str, Decimal] = {}
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
            modes, amounts = stock_purchase.write_stock_in_lines(
                account_dir,
                docnum,
                docdat,
                part,
                entry.supplier_code,
                depcod=depcod,
                acccod=getattr(entry, "stock_acccod", ""),
                userid=userid,
                ref_no=entry.ref_no,
                **({"locations": locations, "moves": moves} if locations is not None else {}),
                **numbered,
            )
        except Exception as e:  # 入库任一步失败 → 统一交会计(整单回滚·不留半截库存)
            raise StockLineError(str(e)) from e
        line_modes += modes
        for acc, amount in amounts.items():
            inv_amounts[acc] = inv_amounts.get(acc, Decimal("0")) + amount

    if not any(stock for stock, _seqs, _part in segments):
        return line_modes, []
    return line_modes, _reclass_rows(inv_amounts, gl_rows, getattr(entry, "items_account", ""))


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


def _reclass_rows(inv_amounts: Dict[str, Decimal], gl_rows: List[GlRow], items_account: str = "") -> List[GlRow]:
    """{存货科目: 入库净额} → 逐科目一对 借 存货 / 贷 购货。

    购货科目取本单已有的采购分录(各账套科目码不同,不硬编码)。搬走的钱不许超过本单购货额,
    否则购货会被贷成负数 —— 那说明行净额与税前额对不上(漏行/含税混算),交会计而不是硬写。
    ``items_account``:库存行记在的那个采购科目(载荷 ``items_account``)。混行单的采购分录按行科目拆成几条(费用行记自己的科目),
    重分类要贷库存行那一条,不是排在第一的费用科目。
    """
    purchase_acc, purchase_amt = _purchase_line(gl_rows, items_account)
    rows: List[GlRow] = []
    moved = Decimal("0")
    for inv_acc, raw in inv_amounts.items():
        amt = Decimal(str(raw))
        if amt <= 0:
            continue
        moved += amt
        rows.append((RECLASS_SEQIT_DR, inv_acc, S.TRNTYP_DEBIT, amt))
        rows.append((RECLASS_SEQIT_CR, purchase_acc, S.TRNTYP_CREDIT, amt))
    if moved > purchase_amt:
        raise StockLineError(f"入库额 {moved} 超出本单购货额 {purchase_amt}")
    return rows


def _purchase_line(gl_rows: List[GlRow], items_account: str = "") -> Tuple[str, Decimal]:
    """库存行的购货分录:有记在 ``items_account`` 的采购分录用它,没有用第一条采购分录(以前的口径)。"""
    purchases = [(accnum, amt) for seqit, accnum, _trntyp, amt in gl_rows if seqit == S.SEQIT["purchase"]]
    if not purchases:
        raise StockLineError("本单缺购货分录,拼不出存货重分类")
    wanted = str(items_account or "").strip()
    return next((row for row in purchases if wanted and str(row[0]).strip() == wanted), purchases[0])
