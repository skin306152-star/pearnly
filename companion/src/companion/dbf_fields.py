# -*- coding: utf-8 -*-
"""Express 单据金额/审核字段口径(采购 + 销项共用 · 按 FLGVAT 自洽)。

从 dbf_writer 抽出(行数收口·逻辑一字不动):
  · amount_fields —— 按现/赊/无税返回 (FLGVAT, AMOUNT, AFTDISC, TOTAL, NETVAL, NETAMT)。
  · audit_fields  —— 补 Express GUI 自填、外部直写**必补**的审核字段(缺 CMPLAPP='Y' 报表不可见)。
dbf_writer 再导出本模块符号,dbf_sales 仍从 dbf_writer 取(import 路径不变)。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List


def _f(v: Any) -> float:
    """decimal/str 金额 → VFP double。"""
    return float(Decimal(str(v)))


def amount_fields(is_cash: bool, has_vat: bool, base, vat, total):
    """按 Express 真单 FLGVAT 口径返回 (FLGVAT, AMOUNT, AFTDISC, TOTAL, NETVAL, NETAMT)。

    逐列比对真单锁定(RR/IV 赊单 vs HP/HS 现单):
      · 赊单 FLGVAT='2'(价不含税)→ AMOUNT/AFTDISC/TOTAL/NETVAL=税前 · NETAMT=含税
      · 现单 FLGVAT='1'(价含税)  → AMOUNT/AFTDISC/TOTAL/NETAMT=含税 · NETVAL=税前
      · 无税 FLGVAT='0' → 各额相等
    金额字段口径必须与 FLGVAT 自洽,否则 Express 读数错乱。
    """
    b, t = _f(base), _f(total)
    if not has_vat:
        return "0", b, b, b, b, b
    if is_cash:
        return "1", t, t, t, b, t
    return "2", b, b, b, b, t


EXEMPT_VATCOD = "0"  # Express 商品「ประเภท VAT」：ได้รับยกเว้นVAT = '0'，อัตราปกติ = '1'，ไม่ระบุ = 空


def exempt_line(row: Dict[str, Any], item, fields) -> Dict[str, Any]:
    """来源标的免税行（``vat_exempt``，ERP 增值税登记户的收银日汇总 / 退货单）：照 Express 原生明细记 VATCOD '0'、NETVAL = TRNVAL
    （免税行不拆税；验收实录第 10 条，S 盘副本 6412NLT 的 HH2200117）。``fields`` 是 STCRD 的栏名（大写），老账套没有 VATCOD 栏不写。"""
    if item.get("vat_exempt"):
        if "VATCOD" in fields:
            row["VATCOD"] = EXEMPT_VATCOD
        if "TRNVAL" in row:
            row["NETVAL"] = row["TRNVAL"]
    return row


def _q2(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def line_grosses(items, total) -> List[Decimal]:
    """各行含税额：免税行（``vat_exempt``）含税额就是税前额；含税合计减去它们后按税前额占比摊给其余行，尾差落最后一个应税行，
    合计恒等于含税合计（载荷只带各行税前额。验收实测 10-26：应税 100 + 免税 50 退货，原来按占比摊成 104.67 / 52.33）。
    全是免税行时照占比摊（合计里本来就没有税）。"""
    total = Decimal(str(total))
    nets = [Decimal(str(item.get("amount") or "0")) for item in items]
    exempt = {index for index, item in enumerate(items) if item.get("vat_exempt")}
    if len(exempt) == len(items):
        exempt = set()
    out: List[Decimal] = [nets[index] if index in exempt else Decimal(0) for index in range(len(items))]
    taxable = [index for index in range(len(items)) if index not in exempt]
    rest = total - sum((nets[index] for index in exempt), Decimal(0))
    whole = sum((nets[index] for index in taxable), Decimal(0))
    if whole > 0:
        for index in taxable[:-1]:
            out[index] = _q2(nets[index] * rest / whole)
    out[taxable[-1]] = rest - sum((out[index] for index in taxable[:-1]), Decimal(0))
    return out


GROSS_KEY = "_gross"  # 价含税单（FLGVAT '1'）明细行的含税额，:func:`inclusive_items` 算好、:func:`priced_line` 写进 STCRD


def inclusive_items(items, flgvat: str, total) -> list:
    """价含税的单（现销 HS、现购 HP，FLGVAT '1'）：照 Express 原生，明细 UNITPR / TRNVAL 记含税、NETVAL 记税前（S 盘副本 286 本
    HS 32 万行 98% 如此，HP 同样；FLGVAT '2' 的赊销 / 赊购 TRNVAL = NETVAL，10-26 主会话转试点窗口发现）。给各行带上含税额
    （:func:`line_grosses`），其余单原样返回。"""
    if flgvat != "1" or not items:
        return list(items or [])
    return [{**item, GROSS_KEY: gross} for item, gross in zip(items, line_grosses(items, total))]


def priced_line(row: Dict[str, Any], item, fields) -> Dict[str, Any]:
    """一条刚建好的明细行按单据的价格口径落：带含税额的（:func:`inclusive_items`）UNITPR / TRNVAL 记含税（UNITPR 按交易单位数量）、
    NETVAL 不动；再按 :func:`exempt_line` 标免税行。"""
    gross = item.get(GROSS_KEY)
    if gross is not None:
        qty = Decimal(str(row.get("TRNQTY") or 0))
        row["TRNVAL"] = float(gross)
        row["UNITPR"] = float(_q2(gross / qty)) if qty else float(gross)
    return exempt_line(row, item, fields)


def audit_fields(docdat, depcod: str, has_vat: bool, orgnum: int = 0, duedat=None) -> Dict[str, Any]:
    """Express GUI 录入时自动填、外部直写**必须补**的"审核/单据"字段。

    缺这些(尤其 `CMPLAPP='Y'`)→ Express 视为未审核/不完整,报表与放大镜列表都看不到
    (逐列比对真单 1739/1741 皆 CMPLAPP='Y' 锁定)。DEPCOD 账套相关 → 由连接 config 给,
    不用部门的公司留空。ORGNUM = 对方档的分店号(0 = 总店;真账表头与对方档一致 99%+,
    以前恒写 0 会让分店票在税表上显示成总店)。
    """
    return {
        "CMPLAPP": "Y",
        "CMPLDAT": docdat,
        "DUEDAT": duedat or docdat,  # 赊账单 = 单据日期 + 对方档付款期(调用方算);现金单 = 单据日期
        "VATDAT": docdat,
        "BILNUM": "~",
        "NXTSEQ": "1",
        "DEPCOD": (depcod or ""),
        "ORGNUM": int(orgnum or 0),
        "SRV_VATTYP": ("1" if has_vat else "-"),
    }
