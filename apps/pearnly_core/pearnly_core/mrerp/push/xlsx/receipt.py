"""MR.ERP 收款导入表（impfarev · รับชำระหนี้，ERP 收款服务端直写 2026-11-17）：纯函数，只用标准库。

三张表（`impfarev/component/importpc.php`、`formrdpc.php`，11-16 测试账套真写确认）：

- Sheet1 一行一张收款单，20 列：单号（≤ 18 字，MR.ERP 存成「菜单前缀 + 单号」）、日期、分店（5 位）、部门、งาน、销售员（员工
  「名 姓」，MR.ERP 去掉空格比 CONCAT(名, 姓)）、客户码；现金 / 零用金 / 其他 1 / 其他 2 / 其他 3 各一对「科目 + 金额」（科目都必填、
  要在科目表里，金额可 0）；备注 1–3。**第 20 列必须有值**：PHPExcel 按「最后有数据的列」数列数，不到 20 列整表拒；
- Sheet2 冲哪几张应收：单号、应收单号（带前缀，赊销 / 期初 / 增减债都按单号认）、本次金额（属于这位客户、不超过未收余额）；
- Sheet3 支票：只写表头 5 列（支票不做）。

平衡（MR.ERP 判「รับสุทธิ = รวมรับชำระ」）：现金 + 零用金 = 核销合计 + 其他 1 + 其他 2 + 其他 3；其他三格可负——客户少付的折扣、
扣下的预扣税记负数（例：发票 100、收到 95、折扣 5 → 其他 2 = -5）。

照现有生成器的模板克隆写法拼 XML（同 `purchase.generate_xlsx_purchase`，借用它那份 3-sheet 模板的样式与相对路径关系文件），
不用 openpyxl：MR.ERP 的旧 PHPExcel 读不出 openpyxl 写的绝对路径关系文件。
"""
from __future__ import annotations

import io
import zipfile
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from pearnly_core.mrerp.push.xlsx.purchase import _col_letter, _replace_sheet, _template_path, _xml_escape
from pearnly_core.mrerp.push.xlsx.sales_credit import _format_num

HEADERS_1 = ["เลขที่", "วันที่", "สาขา", "แผนก", "งาน", "พนักงานขาย", "รหัสลูกค้า", "เงินสด", "จำนวนเงินสด",
             "เงินสดย่อย", "จำนวนเงินสดย่อย", "รับอื่นๆ 1", "จำนวนเงินอื่นๆ 1", "รับอื่นๆ 2", "จำนวนเงินอื่นๆ 2",
             "รับอื่นๆ 3", "จำนวนเงินอื่นๆ 3", "หมายเหตุ 1", "หมายเหตุ 2", "หมายเหตุ 3"]
HEADERS_2 = ["เลขที่", "เลขที่บิล", "รับชำระ"]
HEADERS_3 = ["เลขที่", "เลขที่เช็ค", "เช็คลงวันที่", "ธนาคาร", "จำนวนเงิน"]
COLUMNS = len(HEADERS_1)  # 20
NUMBER_MAX, BILL_MAX, NOTE_MAX, AMOUNT_MAX = 18, 20, 50, Decimal("999999999.99")
CENT = Decimal("0.01")
# 五对「科目 + 金额」（Sheet1 第 8–17 列）
SLOTS = ("cash", "petty", "other1", "other2", "other3")
_NUM_COLS_1 = {9, 11, 13, 15, 17}
_S_HEADER, _S_TEXT, _S_DATE, _S_NUM = 13, 7, 14, 9  # 同采购模板的 cellXfs

# 导入表规则不过（生成前就拦，不上传）
NUMBER_INVALID = "ERR_MRERP_RECEIPT_NUMBER"      # 单号空或超过 18 字
BILLS_EMPTY = "ERR_MRERP_RECEIPT_BILLS"          # 没有冲任何应收 / 应收单号空或超过 20 字
AMOUNT_INVALID = "ERR_MRERP_RECEIPT_AMOUNT"      # 金额不是两位小数、负数（其他 3 可负）或超上限
UNBALANCED = "ERR_MRERP_RECEIPT_UNBALANCED"     # 现金 + 零用金 ≠ 核销合计 + 其他 1–3
FIELD_MISSING = "ERR_MRERP_RECEIPT_FIELD"        # 表头必填项（日期、分店、部门、งาน、销售员、客户、五个科目、第 20 列）空


def money(value, *, negative=False) -> Decimal:
    """两位小数 Decimal；不是数、超过两位小数、超上限、不许负时为负 → :data:`AMOUNT_INVALID`（抛 ValueError）。"""
    try:
        number = Decimal(str(value if value is not None else "0").replace(",", "").strip() or "0")
    except InvalidOperation:
        raise ValueError(AMOUNT_INVALID) from None
    if not number.is_finite() or number != number.quantize(CENT) or abs(number) > AMOUNT_MAX or \
            (number < 0 and not negative):
        raise ValueError(AMOUNT_INVALID)
    return number.quantize(CENT)


def check(receipt: Dict[str, Any]) -> str:
    """收款单（:func:`row` 读的形状）过不过导入表规则：过了回空串，否则回原因码。"""
    number = str(receipt.get("number") or "").strip()
    if not number or len(number) > NUMBER_MAX:
        return NUMBER_INVALID
    bills = receipt.get("bills") or []
    if not bills or any(not str(b.get("docnum") or "").strip() or len(str(b["docnum"]).strip()) > BILL_MAX
                        for b in bills):
        return BILLS_EMPTY
    try:
        amounts = {slot: money(receipt.get(slot), negative=slot.startswith("other")) for slot in SLOTS}
        billed = sum((money(b.get("amount")) for b in bills), Decimal("0.00"))
        if any(money(b.get("amount")) <= 0 for b in bills):
            return AMOUNT_INVALID
    except ValueError as error:
        return str(error)
    values = row(receipt)
    if any(not str(values[i] or "").strip() for i in (2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, COLUMNS)):
        return FIELD_MISSING
    if amounts["cash"] + amounts["petty"] != billed + amounts["other1"] + amounts["other2"] + amounts["other3"]:
        return UNBALANCED
    return ""


def row(receipt: Dict[str, Any]) -> Dict[int, Any]:
    """Sheet1 那一行：{列号: 值}（1–20）。``receipt``::

        {number, date, branch, dept, job, employee, customer,
         cash_account, cash, petty_account, petty, other1_account, other1, other2_account, other2, other3_account, other3,
         notes: [备注1, 备注2, 备注3], bills: [{docnum, amount}]}
    """
    notes = list(receipt.get("notes") or [])[:3]
    notes += [""] * (3 - len(notes))
    out = {1: str(receipt.get("number") or "").strip(), 2: str(receipt.get("date") or "")[:10],
           3: receipt.get("branch") or "", 4: receipt.get("dept") or "", 5: receipt.get("job") or "",
           6: receipt.get("employee") or "", 7: receipt.get("customer") or ""}
    for index, slot in enumerate(SLOTS):
        out[8 + index * 2] = receipt.get(f"{slot}_account") or ""
        out[9 + index * 2] = receipt.get(slot) or "0"
    for index, note in enumerate(notes):
        out[18 + index] = str(note or "")[:NOTE_MAX]
    return out


def generate(receipt: Dict[str, Any]) -> bytes:
    """一张收款单的导入表（Sheet1 一行、Sheet2 每张应收一行、Sheet3 只表头）。调用方先过 :func:`check`。"""
    with open(_template_path(), "rb") as handle:
        template = handle.read()
    files: Dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(template), "r") as zf:
        for name in zf.namelist():
            files[name] = zf.read(name)
    shared: "OrderedDict[str, int]" = OrderedDict()

    def sidx(text: Any) -> int:
        text = "" if text is None else str(text)
        if text not in shared:
            shared[text] = len(shared)
        return shared[text]

    def text_cell(col: int, line: int, value: Any, style: int = _S_TEXT) -> str:
        return f'<c r="{_col_letter(col)}{line}" s="{style}" t="s"><v>{sidx(value)}</v></c>'

    def number_cell(col: int, line: int, value: Any) -> str:
        return f'<c r="{_col_letter(col)}{line}" s="{_S_NUM}"><v>{_format_num(value)}</v></c>'

    def header(headers: List[str]) -> str:
        cells = "".join(text_cell(i, 1, h, _S_HEADER) for i, h in enumerate(headers, 1))
        return f'<row r="1" spans="1:{len(headers)}" x14ac:dyDescent="0.2">{cells}</row>'

    values = row(receipt)
    cells = [number_cell(col, 2, values[col]) if col in _NUM_COLS_1 else
             text_cell(col, 2, values[col], _S_DATE if col == 2 else _S_TEXT) for col in range(1, COLUMNS + 1)]
    sheet1 = [header(HEADERS_1), f'<row r="2" spans="1:{COLUMNS}" x14ac:dyDescent="0.2">{"".join(cells)}</row>']
    _replace_sheet(files, "xl/worksheets/sheet1.xml", sheet1, f"A1:{_col_letter(COLUMNS)}2")

    sheet2 = [header(HEADERS_2)]
    for line, bill in enumerate(receipt.get("bills") or [], start=2):
        sheet2.append(f'<row r="{line}" spans="1:3" x14ac:dyDescent="0.2">'
                      f'{text_cell(1, line, values[1])}{text_cell(2, line, str(bill["docnum"]).strip())}'
                      f'{number_cell(3, line, bill["amount"])}</row>')
    _replace_sheet(files, "xl/worksheets/sheet2.xml", sheet2, f"A1:C{len(sheet2)}")
    _replace_sheet(files, "xl/worksheets/sheet3.xml", [header(HEADERS_3)], "A1:E1")
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n',
             f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared)}" '
             f'uniqueCount="{len(shared)}">']
    parts.extend(f'<si><t xml:space="preserve">{_xml_escape(text)}</t></si>' for text in shared)
    parts.append("</sst>")
    files["xl/sharedStrings.xml"] = "".join(parts).encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def employee_name(options: Any, wanted: Any) -> str:
    """收款单上选的销售员 → MR.ERP 员工「名 姓」：按员工代码一字不差，或名字去掉全部空格后相同（MR.ERP 导入同样去空格比）；
    对不上回空串（调用方用账套默认员工，不拦、不新造员工）。``options``：主档员工 ``[{code, name}]``。"""
    want = str(wanted or "").strip()
    if not want:
        return ""
    rows = [o for o in options or [] if isinstance(o, dict) and o.get("name")]
    hit = next((o for o in rows if str(o.get("code") or "").strip() == want), None)
    if hit is None:
        flat = "".join(want.split())
        hit = next((o for o in rows if "".join(str(o["name"]).split()) == flat), None)
    return str(hit["name"]) if hit else ""


__all__ = ["AMOUNT_INVALID", "BILLS_EMPTY", "COLUMNS", "FIELD_MISSING", "HEADERS_1", "HEADERS_2", "HEADERS_3",
           "NUMBER_INVALID", "SLOTS", "UNBALANCED", "check", "employee_name", "generate", "money", "row"]
