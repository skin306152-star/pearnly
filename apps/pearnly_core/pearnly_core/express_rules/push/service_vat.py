"""服务品进项税口径（Express APTRN.SRV_VATTYP）按账套历史定（主会话 10-29 批，评估见代录分支
`docs/work/2026-10-29-service-vat-basis-评估.md`）。纯函数，不连库；直写（CC 桌面版写账时）与服务端（写之前要不要问人）同一套，
CC 里有一份同样的副本（`companion/service_vat.py`），由契约测试守住两边一致。

Express 存赊购单（RR）有服务品时会问服务品的进项税按 ``1`` เกณฑ์สิทธิ（权责制）还是 ``2`` เกณฑ์เงินสด（收付实现制）。
语料 158 本（10-29 只读统计）：现购单（HP）全是 ``1``；只有货品明细的赊购单全是 ``1``（59,676 张）；带服务品或没有明细的
赊购单才有 ``2``，口径跟着供应商走（带服务品的赊购单：一贯 ``1`` 的 8,117 家、一贯 ``2`` 的 849 家、两种都有的 465 家）。
按时间顺序回放本规则（价格类型 1 / 2、带服务品或没有明细的赊购单 98,186 张，问过的按真值记住）：历史定下 86,527 张、错 533 张；
问 7,175 次；记住后复用 4,484 张、错 1,989 张（两种都有的供应商）；合计错 2,522 张。以前恒写 ``1`` 错 14,003 张。

规则：
1. 没有税的单（价格类型 0）：``-``（同原生）。
2. 现购单：``1``。赊购单只有库存品明细：``1``。
3. 其余赊购单：这家供应商在本账套的赊购历史一贯一种就照用；这家没有历史，看整本账套的赊购单，一贯一种就照用；
   都定不了才用人选过的（按「这本账套 × 这家供应商」记住，``KIND``）；没选过回 None（要问）。

历史只算 Express 里的人录的价格类型 1 / 2 的赊购单（作废的、Pearnly 自己写的不算），值为 ``1`` / ``2`` 的。供应商的历史给成「口径串」：
``"1"`` / ``"2"`` 一贯一种、``"12"`` 两种都有、``""`` 没有历史。整本账套的口径就是各家口径的并集（:func:`book_verdict`）。
"""
from __future__ import annotations

from typing import Iterable, Optional

ACCRUAL = "1"   # เกณฑ์สิทธิ
CASH_BASIS = "2"  # เกณฑ์เงินสด
NO_VAT = "-"
VALUES = (ACCRUAL, CASH_BASIS)
KIND = "service_vat"  # 推送选择的一类：人选过的口径，按「这本账套 × 这家供应商」记住
# 选项的编码（存进账套记忆的值）→ 写进 Express 的值。用词不用数字：页面按编码取 Express 原词（เกณฑ์สิทธิ / เกณฑ์เงินสด）
OPTIONS = {"accrual": ACCRUAL, "cash_basis": CASH_BASIS}
# CC 桌面版写赊购单时定不了、载荷里也没有人选的值：不写，回这个码（转人工，服务端在推送停下的同一处让人选一次）
CHOICE_CODE = "SERVICE_VAT_BASIS_CHOICE"
# 载荷带人选的口径（``srv_vattyp``）：旧版 CC 桌面版不认这个键、恒写 1，盖 13 让它诚实拒收
PAYLOAD_VERSION = 13
PAYLOAD_KEY = "srv_vattyp"


def verdict(values: Iterable[str]) -> str:
    """一组历史口径 → 口径串：只留 ``1`` / ``2``，按序拼（``"1"`` / ``"2"`` / ``"12"`` / ``""``）。"""
    seen = {str(value or "").strip() for value in values or ()}
    return "".join(value for value in VALUES if value in seen)


def book_verdict(suppliers: Iterable[str]) -> str:
    """整本账套的口径 = 各家供应商口径串的并集。"""
    return verdict("".join(str(item or "") for item in suppliers or ()))


def chosen_value(code) -> str:
    """人选的值（选项编码 ``accrual`` / ``cash_basis``，或已是 ``1`` / ``2``）→ 写进 Express 的值；认不出回空。"""
    code = str(code or "").strip()
    return code if code in VALUES else OPTIONS.get(code, "")


def basis(*, has_vat: bool, cash: bool, service: bool, supplier: str = "", book: str = "",
          chosen: str = "") -> Optional[str]:
    """这张进货单的服务品进项税口径（见文件头规则）。``service``：有服务品明细或没有明细（只有库存品明细的为假）；
    ``supplier`` / ``book``：口径串；``chosen``：人选过的（选项编码或 ``1`` / ``2``）。回 ``1`` / ``2`` / ``-``，定不了回 None。"""
    if not has_vat:
        return NO_VAT
    if cash or not service:
        return ACCRUAL
    supplier, book = verdict(supplier), verdict(book)
    if supplier:
        decided = supplier if len(supplier) == 1 else ""
    else:
        decided = book if len(book) == 1 else ""
    return decided or chosen_value(chosen) or None


__all__ = ["ACCRUAL", "CASH_BASIS", "CHOICE_CODE", "KIND", "NO_VAT", "OPTIONS", "PAYLOAD_KEY", "PAYLOAD_VERSION", "VALUES",
           "basis", "book_verdict", "chosen_value", "verdict"]
