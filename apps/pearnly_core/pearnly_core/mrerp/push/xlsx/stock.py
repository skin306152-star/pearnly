"""MR.ERP 库存入库 / 出库导入表（impstktranrec · รับสินค้า / impstktraniss · จ่ายสินค้า，ERP 库存单服务端直写 2026-11-19）：
纯函数，只用标准库。

两张表（`impstktranrec/component/importpc.php`、`impstktraniss/component/importpc.php`、`formrdpc.php`，11-19 读源码）：

- Sheet1 一行一张单，6 列：单号（≤ 18 字，MR.ERP 存成「菜单前缀 + 单号」、同菜单查重）、日期（公历 YYYY-MM-DD）、分店（正好 5 位）、
  备注 1–3。PHPExcel 按「最后有数据的列」数列数，不到 6 列整表拒 → 备注 3 空时也写一个值（同收款第 20 列）；
- Sheet2 一行一件物料：单号（等于 Sheet1 的单号、按单连续排）、物料码（2–15 字）、แผนก（部门）、งาน（工作代码，必填）、คลัง（仓库码，
  正好 4 位）、数量；**入库**另有单价、金额两列（必填，MR.ERP 不核「数量 × 单价」，单头合计 = 各行金额之和），**出库**没有（成本系统算）。
  入库 8 列、出库 6 列；没有单位列（MR.ERP 记物料的标准单位，调用方按库存单位交）。

MR.ERP 把数量、单价、金额里数字与小数点以外的字符全删掉（`component/php/onlynumber.php`）：负号会被静默去掉、0 也不拦 → 生成前
在这里拦（:data:`QTY_INVALID`）。

模板：借用采购那份 3-sheet 模板的样式与相对路径关系文件（同收款，MR.ERP 的旧 PHPExcel 读不出 openpyxl 写的绝对路径关系文件），
去掉第 3 张表。
"""
from __future__ import annotations

import io
import re
import zipfile
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List

from pearnly_core.mrerp.push.xlsx.purchase import _col_letter, _replace_sheet, _template_path, _xml_escape
from pearnly_core.mrerp.push.xlsx.sales_credit import _format_num

IN, OUT = "in", "out"
HEADERS_1 = ["เลขที่", "วันที่", "สาขาบริษัท", "หมายเหตุ 1", "หมายเหตุ 2", "หมายเหตุ 3"]
HEADERS_2 = {IN: ["เลขที่", "รหัสสินค้า", "แผนก", "งาน", "คลัง", "จำนวน", "ราคา/หน่วย", "จำนวนเงิน"],
             OUT: ["เลขที่", "รหัสสินค้า", "แผนก", "งาน", "คลัง", "จำนวน"]}
NUMBER_MAX, NOTE_MAX, CODE_MIN, CODE_MAX, STORE_LEN, BRANCH_LEN = 18, 50, 2, 15, 4, 5
LIMIT = Decimal("999999999.99")
CENT = Decimal("0.01")
FILLER = "-"  # 备注 3 空时占住第 6 列（MR.ERP 按最后有数据的列数列数）
_S_HEADER, _S_TEXT, _S_DATE, _S_NUM = 13, 7, 14, 9  # 同采购模板的 cellXfs

# 导入表规则不过（生成前就拦，不上传）
NUMBER_INVALID = "ERR_MRERP_STOCK_NUMBER"    # 单号空或超过 18 字
LINES_EMPTY = "ERR_MRERP_STOCK_LINES"        # 没有明细
FIELD_MISSING = "ERR_MRERP_STOCK_FIELD"      # 日期、分店（5 位）、物料码（2–15 字）、部门、งาน、仓库码（4 位）空或不合位数
QTY_INVALID = "ERR_MRERP_STOCK_QTY"          # 数量不是正数（MR.ERP 会吃掉负号）、超上限
PRICE_MISSING = "ERR_MRERP_STOCK_PRICE"      # 入库行没有单价（MR.ERP 入库导入必填）
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _number(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else "").replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(QTY_INVALID) from None


def amount(qty, price) -> Decimal:
    """入库行金额 = 数量 × 单价，四舍五入到分。"""
    return (_number(qty) * _number(price)).quantize(CENT, rounding=ROUND_HALF_UP)


def check(entry: Dict[str, Any]) -> str:
    """一张库存单（:func:`generate` 读的形状）过不过导入表规则：过了回空串，否则回原因码。"""
    number = str(entry.get("number") or "").strip()
    if not number or len(number) > NUMBER_MAX:
        return NUMBER_INVALID
    lines = entry.get("lines") or []
    if not lines:
        return LINES_EMPTY
    if not _DATE.match(str(entry.get("date") or "")[:10]) or len(str(entry.get("branch") or "").strip()) != BRANCH_LEN:
        return FIELD_MISSING
    kind = entry.get("kind")
    for line in lines:
        code = str(line.get("code") or "").strip()
        if not (CODE_MIN <= len(code) <= CODE_MAX) or len(str(line.get("store") or "").strip()) != STORE_LEN or \
                not str(line.get("dept") or "").strip() or not str(line.get("job") or "").strip():
            return FIELD_MISSING
        try:
            qty = _number(line.get("qty"))
        except ValueError:
            return QTY_INVALID
        if not qty.is_finite() or qty <= 0 or qty > LIMIT:
            return QTY_INVALID
        if kind == IN:
            price = str(line.get("price") if line.get("price") is not None else "").strip()
            if not price:
                return PRICE_MISSING
            try:
                value = _number(price)
            except ValueError:
                return QTY_INVALID
            if not value.is_finite() or value < 0 or value > LIMIT or amount(qty, value) > LIMIT:
                return QTY_INVALID
    return ""


def _drop_third_sheet(files: Dict[str, bytes]) -> None:
    """采购模板有 3 张表；库存导入只要 2 张：去掉第 3 张与计算链（它引用各表）。"""
    for name in ("xl/worksheets/sheet3.xml", "xl/calcChain.xml"):
        files.pop(name, None)
    workbook = files["xl/workbook.xml"].decode("utf-8")
    files["xl/workbook.xml"] = re.sub(r'<sheet [^>]*r:id="rId3"\s*/>', "", workbook).encode("utf-8")
    rels = files["xl/_rels/workbook.xml.rels"].decode("utf-8")
    rels = re.sub(r'<Relationship [^>]*Target="(?:worksheets/sheet3\.xml|calcChain\.xml)"\s*/>', "", rels)
    files["xl/_rels/workbook.xml.rels"] = rels.encode("utf-8")
    types = files["[Content_Types].xml"].decode("utf-8")
    types = re.sub(r'<Override [^>]*PartName="/xl/(?:worksheets/sheet3|calcChain)\.xml"[^>]*/>', "", types)
    files["[Content_Types].xml"] = types.encode("utf-8")


def generate(entry: Dict[str, Any]) -> bytes:
    """一张库存单的导入表。``entry``::

        {kind: "in" | "out", number, date, branch, notes: [备注1, 备注2, 备注3],
         lines: [{code, dept, job, store, qty, price（只入库）}]}

    调用方先过 :func:`check`。"""
    kind = entry.get("kind")
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

    number = str(entry.get("number") or "").strip()
    notes = [str(note or "")[:NOTE_MAX] for note in list(entry.get("notes") or [])[:3]]
    notes += [""] * (3 - len(notes))
    notes[2] = notes[2] or FILLER
    head = [number, str(entry.get("date") or "")[:10], str(entry.get("branch") or "").strip(), *notes]
    cells = "".join(text_cell(col, 2, value, _S_DATE if col == 2 else _S_TEXT) for col, value in enumerate(head, 1))
    sheet1 = [header(HEADERS_1), f'<row r="2" spans="1:{len(HEADERS_1)}" x14ac:dyDescent="0.2">{cells}</row>']
    _replace_sheet(files, "xl/worksheets/sheet1.xml", sheet1, f"A1:{_col_letter(len(HEADERS_1))}2")

    headers = HEADERS_2[kind]
    sheet2 = [header(headers)]
    for row, line in enumerate(entry.get("lines") or [], start=2):
        values = [text_cell(1, row, number), text_cell(2, row, str(line["code"]).strip()),
                  text_cell(3, row, str(line["dept"]).strip()), text_cell(4, row, str(line["job"]).strip()),
                  text_cell(5, row, str(line["store"]).strip()), number_cell(6, row, line["qty"])]
        if kind == IN:
            values += [number_cell(7, row, line["price"]), number_cell(8, row, amount(line["qty"], line["price"]))]
        sheet2.append(f'<row r="{row}" spans="1:{len(headers)}" x14ac:dyDescent="0.2">{"".join(values)}</row>')
    _replace_sheet(files, "xl/worksheets/sheet2.xml", sheet2, f"A1:{_col_letter(len(headers))}{len(sheet2)}")
    _drop_third_sheet(files)
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


__all__ = ["FIELD_MISSING", "HEADERS_1", "HEADERS_2", "IN", "LINES_EMPTY", "NUMBER_INVALID", "OUT", "PRICE_MISSING",
           "QTY_INVALID", "amount", "check", "generate"]
