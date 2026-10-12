"""POS 导出文件的字节 → 表头与数据行：只在内存里读（.xlsx / .xlsm 用 openpyxl，.csv / .tsv 嗅探编码与分隔符）。

旧版 Excel（.xls）不读：镜像里不带 xlrd，提示另存为 .xlsx 或 .csv。一份文件有几张表时取表头认得最全的那张。
"""
from __future__ import annotations

from pearnly_cowork.pos_import.contracts import PosError, cell_text, header_hits, header_row
from pearnly_cowork.tabular.readers import load_csv_sheets, load_workbook_sheets

WORKBOOKS = (".xlsx", ".xlsm")
DELIMITED = (".csv", ".tsv")
EXTENSIONS = WORKBOOKS + DELIMITED
LEGACY = ".xls"
MAX_CELLS = 500_000


def kind_of(filename) -> str:
    """``workbook`` / ``delimited`` / ``legacy``（.xls）/ ``""``（不是 POS 导出能用的类型）。"""
    name = str(filename or "").lower()
    if name.endswith(WORKBOOKS):
        return "workbook"
    if name.endswith(DELIMITED):
        return "delimited"
    if name.endswith(LEGACY):
        return "legacy"
    return ""


def read(content: bytes, filename: str) -> tuple:
    """→ ``(表头, 数据行, 第一行数据在文件里的行号)``；读不了抛 `PosError`。"""
    kind = kind_of(filename)
    if kind == "legacy":
        raise PosError("pos.xls_not_supported")
    if kind == "workbook":
        sheets = load_workbook_sheets(content, max_cells=MAX_CELLS)
    elif kind == "delimited":
        sheets = load_csv_sheets(content, max_cells=MAX_CELLS)
    else:
        raise PosError("pos.type_unsupported")
    if not sheets:
        raise PosError("pos.file_unreadable")
    best = None
    for _title, rows in sheets:
        try:
            index = header_row(rows)
        except PosError:
            continue
        hits = header_hits(rows[index])
        if best is None or hits > best[0]:
            best = (hits, rows, index)
    if best is None:
        raise PosError("pos.no_header")
    _hits, rows, index = best
    return [cell_text(cell) for cell in rows[index]], rows[index + 1:], index + 2


__all__ = ["EXTENSIONS", "kind_of", "read"]
