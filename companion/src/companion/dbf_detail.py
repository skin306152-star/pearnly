# -*- coding: utf-8 -*-
"""STCRD 明细行写入 + item_mode 分发 + 读回校验 —— 销项/采购共用(去重)。

销项/采购唯一差异是 PEOPLE(客户码 vs 供应商码),由 party_code 参数传入。
item_mode 分发:
- non_stock_item:product_master.ensure_item 拿非库存主档码 → 写非库存行(X=净额·不动库存)。
- direct_account:直接记科目 —— 照 Express 原生先保证主档里有编码 = 这个收入 / 费用科目码的非库存商品
  (product_master.ensure_account_item,有就复用、没有就建),这一行引用它、按非库存商品行写(11-02:只写科目码
  的行 Express 原生作废报 2101)。ensure_item 失败回落亦走此并记因;建这件商品也失败才只写科目码(X=0)。
- stock_item:不归本模块 —— 走到这里即路由漏了,显性报错(见 StockLineMisrouted)。

账务正确性由调用方显式写的 GLJNLIT 保证,不依赖主档;故建/配主档失败回落 direct_account
不阻断整单(诚实记 reason)。
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import dbf

from . import account_rules
from . import dbf_fields
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
    cost_ignored: bool = False,
) -> dict:
    """逐行回执(`line_modes`)的唯一构造口 —— 非库存/卖库存/进库存三条路共用。

    这些键名是与云端的**契约**:主站 services/erp/push_log_meta 按 reason 标「未结转成本」、
    按 created 标「新建了主档」、按 unit_guessed + unit_code 标「单位是系统按账套众数填的」。
    三处各拼一份 dict,漏一个键就是推送日志上少一条标注、还不报错 —— 故收成一个函数。
    改键名必须连着改主站那侧,否则字段名对不上=标记静默消失。
    """
    row = {
        "seq": seq,
        "name": name,
        "mode": mode,
        "stkcod": stkcod,
        "reason": reason,
        "created": created,
        "unit_guessed": unit_guessed,
        "unit_code": unit_code,
    }
    if cost_ignored:
        # 载荷 21:这一行带了人填的单位成本,但账里有成本基础(正结存的移动均价 / 标准成本),没用它。只在发生时带这个键
        row["cost_ignored"] = True
    return row


def line_numbers(items: list, seqs: Optional[List[int]] = None) -> List[int]:
    """明细行号(STCRD.SEQNUM):给了 ``seqs`` 用它(混行单分段写),否则从 1 起连续编。"""
    if seqs is None:
        return list(range(1, len(items) + 1))
    if len(seqs) != len(items):
        raise ValueError(f"line numbers {len(seqs)} != items {len(items)}")
    return list(seqs)


def runs(items: list, is_stock) -> List[Tuple[bool, List[int], list]]:
    """一张单的明细按载荷顺序切成「连续的同一类」几段:``[(是不是库存行, [行号…], [行…])]``,行号从 1 起、整张单连续。

    混行单(库存 + 非库存,载荷 19)照载荷顺序一段段写:库存段走入库 / 出库路,其余段走非库存路,每行的 SEQNUM 是它在整张单里的
    行号,不再各从 1 起撞号(V1 拒收的原因)。"""
    out: List[Tuple[bool, List[int], list]] = []
    for seq, item in enumerate(items, start=1):
        kind = bool(is_stock(item))
        if out and out[-1][0] == kind:
            out[-1][1].append(seq)
            out[-1][2].append(item)
        else:
            out.append((kind, [seq], [item]))
    return out


def _open(account_dir, name: str, mode):
    t = dbf.Table(str(Path(account_dir) / f"{name}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(mode)
    return t


def resolve_line(
    account_dir, mode: str, name: str, account: str, unit: str, docdat, userid: str
):
    """决定该行 STKCOD + 最终 mode。non_stock_item 调 ensure_item;失败回落 direct_account 并记原因。
    direct_account 的 STKCOD = 科目码,主档里编码 = 科目码的非库存商品由 :func:`_account_line` 保证。
    返回 (stkcod, eff_mode, reason, created)。

    created:True=本次新建主档 · False=复用既有 · None=建主档失败、只写科目码的旧写法。供导出标"新建/复用",也定这一行
    按非库存商品行写(有主档)还是只写科目码的直接科目行。
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
            return _account_line(account_dir, account, unit, docdat, userid, name, f"ensure_item_failed: {e}")
    return _account_line(account_dir, account, unit, docdat, userid, name)


def _account_line(account_dir, account: str, unit: str, docdat, userid: str, name: str, reason: str = ""):
    """直接记科目的行:照 Express 原生,先保证主档里有编码 = 科目码的非库存商品(`product_master.ensure_account_item`),
    这一行引用它、按非库存商品行写——只写科目码、主档里没有这件商品时,Express 原生作废报 2101「找不到商品编码」,单子停在
    「作废不完整」(11-02)。同码的是库存品就抛 ``AccountItemIsStock``(整单回滚、交人)。建这件商品本身失败时最后才退回只写
    科目码的旧写法,原因随回执带回(``created`` 为 None)。"""
    try:
        code, created = product_master.ensure_account_item(
            account_dir, account, created_date=docdat, userid=userid, unit=unit, name=name)
    except product_master.AccountItemIsStock:
        raise
    except Exception as e:  # noqa: BLE001 — 账务仍对(GL 显式写),不阻断整单
        log.warning("ensure_account_item 失败,只写科目码: %s (%s)", account, e)
        return account, "direct_account", "; ".join(x for x in (reason, f"account_item_failed: {e}") if x), None
    return code, "direct_account", reason, created


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
    seqs: Optional[List[int]] = None,
    slmcod: str = "",
) -> List[dict]:
    """逐行 OCR 商品写 STCRD,按 item_mode 分发。``slmcod``:销货单头的销售员,明细行照原生也带(进货不传)。返回逐行 mode 结果(供状态诚实化)。
    ``locations``:载荷 4 的逐行仓库码(None = 主仓);这些行不动库存,不碰 STLOC。
    ``seqs``:这几行在整张单里的行号(混行单分段写时给,见 :func:`runs`;``locations`` 照整张单的下标取);不给 = 从 1 起。
    NETVAL 逐行求和已在云端对账闸 ≈ 税前额;此处只忠实落行。

    `ref_no` 只有采购侧传(真账镜像:采购行带 REFNUM 98%+,销项行 0/14834 带)。"""
    line_modes: List[dict] = []
    location = account_rules.main_location(account_dir)
    if product_master.backfill_due(account_dir):
        # 1.12.11 以前写的只写科目码的直接科目行:同一张单的写前备份里把它们要的商品补建上(提交成功才记补过,见
        # `product_master.backfill_committed`)
        made = product_master.backfill_account_items(Path(account_dir), created_date=docdat, userid=userid)
        if made:
            log.info("补建直接科目行要的商品 %s: %s", account_dir, ",".join(made))
    t = _open(account_dir, "STCRD", dbf.READ_WRITE)
    fields = {name.upper() for name in t.field_names}
    try:
        for seq, it in zip(line_numbers(items, seqs), items):
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
            # 引用主档商品的行(非库存品、编码 = 科目码的直接科目行)按原生非库存商品行写;只有建主档失败的直接科目行
            # (created 为 None)才是只写科目码的旧写法。原生非库存行的 X 栏两边不同(S 盘 95 本人录的单,11-07 取证):
            # 进货 RR / HP 记税前额(30 万行全是),销货 IV / HS 是 0(10.3 万行全是,售价只在 XSALVAL)——销货照直接科目行写
            costed = created is not None and posopr == S.STCRD_POSOPR_PURCHASE
            builder = S.stcrd_nonstock_row if costed else S.stcrd_detail_row
            S.append_stcrd(t, S.with_salesman(dbf_fields.priced_line(
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
                ), unit, stock_location.location_at(locations, seq - 1) or location), it, fields), slmcod, fields)
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
    stock_seqs: Optional[Iterable[int]] = None,
) -> Optional[str]:
    """读回明细:行数==detail_n、行合计≈税前额。非库存/直接科目行必须零库存影响
    (LOT/余额全 0);V2-b 卖库存品(allow_stock_impact=True)本就动库存,跳过该闸。
    不符 → raise DetailVerifyError(带结构化现场证据,供 ack meta.evidence);库存影响
    不符仍返错误描述(无证据场景)。
    ``stock_seqs``:放行库存影响时,只放行这几个行号(库存行);混行单其余行照样必须零库存影响。不给 = 整张单都放行(以前的口径)。"""
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
    elif stock_seqs is not None:
        allowed = {int(seq) for seq in stock_seqs}
        for r in rows:
            if _seqnum(r) not in allowed and not S.stcrd_no_stock_impact(r):
                return f"STCRD non-stock line {_seqnum(r)} touches stock/COGS (LOT/bal non-zero)"
    return None


def _seqnum(rec) -> Optional[int]:
    try:
        return int(str(rec.SEQNUM or "").strip())
    except (ValueError, AttributeError):
        return None


def stock_line_numbers(line_modes: List[dict]) -> frozenset:
    """回执里动库存的行号(采购入库 ``stock_item``、销售出库 ``stock_sale``),给读回逐行放行库存影响。"""
    return frozenset(int(m["seq"]) for m in line_modes or [] if m.get("mode") in (ITEM_MODE_STOCK, "stock_sale"))
