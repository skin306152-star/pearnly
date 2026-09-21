"""Excel 商品导入的解析层（只读表格，不连库、不鉴权）。

为什么单独一层：解析错了会把整批脏数据写进主数据，而"表头认不出""金额不是数字""少了商品名"
这些**都可以在没有 Site 的地方断言**。入库由 `service.import_products` 逐行调用同一个
`create_product`，所以导入与手工录入走的是同一条校验路径，不会有两套规则。

表头按**别名**认（泰文/中文/英文都收）：客户手里那张表是旧的，改表头不现实。
字段名与旧线上 `services/sales/products.py` 的列一致 —— 一个不多一个不少。
"""

from __future__ import annotations

import io
from decimal import Decimal, InvalidOperation

# 表头别名（去空白、转小写后比对）→ 我们的字段名。
HEADER_ALIASES = {
    "product_name": "product_name",
    "name": "product_name",
    "name_th": "product_name",
    "ชื่อสินค้า": "product_name",
    "ชื่อ": "product_name",
    "商品名称": "product_name",
    "名称": "product_name",
    "name_en": "name_en",
    "english": "name_en",
    "英文名": "name_en",
    "name_zh": "name_zh",
    "中文名": "name_zh",
    "code": "code",
    "รหัส": "code",
    "รหัสสินค้า": "code",
    "编码": "code",
    "货号": "code",
    "barcode": "barcode",
    "บาร์โค้ด": "barcode",
    "条码": "barcode",
    "qr_payload": "qr_payload",
    "qr": "qr_payload",
    "二维码": "qr_payload",
    "unit": "unit",
    "หน่วย": "unit",
    "单位": "unit",
    "base_unit": "base_unit",
    "unit_price": "unit_price",
    "price": "unit_price",
    "ราคา": "unit_price",
    "单价": "unit_price",
    "价格": "unit_price",
    "default_cost": "default_cost",
    "cost": "default_cost",
    "ต้นทุน": "default_cost",
    "成本": "default_cost",
    "min_stock": "min_stock",
    "สต็อกต่ำ": "min_stock",
    "低库存": "min_stock",
    "vat_applicable": "vat_applicable",
    "vat": "vat_applicable",
    "含税": "vat_applicable",
    "category_id": "category_id",
    "分类": "category_id",
    "track_batch": "track_batch",
    "批次": "track_batch",
    "track_expiry": "track_expiry",
    "效期": "track_expiry",
    "is_weighed": "is_weighed",
    "称重": "is_weighed",
    "image_url": "image_url",
    "图片": "image_url",
}

TEXT_FIELDS = ("product_name", "name_en", "name_zh", "code", "barcode", "qr_payload",
               "unit", "base_unit", "image_url")
NUMBER_FIELDS = ("unit_price", "default_cost", "min_stock")
FLAG_FIELDS = ("vat_applicable", "track_batch", "track_expiry", "is_weighed")

_TRUTHY = {"1", "true", "yes", "y", "ใช่", "是"}


def _norm_header(value) -> str:
    return str(value).strip().lower() if value is not None else ""


def _is_blank(row) -> bool:
    return row is None or all(cell is None or str(cell).strip() == "" for cell in row)


def _to_number(value) -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError("not_a_number") from None
    if not number.is_finite() or number < 0:
        raise ValueError("negative_or_invalid")
    return number


def _to_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY


def _record(row, columns: dict) -> dict:
    record: dict = {}
    for index, field in columns.items():
        if index >= len(row):
            continue
        value = row[index]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if field in NUMBER_FIELDS:
            record[field] = _to_number(value)
        elif field in FLAG_FIELDS:
            record[field] = _to_flag(value)
        elif field == "category_id":
            try:
                record[field] = int(value)
            except (TypeError, ValueError):
                raise ValueError("not_a_number") from None
        else:
            record[field] = str(value).strip()
    if not record.get("product_name"):
        raise ValueError("name_required")
    return record


def parse_workbook(data: bytes) -> tuple[list[dict], list[dict]]:
    """xlsx 字节 → (可入库的行, 行级错误)。错误每条 `{row, error}`（行号从 2 起，表头是第 1 行）。

    认不出表头或整张表空时抛 `ValueError`，由调用方翻译成给用户看的提示。
    """
    if not data:
        raise ValueError("empty_file")
    # 延迟导入：本模块要能在**没有 openpyxl** 的宿主上被导入（解析逻辑的断言在
    # bench 环境里跑，见 tests 的跳过守卫）。
    import openpyxl

    try:
        book = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception:
        raise ValueError("not_a_workbook") from None
    try:
        sheet = book.active
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise ValueError("empty_file") from None
        columns = {index: HEADER_ALIASES[_norm_header(cell)]
                   for index, cell in enumerate(header)
                   if _norm_header(cell) in HEADER_ALIASES}
        if "product_name" not in columns.values():
            raise ValueError("missing_name_column")

        valid: list[dict] = []
        errors: list[dict] = []
        for line_no, row in enumerate(rows, start=2):
            if _is_blank(row):
                continue
            try:
                valid.append(_record(row, columns))
            except ValueError as exc:
                errors.append({"row": line_no, "error": str(exc)})
        if not valid and not errors:
            raise ValueError("empty_file")
        return valid, errors
    finally:
        book.close()
