# -*- coding: utf-8 -*-
"""STCRD 明细行写入 + item_mode 分发 + 读回校验 —— 销项/采购共用(去重)。

销项/采购唯一差异是 PEOPLE(客户码 vs 供应商码),由 party_code 参数传入。
item_mode 分发:
- non_stock_item:product_master.ensure_item 拿非库存主档码 → 写非库存行(X=净额·不动库存)。
- direct_account:STKCOD=收入/费用账码 → 直接科目行(X=0)。ensure_item 失败回落亦走此并记因。
- stock_item:不归本模块 —— 走到这里即路由漏了,显性报错(见 StockLineMisrouted)。

账务正确性由调用方显式写的 GLJNLIT 保证,不依赖主档;故建/配主档失败回落 direct_account
不阻断整单(诚实记 reason)。
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

import dbf

from . import account_rules
from . import dbf_schema as S
from . import product_master
from . import stock_location
from .logger import get_logger

log = get_logger(__name__)

ITEM_MODE_STOCK = "stock_item"  # 采购建/进真库存(路由到 stock_purchase,不进本模块)


class DetailVerifyError(Exception):
    """明细读回不符 —— message 与 evidence 分开放:调用方把它包成 _WriteError 时 message
    一字不改(云端按错误文本 match 的既有断言依赖),evidence 随 _WriteError 带回 ack。"""

    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.message = message
        self.evidence = evidence


class StockLineMisrouted(Exception):
    """库存品行漏到了非库存路。静默回落 direct_account 会写出不动库存的科目行 —— 库存
    永远建不起来、下一单销售必卡 STOCK_ITEM_NOT_FOUND,且没人察觉。宁可整单失败。"""


def _row_summary(r) -> dict:
    """证据用行摘要:序号/日期/数量/金额列取到就带,取不到省略。值转原生 JSON 型
    (日期→ISO 串·Decimal→float·字符串去尾空),保证 ack meta 可 json.dumps。"""
    out: dict = {}
    for key, col in (
        ("seq", "SEQNUM"),
        ("date", "DOCDAT"),
        ("qty", "TRNQTY"),
        ("amount", "NETVAL"),
    ):
        try:
            val = r[col]
        except (KeyError, AttributeError):
            continue
        if isinstance(val, _dt.date):
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = float(val)
        elif isinstance(val, str):
            val = val.strip()
        out[key] = val
    return out


def line_mode(
    seq: int,
    name: str,
    stkcod: str,
    *,
    mode: str,
    reason: str = "",
    created: bool = False,
    unit_guessed: bool = False,
    unit_code: str = "",
) -> dict:
    """逐行回执(`line_modes`)的唯一构造口 —— 非库存/卖库存/进库存三条路共用。

    这些键名是与云端的**契约**:主站 services/erp/push_log_meta 按 reason 标「未结转成本」、
    按 created 标「新建了主档」、按 unit_guessed + unit_code 标「单位是系统按账套众数填的」。
    三处各拼一份 dict,漏一个键就是推送日志上少一条标注、还不报错 —— 故收成一个函数。
    改键名必须连着改主站那侧,否则字段名对不上=标记静默消失。
    """
    return {
        "seq": seq,
        "name": name,
        "mode": mode,
        "stkcod": stkcod,
        "reason": reason,
        "created": created,
        "unit_guessed": unit_guessed,
        "unit_code": unit_code,
    }


def _open(account_dir, name: str, mode):
    t = dbf.Table(str(Path(account_dir) / f"{name}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(mode)
    return t


def resolve_line(
    account_dir, mode: str, name: str, account: str, unit: str, docdat, userid: str
):
    """决定该行 STKCOD + 最终 mode。non_stock_item 调 ensure_item;失败回落 direct_account
    (STKCOD=账码)并记原因。返回 (stkcod, eff_mode, reason, created)。

    created:True=本次新建主档 · False=复用既有 · None=直接科目行(无主档概念)。供导出标"新建/复用"。
    """
    if mode == ITEM_MODE_STOCK:
        raise StockLineMisrouted(f"库存品行未被路由到入库路:{name!r}(item_mode={mode})")
    if mode == "non_stock_item":
        pre_existing = product_master.find_item_code(
            account_dir, name
        )  # 建前查:决定新建/复用
        try:
            stkcod = product_master.ensure_item(
                account_dir,
                name,
                account,
                created_date=docdat,
                userid=userid,
                unit=unit,
            )
            return stkcod, "non_stock_item", "", (pre_existing is None)
        except Exception as e:  # 主档建/配失败 → 回落(账务仍对·GL 显式写),不阻断整单
            log.warning("ensure_item 回落 direct_account: %s (%s)", name, e)
            return account, "direct_account", f"ensure_item_failed: {e}", None
    return account, "direct_account", "", None


def write_stcrd_detail(
    account_dir,
    docnum: str,
    docdat,
    items: List[dict],
    party_code: str,
    items_account: str,
    *,
    depcod: str = "",
    userid: str = S.PEARNLY_USERID,
    ref_no: str = "",
    posopr: str = S.STCRD_POSOPR,
    locations: Optional[List[Optional[str]]] = None,
) -> List[dict]:
    """逐行 OCR 商品写 STCRD,按 item_mode 分发。返回逐行 mode 结果(供状态诚实化)。
    ``locations``:载荷 4 的逐行仓库码(None = 主仓);这些行不动库存,不碰 STLOC。
    NETVAL 逐行求和已在云端对账闸 ≈ 税前额;此处只忠实落行。

    `ref_no` 只有采购侧传(真账镜像:采购行带 REFNUM 98%+,销项行 0/14834 带)。"""
    line_modes: List[dict] = []
    location = account_rules.main_location(account_dir)
    t = _open(account_dir, "STCRD", dbf.READ_WRITE)
    try:
        for seq, it in enumerate(items, start=1):
            qty = float(Decimal(str(it.get("qty") or "1")))
            unit_price = float(Decimal(str(it.get("unit_price") or "0")))
            amount = float(Decimal(str(it.get("amount") or "0")))
            name = str(it.get("name") or "")
            unit = str(it.get("unit") or "")
            account = str(it.get("account") or items_account)
            mode = (it.get("item_mode") or "direct_account").strip()
            stkcod, eff_mode, reason, created = resolve_line(
                account_dir, mode, name, account, unit, docdat, userid
            )
            builder = (
                S.stcrd_nonstock_row
                if eff_mode == "non_stock_item"
                else S.stcrd_detail_row
            )
            t.append(
                account_rules.localize_stcrd(account_dir, builder(
                    docnum,
                    seq,
                    docdat,
                    stkcod,
                    party_code,
                    name,
                    qty,
                    unit_price,
                    amount,
                    unit=unit,
                    depcod=depcod,
                    ref_no=ref_no,
                    posopr=posopr,
                ), unit, stock_location.location_at(locations, seq - 1) or location)
            )
            line_modes.append(
                line_mode(
                    seq, name, stkcod, mode=eff_mode, reason=reason, created=created
                )
            )
    finally:
        t.close()
    return line_modes


def verify_detail(
    account_dir,
    docnum: str,
    base: Optional[Decimal],
    detail_n: int,
    *,
    allow_stock_impact: bool = False,
    since_recno: int = 0,
) -> Optional[str]:
    """读回明细:行数==detail_n、行合计≈税前额。非库存/直接科目行必须零库存影响
    (LOT/余额全 0);V2-b 卖库存品(allow_stock_impact=True)本就动库存,跳过该闸。
    不符 → raise DetailVerifyError(带结构化现场证据,供 ack meta.evidence);库存影响
    不符仍返错误描述(无证据场景)。"""
    st = _open(account_dir, "STCRD", dbf.READ_ONLY)
    try:
        # 只数还算数的行:删单后旧明细软删仍在表里,连它一起数则行数与合计都翻倍,
        # 重推同一张票必被判「行数对不上」而整单回滚(单据永远进不去的第三堵墙)。
        # S.live_rows 是软删过滤的判据 seam(变异测试打它的补丁验证这道闸),必须走它。
        # since_recno:只看本次追加的行。销项凭证号就是票面发票号,账套里可能已有会计手录的同号单
        # (erpcowork 2026-09-30:IV69/08-001… 已在 Express 里),连它们一起数行数就翻倍、整单回滚。
        # 用户要推就推,不在这里拦(用户 2026-09-30)。
        rows = [r for r in S.live_rows(st)
                if (r.DOCNUM or "").strip() == docnum and dbf.recno(r) >= since_recno]
        # 同 DOCNUM 的软删行数(evidence 用)。live_rows 会把软删行藏起来,证据里数不到
        # 它们 —— 这里按 DOCNUM 过滤一遍原始表,不多扫别的表。
        deleted_count = sum(
            1
            for r in st
            if dbf.is_deleted(r) and (r.DOCNUM or "").strip() == docnum
        )
    finally:
        st.close()
    if len(rows) != detail_n:
        raise DetailVerifyError(
            f"STCRD line count {len(rows)} != {detail_n}",
            {
                "kind": "detail_mismatch",
                "table": "STCRD",
                "docnum": docnum,
                "expected_lines": detail_n,
                "found_lines": len(rows),
                "existing_rows": [_row_summary(r) for r in rows[:5]],
                "deleted_count": deleted_count,
            },
        )
    line_sum = sum((Decimal(str(r.NETVAL or 0)) for r in rows), Decimal("0"))
    if base is not None and abs(line_sum - base) > Decimal("1.00"):
        raise DetailVerifyError(
            f"STCRD sum {line_sum} != base {base}",
            {
                "kind": "detail_sum_mismatch",
                "table": "STCRD",
                "docnum": docnum,
                "line_sum": float(line_sum),
                "base": float(base),
            },
        )
    if not allow_stock_impact:
        for r in rows:
            if not S.stcrd_no_stock_impact(r):
                return "STCRD line touches stock/COGS (LOT/bal non-zero)"
    return None
