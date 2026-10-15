"""报税草稿的数据来源（只读）：Express 回读的 ISVAT（`readback`）与识别记录、50 ทวิ 参考记录（`history`）。

识别记录经 DocType 自己的权限查询读（`frappe.get_list`，同智能体列表工具），先核读识别记录的权限。

50 ทวิ 不管租户选的录不录都读得到（草稿只读，不看推送的选择）：选「录入」或没选的成了识别记录（主页就是凭证）；选「不录入」
的存成参考记录；和票扫在同一份原件里、挂在票上作证据的凭证页（非发票页）也从那条识别记录里取。同一页既有参考记录又挂在票上
的，只算参考记录那一条。认凭证用同一条规则（`contracts.document_policy.document_class`）。
"""
import json
from datetime import timedelta

import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.contracts.history import primary_page_fields
from pearnly_cowork.contracts.recognized_names import name_key
from pearnly_cowork.history import certificates
from pearnly_cowork.tax import input_vat, wht

HISTORY = "Cowork OCR History"
CLIENT = "Cowork Client"
PAGE = 200
SCAN_MAX = 5000
CHUNK = 200
# 认公司名用的名称至少这么长（比对键，去空白标点后）：读坏了只剩「บริษัท」「Co., Ltd.」的名称认不出是哪家
NAME_MIN = 8
WHT_FIELDS = ("document_type", "document_flag", "direction", "invoice_number", "date", "seller_name", "seller_tax",
              "seller_addr", "seller_branch", "buyer_name", "buyer_tax", "buyer_addr", "buyer_branch", "subtotal",
              "discount", "vat", "total_amount", "wht_rate", "wht_amount", "payment_method", "posting_payment_manual",
              "category", "is_not_invoice")


def vat_readback(connection, account_set_key, year, month):
    """该账套该月最近一次完成的 Express 回读（谁发起的都算，用户 2026-10-15 夜定；`readback.service.vat_records` 的约定形状）；没有回 None。"""
    try:
        from pearnly_cowork.readback import service
    except ModuleNotFoundError as error:
        if error.name not in ("pearnly_cowork.readback", "pearnly_cowork.readback.service"):
            raise
        return None
    return service.vat_records(connection, account_set_key, year, month)


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), CHUNK):
        yield values[start:start + CHUNK]


def _pages(value) -> list:
    try:
        pages = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return []
    return [page for page in pages if isinstance(page, dict)] if isinstance(pages, list) else []


def _fields(pages) -> dict:
    return primary_page_fields(_pages(pages))


def can_read_documents() -> bool:
    return scope.allowed_any(scope.READ_FEATURES)


def purchase_documents(purchases) -> dict:
    """进项行能关联到的识别记录主页字段：{记录名: 字段, document_key(卖方税号, 票号): 字段}。"""
    names = sorted({record.history for record in purchases if record.history})
    numbers = sorted({record.refnum for record in purchases if record.refnum})
    found = {}
    lookups = [{"name": ["in", chunk]} for chunk in _chunks(names)]
    lookups += [{"invoice_no": ["in", chunk]} for chunk in _chunks(numbers)]
    for filters in lookups:
        for row in frappe.get_list(HISTORY, filters=filters, fields=["name", "invoice_no", "seller_tax", "pages"],
                                   limit_page_length=0):
            fields = _fields(row.get("pages"))
            if not fields.get("document_type"):  # 没有可读的主页：当作没关联到，不说成「不是税票」
                continue
            found[row.name] = fields
            found[input_vat.document_key(fields.get("seller_tax") or row.get("seller_tax"), row.get("invoice_no"))] = fields
    return found


def _owner(workspace, company) -> tuple[set, bool]:
    """票面没有本公司税号时认归属：(税号是本公司的客户档案名, 工作空间本身就是这家公司)。"""
    clients = {row.name for row in frappe.get_all(CLIENT, filters={"workspace": workspace}, fields=["name", "tax_id"],
                                                  limit_page_length=0) if wht.tax_id(row.get("tax_id")) == company}
    return clients, wht.tax_id(frappe.db.get_value("Cowork Workspace", workspace, "tax_id")) == company


def _match(row, company, clients, own_workspace, names=frozenset()) -> str:
    """这条识别记录算不算这家公司的：票面有本公司税号（tax_id）；票面没有 / 读错，但归在本公司的客户档案（client）；
    单一公司的工作空间里买方税号空着、没挂客户的（workspace）；买方税号空着、买方名称就是本公司的名称（name，名称从本期
    印了本公司税号的记录学来，见 ``_company_names``）。都不是回空串。后三种先不计入，待会计确认。"""
    seller, buyer = wht.tax_id(row.get("seller_tax")), wht.tax_id(row.get("buyer_tax"))
    if company in (seller, buyer):
        return "tax_id"
    if row.get("client") and row.get("client") in clients:
        return wht.MATCH_CLIENT
    if own_workspace and not buyer and not row.get("client"):
        return wht.MATCH_WORKSPACE
    if names and not buyer and name_key(row.get("buyer_name")) in names:
        return wht.MATCH_NAME
    return ""


def _company_names(rows, company) -> frozenset:
    """这家公司的名称（比对键）：取到的记录里买方税号是本公司的那些买方名称；同一名称也印过别家税号的不算（认不准）。
    2026-10-13 测试机：多公司工作空间里个人开的收据没读出买方税号、买方名称就是本公司，原先整张没取到。"""
    ours, others = set(), set()
    for row in rows:
        key = name_key(row.get("buyer_name"))
        buyer = wht.tax_id(row.get("buyer_tax"))
        if len(key) < NAME_MIN or not buyer:
            continue
        (ours if buyer == company else others).add(key)
    return frozenset(ours - others)


def _scan(window, order) -> tuple[list, bool]:
    """票据日在 window 内的识别记录（只取认归属用的几列）：(行, 是否只看了 SCAN_MAX 张)。"""
    found, offset = [], 0
    while True:
        rows = frappe.get_list(HISTORY, filters={"invoice_date": ["between", list(window)]},
                               fields=["name", "seller_tax", "buyer_tax", "buyer_name", "client"],
                               order_by=f"invoice_date {order}, name {order}", limit_start=offset,
                               limit_page_length=PAGE)
        found.extend(rows)
        offset += PAGE
        if len(rows) < PAGE:
            return found, False
        if offset >= SCAN_MAX:
            return found, True


def _heads(rows, company, owner, names) -> dict:
    """算这家公司的识别记录：{记录名: 怎么认的}。"""
    heads = {}
    for row in rows:
        found = _match(row, company, *owner, names)
        if found:
            heads[row.name] = found
    return heads


def _wht_fields(fields) -> dict:
    return {key: fields.get(key) for key in WHT_FIELDS if fields.get(key) not in (None, "")}


def _documents(heads) -> list:
    """取到的识别记录：主页一条；记录里其他页中的 50 ทวิ（挂在票上作证据的凭证页）每页一条，ref 是「记录名#页号」。
    凭证带上它在原件里的位置 ``page``（原件, 页号）：同一页也存了参考记录的只算参考记录那条（``wht_documents``）。"""
    documents = []
    for chunk in _chunks(heads):
        for row in frappe.get_list(HISTORY, filters={"name": ["in", chunk]}, fields=["name", "pages", "source_file"],
                                   limit_page_length=0):
            pages = _pages(row.get("pages"))
            primary = primary_page_fields(pages)
            if not wht.is_certificate(primary):
                documents.append({"ref": row.name, "origin": "history", "match": heads[row.name],
                                  "fields": _wht_fields(primary)})
            for position, page in enumerate(pages):
                fields = page.get("fields")
                if not isinstance(fields, dict) or not wht.is_certificate(fields):
                    continue
                if fields is not primary and (page.get("is_copy") or page.get("is_duplicate")):
                    continue  # 副本页不另算一张
                number = int(page.get("page_index") or position + 1)
                document = {"ref": row.name if fields is primary else f"{row.name}#{number}", "origin": "history",
                            "page": (row.get("source_file"), number) if row.get("source_file") else None,
                            "fields": _wht_fields(fields)}
                if fields is primary:
                    document["match"] = heads[row.name]
                documents.append(document)
    return documents


def wht_documents(workspace, company_tax_id, start, end) -> tuple[list, bool]:
    """这家公司（付款方）票据日在 [start − 90 天, end + 90 天] 的识别记录（月末以后的：本月的票可能以后几个月才付款开
    凭证，配上了就按凭证日落月；以后的票也一起配，别的月配给它的凭证这个月也配给它，不按票据日在两个月各算一次），
    加上同一区间的 50 ทวิ 参考记录：([{ref, origin, fields, match?, page?}], 是否只看了 SCAN_MAX 张)。按票据日取：付款日只会
    等于或晚于票据日。"""
    scope.require_read()
    company = wht.tax_id(company_tax_id)
    window = start - timedelta(days=wht.LOOKBACK_DAYS)
    ahead = end + timedelta(days=wht.FORWARD_DAYS)
    owner = _owner(workspace, company)
    rows, truncated = _scan((window, end), "desc")
    later_rows, later_truncated = _scan((end + timedelta(days=1), ahead), "asc")
    names = _company_names(rows + later_rows, company)
    heads, later = _heads(rows, company, owner, names), _heads(later_rows, company, owner, names)
    references = certificates.listing(workspace, window, ahead)
    kept = {item.get("page") for item in references} - {None}
    documents = [item for item in _documents(heads) + _documents(later) if item.get("page") not in kept]
    documents.sort(key=lambda item: (str(item["fields"].get("date") or ""), item["ref"]))
    return documents + references, truncated or later_truncated
