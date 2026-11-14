# 来源：旧线上 ec2e5c957e6b · services/erp/mrerp_xlsx_purchase.py（只改导入路径与模板位置）
"""MR.ERP xlsx · purchase(impaptran · selmenu 67 货品 / 453 费用)· 克隆官方 example.xlsx 生成。

采购 = 3-sheet:表头(供应商/供应商票号/税率)+ 明细(商品/量/单价/金额)+ 尾(押金/收货 · 留空)。
账套主体=买方;供应商(ผู้จำหน่าย)=采购票卖方 → 码经 mappings['suppliers'] 解析,无则回退卖方税号
(与自建供应商码同口径 · 幂等)。克隆策略同 sales_cash(known-facts §6.3):字节模板只重写
<sheetData>+sharedStrings,保留 styles。该模板 cellXfs:表头 s13/文本 s7/日期 s14/数值 s9。

expense=True(453 · 2026-07-09 真机口径):费用票无完整税票,进项 VAT 不可抵扣计入成本 →
税率 นอกระบบ(体系外 0% 不进 VAT 报表)· 明细固定单行(通用费用物料 · 金额=含税总额)·
表头备注 1 留 items 名(人读线索)。不逐行展开 items:费用 GL 粒度来自物料科目,逐行无
会计价值,且行基额与含税总额对不上。
"""

import io
import os
import re
import zipfile
from collections import OrderedDict
from typing import Any, Dict, List

from pearnly_core.mrerp.push.xlsx import defaults as account_values, generator as _gen
from pearnly_core.mrerp.push.xlsx.fmt import (
    allocate_discount,
    fmt_date,
    fmt_number,
    history_number,
    line_amount,
    OUTSIDE,
    mrerp_bill_no,
    mrerp_tax_kind,
    mrerp_tax_label,
    vat_label,
    supplier_code_from_name,
)
from pearnly_core.mrerp.push.xlsx.lookups import (
    MRERP_CASH_CUSTOMER,
    _build_product_lookup,
    _resolve_product_code,
)
from pearnly_core.mrerp.push.xlsx.sales_credit import _format_num
from pearnly_core.express_rules.contracts.push import clean_tax_id

_TEMPLATE_NAME = "test_data_mrerp_sample_purchase.xlsx"

_S_HEADER = 13  # numFmt 49 文本(表头)
_S_TEXT = 7  # numFmt 49 文本数据
_S_DATE = 14  # numFmt 187 日期(存文本)
_S_NUM = 9  # numFmt 4 数值

_HEADERS_1 = [
    "เลขที่",
    "วันที่",
    "อัตราภาษี",
    "สาขา",
    "แผนก",
    "งาน",
    "พนักงาน",
    "กำหนดส่งสินค้า",
    "รหัสผู้จำหน่าย",
    "รหัสผู้จำหน่าย (บิล)",
    "เลขที่บิล",
    "วันที่",
    "หักส่วนลด",
    "หมายเหตุ 1",
    "หมายเหตุ 2",
    "หมายเหตุ 3",
]
_HEADERS_2 = ["เลขที่", "รหัสสินค้า", "แผนก", "งาน", "คลัง", "จำนวน", "ราคา/หน่วย", "จำนวนเงิน"]
_HEADERS_3 = ["เลขที่", "เลขที่เงินมัดจำ", "ออกใบรับสินค้า"]

_NUM_COLS_1 = {13}  # 折扣
_DATE_COLS_1 = {2, 8, 12}


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xml_escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _template_path() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", _TEMPLATE_NAME)
    if os.path.exists(path):
        return path
    raise FileNotFoundError(f"purchase template missing: {_TEMPLATE_NAME}")


def _supplier_code(history: Dict[str, Any], mappings: Dict[str, Any]) -> str:
    """供应商码:mappings['suppliers'] 命中(按 client_id / 卖方税号)优先,无则回退卖方税号。"""
    fields = history.get("fields") if isinstance(history.get("fields"), dict) else {}
    seller_tax = clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id"))
    cid = str(history.get("client_id") or "")
    for row in (mappings or {}).get("suppliers") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("erp_code") or "").strip()
        if not code:
            continue
        if seller_tax and clean_tax_id(row.get("seller_tax") or row.get("tax_id")) == seller_tax:
            return code
        if cid and str(row.get("client_id") or "") == cid:
            return code
    # 回退链与 autocreate._seller_from_history 严格同源(税号 → 名字派生 → V+cid),
    # 否则自建供应商的码在这儿解析不到 → ERR_NO_SUPPLIER(真机语料 SISTER MAKEUP)。
    name_code = supplier_code_from_name(fields.get("seller_name") or fields.get("supplier_name"))
    code = seller_tax or name_code or (("V" + cid) if cid else "")
    # 卖方完全无身份(现金采购小票常态)→ 现金供应商兜底(与销项现金客户对称 · เงินสด),
    # 由 provision_suppliers 幂等自建。可经 mappings['_mrerp_cash_supplier_fallback']=False 关闭。
    if (
        not code
        and isinstance(mappings, dict)
        and mappings.get("_mrerp_cash_supplier_fallback", True)
    ):
        return MRERP_CASH_CUSTOMER
    return code


def is_cash_purchase(history: Dict[str, Any]) -> bool:
    """现购：采购票已付（入口选现金 / ERP 采购单已付都写成 ``posting_payment_manual=cash``，同 Express 判已付的漏斗）。"""
    from pearnly_core.express_rules.push.payment import payment_is_paid

    fields = history.get("fields") if isinstance(history.get("fields"), dict) else history
    return payment_is_paid(fields) is True


def posting_supplier(history: Dict[str, Any], mappings: Dict[str, Any]) -> str:
    """第 9 列「รหัสผู้จำหน่าย」= 记账用的供应商：现购是准备时定好的现金供应商（`defaults.cash_supplier`，MR.ERP 记账时应付
    那笔贷方取它的科目），赊购是真实卖家。第 10 列「(บิล)」= 开票用的供应商（进项税挂它）始终是真实卖家。"""
    if is_cash_purchase(history):
        return str((mappings or {}).get("_mrerp_cash_supplier") or "")
    return _supplier_code(history, mappings)


def _detail_rows(history: Dict[str, Any], mappings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """明细行:商品码经 lookup 解析,量/单价/金额来自 items(无 items → 单行按总额兜底)。"""
    lookup = _build_product_lookup(mappings)
    items = history.get("items")
    if not isinstance(items, list):
        items = (
            (history.get("fields") or {}).get("items")
            if isinstance(history.get("fields"), dict)
            else None
        )
    rows: List[Dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or it.get("description") or "").strip()
        qty = fmt_number(it.get("qty") or it.get("quantity")) or 1
        price = fmt_number(it.get("unit_price") or it.get("price")) or 0
        amount = line_amount(it)
        if amount is None:
            amount = round((qty or 0) * (price or 0), 2)
        rows.append(
            {
                "code": _resolve_product_code(name, lookup) or "",
                "qty": qty,
                "price": price,
                "amount": amount,
            }
        )
    if not rows:
        # 无明细行兜底:金额须是税基(表头税率 7 แยก=价外)。subtotal 缺(真实流只在
        # fields)→ 从含税总额剥 7% 反推,与 sales_credit 兜底同口径,不把含税额当税基。
        sub = history_number(history, "subtotal", "amount_before_tax")
        if sub is None:
            tot = history_number(history, "total_amount")
            sub = round(tot / 1.07, 2) if tot else 0
        rows.append({"code": "", "qty": 1, "price": sub, "amount": sub})
    return rows


def _gross_total(history: Dict[str, Any]) -> float:
    """含税总额:票面 total_amount 优先,缺则 subtotal+vat(费用档单行金额口径)。"""
    total = history_number(history, "total_amount")
    if total is None:
        sub = history_number(history, "subtotal") or 0
        total = round(sub + (history_number(history, "vat") or 0), 2)
    return total or 0


# 费用档(453)税率 = 这家公司税率表里「不进税报告」那一档（初始库名 นอกระบบ：0% · 不进 VAT 报表，`fmt.vat_rates`）
# 实测 นอกระบบ+单行含税总额落库成功(PNPXN-11B2)
def expense_label(mappings: Dict[str, Any]) -> str:
    return expense_cfg(mappings, "_mrerp_expense_vat_label", vat_label(OUTSIDE, mappings))
# 通用费用物料码 · 单一事实源:autocreate 建档端 import 同一常量/读取器,
# 两端必须字节级一致,否则「建了码 X、明细写码 Y」→ ERP 因物料不存在拒收。
EXPENSE_ITEM_CODE_DEFAULT = "EXPENSE"


def expense_cfg(mappings: Dict[str, Any], key: str, default: str) -> str:
    """费用档覆盖键读取(key 传全名,如 _mrerp_expense_item_code)· 空/缺回默认。"""
    if isinstance(mappings, dict):
        v = str(mappings.get(key) or "").strip()
        if v:
            return v
    return default


def _expense_item_code(mappings: Dict[str, Any]) -> str:
    return expense_cfg(mappings, "_mrerp_expense_item_code", EXPENSE_ITEM_CODE_DEFAULT)


def _expense_note(history: Dict[str, Any]) -> str:
    """表头备注 1(人读线索):items 名 join,无 items 回退卖方名 · ≤60 字符。"""
    fields = history.get("fields") if isinstance(history.get("fields"), dict) else {}
    items = history.get("items")
    if not isinstance(items, list):
        items = fields.get("items") if isinstance(fields, dict) else None
    names = [
        str(it.get("name") or it.get("description") or "").strip()
        for it in items or []
        if isinstance(it, dict)
    ]
    note = " / ".join(n for n in names if n)
    if not note:
        note = str(history.get("seller_name") or fields.get("seller_name") or "").strip()
    return note[:60]


def validate_purchase_history(history: Dict[str, Any], mappings: Dict[str, Any]):
    """采购 preflight(最小 · 非 sales_credit 口径):日期 + 正金额 + 供应商码可解析。
    返回 (ok, err_code, warnings)。供应商缺失由 provision_suppliers 前置补,故这里只兜底。"""
    ok, err, warnings = validate_expense_history(history, mappings)
    if not ok:
        return ok, err, warnings
    # VAT 税率合理性(对抗票 06 · 与销项同口径):进项票隐含税率非 ≈7% → 转人工。
    if _gen.vat_rate_anomaly(history):
        return False, "ERR_VAT_RATE_ANOMALY", []
    return True, None, []


def validate_expense_history(history: Dict[str, Any], mappings: Dict[str, Any]):
    """费用采购(453)preflight:日期 + 含税总额>0 + 供应商码可解析。

    不跑 vat_rate_anomaly——นอกระบบ 不抵扣,票面税率异常无会计后果(2026-07-09 拍板)。"""
    if not history:
        return False, "ERR_NO_HISTORY", []
    if not history.get("invoice_date"):
        return False, "ERR_NO_INVOICE_DATE", []
    if _gross_total(history) <= 0:
        return False, "ERR_NO_TOTAL_AMOUNT", []
    from pearnly_core.mrerp.push.xlsx.fmt import MRERP_BILL_NO_MAX

    if len(mrerp_bill_no(history, _gen.derive_mrerp_invoice_no(history), mappings)) > MRERP_BILL_NO_MAX:
        return False, "ERR_BILL_NO_TOO_LONG", []  # MR.ERP 拒收超过 20 字的税票号；不截（截了进项税报告里号就错了）
    if not _supplier_code(history, mappings):
        return False, "ERR_NO_SUPPLIER", []
    if not posting_supplier(history, mappings):
        # 现购没定下记账用的现金供应商：不写（不把现购记成挂账）；`transport._cash_supplier` 已按原因挡下，这里兜底
        from pearnly_core.mrerp.push.defaults import CASH_SUPPLIER_NOT_CREATED

        return False, CASH_SUPPLIER_NOT_CREATED, []
    return True, None, []


def generate_xlsx_purchase(
    histories: List[Dict[str, Any]], mappings: Dict[str, Any], *, expense: bool = False
) -> bytes:
    """克隆官方模板生成 purchase xlsx(AP + 采购明细 · 押金/收货页留空)。

    expense=True = 费用档(453)形态:税率 นอกระบบ · 单行(通用费用物料 · 含税总额)· 备注 1。"""
    with open(_template_path(), "rb") as f:
        template_bytes = f.read()
    files: Dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(template_bytes), "r") as zf:
        for name in zf.namelist():
            files[name] = zf.read(name)

    shared: "OrderedDict[str, int]" = OrderedDict()

    def sidx(text: Any) -> int:
        text = "" if text is None else str(text)
        if text not in shared:
            shared[text] = len(shared)
        return shared[text]

    def _str_cell(col: int, row: int, val: Any, style: int) -> str:
        return f'<c r="{_col_letter(col)}{row}" s="{style}" t="s"><v>{sidx(val)}</v></c>'

    def _num_cell(col: int, row: int, val: Any) -> str:
        return f'<c r="{_col_letter(col)}{row}" s="{_S_NUM}"><v>{_format_num(val)}</v></c>'

    def _header_row(headers: List[str], span: int) -> str:
        cells = "".join(_str_cell(i, 1, h, _S_HEADER) for i, h in enumerate(headers, 1))
        return f'<row r="1" spans="1:{span}" x14ac:dyDescent="0.2">{cells}</row>'

    # ── Sheet1 header ──────────────────────────────────────────────
    # 货品单的表头折扣与明细行同赊销、现销一个做法（`fmt.allocate_discount`）；费用档固定单行含税全额
    documents = [(0, None) if expense else allocate_discount(history, _detail_rows(history, mappings),
                                                            mrerp_tax_kind(history, purchase=True))
                 for history in histories]
    rows1 = [_header_row(_HEADERS_1, 16)]
    head = account_values.header(mappings)
    for ridx, history in enumerate(histories, start=2):
        inv = _gen.derive_mrerp_invoice_no(history)
        date = fmt_date(history.get("invoice_date")) or ""
        supplier = _supplier_code(history, mappings)
        bill_no = mrerp_bill_no(history, inv, mappings)  # 供应商税票号原样（进项税报告「ใบกำกับภาษี เลขที่」）；超长预检拦下
        col_vals = {
            1: inv,
            2: date,
            3: expense_label(mappings) if expense else mrerp_tax_label(history, mappings, purchase=True),
            4: head["branch"],
            5: head["dept"],
            6: head["job"],
            7: head["employee"],
            8: date,
            9: posting_supplier(history, mappings),  # 记账用（现购 = 现金供应商）
            10: supplier,  # 开票用：真实卖家，进项税挂它
            11: bill_no,
            12: date,
            13: documents[ridx - 2][0],  # 表头折扣（`fmt.allocate_discount`；费用档单行含税全额，没有折扣）
        }
        if expense:
            col_vals[14] = _expense_note(history)  # หมายเหตุ 1 · 人读线索
        cells = []
        for col in range(1, max(col_vals) + 1):
            if col in _NUM_COLS_1:
                cells.append(_num_cell(col, ridx, col_vals[col]))
            else:
                style = _S_DATE if col in _DATE_COLS_1 else _S_TEXT
                cells.append(_str_cell(col, ridx, col_vals[col], style))
        rows1.append(f'<row r="{ridx}" spans="1:16" x14ac:dyDescent="0.2">{"".join(cells)}</row>')
    _replace_sheet(files, "xl/worksheets/sheet1.xml", rows1, f"A1:P{1 + len(histories)}")

    # ── Sheet2 items ───────────────────────────────────────────────
    rows2 = [_header_row(_HEADERS_2, 8)]
    cur = 2
    for history, (_discount, allocated) in zip(histories, documents):
        inv = _gen.derive_mrerp_invoice_no(history)
        if expense:
            # 费用档单行:通用费用物料 × 含税总额(นอกระบบ 税率下服务器不再拆税)
            gross = _gross_total(history)
            details = [
                {"code": _expense_item_code(mappings), "qty": 1, "price": gross, "amount": gross}
            ]
        else:
            details = allocated
        for d in details:
            cells = [
                _str_cell(1, cur, inv, _S_TEXT),
                _str_cell(2, cur, d["code"], _S_TEXT),
                _str_cell(3, cur, head["dept"], _S_TEXT),
                _str_cell(4, cur, head["job"], _S_TEXT),
                _str_cell(5, cur, head["store"], _S_TEXT),
                _num_cell(6, cur, d["qty"]),
                _num_cell(7, cur, d["price"]),
                _num_cell(8, cur, d["amount"]),
            ]
            rows2.append(f'<row r="{cur}" spans="1:8" x14ac:dyDescent="0.2">{"".join(cells)}</row>')
            cur += 1
    _replace_sheet(files, "xl/worksheets/sheet2.xml", rows2, f"A1:H{max(cur - 1, 1)}")

    # ── Sheet3 tail · 仅表头(无押金/不开收货单 · 库存 S4 另做)──
    _replace_sheet(files, "xl/worksheets/sheet3.xml", [_header_row(_HEADERS_3, 3)], "A1:C1")

    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n',
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared)}" uniqueCount="{len(shared)}">',
    ]
    for text in shared:
        parts.append(f'<si><t xml:space="preserve">{_xml_escape(text)}</t></si>')
    parts.append("</sst>")
    files["xl/sharedStrings.xml"] = "".join(parts).encode("utf-8")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def _replace_sheet(files: Dict[str, bytes], name: str, rows_xml: List[str], dim: str) -> None:
    xml = files[name].decode("utf-8")
    xml = re.sub(
        r"<sheetData>.*?</sheetData>",
        "<sheetData>" + "".join(rows_xml) + "</sheetData>",
        xml,
        flags=re.DOTALL,
    )
    xml = re.sub(r'<dimension ref="[^"]+"', f'<dimension ref="{dim}"', xml)
    files[name] = xml.encode("utf-8")
