# -*- coding: utf-8 -*-
"""Express 载荷映射的共享纯函数(进项/销项共用 · 不连库 · 不调 LLM)。

进项 mapper 与销项 sales_mapper 都依赖这里:金额自洽求 (base,vat,total)、佛历日期、
科目解析、法人前缀、付款判定。钱一律 decimal,借贷/税额由确定性代码算
(见 [[line-accounting-honest-status-boundary]])。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

# facade re-export(体积闸拆分 · 付款判据簇下沉 payment.py · common.X 单一对象不变)
from pearnly_core.express_rules.push.payment import (  # noqa: F401,E402
    SRC_BANK,
    SRC_DOC_TYPE,
    SRC_EXPLICIT,
    SRC_MANUAL,
    SRC_NONE,
    SRC_PROFILE,
    payment_is_paid,
    payment_verdict,
    payment_verdict_for,
)

from pearnly_core.express_rules.contracts.line_items import TOLERANCE as _ITEMS_TOL, matched_target
from pearnly_core.express_rules.push.primitives import _d, parse_invoice_date
from pearnly_core.express_rules.push.written_check import clean as clean_verify


_CENT = Decimal("0.01")
_BALANCE_TOL = Decimal("0.02")  # 税前+税额 与 含税 容差
_VAT_RATE = Decimal("7")

# 泰国法人前缀(prename)· 按长度降序匹配(长的先,防 หจก. 被 ห้าง 短前缀截断)。
_PRENAMES = (
    "บริษัทจำกัด",
    "บริษัท",
    "ห้างหุ้นส่วนจำกัด",
    "ห้างหุ้นส่วนสามัญ",
    "หจก.",
    "หจก",
    "หสน.",
)


@dataclass(frozen=True)
class ExpressMapResult:
    """映射结果。ok=True → payload 可入队;ok=False → reason 落 manual 留人工。"""

    ok: bool
    payload: Optional[Dict[str, Any]]
    reason: str
    # 失败时票上的商品行(name/stkcod)。库存类失败要靠它渲染「补期初 / 选存货科目组」卡:
    # 闸在载荷构造之前就退出,不带出来的话日志里只剩一个 reason,卡拿到空列表退化成一句话。
    items: Optional[List[Dict[str, Any]]] = None




def _q(v: Decimal) -> Decimal:
    return v.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def _s(v: Decimal) -> str:
    """decimal → 定点字符串(钱不用 float · 保两位)。"""
    return format(_q(v), "f")


def fail(reason: str, items: Optional[List[Dict[str, Any]]] = None) -> ExpressMapResult:
    return ExpressMapResult(False, None, reason, items)


def line_item_names(fields: Dict[str, Any]) -> List[Dict[str, Any]]:
    """票面商品行的名字(去重保序)· 给库存类失败卡当「这张票涉及哪些商品」用。

    直接取 OCR 行,不过对账闸 —— 卡只是让会计认出该补哪个商品,不参与记账金额。
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for it in fields.get("items") or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or it.get("description") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name})
    return out


def detect_prename(name: str) -> str:
    s = (name or "").strip()
    for p in _PRENAMES:
        if s.startswith(p):
            return p
    return ""




def be_dates(invoice_date: Any) -> Optional[tuple[str, str]]:
    """公历日期 → (docdate_be, vat_period_be)。无法解析 → None(缺日期不建账)。"""
    d = parse_invoice_date(invoice_date)
    if d is None:
        return None
    yy = (d.year + 543) % 100
    return (f"{yy:02d}{d.month:02d}{d.day:02d}", f"{yy:02d}{d.month:02d}01")


# 科目来源标记(诚实状态 · 写进 push 日志 / 详情卡):
#   category_map  品类→科目映射命中(规则/学习·可信)
#   config_default 回落账套默认(ISINFO 默认)→ 标"默认·待核",提示人核一眼
#   ""            未解出(留人工)
SRC_CATEGORY = "category_map"
SRC_DEFAULT = "config_default"


def resolve_account_sourced(
    accounts: List[Dict[str, Any]], category: str, config_code: Optional[str]
) -> tuple[Optional[str], str]:
    """科目解析 + 来源。先查映射束(erp_type=express · pearnly_category 命中)→ category_map;
    否则 config 兜底码 → config_default(账套默认 · 待核);都无 → (None, '')。"""
    cat = (category or "").strip()
    if cat:
        for a in accounts or []:
            if (a.get("erp_type") or "").lower() == "express" and (
                a.get("pearnly_category") or ""
            ) == cat:
                code = (a.get("erp_code") or "").strip()
                if code:
                    return code, SRC_CATEGORY
    code = (config_code or "").strip()
    return (code, SRC_DEFAULT) if code else (None, "")


def resolve_account(
    accounts: List[Dict[str, Any]], category: str, config_code: Optional[str]
) -> Optional[str]:
    """科目码(来源见 resolve_account_sourced)。固定项(AR/AP/VAT)不关心来源 · 用此瘦封装。"""
    return resolve_account_sourced(accounts, category, config_code)[0]


# 明细行采信状态(诚实 · 写进 payload → 决定是否落 STCRD 明细 + posted_partial):
#   ok          逐行齐全 + 行合计≈税前额 → 可落明细
#   empty       OCR 无行项目 → 退回表头模式(非错误)
#   incomplete  有行但缺金额/品名 → 不可信,退回表头
#   mismatch    行合计 与税前额对不上 → 不可信,退回表头
ITEMS_OK = "ok"
ITEMS_EMPTY = "empty"
ITEMS_INCOMPLETE = "incomplete"
ITEMS_MISMATCH = "mismatch"



ITEM_MODE_NONSTOCK = "non_stock_item"  # V2 默认:非库存商品主档(不动库存/COGS)
ITEM_MODE_DIRECT = "direct_account"  # 兜底:直接科目行(V1)
ITEM_MODE_STOCK = "stock_item"  # V2-b(gated):真实进销存主档 STKTYP=0 · 本期 mapper 不发

# 记账画像 posting_mode → 明细行 item_mode 的单一映射(两 mapper 共用,防口径漂移)。
# manual_review 不该走到这(调用方已 escalate 交人);未知 posting_mode 保守回落非库存。
_POSTING_ITEM_MODE = {
    "non_stock": ITEM_MODE_NONSTOCK,
    "direct_account": ITEM_MODE_DIRECT,
    "stock": ITEM_MODE_STOCK,
}


def item_mode_for(posting_mode: str) -> str:
    """记账画像 posting_mode → 明细行 item_mode。未知/manual_review → 非库存(安全兜底)。"""
    return _POSTING_ITEM_MODE.get(posting_mode, ITEM_MODE_NONSTOCK)


# OCR 偶把泰文声调符输出成 MS Thai 私用区浮动字形(U+F70A..F712),Express DBF=cp874 编不了 →
# 写盘崩(DBF_WRITE_FAILED 'charmap')。映回标准泰文(权威 8 项与 recon._norm_thai 同源 · 共享
# util 去重是后续项);仍编不了的残字兜底剔除。用 chr(码点) 写键避免私用区字符被编辑器吞掉。
_THAI_PUA_MAP = {
    chr(0xF70A): "\u0e48",  # MAI EK
    chr(0xF70B): "\u0e49",  # MAI THO
    chr(0xF70C): "\u0e4a",  # MAI TRI
    chr(0xF70D): "\u0e4b",  # MAI CHATTAWA
    chr(0xF70E): "\u0e4c",  # THANTHAKHAT
    chr(0xF710): "\u0e4d",  # NIKHAHIT
    chr(0xF711): "\u0e31",  # MAI HAN-AKAT
    chr(0xF712): "\u0e47",  # MAI TAIKHU
}


def thai_dbf_safe(s: str) -> str:
    """规整 DBF 写入文本:泰文私用区声调符 → 标准泰文;剩余非 cp874 字符剔除(写盘不崩)。"""
    if not s:
        return s
    out: List[str] = []
    for ch in s:
        ch = _THAI_PUA_MAP.get(ch, ch)
        try:
            ch.encode("cp874")
        except UnicodeEncodeError:
            continue
        out.append(ch)
    return "".join(out)


def sanitize_payload_cp874(obj: Any) -> Any:
    """写盘前唯一收口:递归把 payload 里每个字符串字段过 thai_dbf_safe,文本字段只此一处
    统一净化(不再逐字段散落 · 自动覆盖 desc/ref_no/prename 等现有及将来所有字段)。
    非字符串原样返回。"""
    if isinstance(obj, str):
        return thai_dbf_safe(obj)
    if isinstance(obj, dict):
        return {k: sanitize_payload_cp874(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_payload_cp874(v) for v in obj]
    return obj


# 载荷契约版本(与小助手协商 · preflight 拿 endpoint 上报的 max_payload_version 比对,
# 老客户端跟不上新字段就拦下而非推一份它解析不了的载荷)。加不兼容字段才需要 bump。
PAYLOAD_VERSION = 1


# 明细行带仓库码（``loccod``）的载荷：旧版小助手会静默写进主仓，用版本号让它诚实拒收（CC 桌面版按仓写入、维护 STLOC）
LOCATION_PAYLOAD_VERSION = 4
# 总账分录带角色（``role``，ERP 前台明细按行科目拆，费用 / 收入可以有几条）：旧版小助手按位置认分录会把第二条费用当进项税，
# 用版本号让它诚实拒收（CC 桌面版 1.12.0 起按角色写）
ROLE_PAYLOAD_VERSION = 5
_STOCK_MODES = ("stock_item", "stock_sale")


def finalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """进项/销项两个 mapper 共用的写盘前收口:盖版本号 + CP874 净化,单一落点不两处复制。

    带了选定存货码 / 部门码的载荷已被 `choices.apply_to_payload` 升到 2,这里不往回盖；明细行带了存货码的至少 2，
    带了仓库码的 4。"""
    lines = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    floor = PAYLOAD_VERSION
    if any(item.get("code") for item in lines):
        floor = max(floor, 2)
    if any(item.get("loccod") for item in lines):
        floor = max(floor, LOCATION_PAYLOAD_VERSION)
    if any(isinstance(line, dict) and line.get("role") for line in payload.get("lines") or []):
        floor = max(floor, ROLE_PAYLOAD_VERSION)
    payload["payload_version"] = max(floor, int(payload.get("payload_version") or 0))
    return sanitize_payload_cp874(payload)


def split_by_account(items, default_acc: str, base: Decimal, side: str, desc: str) -> Optional[List[Dict[str, str]]]:
    """总账里收入（贷）或费用（借）那一边按行科目拆（10-24 验收问题 24：按供应商记住的费用科目盖掉了明细里认出的 Express 物料，
    仓库维修记成了电费）：自带科目（`account`，ERP 前台给的）的行记到自己的科目，其余行记单据的科目；同科目合并、按出现顺序。
    明细合计与税前额的分位差（明细已过对账闸）落最后一个科目，合计恒等于税前额；差得多回 None。"""
    totals: Dict[str, Decimal] = {}
    for item in items:
        account = str(item.get("account") or "").strip() or default_acc
        totals[account] = totals.get(account, Decimal("0")) + (_d(item.get("amount")) or Decimal("0"))
    if not totals:
        return None
    gap = _q(base) - _q(sum(totals.values(), Decimal("0")))
    if abs(gap) > Decimal("1"):
        return None
    last = list(totals)[-1]
    totals[last] += gap
    return [{"acc": account, "side": side, "amount": _s(amount), "desc": desc} for account, amount in totals.items()]


def extract_line_items(
    fields: Dict[str, Any],
    base: Decimal,
    *,
    total: Optional[Decimal] = None,
    item_mode: Any = ITEM_MODE_NONSTOCK,
) -> Dict[str, Any]:
    """OCR 行项目 → 规整明细 + 对账闸。确定性纯函数,绝不为"好看"采信不自洽的明细。

    入:fields.items = [{name, qty, price, subtotal}](OCR · 数值为字符串/可空);base=税前额;
    item_mode=每行写入模式(V2 默认 non_stock_item·companion 据此匹配/建非库存主档)。
    出:{items, status, line_sum}。status 见上常量。仅 status==ok 时 items 应落 STCRD。
    每行 items: {name, qty, unit, unit_price, amount, item_mode}(amount=行净额,逐行求和须≈base)。
    """
    raw = fields.get("items")
    if not isinstance(raw, list) or not raw:
        return {"items": [], "status": ITEMS_EMPTY, "line_sum": _s(Decimal("0"))}

    items: List[Dict[str, str]] = []
    incomplete = False
    for idx, it in enumerate(raw):
        if not isinstance(it, dict):
            incomplete = True
            continue
        name = str(it.get("name") or "").strip()
        qty = _d(it.get("qty"))
        price = _d(it.get("price") or it.get("unit_price"))
        amount = _d(it.get("subtotal") or it.get("amount") or it.get("total"))
        if amount is None and qty is not None and price is not None:
            amount = _q(qty * price)
        # 品名是行的身份;金额是对账依据。任一缺 → 整组不可信(不静默吞行/补 0)。
        if not name or amount is None:
            incomplete = True
            continue
        if qty is None or qty == 0:
            qty = Decimal("1")
        if price is None:
            price = _q(amount / qty) if qty else amount
        mode = item_mode(idx, it) if callable(item_mode) else item_mode
        row = {
            "name": name[:50],
            "qty": _s(qty),
            "unit": str(it.get("unit") or it.get("uom") or "").strip()[:10],
            "unit_price": _s(price),
            "amount": _s(amount),
            "item_mode": mode,
        }
        if mode in _STOCK_MODES:
            # 来源已给出 Express 存货码 / 仓库码（ERP 前台的单，CC 桌面版直连 ERP，ADR-025）：照码写，不再按品名对；OCR 的票没有这两键
            code = str(it.get("code") or "").strip()
            if code:
                row["code"] = code[:20]
            loccod = str(it.get("loccod") or "").strip()
            if loccod:
                row["loccod"] = loccod[:4]
        account = str(it.get("account") or "").strip()
        if account:
            # 来源已给出这一行自己的费用科目（ERP 前台：明细认出的 Express 费用类物料，编码就是科目）：照它记；OCR 的票没有这一键
            row["account"] = account[:20]
        items.append(row)

    if incomplete or not items:
        return {
            "items": items,
            "status": ITEMS_INCOMPLETE if raw else ITEMS_EMPTY,
            "line_sum": _s(sum((_d(i["amount"]) for i in items), Decimal("0"))),
        }

    line_sum = sum((_d(i["amount"]) for i in items), Decimal("0"))
    # 对账判定与识别后的复核提示同一个(`contracts.line_items`,单一来源)。
    target = matched_target(line_sum, base, total)
    if target == "base":
        return {"items": items, "status": ITEMS_OK, "line_sum": _s(line_sum)}
    # 含税单价小票(泰国零售通例):逐行含税 · 合计≈含税总额 → 按比例摊回不含税(×base/total),
    # 余数(逐行四舍五入漂移)落末行,摊后逐行合计精确==税前额。这是多认一个【合法】对账目标,
    # 不放松容差、不跳过对账:真读错的明细两个目标都对不上,照旧 mismatch 转人工。
    if target == "total":
        ex = _rescale_items_exvat(items, base, total)
        if ex is not None:
            return {"items": ex, "status": ITEMS_OK, "line_sum": _s(base)}
    # 票面整单折扣（泰国进货票常见）：行合计 − 折扣 ≈ 税前额也是对得上的。小助手的明细没有折扣列（折扣固定写 0），
    # 折扣按金额比例摊进各行，余数落末行，摊后逐行合计 == 税前额（10-09 实测：读对了折扣 500 仍停在明细不符）。
    discount = _d(fields.get("discount"))
    if discount is not None and discount > 0 and line_sum > 0 and matched_target(line_sum - discount, base) == "base":
        spread = _rescale_items_exvat(items, base, line_sum)
        if spread is not None:
            return {"items": spread, "status": ITEMS_OK, "line_sum": _s(base)}
    return {"items": items, "status": ITEMS_MISMATCH, "line_sum": _s(line_sum)}


def _rescale_items_exvat(
    items: List[Dict[str, str]], base: Decimal, total: Decimal
) -> Optional[List[Dict[str, str]]]:
    """逐行含税额按比例摊成不含税(×base/total)· 余数落末行 · 摊后逐行合计精确==base。
    单价同步重算。摊后仍对不平(异常)→ None(退回 mismatch · 绝不硬采信)。"""
    if not items:
        return None
    factor = base / total
    scaled: List[tuple] = []
    running = Decimal("0")
    for it in items:
        amt = _q(_d(it["amount"]) * factor)
        running += amt
        scaled.append((it, amt))
    resid = _q(base) - _q(running)
    scaled[-1] = (scaled[-1][0], _q(scaled[-1][1] + resid))
    out: List[Dict[str, str]] = []
    tot = Decimal("0")
    for it, amt in scaled:
        tot += amt
        q = _d(it["qty"])
        up = _q(amt / q) if q and q != 0 else amt
        out.append({**it, "amount": _s(amt), "unit_price": _s(up)})
    if abs(_q(tot) - _q(base)) > _ITEMS_TOL:
        return None
    return out


# ── V3 推送阶段(细粒度状态)· 存 erp_push_logs.response_body.meta ────────────────
# erp_push_logs.status 仍粗粒度(pending/success/manual/failed),本组是其【细化】不替代:
# 前端据 meta.stage 显诚实状态。queued/leased/writing/indexing/verifying 是流转,
# 其余为落点。waiting_lock = Express 占用账套,稍后自动重领(不算失败、不烧重试次数)。
STAGE_QUEUED = "queued"
STAGE_LEASED = "leased"
STAGE_WRITING = "writing"
STAGE_INDEXING = "indexing"
STAGE_VERIFYING = "verifying"
STAGE_SUCCESS = "success"
STAGE_WAITING_LOCK = "waiting_lock"
STAGE_FAILED = "failed"
STAGE_ROLLED_BACK = "rolled_back"  # 写了一半已恢复账套备份
STAGE_NEEDS_MAPPING = "needs_mapping"  # 缺科目映射
STAGE_NEEDS_REVIEW = "needs_review"  # 对账失败/疑似重复

# Agent ack 上报的 outcome(覆盖 success 布尔 · 旧客户端不传则按布尔走,向后兼容)。
ACK_OUTCOMES = (
    STAGE_SUCCESS,
    STAGE_WAITING_LOCK,
    STAGE_FAILED,
    STAGE_ROLLED_BACK,
    STAGE_NEEDS_MAPPING,
    STAGE_NEEDS_REVIEW,
)

# 富元数据白名单(Agent 上报 · 落 response_body.meta · 供前端诚实展示)。限键限长防脏。
_META_STR_KEYS = (
    "companion_version",
    "account_dir",
    # 小助手实际落盘的绝对路径。只在与 account_dir 不一致时上报 —— 不一致=配置里的路径被系统
    # 重新解释过(典型:UNC 少写一个反斜杠 → 静默写进本机 C: 影子目录),这条能一眼看穿。
    "account_dir_resolved",
    "doc_type",
    "docnum",
    # ERP 里实际用的客户/供应商码。会计要清理误建的档、或核对配对对不对,都得知道码。
    "party_code",
    # MR.ERP 推送成功时进的导入菜单名（服务端执行器写，doc_type 同键记单据类型）
    "mrerp_menu",
    "stage",
    "error_code",
)
_META_BOOL_KEYS = (
    "created_customer",
    "created_supplier",
    "index_requery",
    "rolled_back",
    "written",
    "duplicate_skipped",
)
_META_LIST_KEYS = ("tables_written", "created_masters", "cdx_failed_tables")
_META_MAX_LIST = 40
# POS 日汇总票同一次写入的付款方式转账凭证号(小助手回执 ``notes.pos_vouchers``,载荷 3):留在 meta 顶层,
# Express 回读据此认出这几张凭证是 Pearnly 写的、并按 `pos.transfers` 比分录(2026-10 CC 闭环)。
POS_VOUCHERS = "pos_vouchers"


def sanitize_push_meta(raw: Any) -> Dict[str, Any]:
    """净化 Agent 上报的富元数据:只留白名单键、限长限量、布尔归一(防被塞脏 jsonb)。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in _META_STR_KEYS:
        v = raw.get(k)
        if v is not None and str(v).strip() != "":
            out[k] = str(v).strip()[:200]
    for k in _META_BOOL_KEYS:
        if k in raw:
            out[k] = bool(raw.get(k))
    for k in _META_LIST_KEYS:
        v = raw.get(k)
        if isinstance(v, list):
            out[k] = [str(x).strip()[:60] for x in v[:_META_MAX_LIST] if str(x).strip()]
    # 写完顺手核对（数字会计员第 3 期第 6 条，CC 桌面版 1.8.0 起）：只留 state / fields / party 三个键的白名单
    verify = clean_verify(raw.get("verify"))
    if verify is not None:
        out["verify"] = verify
    notes = raw.get("notes")
    vouchers = notes.get(POS_VOUCHERS) if isinstance(notes, dict) else None
    if isinstance(vouchers, list):
        kept = [str(x).strip()[:60] for x in vouchers[:_META_MAX_LIST] if str(x).strip()]
        if kept:
            out[POS_VOUCHERS] = kept
    return out


def amounts(fields: Dict[str, Any], history: Dict[str, Any]) -> Optional[tuple]:
    """从票面字段确定性求 (base, vat, total)。返回 None = 数不自洽/缺总额(留人工)。

    优先采信票面 税前+税额 自洽;缺 VAT 用 总额−税前;只有总额按 7% 含税反推。
    """
    from pearnly_core.express_rules.push.field_rules import vat_from_inclusive

    total = (
        _d(history.get("total_amount")) or _d(fields.get("total_amount")) or _d(fields.get("total"))
    )
    if total is None or total <= 0:
        return None
    base = _d(fields.get("subtotal"))
    vat = _d(fields.get("vat"))

    if base is not None and vat is not None and base > 0:
        pass
    elif base is not None and base > 0:
        vat = total - base
    elif vat is not None and vat >= 0:
        base = total - vat
    else:
        # 只有总额 → 按 7% 含税反推。
        vat = _q(vat_from_inclusive(total))
        base = total - vat

    base, vat, total = _q(base), _q(vat), _q(total)
    # 小计印的是折扣前的数：小计 − 折扣 + 税 ≈ 总额时，税前额取折后（与识别校验 `sanity.line_sum_mismatch` 同口径）。
    discount = _d(fields.get("discount"))
    if (discount is not None and discount > 0 and abs(base + vat - total) > _BALANCE_TOL
            and abs(base - discount + vat - total) <= _BALANCE_TOL):
        base = _q(base - discount)
    if base <= 0 or vat < 0:
        return None
    if abs(base + vat - total) > _BALANCE_TOL:
        return None
    return base, vat, total
