"""扫码认商品的「码」共用一个命名空间（主会话 10-26，验收：内部编码填了别件商品的条码，三个入口都把那件顶掉了）。

同一家公司看得到的商品里（本公司从 Express 同步来的档 + ERP 本地档；本地档各家公司都看得到），所有能拿来扫码认商品的码
任意两个都不能撞：系统编码 ``code``、内部编码、Express 编码、条码、二维码内容、售卖单位条码，以及按台记录在用的主号与第二个号
（车：车架号、发动机号；作废的台不占号；按台记录有公司，只在这家公司里算）。同一件商品自己的几种码相同不算撞（Express 商品的系统编码常就是 Express 编码）。

- 新建 / 修改时由服务端拒：:func:`assert_free`，报出是和哪件商品的哪种码撞了；
- 认码（扫码、手输、导入）：:func:`candidates` 收齐所有命中的商品，只有一件才认；几件就交给人选，不默认挑一个。
  原文对所有码；GS1 码（Data Matrix、DataBar、GS1-128）解出的 GTIN、以及 GTIN 的各种长度写法（14 / 13 / 12 位、UPC-E）对条码
  与售卖单位条码（:mod:`products.gs1`，10-30）；
- 存量里已经撞的：:func:`clashes` 只读列出来，不自动改。
"""
import frappe
from frappe import _

PRODUCT = "ERP Product"
UNIT = "ERP Product Unit"
# 认码查的列，按这个顺序报「怎么认到的」（`matched_by`）
FIELDS = ("code", "internal_code", "express_code", "barcode", "qr_payload")
UNIT_BARCODE = "unit_barcode"
SERIAL = "ERP Serial No"
SERIAL_FIELDS = {"serial_no": "primary", "secondary_no": "secondary"}
KIND_LABELS = {"code": "product.codeKind.code", "internal_code": "product.codeKind.internalCode",
               "express_code": "product.codeKind.expressCode", "barcode": "product.codeKind.barcode",
               "qr_payload": "product.codeKind.qrPayload", UNIT_BARCODE: "product.codeKind.unitBarcode"}
CLASH = "This code is already used by {0} ({1})"
TAKEN = "This code is already in use"
AMBIGUOUS = "Several matches found for {0}. Please select a record."


def _scope(owner):
    """``owner`` = 这件商品所属的公司（Express 同步来的）：本公司的档 + 本地档；本地档（``owner`` 空）各家公司都看得到 → 全租户。"""
    return (" and ifnull(p.express_company, '') in ('', %s)", [owner]) if owner else ("", [])


def holders(value, *, owner=None, exclude=None) -> list:
    """这个码在看得到的范围里被哪些商品占着：``[{product, product_name, kind}]``（按库的排序规则比）。"""
    value = str(value or "").strip()
    if not value:
        return []
    scope, params = _scope(owner)
    skip = " and p.name != %s" if exclude else ""
    extra = [exclude] if exclude else []
    out = []
    for field in FIELDS:
        rows = frappe.db.sql(f"select p.name, p.product_name, p.express_company from `tab{PRODUCT}` p where p.`{field}` = %s{skip}{scope} limit 5",
                             [value, *extra, *params], as_dict=True)
        out += [{"product": row.name, "product_name": row.product_name, "kind": field,
                 "express_company": row.express_company or ""} for row in rows]
    rows = frappe.db.sql(f"""select p.name, p.product_name, p.express_company from `tab{UNIT}` u join `tab{PRODUCT}` p
        on p.name = u.parent and u.parenttype = %s where u.barcode = %s{skip}{scope} limit 5""",
                         [PRODUCT, value, *extra, *params], as_dict=True)
    out += [{"product": row.name, "product_name": row.product_name, "kind": UNIT_BARCODE,
             "express_company": row.express_company or ""} for row in rows]
    serial_scope = " and s.company = %s" if owner else ""
    for field, slot in SERIAL_FIELDS.items():
        rows = frappe.db.sql(f"""select p.name, p.product_name, p.serial_kind, s.company from `tab{SERIAL}` s join `tab{PRODUCT}` p
            on p.name = s.product where s.`{field}` = %s and ifnull(s.serial_key, '') != ''{skip}{serial_scope} limit 5""",
                             [value, *extra, *([owner] if owner else [])], as_dict=True)
        out += [{"product": row.name, "product_name": row.product_name, "kind": field, "express_company": row.company or "",
                 "label": f"serial.{row.serial_kind if row.serial_kind == 'device' else 'vehicle'}.{slot}"} for row in rows]
    return out


def assert_free(value, *, owner=None, exclude=None):
    """新建 / 修改商品的码之前核：撞了就拒，报出撞的那件商品与那种码。撞到的是别家公司从 Express 同步来的档（这个人平时看
    不到那家的主档）只说「已被占用」，不带名称（验收 10-26）。"""
    found = holders(value, owner=owner, exclude=exclude)
    if not found:
        return
    hit = found[0]
    if hit["express_company"] and hit["express_company"] != (owner or _active_company()):
        frappe.throw(_(TAKEN))
    frappe.throw(_(CLASH).format(hit["product_name"] or hit["product"], _(hit.get("label") or KIND_LABELS[hit["kind"]])))


def _active_company():
    from pearnly_erp_portal.masters import company as companies

    try:
        return companies.active_company()
    except Exception:  # noqa: BLE001 - 没有当前公司（后台任务）：一律按别家处理，不带名称
        return None


def candidates(value, company=None, *, active_only=True, exclude=None, rows_of=None) -> list:
    """扫到 / 手输的码 → 命中的商品（同一件只出一次）：``[{name, product_name, code, internal_code, express_code, barcode,
    matched_by}]``。``company`` 给了只认这家公司 Express 的档和本地档（不给只认本地档）；``rows_of(filters, fields)``
    给了就用调用方的查法（比如跟着权限与当前公司的 ``get_list``），不再另按公司筛。"""
    from pearnly_erp_portal.products import gs1

    value = str(value or "").strip()
    if not value:
        return []
    wanted = gs1.lookup(value)
    fields = ["name", "product_name", "code", "internal_code", "express_code", "barcode", "express_company", "attribute"]

    def visible(row):
        if rows_of:
            return True
        return (company and row.get("express_company") in (company, "", None)) or (not company and not row.get("express_company"))

    def query(field, wanted):
        filters = {field: wanted, **({"is_active": 1} if active_only else {})}
        if exclude:
            filters["name"] = ["!=", exclude]
        if rows_of:
            return rows_of(filters, fields)
        return frappe.get_all(PRODUCT, filters=filters, fields=fields, limit_page_length=20)

    found = {}
    for field in FIELDS:
        values = wanted["exact"] + (wanted["gtin"] if field == "barcode" else [])
        for row in query(field, values[0] if len(values) == 1 else ["in", values]):
            if visible(row) and row["name"] not in found:
                found[row["name"]] = {**row, "matched_by": field}
    unit_values = wanted["exact"] + wanted["gtin"]
    parents = frappe.get_all(UNIT, filters={"barcode": unit_values[0] if len(unit_values) == 1 else ["in", unit_values],
                                            "parenttype": PRODUCT}, pluck="parent", limit_page_length=20)
    if parents:
        for row in query("name", ["in", parents]):
            if visible(row) and row["name"] not in found:
                found[row["name"]] = {**row, "matched_by": UNIT_BARCODE}
    return list(found.values())


def clashes(company=None, limit=500) -> list:
    """存量里已经撞的码（只读，不改）：``[{value, products: [{product, product_name, kind}]}]``。``company`` 给了只看这家公司
    看得到的档（本公司 Express 的 + 本地的）；不给看全租户，两件都是别家公司各自 Express 的档不算撞。"""
    where = " and ifnull(express_company, '') in ('', %(company)s)" if company else ""
    union = " union all ".join(f"select `{field}` as value, name, '{field}' as kind from `tab{PRODUCT}` "
                               f"where ifnull(`{field}`, '') != ''{where}" for field in FIELDS)
    union += (f" union all select u.barcode as value, u.parent as name, '{UNIT_BARCODE}' as kind from `tab{UNIT}` u "
              f"where u.parenttype = '{PRODUCT}' and ifnull(u.barcode, '') != ''")
    serial_where = " and company = %(company)s" if company else ""
    union += "".join(f" union all select `{field}` as value, product as name, '{field}' as kind from `tab{SERIAL}` "
                     f"where ifnull(`{field}`, '') != '' and ifnull(serial_key, '') != ''{serial_where}" for field in SERIAL_FIELDS)
    groups = frappe.db.sql(f"""select c.value, group_concat(distinct concat(c.name, '|', c.kind) separator ',') as holders
        from ({union}) c group by c.value having count(distinct c.name) > 1 limit {int(limit)}""",
                           {"company": company}, as_dict=True)
    names = {item.split("|")[0] for row in groups for item in row.holders.split(",")}
    info = {row.name: row for row in frappe.get_all(PRODUCT, filters={"name": ["in", list(names) or [""]]},
                                                    fields=["name", "product_name", "express_company"],
                                                    limit_page_length=len(names) or 1)}
    out = []
    for row in groups:
        products = []
        for item in row.holders.split(","):
            name, kind = item.split("|", 1)
            if name in info and (not company or (info[name].express_company or "") in ("", company)):
                products.append({"product": name, "product_name": info[name].product_name, "kind": kind,
                                 "express_company": info[name].express_company or ""})
        owners = {item["express_company"] for item in products}
        if len({item["product"] for item in products}) > 1 and (len(owners) == 1 or "" in owners):
            out.append({"value": row.value, "products": products})
    return out


__all__ = ["AMBIGUOUS", "FIELDS", "KIND_LABELS", "UNIT_BARCODE", "assert_free", "candidates", "clashes", "holders"]
