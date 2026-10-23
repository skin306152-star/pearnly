"""商品主数据的服务端实现。

取数一律走 `frappe.get_list`（跟随角色权限），成本按岗位遮蔽后再返回；停用是软删除。
"""

import frappe
from frappe import _

from pearnly_core import browser_text
from pearnly_erp_portal.access import guard, listing, rules
from pearnly_erp_portal.products import contracts, workbook

DOCTYPE = "ERP Product"
UNIT_DOCTYPE = "ERP Product Unit"

NAME_REQUIRED = "A product needs a name"
# 导入的错误提示（用户可见，走语言包）。
IMPORT_NO_FILE = "Choose a file to import"
IMPORT_BAD_FILE = "That file could not be read; please use the template"
IMPORT_NOT_NUMBER = "The value is not a number"
IMPORT_NEGATIVE = "The value must be zero or more"
IMPORT_ERRORS = {
    "empty_file": "That file has no rows",
    "not_a_workbook": "That file is not an Excel workbook",
    "missing_name_column": "The sheet needs a product name column",
}
# 模板文件名用户会看到（下载存盘时）。
TEMPLATE_FILE = "products-import-template.xlsx"
# 逐行校验的 token → 用户看得懂的提示（解析层是纯规则层，不翻文案，只报 token）。
ROW_ERRORS = {
    "not_a_number": IMPORT_NOT_NUMBER,
    "negative_or_invalid": IMPORT_NEGATIVE,
    "name_required": NAME_REQUIRED,
}
CODE_EXISTS = "This product code already exists"
BARCODE_EXISTS = "This barcode already exists"
CODE_LOCKED = "The product code cannot be changed"
NOT_FOUND = "Product not found"
INVALID_VALUE = "Invalid value"

# 列表与详情一起用的字段：与旧线上 `services/sales/products.py::_COLS` 同一份，
# 一个不多一个不少。我们的 `price_set` / `cost_set` 是"设过没有"的标记 ——
# Frappe 的 Currency 列存不下 NULL，旧线上靠可空列区分的"没设价 / 真的 ฿0"，
# 在这里只能靠标记还原。
LIST_FIELDS = (
    "name", "product_name", "code", "barcode", "qr_payload",
    "unit", "base_unit", "category_id", "unit_price", "price_set", "cost_set",
    "vat_applicable", "is_active", "min_stock", "default_cost", "image_url",
    "track_batch", "track_expiry", "default_location", "express_code",
)

# 扫码当场查重查的是这几列（扫码枪扫出来的就是条码/编码/二维码内容）。
LOOKUP_FIELDS = ("code", "barcode", "qr_payload")


def _mask(rows):
    visible = guard.cost_visible()
    records = [rows] if isinstance(rows, dict) else rows
    names = [row.get("name") for row in records if row.get("name")]
    # Only supplement records already admitted by the product lookup above.
    costs = {row.name: row for row in frappe.get_all(DOCTYPE,
        filters={"name": ["in", names]}, fields=["name", "default_cost", "cost_set"])} if visible and names else {}
    def masked(row):
        # Native field permissions omit restricted fields; keep the product API's null contract.
        data = {field: None for field in contracts.COST_FIELDS}
        data.update(row)
        data.update(costs.get(row.get("name")) or {})
        return contracts.mask_costs(data, visible)
    if isinstance(rows, dict):
        return masked(rows)
    return [masked(row) for row in rows]


def _optional(value):
    try:
        return contracts.decimal_or_none(value)
    except ValueError:
        frappe.throw(_(INVALID_VALUE))


def _cost_fields(value) -> dict:
    """参考成本与"是否设过"一起写。与 `_price_fields` 同一个理由：
    Currency 列存不下 NULL，0 既是"真的免费"也是"没设过"，收银/毛利那边要能分辨。"""
    cost = _optional(value)
    return {"default_cost": cost or 0, "cost_set": 1 if cost is not None else 0}


def _price_fields(value) -> dict:
    """价格与"是否设过价"必须一起写。

    Frappe 的 Currency 列不能存 NULL，所以"没设价"和"真的 ฿0"在库里是同一个数字。
    旧线上这点被当成事故处理过（扫码就地建品建出来的商品全部 ฿0 可售），所以这里
    用一个显式标记把两者区分开，收银台的零元闸读它而不是猜数字。
    """
    price = _optional(value)
    return {"unit_price": price or 0, "price_set": 1 if price is not None else 0}


def _text(value, limit):
    try:
        return contracts.clean_text(value, limit)
    except ValueError:
        frappe.throw(_(INVALID_VALUE))


def _flag(value) -> int:
    """勾选项：表单来的 "1"/true/on 与真布尔都认，其余一律 0。"""
    return 1 if value in (True, 1, "1", "true", "True", "on") else 0


def taken(field: str, value, *, owner: str | None = None, exclude: str | None = None) -> str | None:
    """编码 / 条码在「看得到它的那家公司」范围内被哪件商品占着（2026-10-24 起不再全租户唯一）：一个租户挂几家公司，
    各家从自己的 Express 同步商品（`express_company`），同一个国际条码在两家各有一件是常事。``owner`` 是这件商品所属的
    公司（Express 同步来的）：本公司的档 + ERP 本地档里不能重；本地档（``owner`` 空）各家公司都看得到，全租户不能重。
    按库的排序规则比（泰文声调符号、大小写、尾随空格按库认作一样）。"""
    if field not in contracts.UNIQUE_FIELDS or not value:
        return None
    sql = f"select name from `tab{DOCTYPE}` where `{field}` = %s"
    params = [value]
    if exclude:
        sql += " and name != %s"
        params.append(exclude)
    if owner:
        sql += " and ifnull(express_company, '') in ('', %s)"
        params.append(owner)
    found = frappe.db.sql(sql + " limit 1", params)
    return found[0][0] if found else None


def find(field: str, value, company: str | None = None) -> str | None:
    """按编码 / 条码认商品：只认这家公司的 Express 档与本地档（别家公司同码的不认），本公司的先。"""
    if field not in contracts.UNIQUE_FIELDS or not value:
        return None
    if not company:
        return frappe.db.get_value(DOCTYPE, {field: value, "express_company": ["is", "not set"]}, "name")
    found = frappe.db.sql(f"""select name from `tab{DOCTYPE}` where `{field}` = %s
        and ifnull(express_company, '') in ('', %s) order by ifnull(express_company, '') = '' limit 1""", (value, company))
    return found[0][0] if found else None


def _assert_unique(code, barcode, *, exclude: str | None = None, owner: str | None = None) -> None:
    """编码 / 条码在看得到这件商品的范围内唯一（:func:`taken`）。"""
    for field, value, message in (
        ("code", code, CODE_EXISTS),
        ("barcode", barcode, BARCODE_EXISTS),
    ):
        if taken(field, value, owner=owner, exclude=exclude):
            frappe.throw(_(message))


def _get(name: str):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def _attach_image(image_url, product: str):
    """把商品图挂到这条商品上 —— 读权限因此跟着商品走。

    不挂的话它是"私有且无归属"的 File：框架的 `file.has_permission` 对这种文件只认上传者
    本人，别的员工在商品页看到的是裂图（老板传的图，店员看不着）。用 `db.set_value` 而不是
    `file.save()`：后者会走一遍 File 的命名字段逻辑，可能把磁盘上的文件改名，落库的 URL 就
    对不上了。外链（不是本站 URL）不碰。
    """
    url = str(image_url or "")
    if not url.startswith("/"):
        return
    # 同一个 URL 理论上只有一条，但传过失败/重传时可能留下同名多条 —— 取最新那条。
    name = frappe.db.get_value("File", {"file_url": url}, "name", order_by="creation desc")
    if not name:
        return
    current = frappe.db.get_value("File", name, ["attached_to_doctype", "attached_to_name"],
                                  as_dict=True) or {}
    if current.get("attached_to_doctype") == DOCTYPE and current.get("attached_to_name") == product:
        return
    frappe.db.set_value("File", name,
                        {"attached_to_doctype": DOCTYPE, "attached_to_name": product})


def _allocate_code() -> str:
    """发下一个商品编码（旧线上 ERP 门户里编码是只读的，新平台同样不让手输）。

    发号看全租户（本地档各家公司都看得到）；建档前 `_assert_unique` 再核一次。
    """
    codes = frappe.get_all(DOCTYPE, filters={"code": ["like", f"{contracts.CODE_PREFIX}%"]},
                           pluck="code", limit_page_length=0)
    return contracts.next_code(codes)


def next_code_preview() -> dict:
    """下一个商品编码的**只读预览**（不占号）。

    用户 2026-09-22 口径：编码由系统发、不给手输，但填表时要看得见 —— 与入库/出库表单
    显示"当前要发的单据号"同一个道理（旧系统也是把号填进只读输入框）。真正的编码在
    入库那一刻才定，所以这里只是预览、不写任何东西。
    """
    guard.require("products.view")
    return {"code": _allocate_code()}


def create_product(*, product_name, code=None, barcode=None, unit=None, unit_price=None,
                   vat_applicable=True, image_url=None, min_stock=None, default_cost=None,
                   qr_payload=None, base_unit=None,
                   category_id=None, track_batch=None, track_expiry=None,
                   default_location=None):
    guard.require("products.manage")
    try:
        product_name = contracts.require_name(product_name)
    except ValueError:
        frappe.throw(_(NAME_REQUIRED))
    # 编码留空就发一个（导入的表里带码时照收）—— 与旧线上 ERP 门户的 `prepare()` 同一条规则。
    code = _text(code, contracts.MAX_CODE) or _allocate_code()
    barcode = _text(barcode, contracts.MAX_BARCODE)
    _assert_unique(code, barcode)

    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "product_name": product_name,
        "code": code,
        "barcode": barcode,
        "qr_payload": _text(qr_payload, contracts.MAX_QR),
        "unit": _text(unit, contracts.MAX_UNIT),
        **_price_fields(unit_price),
        "vat_applicable": 1 if vat_applicable in (True, 1, "1", "true") else 0,
        "image_url": _text(image_url, contracts.MAX_IMAGE_URL),
        "min_stock": _optional(min_stock),
        **_cost_fields(default_cost),
        # 库存单位留空 = 跟售卖单位一样（旧线上 ERP 建品就是这么补的）。
        "base_unit": _text(base_unit, contracts.MAX_BASE_UNIT) or unit,
        "category_id": int(category_id) if str(category_id or "").strip() else None,
        "track_batch": _flag(track_batch),
        "track_expiry": _flag(track_expiry),
        "is_active": 1,
        "default_location": _text(default_location, 140),
    })
    doc.insert()
    _attach_image(doc.image_url, doc.name)
    frappe.db.commit()
    return {"name": doc.name}


def _filters(search=None, include_inactive=False, is_active=None):
    """列表与计数共用的一份筛选，避免两处各写一遍导致"总数与实际不符"。

    名称、**物料号**、条码、二维码都要能搜到：扫码后就地找品是收银台的主路径
    （官方 POS 那一格的提示就是"按物料号，序列号，批号搜索"）。物料号是我们自动发的
    `P-#####`，很多商品根本没有条码，只按条码搜等于扫自家货架号都找不到。

    这几样必须一起进 `or_filters`：框架把 `filters` 与 `or_filters` 之间按 AND 组合，
    只把条码放进 or 会让"搜名称"变成"名称与条码同时包含"，等于搜不到 —— 实测过。
    """
    filters: list = []
    if is_active is not None and str(is_active) != "":
        # 原生筛选直接给了"上架 / 停用"这一条就照它来（用户 2026-09-23 把自研的那个
        # "显示已停用"勾选换成了官方原生筛选条件）。
        filters.append(["is_active", "=", 1 if int(is_active) else 0])
    elif not include_inactive:
        filters.append(["is_active", "=", 1])
    or_filters = ([[field, "like", f"%{search}%"]
                   for field in ("product_name", "code", "barcode", "qr_payload")]
                  if search else None)
    return filters, or_filters


# 列头排序白名单（前端列名 → 真正能排的库字段）。`order_by` 是拼进 SQL 的，
# 只认这几项；别的字段回落到默认顺序（见 `listing.order_by`）。
SORT_FIELDS = {
    "product_name": "product_name",
    "code": "code",
    "barcode": "barcode",
    "unit": "unit",
    "unit_price": "unit_price",
    "default_cost": "default_cost",
    "place": "default_location",
    "is_active": "is_active",
}


def list_products(*, search=None, include_inactive=False, is_active=None, limit=50, start=0,
                  sort_by=None, sort_order=None):
    guard.require("products.view")
    filters, or_filters = _filters(search, include_inactive, is_active)
    rows = _mask(frappe.get_list(
        DOCTYPE, filters=filters, or_filters=or_filters, fields=list(LIST_FIELDS),
        order_by=listing.order_by(sort_by, sort_order, SORT_FIELDS, "modified desc"),
        limit_start=int(start), limit_page_length=int(limit),
    ))
    return rows


def find_product(value, exclude=None):
    """扫到一个码，先看它已经属于谁 —— 撞码**当场**说，别等填完表保存才报错。

    查四处：编码、条码、二维码内容，以及售卖单位上的条码（箱码/瓶码常挂在单位上）。
    只认在架商品（停用品的码可以用，与旧线上"先下架再占码"一个意思）。查不到回空字典。
    """
    guard.require("products.view")
    wanted = str(value or "").strip()
    if not wanted:
        return {}
    fields = ["name", "product_name", "code", "barcode"]
    base = [["is_active", "=", 1]]
    if exclude:
        base.append(["name", "!=", exclude])
    for field in LOOKUP_FIELDS:
        rows = frappe.get_list(DOCTYPE, filters=base + [[field, "=", wanted]], fields=fields,
                               limit_page_length=1)
        if rows:
            return {**rows[0], "matched_by": field}
    # 售卖单位上的条码：先按条码拿到子表行，再把父商品过一遍权限与在架闸。
    # 几家公司各有一件挂同一个单位条码时，先取全部再过权限（按当前公司）
    parents = frappe.get_all(UNIT_DOCTYPE, filters={"barcode": wanted, "parenttype": DOCTYPE}, pluck="parent",
                             limit_page_length=20)
    if parents:
        rows = frappe.get_list(DOCTYPE, filters=base + [["name", "in", parents]], fields=fields,
                               limit_page_length=1)
        if rows:
            return {**rows[0], "matched_by": "unit_barcode"}
    return {}


def count_products(*, search=None, include_inactive=False, is_active=None) -> int:
    """列表总数（页脚"第 N 项/共 M 项"与"加载更多"用）。

    为什么不用 `frappe.db.count`：那条路**绕过 DocType 权限**，而本站点商品表是按岗位授权的；
    `frappe.get_list` 跟着权限走。商品是主数据、量级不大，用列表长度换权限正确性。
    """
    guard.require("products.view")
    filters, or_filters = _filters(search, include_inactive, is_active)
    return len(frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters,
                               pluck="name", limit_page_length=0))


def get_product(name: str):
    guard.require("products.view")
    doc = _get(name)
    data = {field: doc.get(field) for field in LIST_FIELDS}
    data["units"] = [
        {"unit_name": row.unit_name, "factor_to_base": row.factor_to_base,
         "barcode": row.barcode, "price": row.price, "is_default_sell": bool(row.is_default_sell)}
        for row in doc.get("units") or []
    ]
    return _mask(data)


def update_product(name: str, **changes):
    guard.require("products.manage")
    doc = _get(name)
    # 编码是系统发的，改它等于让历史单据行换了货（旧线上 ERP 门户也把它按原值写回）。
    if "code" in changes and (_text(changes["code"], contracts.MAX_CODE) or "") != (doc.code or ""):
        frappe.throw(_(CODE_LOCKED))
    barcode = (_text(changes.get("barcode"), contracts.MAX_BARCODE)
               if "barcode" in changes else doc.barcode)
    _assert_unique(doc.code, barcode, exclude=doc.name, owner=doc.get("express_company"))

    # 商品上的"默认仓"已经删掉（用户 2026-09-24："进货就进卖货那个仓" —— 进货那一侧改用
    # 收银台上配的仓，见 `warehouses.receiving`），只剩仓位这一栏还是商品级建议值。
    for field in ("default_location",):
        if field not in changes:
            continue
        value = _text(changes[field], 140)
        doc.set(field, value)
    old_unit, old_base = doc.unit or "", doc.base_unit or ""
    for field in ("barcode", "qr_payload", "unit", "image_url",
                  "base_unit"):
        if field in changes:
            doc.set(field, _text(changes[field], {
                "barcode": contracts.MAX_BARCODE, "qr_payload": contracts.MAX_QR,
                "unit": contracts.MAX_UNIT,
                "image_url": contracts.MAX_IMAGE_URL,
                "base_unit": contracts.MAX_BASE_UNIT,
            }[field]))
    # 两者原本一致时，改售卖单位就把库存单位一起带走（别让它悄悄变成"另一个单位"）；
    # 明确分过家的（库存单位与单位不同）不动 —— 那是商户自己的口径。
    if "unit" in changes and "base_unit" not in changes and old_base == old_unit:
        doc.base_unit = doc.unit
    if "category_id" in changes:
        doc.category_id = (int(changes["category_id"])
                           if str(changes["category_id"] or "").strip() else None)
    for field in ("track_batch", "track_expiry"):
        if field in changes:
            doc.set(field, _flag(changes[field]))
    if "product_name" in changes:
        try:
            doc.product_name = contracts.require_name(changes["product_name"])
        except ValueError:
            frappe.throw(_(NAME_REQUIRED))
    if "unit_price" in changes:
        for field, value in _price_fields(changes["unit_price"]).items():
            doc.set(field, value)
    if "default_cost" in changes:
        for field, value in _cost_fields(changes["default_cost"]).items():
            doc.set(field, value)
    for field in ("min_stock",):
        if field in changes:
            doc.set(field, _optional(changes[field]))
    if "vat_applicable" in changes:
        doc.vat_applicable = 1 if changes["vat_applicable"] in (True, 1, "1", "true") else 0
    if "is_active" in changes:
        doc.is_active = 1 if changes["is_active"] in (True, 1, "1", "true") else 0
    doc.save()
    if "image_url" in changes:
        _attach_image(doc.image_url, doc.name)
    frappe.db.commit()
    return {"name": doc.name}


def set_active(name: str, active):
    """停用/重新上架。旧线上商品是软删除，历史单据继续指向它。"""
    guard.require("products.manage")
    doc = _get(name)
    doc.is_active = 1 if active in (True, 1, "1", "true") else 0
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "is_active": bool(doc.is_active)}


def add_unit(name: str, *, unit_name, factor_to_base, barcode=None, price=None,
             is_default_sell=False):
    guard.require("products.manage")
    doc = _get(name)
    try:
        factor = contracts.unit_factor(factor_to_base)
    except ValueError:
        frappe.throw(_(INVALID_VALUE))
    unit_name = _text(unit_name, contracts.MAX_UNIT)
    barcode = _text(barcode, contracts.MAX_BARCODE)
    if barcode:
        for row in doc.get("units") or []:
            if row.barcode == barcode:
                frappe.throw(_(BARCODE_EXISTS))
    if is_default_sell in (True, 1, "1", "true"):
        for row in doc.get("units") or []:
            row.is_default_sell = 0
    doc.append("units", {
        "unit_name": unit_name,
        "factor_to_base": factor,
        "barcode": barcode,
        "price": _optional(price),
        "is_default_sell": 1 if is_default_sell in (True, 1, "1", "true") else 0,
    })
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "units": len(doc.units)}


@frappe.whitelist()
def api_list_products(**kwargs):
    kwargs.pop("cmd", None)
    return list_products(**kwargs)


@frappe.whitelist()
def api_find_product(value=None, exclude=None, **kwargs):
    kwargs.pop("cmd", None)
    return find_product(value, exclude=exclude)


@frappe.whitelist()
def api_next_product_code(**kwargs):
    kwargs.pop("cmd", None)
    return next_code_preview()


@frappe.whitelist()
def api_count_products(**kwargs):
    kwargs.pop("cmd", None)
    return count_products(**kwargs)


@frappe.whitelist()
def api_get_product(name=None, **kwargs):
    kwargs.pop("cmd", None)
    return get_product(name)


@frappe.whitelist(methods=["POST"])
def api_create_product(**kwargs):
    kwargs.pop("cmd", None)
    return create_product(**kwargs)


@frappe.whitelist(methods=["POST"])
def api_update_product(name=None, **kwargs):
    kwargs.pop("cmd", None)
    return update_product(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_active(name=None, active=None, **kwargs):
    kwargs.pop("cmd", None)
    return set_active(name, active)


@frappe.whitelist(methods=["POST"])
def api_add_unit(name=None, **kwargs):
    kwargs.pop("cmd", None)
    return add_unit(name, **kwargs)

# ── Excel 批量导入 ──────────────────────────────────────────────────────────


def _row_error(token) -> str:
    """逐行错误：认识的 token 翻成用户看得懂的那一句，不认识的照原样截断带上。"""
    token = str(token)
    return _(ROW_ERRORS[token]) if token in ROW_ERRORS else token[:200]


def _label_texts() -> dict:
    """界面标签 → 字段：四种语言都给（模板可能是在另一种语言的站点上下载的）。

    键名本身也收 —— 语言包缺这条时页面与模板都会露出键名，露出来也得认得。
    """
    labels: dict = {}
    for field, key in workbook.FIELD_LABELS:
        labels[key] = field
        for text in browser_text.all_languages(key).values():
            if text:
                labels[text] = field
    return labels


def _template_headers() -> list:
    """模板表头按**站点语言**给（租户是泰文就给泰文表头）。"""
    lang = (frappe.local.lang or browser_text.FALLBACK_LANG)[:2]

    def label_of(key):
        texts = browser_text.all_languages(key)
        return texts.get(lang) or texts.get("en") or key

    return workbook.template_headers(label_of)


@frappe.whitelist()
def api_import_template():
    """下载导入模板：表头就是商品页那几句界面文案，别的什么都没有。

    走 `binary` 直接回文件内容，不在租户里留 `File` 记录 —— 模板是产品自带的，
    不该在每位客户的库里各存一份。
    """
    guard.require("products.manage")
    frappe.local.response.filename = TEMPLATE_FILE
    frappe.local.response.filecontent = workbook.template_bytes(_template_headers())
    frappe.local.response.type = "binary"


def import_products(data: bytes) -> dict:
    """把上传的表逐行入库。**逐行报错、不整批回滚** —— 一行错不该让 200 行白导。

    入库走的是 `create_product`，与手工录入同一条校验路径（编码/条码唯一、金额非负、
    "没设价"与 ฿0 的区别），所以导入不会绕开任何规则。
    """
    guard.require("products.manage")
    try:
        rows, errors = workbook.parse_workbook(data, workbook.header_aliases(_label_texts()))
    except ValueError as exc:
        frappe.throw(_(IMPORT_ERRORS.get(str(exc), IMPORT_BAD_FILE)))
    # 解析层只报 token，文案在这里翻（纯层不连 Frappe、也不该管界面话术）。
    errors = [{"row": row["row"], "error": _row_error(row["error"])} for row in errors]
    created = 0
    for index, row in enumerate(rows, start=1):
        try:
            create_product(**row)
            created += 1
        except Exception as exc:  # noqa: BLE001 - 单行失败要变成行级错误，不连累整批
            frappe.db.rollback()
            errors.append({"row": index, "error": _row_error(str(exc))})
    return {"created": created, "failed": len(errors), "errors": errors[:100]}


@frappe.whitelist(methods=["POST"])
def api_import_products(**kwargs):
    """导入：前端用原生上传组件把文件传上来，这里按 `file_url` 读回内容再解析。

    为什么不让上传直接打到这个方法：原生上传（`frappe.ui.FileUploader` 的默认通道）
    会先把文件落成 `File` 记录并做大小/类型限制，我们只要按 URL 取回字节即可 ——
    两件事分开，各自的失败也分得清（传失败 vs 解析失败）。
    """
    kwargs.pop("cmd", None)
    file_url = str(kwargs.get("file_url") or "").strip()
    if not file_url:
        frappe.throw(_(IMPORT_NO_FILE))
    name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not name:
        frappe.throw(_(IMPORT_NO_FILE))
    return import_products(frappe.get_doc("File", name).get_content())
