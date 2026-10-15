"""上传盘点表：表头识别与逐行校验（纯逻辑，不连库、不读文件、不依赖框架）。

用户 2026-09-26 定：Cowork 不做在线盘点，只做**上传盘点**——一张表同时带账面与实盘，
错行跳过并逐条列出原因。列照 Cowork 旧模板（编码 / 名称 / 条码 / 单位 / 账面 / 仓库 / 仓位），
只多一列**实盘数量**，与 ERP「上传导入」同一口径。2026-10-15 再加一列可选的**商品类别**：
多人分区盘点时按「仓库 / 类别」分工（用户："a 在 a 仓库盘某类商品，b 在某仓库盘某类商品"）。

与旧版 `contracts.validate_import_rows`（整批拒收）的差别只在错行处理：这里每行各自判定，
坏行进 `problems`，其余照收；单行规则（必填、长度、编码按文本、数量精度、重复、码冲突）不变。
"""

from pearnly_cowork.stocktake.contracts import MAX_ROWS, StocktakeValidationError, quantity

FIELDS = ("product_code", "product_name", "barcode", "unit", "book_qty", "actual_qty", "warehouse", "location",
          "category")
REQUIRED = ("product_code", "product_name", "unit", "book_qty")
# 模板表头取这几句（按站点语言，词条在 Cowork 术语表）。
FIELD_LABELS = tuple((field, "cowork.stocktake.col." + field) for field in FIELDS)

# 旧模板、旧导出件与 ERP 盘点表的写法照认（比对前去空白、转小写）。
ALIASES = {
    "product_code": "product_code", "code": "product_code", "รหัสสินค้า": "product_code",
    "商品编号": "product_code", "编码": "product_code", "商品コード": "product_code",
    "product_name": "product_name", "name": "product_name", "ชื่อสินค้า": "product_name",
    "商品名称": "product_name", "名称": "product_name", "商品名": "product_name",
    "barcode": "barcode", "บาร์โค้ด": "barcode", "บาร์โค้ด / รหัส qr": "barcode",
    "barcode / qr code": "barcode", "条码": "barcode", "条码/二维码": "barcode", "バーコード": "barcode",
    "unit": "unit", "หน่วย": "unit", "单位": "unit", "単位": "unit",
    "book_qty": "book_qty", "book quantity": "book_qty", "จำนวนตามบัญชี": "book_qty",
    "账面数量": "book_qty", "帳簿数量": "book_qty",
    "actual_qty": "actual_qty", "counted_qty": "actual_qty", "counted quantity": "actual_qty",
    "จำนวนที่นับได้": "actual_qty", "实盘数量": "actual_qty", "実地数量": "actual_qty",
    "warehouse": "warehouse", "คลังสินค้า": "warehouse", "仓库": "warehouse", "倉庫": "warehouse",
    "คลังสินค้าเริ่มต้น (ไม่บังคับ)": "warehouse", "warehouse (optional)": "warehouse",
    "location": "location", "ช่องเก็บ": "location", "ตำแหน่ง": "location", "仓位": "location",
    "库位": "location", "棚番": "location", "ตำแหน่งเริ่มต้น (ไม่บังคับ)": "location",
    "location (optional)": "location",
    "category": "category", "หมวดสินค้า": "category", "หมวดหมู่": "category", "หมวดหมู่สินค้า": "category",
    "类别": "category", "分类": "category", "商品类别": "category", "商品分类": "category", "カテゴリ": "category",
}


def norm(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def columns(header, labels=None) -> dict:
    """表头 → {列序号: 字段}。认不出必填列就整张表不收（不是某一行的问题）。"""
    aliases = {norm(word): field for word, field in ALIASES.items()}
    aliases.update({norm(text): field for text, field in (labels or {}).items() if norm(text)})
    mapping = {}
    for index, value in enumerate(header or ()):
        field = aliases.get(norm(value))
        if field and field not in mapping.values():
            mapping[index] = field
    if any(field not in mapping.values() for field in REQUIRED):
        raise StocktakeValidationError("stocktake.headers_invalid")
    return mapping


def rows(header, cells, labels=None):
    """(header, [[(value, data_type), ...], ...]) → (items, problems)。

    `data_type` 用读表器的记号：'s' 文本、'n' 数字、'f' 公式。行号从 2 起（第 1 行是表头）。
    """
    mapping = columns(header, labels)
    items, problems, seen, identifiers = [], [], set(), {}
    for number, row in enumerate(cells, 2):
        if number > MAX_ROWS + 1:
            raise StocktakeValidationError("stocktake.too_many_rows")
        values = {field: (row[index] if index < len(row) else (None, "n")) for index, field in mapping.items()}
        if all(value is None or str(value).strip() == "" for value, _kind in values.values()):
            continue
        reason = _problem(values)
        item = None
        if not reason:
            item = {field: str(values.get(field, (None, ""))[0] or "").strip() for field in FIELDS}
            key = item["product_code"]
            if key in seen:
                reason = "stocktake.duplicate"
            elif any(identifiers.get(code, key) != key for code in {key, item["barcode"]} - {""}):
                reason = "stocktake.ambiguous_code"
        if reason:
            problems.append({"row": number, "code": (values.get("product_code") or (None,))[0], "reason": reason})
            continue
        seen.add(item["product_code"])
        for code in {item["product_code"], item["barcode"]} - {""}:
            identifiers[code] = item["product_code"]
        item["book_qty"] = format(quantity(item["book_qty"]), ".6f")
        item["actual_qty"] = (format(quantity(item["actual_qty"], nonnegative=True), ".6f")
                              if item["actual_qty"] != "" else None)
        items.append(item)
    if not items:
        raise StocktakeValidationError("stocktake.empty")
    return items, problems


def _problem(values):
    if any(kind == "f" for _value, kind in values.values()):
        return "stocktake.row_invalid"
    text = {field: str(value if value is not None else "").strip() for field, (value, _kind) in values.items()}
    if any(not text.get(field) for field in ("product_code", "product_name", "unit")):
        return "stocktake.row_invalid"
    if any(len(value) > 300 for value in text.values()):
        return "stocktake.row_invalid"
    # 编码 / 条码必须按文本存（旧版同一规则）：数字格会把前导 0 吃掉，认错商品。
    for field in ("product_code", "barcode"):
        value, kind = values.get(field, (None, "s"))
        if value is not None and str(value).strip() and kind != "s":
            return "stocktake.code_as_text"
    try:
        quantity(text.get("book_qty", ""))
        if text.get("actual_qty"):
            quantity(text["actual_qty"], nonnegative=True)
    except StocktakeValidationError:
        return "stocktake.quantity_invalid"
    return None
