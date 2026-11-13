"""收款的服务函数：LINE（第 3 步）与 CC 工具（第 4 步）调同一组，权限、公司、账套、金额都在这里判（网页只读，见 `api`）。

身份一律是当前会话用户（LINE 里是绑定的 ERP 账号，CC 里是登录的人）。写入要 ``collection.write``（收款员岗位），只在本人能用的
公司里、只动本人的记录；看本公司全部要 ``collection.view``。

一笔收款的生命：建草稿（:func:`create_draft`，内容完整、规则全过；账套此刻按 `express.target.pick` 选定并冻结）→ 可改
（:func:`update_draft`，每改一次版本 +1）→ 出确认卡（:func:`card`，带版本号）→ 用户明确同意后放行（:func:`release`：只认卡片
展示的那个版本，对不上回 ``changed``）→ 交写账队列（`push`）→ 写入 / 失败；没被电脑领走前可撤回（:func:`withdraw`）。

规则不过抛 :class:`contracts.CollectionError`（``code`` / ``detail``），没权限抛 ``frappe.PermissionError``；调用方按码出文案。
"""
import secrets

import frappe
from frappe import _
from frappe.utils import now_datetime, nowdate

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.collection import contracts, snapshot
from pearnly_erp_portal.express import choices, contracts as express_contracts, facts, target
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP Collection"
INVOICE = "ERP Collection Invoice"
PAYMENT = "ERP Collection Payment"
COMPANY = "ERP Company"
CUSTOMER = "ERP Customer"
WRITE, VIEW = "collection.write", "collection.view"
SEARCH_MAX = 50
MINE_MAX = 100

# 规则以外、服务层自己的几个码
NO_COMPANY = "collection.company_not_linked"
NO_CUSTOMER = "collection.customer_not_found"
CC_UPDATE = "collection.cc_update_needed"
VOUCHER_INVALID = "collection.voucher_invalid"
LEDGER_NEEDED = "ledger_needed"  # CC 对话里本对话账套未定（同 `express.conversation.LEDGER_NEEDED`）
BOOK_NOT_CONVERSATION = "collection.book_not_conversation"  # 草稿的账套不是本对话账套（之后在右栏换了，`plan.view`）
# 凭证原件的 id 前缀。任务书写的是 ``ERC-``，但共用原件层（`pearnly_core.originals.registered.NAME` / ``links.OBJECT`` /
# ``cloud_run.ORIGINAL``）与甲骨文上的原件服务只认 ``CWS-`` / ``ERO-``：用 ``ERC-`` 要同时改共用层并重新部署甲骨文
# claude-ocr，否则 Claude 租户的凭证存不进去。先用 ERP 原件已有的 ``ERO-``（交主会话定，换前缀只改这一行加共用层）。
VOUCHER_ID_PREFIX = "ERO-"


# ── 公司、账套、客户 ─────────────────────────────────────────────────────────


def _not_permitted():
    frappe.throw(_("Not permitted"), frappe.PermissionError)


def _company(company):
    """本人能用、接了 Express 的公司（公司行）。"""
    from pearnly_erp_portal.express import push as express_push

    if not company or company not in companies.allowed_companies():
        _not_permitted()
    row = frappe.db.get_value(COMPANY, company, ["name", "company_name", "tax_id", "branch"], as_dict=True)
    if not row or not express_push.linked(company):
        raise contracts.CollectionError(NO_COMPANY)
    return row


def companies_for() -> list:
    """收款员能选的公司（本人可用、接了 Express）：``[{name, company_name}]``。只有一家时 LINE 跳过这一步。"""
    from pearnly_erp_portal.express import push as express_push

    guard.require(WRITE)
    out = []
    for name in companies.allowed_companies():
        if express_push.linked(name):
            out.append({"name": name, "company_name": frappe.db.get_value(COMPANY, name, "company_name") or name})
    return out


def _picked(company_row, day):
    """这笔收款该写进哪本（与 ERP 单据同一条规则 `express.target.pick`）。CC 对话里（`express.conversation.wanted`）就是本对话账套，
    不按日期另判：列发票、列方式、建草稿三步同一本（A 线审计 A11，11-12）；网页、LINE 按单据日期选。"""
    from pearnly_erp_portal.express import conversation

    return target.pick(company_row, day, conversation.wanted(company_row))


def book_for(company_row, day):
    """这笔收款写进哪本账套（:func:`_picked`）。没有税号 / 没有账套抛那个原因码；CC 对话里本对话账套未定抛
    :data:`LEDGER_NEEDED`（``detail`` 带可选的几本，问一次用 ``pearnly_conversation_ledger`` 设）；网页 / LINE 同期两份、没选过抛
    ``express.account_set_several``（``detail`` 带可选的几本，经 :func:`choose_book` 选一次）。"""
    from pearnly_erp_portal.express import conversation

    picked = _picked(company_row, day)
    if picked["reason"] == conversation.LEDGER_NEEDED:
        pin = conversation.pinned(company_row) or {}
        raise contracts.CollectionError(LEDGER_NEEDED, {"options": [
            {"code": item["id"], "name": item.get("label") or item["id"]} for item in pin.get("options") or ()]})
    if picked["reason"] == express_contracts.SEVERAL_BOOKS:
        raise contracts.CollectionError(picked["reason"], {"options": picked["options"]})
    if picked["reason"] and picked["reason"] != express_contracts.NO_WRITER:
        raise contracts.CollectionError(picked["reason"])
    return picked["book"]  # 没有电脑能写也照样记（排队，电脑上线补写）


def book_options(company, day=None) -> dict | None:
    """网页 / LINE 开始一笔收款前：这家公司这一天的账套定得下来回 None；同期两份、没选过回 ``{"options": [{code, name, short}]}``
    （``short``：按钮上的短名，见 :func:`_with_short`），
    让收款员选一次（:func:`choose_book`）。其余停下的原因（没有账套、没有税号）照抛。"""
    guard.require(WRITE)
    row = _company(company)
    try:
        book_for(row, day or nowdate())
    except contracts.CollectionError as error:
        if error.code == express_contracts.SEVERAL_BOOKS:
            return {"options": _with_short(row, error.detail["options"])}
        raise
    return None


def _with_short(company_row, options) -> list:
    """可选的几本各带一个短名（按钮上用）：账套目录名（「TEST2」「TEST」）；目录名撞了再带会计年度；还撞用全名。"""
    from pearnly_core.cc_desktop import books

    tax = express_contracts.tax13(company_row.get("tax_id"))
    known = {item["key"]: item for item in (books.for_company(tax, company_row.get("branch")) if tax else [])}
    out = []
    for option in options:
        item = known.get(option["code"]) or {}
        path = str(item.get("path") or "").rstrip("\\/")
        out.append({**option, "short": path.replace("/", "\\").rsplit("\\", 1)[-1] or option["name"],
                    "year": str(item.get("period_start") or "")[:4]})
    dup = {row["short"] for row in out if [r["short"] for r in out].count(row["short"]) > 1}
    for row in out:
        if row["short"] in dup and row["year"]:
            row["short"] = f"{row['short']} {row['year']}"
    shorts = [row["short"] for row in out]
    for row in out:
        if shorts.count(row["short"]) > 1:
            row["short"] = row["name"]  # 同目录名同年度（副本与真账）：用全名（名称 · 位置），按钮上由 `line_ui.labels` 取不同的那段
    return [{key: value for key, value in row.items() if key != "year"} for row in out]


def choose_book(company, key, day=None) -> dict:
    """同期两份账套时选一份：按公司 × 会计年度记住，与 ERP 单据同一份记忆（`express.choices.ACCOUNT_SET`，网页单据上选的、
    CC 对话的默认账套都读它）。只认这一天可选的那几本；已经定下来的那一本再选一次照回。"""
    guard.require(WRITE)
    row = _company(company)
    day = str(day or nowdate())[:10]
    picked = target.pick(row, day)
    if picked["reason"] == express_contracts.SEVERAL_BOOKS:
        found = next((item for item in picked["options"] if item["code"] == key), None)
        if found is None:
            frappe.throw(_("Invalid value"))
        choices.remember(company, choices.ACCOUNT_SET, picked["subject"], key, found["name"])
        return {"chosen": key}
    if not picked.get("book") or picked["book"]["key"] != key:
        frappe.throw(_("Invalid value"))
    return {"chosen": key}


def customers(company, q="", start=0, limit=12) -> dict:
    """找客户：这家公司从 Express 同步来的客户，按编号或名字的一部分（服务端分页，LINE 快捷回复最多 12 个）。
    ``{"items": [{code, name}], "total"}``。收款员看所有客户（不按收款员过滤）。"""
    guard.require(WRITE)
    _company(company)
    filters = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    text = str(q or "").strip()[:60]
    or_filters = [["express_code", "like", f"%{text}%"], ["customer_name", "like", f"%{text}%"]] if text else None
    limit = max(1, min(int(limit or 12), SEARCH_MAX))
    found = frappe.get_all(CUSTOMER, filters=filters, or_filters=or_filters, fields=["express_code", "customer_name"],
                           order_by="customer_name asc, express_code asc", limit_start=max(0, int(start or 0)),
                           limit_page_length=limit)
    from pearnly_core.desk import list_filters

    return {"items": [{"code": row.express_code, "name": row.customer_name} for row in found],
            "total": list_filters.count(CUSTOMER, filters, or_filters, ignore_permissions=True)}


def _customer_name(company, code):
    name = frappe.db.get_value(CUSTOMER, {"express_company": company, "express_code": code, "is_active": 1},
                               "customer_name")
    if name is None:
        raise contracts.CollectionError(NO_CUSTOMER, {"customer": code})
    return name


# ── 收款方式 ─────────────────────────────────────────────────────────────────


def _catalog(company, book):
    """这本账套的收款方式（`contracts.catalog`）。主档还没读过就请电脑读（抛 ``express.masters_reading``）；电脑是旧版、读回来
    没有收款类型抛 :data:`CC_UPDATE`。"""
    data = facts.load(book["key"])
    if data is None:
        from pearnly_erp_portal.express import masters

        masters.ensure(company, book)
        data = facts.load(book["key"])
    if data is None:
        raise contracts.CollectionError(express_contracts.READING)
    if not isinstance(data.get("receipt_types"), list):
        raise contracts.CollectionError(CC_UPDATE)
    return contracts.catalog(data["receipt_types"], data.get("cash_account") or "")


def methods(company, day=None, book=None, every=False) -> dict:
    """收款员能选的方式：``{"cash": bool, "transfers": [{prefix, code, name, label}], "discount": {…} | None}``（现金走现金那一格；
    支票、预扣、费用这类不列，规则见 `contracts`；``label`` 是银行简称 + 账号末 4 位，进同一户头的几种只列一种，`contracts.listed`）。``book``：已有的草稿冻结的那本（:func:`edit_choices`），不再按日期选。
    ``every``：进同一户头的几种都列（改草稿的弹窗要能显示草稿里已用的那种）。"""
    guard.require(WRITE)
    row = _company(company)
    found = _catalog(company, book or book_for(row, day or nowdate()))
    shape = lambda item: {"prefix": item["prefix"], "code": item["code"], "name": item["name"],  # noqa: E731
                          "label": item.get("label") or item["name"]}
    return {"cash": found["cash"], "transfers": [shape(item) for item in (found["transfers"] if every else contracts.listed(found["transfers"]))],
            "discount": shape(found["discount"]) if found["discount"] else None}


# ── 未收清发票与预留 ─────────────────────────────────────────────────────────


def reserved(book_key, docnums, exclude=None) -> dict:
    """别的收款记录占着的金额 ``{docnum: Decimal}``：已放行还没写完的（待写入、写入中），与已写入、但快照还没读到写后余额的
    （写入时刻晚于那张发票的读取时刻）。``exclude``：正在改的这一笔。"""
    if not docnums:
        return {}
    lines = frappe.get_all(INVOICE, filters={"parenttype": DOCTYPE, "docnum": ["in", list(docnums)]},
                           fields=["parent", "docnum", "amount"], limit_page_length=contracts.MAX_INVOICES * 200)
    parents = {row.parent for row in lines if row.parent != exclude}
    if not parents:
        return {}
    heads = {row.name: row for row in frappe.get_all(
        DOCTYPE, filters={"name": ["in", list(parents)], "book_key": book_key,
                          "status": ["in", [*contracts.RESERVING, contracts.WRITTEN]]},
        fields=["name", "status", "written_at"], limit_page_length=len(parents))}
    read = {row.docnum: row.read_at for row in frappe.get_all(
        snapshot.DOCTYPE, filters={"book_key": book_key, "docnum": ["in", list(docnums)]}, fields=["docnum", "read_at"],
        limit_page_length=len(docnums))}
    out = {}
    for row in lines:
        head = heads.get(row.parent)
        if head is None:
            continue
        if head.status == contracts.WRITTEN and (not head.written_at or not read.get(row.docnum)
                                                 or frappe.utils.get_datetime(head.written_at)
                                                 <= frappe.utils.get_datetime(read[row.docnum])):
            continue  # 快照已经是写后的余额
        out[row.docnum] = out.get(row.docnum, contracts.ZERO) + contracts.money(row.amount or 0)
    return out


def _open_rows(book_key, customer, exclude=None) -> dict:
    found = snapshot.rows(book_key, customer)
    held = reserved(book_key, [row.docnum for row in found], exclude=exclude)
    out = {}
    for row in found:
        remaining = contracts.money(row.remaining or 0)
        out[row.docnum] = {"docnum": row.docnum, "doc_date": str(row.doc_date or ""), "salesman": row.salesman or "",
                           "department": row.department or "", "net": contracts.money(row.net or 0),
                           "received": contracts.money(row.received or 0), "remaining": remaining,
                           "reserved": held.get(row.docnum, contracts.ZERO),
                           "available": contracts.available(remaining, held.get(row.docnum)),
                           "read_at": row.read_at}
    return out


def open_invoices(company, customer, day=None, refresh=True, exclude=None, book=None) -> dict:
    """一位客户此刻能收的发票（快照减去别的记录的预留），日期早的在前：``{"items": [{docnum, doc_date, net, received,
    remaining, reserved, available, salesman, department}], "refresh": ready | reading | offline | outdated}``。
    ``refresh``：同时请电脑按这位客户即时读一次（读好前先给快照里的）；``book``：已有的草稿冻结的那本。"""
    guard.require(WRITE)
    row = _company(company)
    _customer_name(company, customer)
    book = book or book_for(row, day or nowdate())
    state = snapshot.request(company, book, customer) if refresh else ""
    rows = _open_rows(book["key"], customer, exclude=exclude)
    items = [{**value, **{key: contracts.text(value[key]) for key in ("net", "received", "remaining", "reserved",
                                                                       "available")}}
             for value in rows.values() if value["available"] > 0]
    for item in items:
        item.pop("read_at", None)
    return {"items": items, "refresh": state, "book": book["key"]}


# ── 草稿 ─────────────────────────────────────────────────────────────────────


def _new_name():
    for _attempt in range(8):
        name = "COL" + now_datetime().strftime("%y%m%d") + "-" + secrets.token_hex(2).upper()
        if not frappe.db.exists(DOCTYPE, name):
            return name
    frappe.throw(_("Try again"))


def _own(name, for_update=False):
    """本人的收款记录（别人的、不存在的一律当没权限）。"""
    guard.require(WRITE)
    if not name or not frappe.db.exists(DOCTYPE, name):
        _not_permitted()
    doc = frappe.get_doc(DOCTYPE, name, for_update=for_update)
    if doc.collector != frappe.session.user or doc.company not in companies.allowed_companies():
        _not_permitted()
    return doc


def _vouchers_ok(methods_value, record=None):
    """方式里引用的凭证：本人上传的私有文件，还没挂别的单（或就挂在这一笔上）。"""
    for row in methods_value:
        file = str(row.get("voucher") or "").strip()
        if not file:
            continue
        found = frappe.db.get_value("File", file, ["owner", "attached_to_doctype", "attached_to_name", "is_private"],
                                    as_dict=True)
        if not found or found.owner != frappe.session.user or not found.is_private or (
                found.attached_to_doctype and (found.attached_to_doctype, found.attached_to_name) != (DOCTYPE, record)):
            raise contracts.CollectionError(VOUCHER_INVALID, {"voucher": file})


def _checked(company, book, customer, draft, record=None, *, require_vouchers=False):
    open_rows = _open_rows(book["key"], customer, exclude=record)
    checked = contracts.check(draft, open_rows, _catalog(company, book), require_vouchers=require_vouchers)
    _vouchers_ok(checked["methods"], record)
    return checked


def _choices(book_key) -> dict:
    """这本账套能选的销售员（未停用）与部门、RE 单据簿的默认部门（主档读回的 ``salesmen`` / ``departments`` / ``receipt_run``）。
    旧版 CC 桌面版没报的项是 None（不核）。"""
    data = facts.load(book_key) or {}
    salesmen = data.get("salesmen") if isinstance(data.get("salesmen"), list) else None
    departments = data.get("departments") if isinstance(data.get("departments"), list) else None
    run = data.get("receipt_run") if isinstance(data.get("receipt_run"), dict) else {}
    return {"salesmen": None if salesmen is None else [
                {"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
                for row in salesmen if isinstance(row, dict) and row.get("code") and not row.get("inactive")],
            "departments": None if departments is None else [
                {"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
                for row in departments if isinstance(row, dict) and row.get("code")],
            "run_department": str(run.get("department") or "")}


def _staff(book_key, checked, salesman=None, department=None, previous=None) -> dict:
    """销售员与部门（`contracts.staff`）；收款员给的值要在这本账套里（停用的销售员、不存在的部门拒）。"""
    found = _choices(book_key)
    out = contracts.staff(checked["invoices"][0], found["run_department"], salesman, department, previous)
    if salesman and found["salesmen"] is not None and out["salesman"] not in {row["code"] for row in found["salesmen"]}:
        raise contracts.CollectionError(contracts.SALESMAN_INVALID, {"salesman": out["salesman"]})
    if department and found["departments"] is not None and \
            out["department"] not in {row["code"] for row in found["departments"]}:
        raise contracts.CollectionError(contracts.DEPARTMENT_INVALID, {"department": out["department"]})
    return out


def staff_choices(name) -> dict:
    """改草稿的弹窗里能选的销售员与部门（本人的记录）。"""
    doc = _own(name)
    found = _choices(doc.book_key)
    return {"salesmen": found["salesmen"] or [], "departments": found["departments"] or []}


def edit_choices(name) -> dict:
    """改草稿的弹窗（网页「我的收款」）：各张此刻能收多少、能选的方式，都按这笔草稿冻结的那本账套（A11：不按草稿日期重新选，
    同期又报来一本也不变）。"""
    doc = _own(name)
    book = {"key": doc.book_key, **_book_row(doc)}
    found = open_invoices(doc.company, doc.customer, refresh=False, exclude=name, book=book)
    available = {row["docnum"]: row["available"] for row in found["items"]}
    return {"available": {row.docnum: available.get(row.docnum, "0.00") for row in doc.invoices},
            "methods": methods(doc.company, book=book, every=True)}


def _fill(doc, checked, people=None):
    first = checked["invoices"][0]
    people = people or {"salesman": first["salesman"], "department": first["department"]}
    doc.update({"salesman": people["salesman"], "department": people["department"], "cash": checked["cash"],
                "transfer": checked["transfer"], "discount": checked["discount"], "total": checked["total"],
                "voucher_count": sum(1 for row in checked["methods"] if row["voucher"]),
                **contracts.summaries(checked)})
    doc.set("invoices", [{"docnum": row["docnum"], "invoice_date": row["doc_date"] or None, "remaining": row["remaining"],
                          "amount": row["amount"], "salesman": row["salesman"]} for row in checked["invoices"]])
    doc.set("payments", [{"prefix": row["prefix"], "code": row["code"], "type_name": row["name"], "role": row["role"],
                          "amount": row["amount"], "voucher": row["voucher"] or None} for row in checked["methods"]])


def _attach(name, checked):
    for row in checked["methods"]:
        if row["voucher"]:
            frappe.db.set_value("File", row["voucher"], {"attached_to_doctype": DOCTYPE, "attached_to_name": name},
                                update_modified=False)


def _draft_input(invoices, cash, methods_value, amount, open_rows):
    """``invoices`` 可以只给单号（``amount`` 是总数，按日期早的先收满，`contracts.allocate`），也可以逐张给金额。"""
    invoices = [row if isinstance(row, dict) else {"docnum": row} for row in invoices or []]
    if amount not in (None, "") and invoices and all(row.get("amount") in (None, "") for row in invoices):
        picked = [open_rows[row["docnum"]] for row in invoices if row.get("docnum") in open_rows]
        if len(picked) != len(invoices):
            missing = next(row.get("docnum") for row in invoices if row.get("docnum") not in open_rows)
            raise contracts.CollectionError(contracts.INVOICE_NOT_FOUND, {"docnum": missing})
        invoices = contracts.allocate(picked, amount)
    return {"invoices": invoices, "cash": cash or 0, "methods": list(methods_value or [])}


def create_draft(company, customer, invoices, cash=0, methods=None, amount=None, doc_date=None, source="line",
                 salesman=None, department=None) -> dict:
    """建一笔收款草稿（内容完整、规则全过，凭证可以之后补）。回 :func:`card`。

    ``invoices``：``[{docnum, amount}]``，或只给单号再给 ``amount``（总数，早的先收满）；``methods``：``[{prefix, amount,
    voucher?}]``（转账、折扣按前缀；``voucher`` 是 :func:`store_voucher` 回的文件）；``source``：``line`` / ``cc``；
    ``salesman`` / ``department``：不给就默认第一张发票的（`contracts.staff`）。"""
    guard.require(WRITE)
    row = _company(company)
    customer = str(customer or "").strip()
    customer_name = _customer_name(company, customer)
    day = str(doc_date or nowdate())[:10]
    book = book_for(row, day)
    if source not in contracts.SOURCES:
        frappe.throw(_("Invalid value"))
    draft = _draft_input(invoices, cash, methods, amount, _open_rows(book["key"], customer))
    checked = _checked(company, book, customer, draft)
    people = _staff(book["key"], checked, salesman, department)
    name = _new_name()
    doc = frappe.get_doc({"doctype": DOCTYPE, "name": name, "company": company, "doc_date": day,
                          "collector": frappe.session.user, "source": source, "customer": customer,
                          "customer_name": customer_name, "status": contracts.DRAFT, "draft_version": 1,
                          "book_key": book["key"], "book_label": target.option_name(book)[:140]})
    _fill(doc, checked, people)
    doc.flags.name_set = True
    doc.insert(ignore_permissions=True, set_name=name)
    _attach(name, checked)
    return card(name)


def update_draft(name, version, invoices, cash=0, methods=None, amount=None, salesman=None, department=None) -> dict:
    """改草稿（整笔换成新内容，规则全过）：``version`` 要是本人看到的那个版本（别处刚改过回 ``changed``），改完版本 +1。
    ``salesman`` / ``department`` 不给：收款员改过的沿用，没改过的跟着第一张发票走（`contracts.staff`）。"""
    doc = _own(name, for_update=True)
    if doc.status != contracts.DRAFT:
        raise contracts.CollectionError(contracts.NOT_DRAFT, {"status": doc.status})
    if str(version) != str(int(doc.draft_version or 0)):
        raise contracts.CollectionError(contracts.CHANGED, {"version": int(doc.draft_version or 0)})
    book = {"key": doc.book_key}
    book.update(_book_row(doc))
    open_rows = _open_rows(doc.book_key, doc.customer, exclude=doc.name)
    draft = _draft_input(invoices, cash, methods, amount, open_rows)
    checked = _checked(doc.company, book, doc.customer, draft, record=doc.name)
    old_first = doc.invoices[0] if doc.invoices else None
    default = contracts.staff({"salesman": old_first.salesman if old_first else "",
                               "department": (open_rows.get(old_first.docnum) or {}).get("department", "") if old_first else ""},
                              _choices(doc.book_key)["run_department"])
    previous = {"salesman": doc.salesman or "", "department": doc.department or "",
                "default_salesman": default["salesman"], "default_department": default["department"]}
    _fill(doc, checked, _staff(doc.book_key, checked, salesman, department, previous))
    doc.draft_version = int(doc.draft_version or 0) + 1
    doc.save(ignore_permissions=True)
    _attach(doc.name, checked)
    return card(doc.name)


def _book_row(doc):
    """记录冻结的那本账套此刻的登记（位置、公司名）；电脑都不报了回只有键的一项（载荷准备时再停下）。"""
    from pearnly_core.cc_desktop import books

    row = frappe.db.get_value(COMPANY, doc.company, ["tax_id", "branch"], as_dict=True) or {}
    tax = express_contracts.tax13(row.get("tax_id"))
    for item in books.for_company(tax, row.get("branch")) if tax else []:
        if item["key"] == doc.book_key:
            return item
    return {"key": doc.book_key, "path": "", "root": "", "company": ""}


def store_voucher(filename, content) -> dict:
    """存一张转账凭证（私有、归本人、先不挂单；统一原件层配了就存原件层，id 前缀见 :data:`VOUCHER_ID_PREFIX`）：回 ``{"file", "file_url"}``，
    交给 :func:`create_draft` / :func:`set_voucher` 的 ``voucher``。"""
    import uuid

    from pearnly_core.originals import registered

    guard.require(WRITE)
    if not content:
        frappe.throw(_("This document has no content"))
    filename = str(filename or "voucher.jpg")[:140]
    stored = registered.put(VOUCHER_ID_PREFIX + (uuid.uuid4().hex + uuid.uuid4().hex)[:48], filename, content)
    if stored is None or isinstance(stored, dict):
        if isinstance(stored, dict):  # 原件层不收（坏图、超页数）
            frappe.throw(_("Invalid file"))
        stored = frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                                 "content": content}).insert(ignore_permissions=True)
    return {"file": stored.name, "file_url": stored.file_url}


def set_voucher(name, version, prefix, voucher) -> dict:
    """给草稿里一种转账补上凭证（版本 +1）。"""
    doc = _own(name, for_update=True)
    if doc.status != contracts.DRAFT:
        raise contracts.CollectionError(contracts.NOT_DRAFT, {"status": doc.status})
    methods_value = [{"prefix": row.prefix, "amount": row.amount,
                      "voucher": voucher if row.prefix == prefix else row.voucher} for row in doc.payments]
    if not any(row.prefix == prefix for row in doc.payments):
        raise contracts.CollectionError(contracts.METHOD_INVALID, {"prefix": prefix})
    return update_draft(name, version, [{"docnum": row.docnum, "amount": row.amount} for row in doc.invoices],
                        doc.cash, methods_value)


# ── 确认卡、放行、撤回、状态 ─────────────────────────────────────────────────


def _view(doc) -> dict:
    return {"name": doc.name, "version": int(doc.draft_version or 0), "status": doc.status, "company": doc.company,
            "company_name": frappe.db.get_value(COMPANY, doc.company, "company_name") or doc.company,
            "book": doc.book_label or "", "customer": doc.customer, "customer_name": doc.customer_name,
            "doc_date": str(doc.doc_date or ""), "source": doc.source,
            "invoices": [{"docnum": row.docnum, "invoice_date": str(row.invoice_date or ""),
                          "remaining": contracts.text(row.remaining or 0), "amount": contracts.text(row.amount or 0)}
                         for row in doc.invoices],
            "cash": contracts.text(doc.cash or 0),
            "methods": [{"prefix": row.prefix, "name": row.type_name, "role": row.role,
                         "amount": contracts.text(row.amount or 0), "voucher": row.voucher or ""} for row in doc.payments],
            "transfer": contracts.text(doc.transfer or 0), "discount": contracts.text(doc.discount or 0),
            "total": contracts.text(doc.total or 0), "docnum": doc.docnum or "", "reason": doc.reason or "",
            "salesman": doc.salesman or "", "department": doc.department or "",
            "balances": frappe.parse_json(doc.balances) if doc.balances else {}}


def _draft_of(doc):
    return {"invoices": [{"docnum": row.docnum, "amount": row.amount} for row in doc.invoices], "cash": doc.cash,
            "methods": [{"prefix": row.prefix, "amount": row.amount, "voucher": row.voucher} for row in doc.payments]}


def card(name) -> dict:
    """确认卡要复述的一切（公司·账套·客户·各张发票收多少·方式·折扣·合计）与版本号；``ready`` 为假时 ``problem`` 说缺什么
    （例如转账还没凭证），这时不出【确认】。"""
    doc = _own(name)
    view = _view(doc)
    view.update(ready=False, problem="")
    if doc.status == contracts.DRAFT:
        try:
            book = {"key": doc.book_key, **_book_row(doc)}
            _checked(doc.company, book, doc.customer, _draft_of(doc), record=doc.name, require_vouchers=True)
            view["ready"] = True
        except contracts.CollectionError as error:
            view.update(problem=error.code, detail=error.detail)
    return view


def release(name, version, consent) -> dict:
    """放行（LINE【确认】/ 打「ยืนยัน / 确认 / ok」、CC「写入」共用这一条）：``version`` 是确认卡展示的版本，``consent`` 是那次
    明确同意的事件（LINE 回调的事件号、CC 的用户消息序号），存进记录备查。

    回 ``{"status": "released", "card"}``；卡片之后改过 ``{"status": "changed", "version"}``（重新出卡）；不是草稿
    ``{"status": "not_draft", "card"}``；规则此刻不过（别人刚收了同一张、凭证没补）``{"status": "invalid", "code", "detail"}``。"""
    doc = _own(name, for_update=True)
    code = contracts.release_check(doc.status, doc.draft_version, version, consent)
    if code == contracts.CHANGED:
        return {"status": "changed", "version": int(doc.draft_version or 0)}
    if code == contracts.NOT_DRAFT:
        return {"status": "not_draft", "card": _view(doc)}
    if code:
        return {"status": "invalid", "code": code, "detail": {}}
    snapshot.lock(doc.book_key, [row.docnum for row in doc.invoices])  # 两人同时放行同一张：排队判，后到的看到前一笔的预留
    book = {"key": doc.book_key, **_book_row(doc)}
    try:
        checked = _checked(doc.company, book, doc.customer, _draft_of(doc), record=doc.name, require_vouchers=True)
    except contracts.CollectionError as error:
        return {"status": "invalid", "code": error.code, "detail": error.detail}
    _fill(doc, checked, {"salesman": doc.salesman or "", "department": doc.department or contracts.staff(
        checked["invoices"][0], _choices(doc.book_key)["run_department"])["department"]})
    # 单据日期按放行当天（隔了一阵才确认、跨了日也记到确认那天，不是建草稿那天）
    doc.update({"status": contracts.QUEUED, "doc_date": nowdate(), "confirmed_at": now_datetime(),
                "consent_ref": str(consent)[:140], "reason": ""})
    doc.save(ignore_permissions=True)
    from pearnly_erp_portal.collection import push

    push.send(doc.name)
    return {"status": "released", "card": _view(frappe.get_doc(DOCTYPE, doc.name))}


def withdraw(name) -> dict:
    """撤回（没被电脑领走前）：草稿、待写入、失败的可以；电脑正在写的回 ``busy``，已写入的不行（由财务在 Express 原生作废）。
    记录不删、凭证保留，标「已撤回」。"""
    doc = _own(name, for_update=True)
    if doc.status not in contracts.WITHDRAWABLE:
        raise contracts.CollectionError(contracts.NOT_WITHDRAWABLE, {"status": doc.status})
    from pearnly_erp_portal.collection import push

    if doc.job and not push.cancellable(doc.job):
        raise contracts.CollectionError(contracts.BUSY)
    doc.update({"status": contracts.WITHDRAWN, "withdrawn_by": frappe.session.user, "withdrawn_at": now_datetime()})
    doc.save(ignore_permissions=True)
    if doc.job:
        push.cancel(doc.job)
    return _view(doc)


def discard_draft(name) -> bool:
    """作废本人一张还没放行的草稿（LINE 里出了新卡、重开一笔、取消、卡片过期）：标「已撤回」，记录与凭证保留。不是草稿的不动。"""
    doc = _own(name, for_update=True)
    if doc.status != contracts.DRAFT:
        return False
    doc.update({"status": contracts.WITHDRAWN, "withdrawn_by": frappe.session.user, "withdrawn_at": now_datetime()})
    doc.save(ignore_permissions=True)
    return True


def status(name) -> dict:
    """一笔收款此刻的样子（本人的；能看收款的人看本公司的）。"""
    if not name or not frappe.db.exists(DOCTYPE, name):
        _not_permitted()
    doc = frappe.get_doc(DOCTYPE, name)
    if not can_read(doc):
        _not_permitted()
    return _view(doc)


def can_read(doc, user=None) -> bool:
    user = user or frappe.session.user
    if doc.company not in companies.allowed_companies(user):
        return False
    if guard.has(VIEW, user=user):
        return True
    return doc.collector == user and guard.has(WRITE, user=user)


def _waiting_jobs(docs) -> set:
    """这几笔里待写入、写账件此刻在等 Express 关闭的那几件（共用通道一次查完，上次回报超过 90 秒不算；只读）。"""
    from pearnly_core.cc_desktop import jobs

    names = [doc.job for doc in docs if doc.status == contracts.QUEUED and doc.job]
    return set(jobs.express_wait_many(names)) if names else set()


def express_waiting(name) -> bool:
    """这笔待写入的收款此刻是不是在等 Express 关闭（它那本账套有写账件在等，`cc_desktop.jobs.book_express_wait`）。收款员放行后
    当场的回复用它带上现成那句 ``express.error.ACCOUNT_BUSY_LOCKED``。当天第一笔放行时电脑还没去试过，回 False。"""
    from pearnly_core.cc_desktop import jobs

    row = frappe.db.get_value(DOCTYPE, name, ["status", "book_key"], as_dict=True) if name else None
    if not row or row.status != contracts.QUEUED or not row.book_key:
        return False
    return bool(jobs.book_express_wait(row.book_key).get("waiting"))


def mine(start=0, limit=20) -> dict:
    """本人最近的收款（新的在前）：``{"items": [view… + express_wait], "total"}``；``express_wait``：待写入、此刻在等 Express 关闭。"""
    guard.require(WRITE)
    filters = {"collector": frappe.session.user, "company": ["in", companies.allowed_companies() or [""]]}
    names = frappe.get_all(DOCTYPE, filters=filters, pluck="name", order_by="creation desc",
                           limit_start=max(0, int(start or 0)), limit_page_length=max(1, min(int(limit or 20), MINE_MAX)))
    from pearnly_core.desk import list_filters

    docs = [frappe.get_doc(DOCTYPE, name) for name in names]
    waiting = _waiting_jobs(docs)
    return {"items": [{**_view(doc), "express_wait": bool(doc.job and doc.job in waiting)} for doc in docs],
            "total": list_filters.count(DOCTYPE, filters, ignore_permissions=True)}


__all__ = ["book_for", "book_options", "can_read", "card", "choose_book", "companies_for", "create_draft", "customers",
           "discard_draft",
           "edit_choices", "express_waiting", "methods", "mine",
           "open_invoices", "release", "reserved", "set_voucher", "staff_choices", "status", "store_voucher", "update_draft",
           "withdraw"]
