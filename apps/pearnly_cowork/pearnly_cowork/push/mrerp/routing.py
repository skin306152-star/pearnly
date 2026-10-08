# 来源：旧线上 ec2e5c957e6b · services/erp/mrerp_http/routing.py
# 改动：判定函数改用新平台已迁同源实现（contracts.push / push.express），「今天」由调用方注入；
# 四语展示文案不搬（用户 2026-09-26：只显示状态），失败只留原因码。
"""任意单据 → 方向(销/采)→ MR.ERP doc_type。

复用 Express 侧确定性方向判定(services/erp/express_push/direction · 税号锚点 · 不靠 LLM):
套账主体税号 == 卖方 → 销项;== 买方 → 采购;两边都对不上/都命中 → ambiguous(None,留人工)。
方向再按付款状态细分成具体导入模块(见 modules.MODULES):
  销项:已付/现金 → sales_cash;否则 → sales_credit
  采购:judge_direction(services/purchase/intake · 与 Pearnly 自身单据同口径)判 expense →
       purchase_expense(费用档);判 purchase_invoice → purchase(货品)。各推这家公司的哪个菜单见 targets/mrerp_menus

只做纯映射;own_tax_id 由调用方从套账主体解析后传入。doc_type=None 时调用方不自动推(留人工)。
"""

from __future__ import annotations

import logging

from typing import Any, Dict, List, Optional

from datetime import date

from pearnly_cowork.contracts.push import clean_tax_id, resolve_direction
from pearnly_cowork.push.express.doc_sanity import check_document
from pearnly_cowork.push.express.field_rules import item_verdict
from pearnly_cowork.push.express.payment import payment_verdict_for
from pearnly_cowork.push.mrerp.models import FailedRow, ImportResult
from pearnly_cowork.push.mrerp.modules import get_module
from pearnly_cowork.push.mrerp.xlsx import generator as _gen

logger = logging.getLogger("mr-pilot")


def _fields(flat: Dict[str, Any]) -> Dict[str, Any]:
    f = (flat or {}).get("fields")
    return f if isinstance(f, dict) else {}


def choose_doc_type(
    flat: Dict[str, Any],
    history: Dict[str, Any],
    *,
    own_tax_id: Any,
    mappings: Optional[Dict[str, Any]] = None,
    today: date,
) -> Optional[str]:
    """套账主体税号 × 票面买卖方 → sales_credit / sales_cash / purchase(_expense) / None。

    mappings 可选(_bank_index · F6 银行佐证喂进销项 payment_verdict);未传 = 旧调用点零改动。
    """
    direction = resolve_direction(flat, history, own_tax_id=own_tax_id)
    fields = _fields(flat)

    if direction == "sales":
        paid, src = payment_verdict_for(flat, fields, mappings, direction="sales")
        doc = "sales_cash" if paid else "sales_credit"
        logger.info("[mrerp-route] doc=%s payment_src=%s", doc, src or "config_default")
        return doc

    # 采购方向货/费分流:人工裁决(posting_item_type_manual · F5 复核屏)优先于自动判据——
    # 会计裁决,不是习惯裁决:有完整税票 = 可抵进项,票面法定证据不许被自动判据/档案习惯压过。
    # 供应商过账档案(supplier_posting_profiles)的 default_item_type 同理在此不自动消费,
    # 只留给工单线预填;唯用户在复核屏显式点过(manual)才算数,精度高于 judge_direction 猜测。
    # 判据抽在 services.purchase.item_verdict(与 Express 采购车道共用 · 防两车道漂移)。
    is_expense, item_src = item_verdict(fields, today=today)

    if direction == "purchase":
        doc = "purchase_expense" if is_expense else "purchase"
        logger.info("[mrerp-route] doc=%s reason=anchor+%s", doc, item_src)
        return doc

    # 税号锚点判不出(零售小票常态:票面无买方身份)→ 跟 Pearnly 自身单据判定走:
    # judge_direction=expense 且买方身份完全缺失 = 本子里记的是费用支出 → ERP 同向
    # 落采购费用档。此前掉端点默认销项 → 销项闸要求挂客户 → 恒 ERR_NO_CLIENT(真机语料
    # SISTER MAKEUP 2026-07-02)。票面读到了买方税号(即便是别家的)不走此兜底,
    # 仍留 None ——那是匹配闸(confirmed_account_set_mismatch)的辖区,不赌方向。
    buyer = clean_tax_id(fields.get("buyer_tax") or fields.get("buyer_tax_id"))
    if is_expense and not buyer:
        logger.info("[mrerp-route] doc=purchase_expense reason=fallback+%s", item_src)
        return "purchase_expense"
    return None


_PURCHASE_DOC_TYPES = ("purchase", "purchase_expense")


def _doc_sanity_reason(history: Dict[str, Any], doc_type: str, *, today: date) -> Optional[str]:
    """单据防呆(外币/贷项/押金/未来日期/坏税号)· 复用 Express doc_sanity · 命中返 reason。"""
    fields = history.get("fields") if isinstance(history.get("fields"), dict) else {}
    direction = "purchase" if doc_type in _PURCHASE_DOC_TYPES else "sales"
    return check_document(fields, history, direction, today=today)


def _sanity_fail_row(history: Dict[str, Any], reason: str) -> FailedRow:
    return FailedRow(
        invoice_no=_gen.derive_mrerp_invoice_no(history),
        reasons=[reason],
        original=history,
    )


MODULE_UNAVAILABLE_CODE = "ERR_MRERP_MODULE_UNAVAILABLE"


def _module_unavailable_fail_row(history: Dict[str, Any]) -> FailedRow:
    """端点未真机验证(verified=False)静态判掉的行 → 转人工。"""
    return FailedRow(
        invoice_no=_gen.derive_mrerp_invoice_no(history),
        reasons=[MODULE_UNAVAILABLE_CODE],
        original=history,
    )


def _walkin_to_cash(history: Dict[str, Any], default_doc: str, mappings: Dict[str, Any]) -> bool:
    """无买方散客销售 → 现金销售单(ขายเงินสด · 账正:钱入现金,不在 เงินสด 客户下虚挂应收)。

    仅当 ① 默认端点是销项 ② 开关开(默认 · mappings['_mrerp_walkin_sales_cash']=False 可关)
    ③ 无买方身份(无 client_id 且票面无买方税号)。现金收款写现金栏,MR.ERP 按菜单关联的现金科目 /
    参考科目「เงินสด」过账(见 xlsx/sales_cash.py 与 push/mrerp/defaults.py)。
    """
    if not default_doc.startswith("sales"):
        return False
    if not (isinstance(mappings, dict) and mappings.get("_mrerp_walkin_sales_cash", True)):
        return False
    if history.get("client_id"):
        return False
    fields = _fields(history)
    return not clean_tax_id(fields.get("buyer_tax") or fields.get("buyer_tax_id"))


def route_and_upload(adapter, histories: List[Dict[str, Any]], mappings: Dict[str, Any], *, today: date):
    """按每张单据的方向路由 doc_type 再推送 · 合并为一个 ImportResult(同一会话内复用登录)。

    推送前先跑**单据防呆**(外币/贷项/押金/未来日期/坏税号 → 转人工·复用 Express doc_sanity),
    命中即落 failed 不推不建。其余**只把【确认为采购】的票切到采购/费用模块**——销项 / 判不出方向
    一律保持默认 doc_type(sales_credit),零倒退;别家的票由匹配闸(_filter_by_account_set)另挡。
    """
    if not histories:
        return ImportResult(total=0)
    total = len(histories)
    own = mappings.get("_own_tax_id") if isinstance(mappings, dict) else None
    default_doc = adapter.module.doc_type
    sanity_failed: List[FailedRow] = []
    groups: Dict[str, List[Dict[str, Any]]] = {}  # dict 保序 → 无需单独 order 列表
    for h in histories:
        chosen = choose_doc_type(h, h, own_tax_id=own, mappings=mappings, today=today)
        if chosen in _PURCHASE_DOC_TYPES or chosen == "sales_cash":
            # 采购(货品)、采购费用、现金销售分别切到各自模块(菜单在导入时按账套所选定)。
            # 有意差异：旧版这里只切采购，销项一律默认赊销（除散客）——用户在入口选的「现金」
            # (posting_payment_manual)算出了 sales_cash 却被丢掉；新平台按用户选择推（用户 2026-09-26）。
            dt = chosen
        elif _walkin_to_cash(h, default_doc, mappings):
            dt = "sales_cash"  # 散客现金销售 → ขายเงินสด(账正)
        else:
            dt = default_doc  # 销项/判不出 → 默认(sales_credit)· 零倒退
        reason = _doc_sanity_reason(h, dt, today=today)
        if reason:
            sanity_failed.append(_sanity_fail_row(h, reason))
            continue
        groups.setdefault(dt, []).append(h)

    # 方向可排查:push 日志 request_body 不记 doc_type,出问题只能反证(真机排查踩过)。
    logger.info(
        "[mrerp-route] groups=%s sanity_failed=%d",
        {
            k: [str(h.get("invoice_number") or h.get("id") or "?")[:24] for h in v]
            for k, v in groups.items()
        },
        len(sanity_failed),
    )
    if not groups:  # 全被防呆挡下
        res = ImportResult(total=total)
        res.failed = sanity_failed
        return res

    # 无防呆命中 且 全部同向=默认 → 走原路(零行为变化 · 单次 upload)。
    if not sanity_failed and list(groups) == [default_doc]:
        return adapter.upload_invoice_batch(histories, mappings, today=today)

    merged = ImportResult(total=total)
    saved = adapter.module
    try:
        for dt in groups:
            m = get_module(dt)
            # 端点未真机验证(master_* 等 verified=False)→ 静态前置判掉,只转这一组人工,
            # 别拖累混批里已打通的组。条件与 adapter 的 verified 闸(upload 前 raise)同源;
            # 这里不 catch MRERPBusinessError——那也是已验证组真服务器拒收的载体,吞了会错标。
            if not m.verified:
                logger.warning(
                    "[mrerp-route] doc=%s module unavailable · %d row(s) held for manual review",
                    dt,
                    len(groups[dt]),
                )
                merged.failed.extend(_module_unavailable_fail_row(h) for h in groups[dt])
                continue
            adapter.module = m
            r = adapter.upload_invoice_batch(groups[dt], mappings, today=today)
            merged.success.extend(r.success)
            merged.failed.extend(r.failed)
            merged.unknown.extend(r.unknown)
            merged.elapsed_ms += r.elapsed_ms or 0
            merged.xlsx_size_bytes += r.xlsx_size_bytes or 0
    finally:
        adapter.module = saved
    merged.failed.extend(sanity_failed)
    return merged


# 匹配闸失败码。
ACCOUNT_SET_MISMATCH_CODE = "ERR_ACCOUNT_SET_MISMATCH"


def confirmed_account_set_mismatch(
    flat: Dict[str, Any], history: Dict[str, Any], *, own_tax_id: Any, expected_direction: str
) -> bool:
    """能【确认】这张票不属于本套账吗?防"把别家的票推错套账"的安全闸。

    只在【确认不符】时返 True(挡下),读不到税号=无法确认→False(不挡·交上游已判的方向)。
    确认不符 = ① 方向刚好相反(如采购票走销项)· 或 ② 票面读到了买卖方税号但都不是套账主体。
    own_tax_id 空(套账税号未解析)→ 无锚点无法判 → False(不挡)。
    """
    if not own_tax_id:
        return False
    direction = resolve_direction(flat, history, own_tax_id=own_tax_id)
    if direction == expected_direction:
        return False  # 确认属于本套账
    if direction:
        return True  # 方向相反(采购票走销项等)→ 确认不符
    # direction=None(ambiguous):仅当票面确实读到买卖方税号、却都不是套账主体,才算确认别家的票
    own = clean_tax_id(own_tax_id)
    fields = _fields(flat)
    read = [
        t
        for t in (
            clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id")),
            clean_tax_id(fields.get("buyer_tax") or fields.get("buyer_tax_id")),
        )
        if t
    ]
    return bool(read) and own not in read
