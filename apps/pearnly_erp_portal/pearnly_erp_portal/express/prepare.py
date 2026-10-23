"""一张 ERP 单 → 冻结好的 Express 写账载荷（ADR-025）。写账规则是共用的那一套（`pearnly_core.express_rules`，与 Cowork 推送
同一份），这里只备齐它要的输入：

- 票面：`contracts.pages`（方向、现 / 赊、对方档码、逐行存货码与仓库、货 / 服务都写明）；
- 账套：`target.pick` 选出的那份（路径、Express 根目录、登记行号、公司、6 个默认科目），载荷里的位置领单时再换成领取那台电脑
  的写法；
- 账套资料：最近一次读回的主档（`facts`，读的时候顺手请电脑读一次，读好了再准备）；
- 要人拿主意的项：写账规则判（`express_rules.push.choices.resolve`），选过的按公司记着（`choices`）；还没选的、同期两份账套、
  建第一个库存品不知挂哪个存货科目组：停下，在 ERP 单据上选一次。

回 ``{"payload", "book"}``（准备好了）或 ``{"hold": 原因码, "needed": [...]}``（停下）或 ``{"failed": 原因码}``（单据本身不对）。
不排队、不写库（读主档的请求除外，交给 `masters.ensure`）。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe

from pearnly_core.express_rules.push import choices as choice_rules
from pearnly_core.express_rules.push import stock_acc_group
from pearnly_core.express_rules.push.prepare import prepare_express
from pearnly_core.express_rules.push.preflight import ExpressContext

from pearnly_erp_portal.express import choices, contracts, facts, target

SOURCE = "erp_desk"
_NO_MAPPINGS = {"accounts": [], "clients": []}


def _config(book, data, company_name):
    reported = facts.reported(data)
    mapping = {**facts.mapping(data), **book.get("mapping")}
    config = {"account_set": book["path"], "account_dir": book["path"], "express_root": book.get("root") or "",
              "account_company": book.get("company") or "", "account_set_row": 0,
              # 写账规则的账套白名单：这一份、能写（领单时再按领取那台电脑核一遍）
              "reported_account_sets": [{"path": book["path"], "writable": True, "root": book.get("root") or ""}],
              **mapping, **reported}
    group = choices.chosen(company_name, choices.STOCK_GROUP, book["key"])
    if group:
        config["stock_acccod"] = group
    return config


def _needed_book(picked, company_name):
    return [{"kind": choices.ACCOUNT_SET, "subject": picked["subject"], "label": company_name,
             "options": picked["options"], "current": ""}]


def _needed_group(config, book):
    groups = stock_acc_group.fit_stock_acc_groups(config)
    return [{"kind": choices.STOCK_GROUP, "subject": book["key"], "label": book.get("label") or "",
             "options": [{"code": row["acccod"], "name": row.get("name") or ""} for row in groups], "current": ""}]


def _current_codes(pages, config):
    """明细行的存货码（ERP 商品上记的 Express 编码）这份账套里已经没有了（Express 改了 / 删了，ERP 还没同步）：去掉这个码，
    交给写账规则按品名认，认不出就停下让人在 ERP 里选（不把认不出的码交给电脑，写一次失败一次）。没读到存货表时不核。"""
    codes = {str(row.get("code") or "").strip() for row in config.get("reported_products") or []
             if str(row.get("kind") or "stock") == "stock"}
    if not codes:
        return pages
    for item in pages[0]["fields"]["items"]:
        if item["posting_kind"] == "stock" and item["code"] and item["code"] not in codes:
            item["code"] = ""
    return pages


# 电脑回报这几种（对方档有疑似重复 / 同税号分店认不准）：重试结论一样，要在 ERP 里选一次现成的对方档
PARTY_CODES = frozenset({"CUSTOMER_DUP_SUSPECTED", "SUPPLIER_DUP_SUSPECTED", "CUSTOMER_BRANCH_AMBIGUOUS",
                         "SUPPLIER_BRANCH_AMBIGUOUS"})


def party_needed(doc, company_row):
    """写进去被电脑以对方档为由拦下后，让人选的那一项（``kind = party``）：这份账套里同税号的对方档，没税号按名称相近的，
    都没有就整张对方档表。选的值按公司记住（与写账规则问对方档同一个归类键），再准备时照选的写。读不到账套资料回 []。"""
    doc = contracts.checked(doc)
    picked = target.pick(company_row, doc["date"])
    data = facts.load(picked["book"]["key"]) if picked.get("book") else None
    if data is None:
        return []
    party = doc["party"]
    rows = facts.reported(data).get("reported_customers" if doc["direction"] == "sales" else "reported_suppliers") or []
    tax, name = choice_rules.digits(party["tax_id"]), choice_rules.norm_name(party["name"])
    same = [row for row in rows if len(tax) == 13 and choice_rules.digits(row.get("tax_id")) == tax]
    if not same and name:
        same = [row for row in rows if name in choice_rules.norm_name(row.get("name"))
                or (choice_rules.norm_name(row.get("name")) or "\0") in name]
    options = [{"code": row["code"], "name": row.get("name") or "", "branch": choice_rules.branch5(row.get("branch"))}
               for row in (same or rows)[:2000]]
    subject = choice_rules.party_subject(doc["direction"], party["tax_id"], party["branch"], party["name"])
    # existing_only：只在现成的档里选（再建一个新档电脑会照样以疑似重复拦下）
    return [{"kind": "party", "subject": subject, "label": party["name"], "options": options, "current": "",
             "existing_only": True}]


def prepare(doc, company_row, ensure_masters):
    """``doc``：`push.document` 出的单；``company_row``：ERP 公司（name、tax_id、branch、company_name）；
    ``ensure_masters(company, book)``：这份账套还没读过主档时请电脑读（`masters.ensure`）。"""
    try:
        doc = contracts.checked(doc)
    except contracts.DocumentError as error:
        return {"failed": error.code}
    picked = target.pick(company_row, doc["date"])
    company_name = company_row.get("company_name") or company_row["name"]
    if picked["reason"] == contracts.SEVERAL_BOOKS:
        return {"hold": contracts.CHOICE + ":" + choices.ACCOUNT_SET, "needed": _needed_book(picked, company_name)}
    if picked["reason"]:
        return {"hold": picked["reason"], "book": picked.get("book")}
    book = picked["book"]
    data = facts.load(book["key"])
    if data is None:
        ensure_masters(company_row["name"], book)
        return {"hold": contracts.READING, "book": book}
    config = _config(book, data, company_row["name"])
    pages = _current_codes(contracts.pages(doc), config)
    decision = choice_rules.resolve(pages[0]["fields"], direction=doc["direction"], posting_kind=None, config=config,
                                    remembered=choices.remembered(company_row["name"]))
    if decision["needed"]:
        kinds = ",".join(dict.fromkeys(item["kind"] for item in decision["needed"]))
        return {"hold": contracts.CHOICE + ":" + kinds, "needed": decision["needed"], "book": book}
    config["choices"] = decision["applied"]
    history = {"id": doc["ref"], "source": SOURCE, "direction": doc["direction"], "pages": pages}
    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    prepared = prepare_express({"adapter": "express", "config": config}, history, context=ExpressContext(
        enabled=True, today=today, own_tax_id=doc["company"]["tax_id"], tenant_id=frappe.local.site,
        mappings=dict(_NO_MAPPINGS), account_tax_id=book.get("tax_id") or ""))
    if prepared.disposition == "ready":
        return {"payload": prepared.payload, "book": book}
    if prepared.reason == stock_acc_group.REASON_ACC_GROUP_REQUIRED:
        return {"hold": contracts.CHOICE + ":" + choices.STOCK_GROUP, "needed": _needed_group(config, book),
                "book": book}
    return {"hold": str(prepared.reason or "express.not_ready")[:200], "book": book}
