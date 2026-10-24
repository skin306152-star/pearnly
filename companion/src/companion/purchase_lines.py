# -*- coding: utf-8 -*-
"""采购单的行:GLJNLIT 分录行构造 + 明细行路由 + 走库存时的存货重分类分录。

一张采购票的明细按 item_mode 走两条路,不混:
- stock_item → stock_purchase.write_stock_in_lines:建/复用 STKTYP=0 库存品 + 真入库 + 真成本;
- 其余(non_stock_item / direct_account)→ dbf_detail.write_stcrd_detail,不动库存。

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
) -> Tuple[List[dict], List[GlRow]]:
    """写本单明细行,返回 (逐行 mode 结果, 要追加的重分类分录行)。

    混行(库存 + 非库存)拒收:两条路的 STCRD SEQNUM 都从 1 起会撞,且只有一半货真进库存,
    V1 不静默处理 → 交会计拆单。
    """
    stock_items = [it for it in entry.items if _mode(it) == dbf_detail.ITEM_MODE_STOCK]
    if not stock_items:
        line_modes = dbf_detail.write_stcrd_detail(
            account_dir,
            docnum,
            docdat,
            entry.items,
            entry.supplier_code,
            entry.items_account,
            depcod=depcod,
            userid=userid,
            ref_no=entry.ref_no,
            posopr=S.STCRD_POSOPR_PURCHASE,
            **({"locations": locations} if locations is not None else {}),
        )
        return line_modes, []
    if len(stock_items) != len(entry.items):
        raise StockLineError("混合库存+非库存明细行(V1 不支持)")

    try:
        line_modes, inv_amounts = stock_purchase.write_stock_in_lines(
            account_dir,
            docnum,
            docdat,
            entry.items,
            entry.supplier_code,
            depcod=depcod,
            acccod=getattr(entry, "stock_acccod", ""),
            userid=userid,
            ref_no=entry.ref_no,
            **({"locations": locations, "moves": moves} if locations is not None else {}),
        )
    except Exception as e:  # 入库任一步失败 → 统一交会计(整单回滚·不留半截库存)
        raise StockLineError(str(e)) from e

    return line_modes, _reclass_rows(inv_amounts, gl_rows)


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


def _reclass_rows(inv_amounts: Dict[str, Decimal], gl_rows: List[GlRow]) -> List[GlRow]:
    """{存货科目: 入库净额} → 逐科目一对 借 存货 / 贷 购货。

    购货科目取本单已有的采购分录(各账套科目码不同,不硬编码)。搬走的钱不许超过本单购货额,
    否则购货会被贷成负数 —— 那说明行净额与税前额对不上(漏行/含税混算),交会计而不是硬写。
    """
    purchase_acc, purchase_amt = _purchase_line(gl_rows)
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


def _purchase_line(gl_rows: List[GlRow]) -> Tuple[str, Decimal]:
    for seqit, accnum, _trntyp, amt in gl_rows:
        if seqit == S.SEQIT["purchase"]:
            return accnum, amt
    raise StockLineError("本单缺购货分录,拼不出存货重分类")
