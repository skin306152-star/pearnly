"""MR.ERP 代录要填的值（多产品共用：Cowork 第 3 步，ERP 第 6 步接同一份；ADR-026 第 2 条、评估 §4、§14.1）。

代录 = 直写同一份准备，只换最后一步：直写把票生成导入表交 MR.ERP 导入，代录把同一批票按 MR.ERP 网页表单要填的值交给 CC 桌面版，
由 CC 在 MR.ERP 自己的页面里一格一格填、存档、读回。两条路共用：

- 分流（`routing.group_by_doc_type`：方向、付款、货 / 费、单据防呆）；
- 到「可以写进 MR.ERP」为止的准备（`transport.MrErpImport.ready_rows`：匹配闸、导入值、科目安全阀、菜单、自建对方、preflight、
  自建商品）——开录前对方 / 商品在 MR.ERP 里没有的，服务端照直写自建（定不下来 / 建不成的这张转人工，不交 CC）；
- 每格的值与导入表同源（供应商码、客户码、商品码、单号、表头的分店 / 部门 / 工作 / 员工 / 仓库、税率选项）。

载荷 ``mrerp`` 块的形状照 CC 第 2 步 ``companion/src/companion/mrerp_operator/draft.py``（分支 `pearnly/cc-mrerp-operator`），
`tests/test_mrerp_operator_draft.py` 用固定输入 → 固定 JSON 的黄金样本锁住这一侧；两边一致的契约测试见施工记录
`docs/work/2026-11-03-cowork-mrerp-operator-wiring.md`。不带任何密码。

与第 2 步草稿的两处口径（写进契约）：

- ``vat_rate`` 是 MR.ERP 页面税率选项的文字（与导入表同值：``7 (แยก)`` 价外、``7 (รวม)`` 价内、``0``、费用档 ``นอกระบบ``），
  ``vat_included`` 同时给出；
- ``totals`` 总是给：按页面同算法（明细合计 − 折扣 = 税前；价外按税前乘税率，价内逐行拆税）由站点算好，CC 读页面合计与它对上才存。
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Tuple

from pearnly_core.mrerp.push import routing
from pearnly_core.mrerp.push.modules import get_module
from pearnly_core.mrerp.push.xlsx import defaults as account_values, generator as _gen
from pearnly_core.mrerp.push.xlsx.fmt import fmt_date, history_number, mrerp_sales_tax_label

CENT = Decimal("0.01")
DOC_TYPES = ("purchase", "purchase_expense", "sales_credit", "sales_cash")
# 税率选项文字 → (税率, 价内)
_RATES = {"7 (แยก)": (Decimal(7), False), "7 (รวม)": (Decimal(7), True), "0": (Decimal(0), False),
          "นอกระบบ": (Decimal(0), False)}


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def _num(value: Any) -> str:
    """数字的文字写法（不带多余的 0）：``2`` / ``100.5`` / ``0.3333``。"""
    found = _dec(value).normalize()
    text = format(found, "f")
    return "0" if text in ("-0", "") else text


def totals(lines: List[Dict[str, str]], vat_label: str, discount: Any = 0) -> Dict[str, str]:
    """页面同算法（第 2 步 ``draft.Draft.totals``，MR.ERP ``allform.js::calcgrandtotal``）：税前 / 税额 / 含税。"""
    rate, included = _RATES.get(str(vat_label or ""), (Decimal(0), False))
    off = _money(_dec(discount))
    amounts = [_money(_dec(line.get("qty")) * _dec(line.get("price"))) for line in lines]
    if included and rate:
        removed = [_money(amount * rate / (100 + rate)) for amount in amounts]
        gross = sum((amount - vat for amount, vat in zip(amounts, removed)), Decimal(0))
        vat = sum(removed, Decimal(0)) - (_money(off * rate / 100) if off else Decimal(0))
        before = gross - off
    else:
        before = sum(amounts, Decimal(0)) - off
        vat = _money(before * rate / 100)
    return {"before_vat": str(_money(before)), "vat": str(_money(vat)), "net": str(_money(before + vat))}


def _line(code: Any, qty: Any, price: Any, store: str, desc: Any = "") -> Dict[str, str]:
    return {"code": str(code or "").strip(), "qty": _num(qty if qty not in (None, "") else 1), "price": _num(price),
            "unit": "", "store": store, "desc": str(desc or "").strip()[:200]}


def block(history: Dict[str, Any], mappings: Dict[str, Any], *, doc_type: str, menu: str, url: str, connection: str,
          account_set: Dict[str, str]) -> Dict[str, Any]:
    """一张已准备好（``ready_rows`` 之后：对方 / 商品码已注入 ``mappings``）的票 → 载荷 ``mrerp`` 块。纯函数，不碰网络。"""
    from pearnly_core.mrerp.push.xlsx import purchase, sales_credit
    from pearnly_core.mrerp.push.xlsx.lookups import resolve_customer_code

    if doc_type not in DOC_TYPES:
        raise ValueError("bad MR.ERP doc type")
    docnum = _gen.derive_mrerp_invoice_no(history)
    day = fmt_date(history.get("invoice_date")) or ""
    discount: Any = 0
    if doc_type.startswith("purchase"):
        head = account_values.header(mappings)
        party = purchase._supplier_code(history, mappings)
        ref_no = str(history.get("invoice_number") or history.get("invoice_no") or docnum)[:20]
        if doc_type == "purchase_expense":
            vat_label = purchase.expense_cfg(mappings, "_mrerp_expense_vat_label", purchase._EXPENSE_VAT_LABEL)
            lines = [_line(purchase._expense_item_code(mappings), 1, purchase._gross_total(history), head["store"],
                           purchase._expense_note(history))]
        else:
            vat_label = "7 (แยก)"
            lines = [_line(row["code"], row["qty"], row["price"], head["store"])
                     for row in purchase._detail_rows(history, mappings)]
    else:
        party = resolve_customer_code(history, mappings)
        head = account_values.header(mappings, party)
        ref_no = ("SI" + docnum)[:20]
        vat_label = mrerp_sales_tax_label(history)
        rows = sales_credit.build_sales_credit_detail_rows(history, mappings)
        if doc_type == "sales_cash":
            # 网页表单按「数量 × 单价」自己算，折扣写在表头（导入表把折扣摊进行金额，是导入的口径）；收款由菜单关联的科目带出
            discount = history_number(history, "discount", "discount_total") or 0
        lines = [_line(row.get("product_code"), row.get("qty"), row.get("unit_price"), head["store"],
                       row.get("item_name")) for row in rows]
    return {"url": str(url or "").strip().rstrip("/"), "connection": str(connection or ""),
            "account_set": {key: str((account_set or {}).get(key) or "")
                            for key in ("comidyear", "seldb", "name", "company_name")},
            "doc_type": doc_type, "menu": str(menu or ""), "docnum": docnum, "date": day,
            "party_code": str(party or "").strip(), "ref_no": ref_no, "ref_date": day, "credit_days": "",
            "vat_rate": vat_label, "vat_included": _RATES.get(vat_label, (0, False))[1], "discount": _num(discount),
            "defaults": {key: str(head.get(key) or "") for key in ("dept", "job", "employee", "branch", "store")},
            "lines": lines, "totals": totals(lines, vat_label, discount)}


def stage(importer, histories: List[Dict[str, Any]], mappings: Dict[str, Any], *, today: date, url: str,
          connection: str, account_set: Dict[str, str]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]]]:
    """开录前的服务端准备（要在 ``with importer:`` 里，同直写一次登录、一次读主档）→ ``({票号: mrerp 块}, {票号: 挡下的原因码})``。

    与直写 ``upload_routed_batch`` 同一套分流与准备，只是不生成导入表、不上传。对方 / 商品在 MR.ERP 里没有的照直写自建
    （这是对 MR.ERP 的外部写入，与直写同一条 `autocreate`）。"""
    importer.note_account_tax(histories, mappings)
    default_doc = importer.module.doc_type
    groups, sanity_failed = routing.group_by_doc_type(histories, mappings, default_doc, today=today)
    blocks: Dict[str, Dict[str, Any]] = {}
    failed: Dict[str, List[str]] = {str(row.original.get("id")): [str(r) for r in row.reasons]
                                    for row in sanity_failed if row.original}
    saved = importer.module
    try:
        for doc_type, rows in groups.items():
            module = get_module(doc_type)
            if not module.verified:
                failed.update({str(h.get("id")): [routing.MODULE_UNAVAILABLE_CODE] for h in rows})
                continue
            importer.module = module
            valid, stopped, menu = importer.ready_rows(rows, mappings)
            failed.update({str(row.original.get("id")): [str(r) for r in row.reasons] for row in stopped if row.original})
            for history in valid:
                blocks[str(history.get("id"))] = block(history, mappings, doc_type=doc_type, menu=menu or "", url=url,
                                                       connection=connection, account_set=account_set)
    finally:
        importer.module = saved
    return blocks, failed
