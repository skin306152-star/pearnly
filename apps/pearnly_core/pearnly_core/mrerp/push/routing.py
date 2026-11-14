# 来源：旧线上 ec2e5c957e6b · services/erp/mrerp_http/routing.py
# 改动：判定函数改用新平台已迁同源实现（contracts.push / push.express），「今天」由调用方注入；
# 四语展示文案不搬（用户 2026-09-26：只显示状态），失败只留原因码。
"""任意单据 → 方向(销/采)→ MR.ERP doc_type。

复用 Express 侧确定性方向判定(services/erp/express_push/direction · 税号锚点 · 不靠 LLM):
套账主体税号 == 卖方 → 销项;== 买方 → 采购;两边都对不上/都命中 → ambiguous(None,留人工)。
有意变化(2026-10-19,同 Express 2b825a00):工作空间税号判不出时再按推往的这家 MR.ERP 公司自己登记的税号判
(`contracts.push.resolve_account_direction`;事务所替客户记账时工作空间税号不是票上的买卖方),方向闸两个税号都认。
不看票是不是这家公司的(用户 2026-11-04:Express 不按单据本方税号拦,我们也不拦):方向明确的别家票照常写。
方向再按付款状态细分成具体导入模块(见 modules.MODULES):
  销项:已付/现金 → sales_cash;否则 → sales_credit
  采购:judge_direction(services/purchase/intake · 与 Pearnly 自身单据同口径)判 expense →
       purchase_expense(费用档);判 purchase_invoice → purchase(货品)。各推这家公司的哪个菜单见 targets/mrerp_menus

只做纯映射;own_tax_id(工作空间税号)与 account_tax_id(这家公司在 MR.ERP 登记的税号,`account_tax` 核过校验位)
由调用方传入。doc_type=None 时调用方不自动推(留人工)。
"""

from __future__ import annotations

import logging

from typing import Any, Dict, List, Optional

from datetime import date

from pearnly_core.tax_documents.amounts import valid_tax_id
from pearnly_core.express_rules.contracts.push import clean_tax_id, resolve_account_direction
from pearnly_core.express_rules.push.doc_sanity import check_document
from pearnly_core.express_rules.push.field_rules import item_verdict
from pearnly_core.express_rules.push.payment import payment_verdict_for
from pearnly_core.mrerp.push.models import FailedRow, ImportResult
from pearnly_core.mrerp.push.modules import get_module
from pearnly_core.mrerp.push.xlsx import generator as _gen

logger = logging.getLogger("mr-pilot")


def _fields(flat: Dict[str, Any]) -> Dict[str, Any]:
    f = (flat or {}).get("fields")
    return f if isinstance(f, dict) else {}


def account_tax(raw: Any) -> str:
    """这家公司在 MR.ERP 登记的税号 → 能当方向锚点的 13 位有效税号;读不到、不是有效税号
    (MR.ERP 样板库填的 8-888-888-88888-8、分店填的 1)→ 空串,只按工作空间税号判。"""
    tax = clean_tax_id(raw)
    return tax if tax and valid_tax_id(tax) else ""


def choose_doc_type(
    flat: Dict[str, Any],
    history: Dict[str, Any],
    *,
    own_tax_id: Any,
    account_tax_id: Any = None,
    mappings: Optional[Dict[str, Any]] = None,
    today: date,
) -> Optional[str]:
    """套账主体税号 × 票面买卖方 → sales_credit / sales_cash / purchase(_expense) / None。

    方向:明确声明的优先,其次工作空间税号,再其次这家公司在 MR.ERP 登记的税号(`resolve_account_direction`)。
    mappings 可选(_bank_index · F6 银行佐证喂进销项 payment_verdict);未传 = 旧调用点零改动。
    """
    direction = resolve_account_direction(flat, history, own_tax_id=own_tax_id, account_tax_id=account_tax_id)
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
    # 仍留 None ——由方向闸(direction_unsettled)停下问一次进项还是销项,不赌方向。
    buyer = clean_tax_id(fields.get("buyer_tax") or fields.get("buyer_tax_id"))
    if is_expense and not buyer:
        logger.info("[mrerp-route] doc=purchase_expense reason=fallback+%s", item_src)
        return "purchase_expense"
    return None


_PURCHASE_DOC_TYPES = ("purchase", "purchase_expense")


# 共用防呆的原因里写到账本名字的两种（词条「需在 Express 中处理」「仅…推送至 Express」）：MR.ERP 这边换成自己的码，
# 词条说 MR.ERP（直写与代录草稿都经这里）；其余（日期）照原码
MRERP_SANITY_CODES = {"credit_note": "mrerp_credit_note", "currency_not_thb": "mrerp_currency_not_thb"}


def _doc_sanity_reason(history: Dict[str, Any], *, today: date) -> Optional[str]:
    """单据防呆(外币/贷项/未来日期)· 复用 Express doc_sanity · 命中返 reason（账本名字相关的换 MR.ERP 码）。"""
    fields = history.get("fields") if isinstance(history.get("fields"), dict) else {}
    reason = check_document(fields, history, today=today)
    if not reason:
        return reason
    code, sep, rest = reason.partition(":")
    return MRERP_SANITY_CODES.get(code, code) + sep + rest


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
    参考科目「เงินสด」过账(见 xlsx/sales_cash.py 与 mrerp/push/defaults.py)。
    """
    if not default_doc.startswith("sales"):
        return False
    if not (isinstance(mappings, dict) and mappings.get("_mrerp_walkin_sales_cash", True)):
        return False
    if history.get("client_id"):
        return False
    fields = _fields(history)
    if str(fields.get("posting_payment_manual") or "").strip().lower() == "credit":
        # 人明确选了赊账（Cowork 入口选的、ERP 未收款的单）：不改成现销。11-14 TEST2019 真写：ERP 一张未收款、客户没税号的销售
        # 被改成现销、挂「เงินสด」客户、整额进了银行科目
        return False
    return not clean_tax_id(fields.get("buyer_tax") or fields.get("buyer_tax_id"))


def route_and_upload(adapter, histories: List[Dict[str, Any]], mappings: Dict[str, Any], *, today: date):
    """按每张单据的方向路由 doc_type 再推送 · 合并为一个 ImportResult(同一会话内复用登录)。

    推送前先跑**单据防呆**(外币/贷项/未来日期 → 转人工·复用 Express doc_sanity),
    命中即落 failed 不推不建。其余**只把【确认为采购】的票切到采购/费用模块**——销项 / 判不出方向
    一律保持默认 doc_type(sales_credit),零倒退;方向判不出、票面却读到税号的由方向闸(_hold_unsettled)停下。
    """
    if not histories:
        return ImportResult(total=0)
    total = len(histories)
    default_doc = adapter.module.doc_type
    groups, sanity_failed = group_by_doc_type(histories, mappings, default_doc, today=today)
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


def group_by_doc_type(histories: List[Dict[str, Any]], mappings: Dict[str, Any], default_doc: str, *,
                      today: date) -> "tuple[Dict[str, List[Dict[str, Any]]], List[FailedRow]]":
    """每张票分到哪个导入模块（直写与代录准备同一套分流）+ 单据防呆挡下的行。``groups`` 按第一次出现的先后。"""
    own = mappings.get("_own_tax_id") if isinstance(mappings, dict) else None
    account = mappings.get("_account_tax_id") if isinstance(mappings, dict) else None
    sanity_failed: List[FailedRow] = []
    groups: Dict[str, List[Dict[str, Any]]] = {}  # dict 保序 → 无需单独 order 列表
    for h in histories:
        chosen = choose_doc_type(h, h, own_tax_id=own, account_tax_id=account, mappings=mappings, today=today)
        if chosen in _PURCHASE_DOC_TYPES or chosen == "sales_cash":
            # 采购(货品)、采购费用、现金销售分别切到各自模块(菜单在导入时按账套所选定)。
            # 有意差异：旧版这里只切采购，销项一律默认赊销（除散客）——用户在入口选的「现金」
            # (posting_payment_manual)算出了 sales_cash 却被丢掉；新平台按用户选择推（用户 2026-09-26）。
            dt = chosen
        elif _walkin_to_cash(h, default_doc, mappings):
            dt = "sales_cash"  # 散客现金销售 → ขายเงินสด(账正)
        else:
            dt = default_doc  # 销项/判不出 → 默认(sales_credit)· 零倒退
        reason = _doc_sanity_reason(h, today=today)
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
    return groups, sanity_failed


# 方向闸失败码:进项还是销项判不出,停下问一次(同 Express 推送的 direction_unknown)。
DIRECTION_UNKNOWN_CODE = "ERR_DIRECTION_UNKNOWN"


def direction_unsettled(
    flat: Dict[str, Any], history: Dict[str, Any], *, own_tax_id: Any, expected_direction: str,
    account_tax_id: Any = None,
) -> bool:
    """这张票要不要停下问一次进项还是销项(同 Express 推送的 direction_unknown;用户 2026-11-04 定方向闸保留)。

    不看票是不是这家公司的:方向明确(写明的,或按工作空间税号 own_tax_id、这家公司在 MR.ERP 登记的税号 account_tax_id
    判出的,同 `choose_doc_type`)且与这一组的导入模块一致就放行,别家公司的票照常写。只在两种情况停下:
    ① 判出的方向与这一组的导入模块相反(分流按方向分组,正常走不到;兜底不把进项写成销项);
    ② 方向判不出、票面却读到了买卖方税号(都不是这两个税号,或买卖方同一个税号)—— 这种单分流时落到默认的赊销模块,
       不停下就会被当成卖给买方的销售单写进去。买卖方同税号以前由单据防呆 `seller_buyer_same_tax` 拦(11-04 取消),
       方向判不出照旧停在这里。
    票面一个税号都没读到(零售小票)照旧走默认模块;两个税号都没有时判不了方向,也照旧。
    """
    anchors = [t for t in (clean_tax_id(own_tax_id), clean_tax_id(account_tax_id)) if t]
    if not anchors:
        return False
    direction = resolve_account_direction(flat, history, own_tax_id=own_tax_id, account_tax_id=account_tax_id)
    if direction == expected_direction:
        return False
    if direction:
        return True  # ① 方向与导入模块相反
    # ② 方向判不出、票面读到了买卖方税号(读到了却判不出方向:都不是这两个税号,或两边同一个税号)
    fields = _fields(flat)
    return any(clean_tax_id(fields.get(f"{side}_tax") or fields.get(f"{side}_tax_id")) for side in ("seller", "buyer"))
