# -*- coding: utf-8 -*-
"""Express 单据金额/审核字段口径(采购 + 销项共用 · 按 FLGVAT 自洽)。

从 dbf_writer 抽出(行数收口·逻辑一字不动):
  · amount_fields —— 按现/赊/无税返回 (FLGVAT, AMOUNT, AFTDISC, TOTAL, NETVAL, NETAMT)。
  · audit_fields  —— 补 Express GUI 自填、外部直写**必补**的审核字段(缺 CMPLAPP='Y' 报表不可见)。
dbf_writer 再导出本模块符号,dbf_sales 仍从 dbf_writer 取(import 路径不变)。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict


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


def audit_fields(docdat, depcod: str, has_vat: bool, orgnum: int = 0) -> Dict[str, Any]:
    """Express GUI 录入时自动填、外部直写**必须补**的"审核/单据"字段。

    缺这些(尤其 `CMPLAPP='Y'`)→ Express 视为未审核/不完整,报表与放大镜列表都看不到
    (逐列比对真单 1739/1741 皆 CMPLAPP='Y' 锁定)。DEPCOD 账套相关 → 由连接 config 给,
    不用部门的公司留空。ORGNUM = 对方档的分店号(0 = 总店;真账表头与对方档一致 99%+,
    以前恒写 0 会让分店票在税表上显示成总店)。
    """
    return {
        "CMPLAPP": "Y",
        "CMPLDAT": docdat,
        "DUEDAT": docdat,
        "VATDAT": docdat,
        "BILNUM": "~",
        "NXTSEQ": "1",
        "DEPCOD": (depcod or ""),
        "ORGNUM": int(orgnum or 0),
        "SRV_VATTYP": ("1" if has_vat else "-"),
    }
