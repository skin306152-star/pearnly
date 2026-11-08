"""代录 Express 的 ERP 这一侧（共用层 ``pearnly_core.express_operator``，``hooks.pearnly_express_operator`` 声明）。

ERP 的单在提交时就排进直写（机器产生的单固定走直写，主会话 10-26 定）；这里只列这家公司这个月**还没交进写账队列**的
采购 / 销售单（排着还没准备、停下等人的），会计可以改用代录录。

- 谁能发起：这家公司在本人可见公司里，且有这类单据的开单权（与 ERP 推送、单据上「再写一次」同一判据）；
- 放文件（A 批 1，ADR-027 代录完整）：代录对话里放进的文件走 ERP 对话收单（`integrations.intake.receive_chat`，同 LINE 收单的识别），
  每张票一条待提交记录，以 ``I:<记录>`` 出现在资料区；开录时先用网页同一个入账函数提交成 ERP 单据（`intake.submit_chat`），再交代录。
  识别中的状态照共用识别队列现读（``uploads``）。目标只有 Express（MR.ERP 执行层两产品一起等岳父账号）。
- 交单：逐张走 ERP 推送同一条路（``push.send``：要人选的照旧停在单据上），载荷加代录标记；回执照直写同一条回到单据上
  （ledger_state / ledger_docnum / ledger_reason）。写账电脑照 ERP 原来的口径（开单的人登录过 CC 的电脑）。
"""
import calendar

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_core.cc_desktop import jobs
from pearnly_erp_portal.express import contracts, push
from pearnly_erp_portal.masters import company as companies

KINDS = {"P": (push.PURCHASE, "purchase.write"), "S": (push.SALES, "sales.write")}
INTAKE = "I"  # 代录放文件识别出、还没提交的收单记录（``I:<记录>``）


def _company_ok(user, company) -> bool:
    return bool(company) and any(row.get("name") == company if isinstance(row, dict) else row == company
                                 for row in companies.allowed_companies(user))


def allowed(user, company) -> bool:
    return _company_ok(user, company) and any(guard.has(capability, user) for _doctype, capability in KINDS.values())


def _month(period: str):
    year, month = (int(part) for part in str(period).split("-")[:2])
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def sources(company, period, offset, limit) -> dict:
    """任务选的这家公司这个月的单。按任务给的公司取，不走单据列表的权限条件：那条只认本人「当前公司」，任务选的公司不是当前公司时
    一张都取不到（10-29 测试机）。同一套判据在这里自己守：公司在本人可见公司里、有开单权，员工只看本人录入的（``guard.scope_filters``）。"""
    if not _company_ok(frappe.session.user, company):
        return {"documents": [], "next_offset": None}
    first, last = _month(period)
    scope = guard.scope_filters()
    rows = []
    for prefix, (doctype, capability) in KINDS.items():
        if not guard.has(capability):
            continue
        party = "customer" if doctype == push.SALES else "supplier_name"
        # 还没交进写账队列的；加上代录交过、停在要人处理的（上一件是代录的、转人工了：代录可以再录，`push.release_operator`）
        found = frappe.get_all(doctype, filters={"company": company, "doc_date": ["between", [first, last]],
                                                 "status": push.ready(doctype),
                                                 "ledger_state": ["in", [*contracts.OPEN_STATES, contracts.FAILED]], **scope},
                               fields=["name", "doc_date", "grand_total", "system_no", party, "ledger_reason",
                                       "ledger_job"],
                               order_by="doc_date asc, name asc", limit_start=0, limit_page_length=offset + limit)
        # 带按台号码的单不交代录（代录还不会在 Express 里点 Note 录行说明，主会话 11-02）：照直写，号码写进附加说明
        units = set(frappe.db.sql_list("""SELECT DISTINCT parent FROM `tabERP Line Serial`
            WHERE parenttype = %s AND parent IN %s""", (doctype, tuple(row.name for row in found)))) if found else set()
        found = [row for row in found if row.name not in units]
        rows += [{"doc_id": f"{prefix}:{row.name}", "kind": "sales" if prefix == "S" else "purchase",
                  "group": "sales" if prefix == "S" else "purchase",
                  "date": str(row.doc_date or ""), "party": row.get(party) or "", "ref": row.system_no or row.name,
                  "total": str(row.grand_total or ""), "stopped": row.ledger_reason or ""} for row in found
                 if not row.ledger_job or _operator_held(row.ledger_job)]
    rows += _intake_rows(company, first, last)
    rows.sort(key=lambda r: (r["date"], r["doc_id"]))
    page = rows[offset:offset + limit]
    return {"documents": page, "next_offset": offset + limit if len(rows) > offset + limit else None}


# ---------------- 放文件：收单记录（``I:``）----------------

INTAKE_MAX = 200  # 一个人一家公司同时等着提交的收单记录（代录一批上限同共用层）


def intake(company, filename, content, request_key) -> dict:
    """资料区 / 输入框放进一份：交 ERP 对话收单识别（方向没说，先按采购读，`intake.receive_chat`）。回包 ``name`` 就是共用识别队列的
    reference（共用层记成这一份的 batch，页面收 ``pearnly_ocr_work`` 推送再读 ``uploads``）。"""
    from pearnly_erp_portal.integrations import intake as chat_intake

    if not _company_ok(frappe.session.user, company):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    found = chat_intake.receive_chat(company=company, direction="", filename=filename, content=content,
                                     request_key=request_key)
    return {"accepted": found.get("status") != chat_intake.FAILED, **found}


def uploads(company, period, batches) -> dict:
    """这几份此刻识别到哪了（只读）：收单记录 + 共用识别队列 → 上传卡片的状态（`express_operator.shapes.work_state`，两产品同一套）；
    识别完的带出这个文件里的每张票（``I:<记录>``：单号、日期、对方、还在不在等着录入）。别人的、不是对话来的不回。"""
    from pearnly_core.express_operator import shapes
    from pearnly_erp_portal.integrations import intake as chat_intake

    out = {}
    rows = chat_intake.chat_records(list(batches or ()))
    by_file = {}
    for row in rows:
        by_file.setdefault(row.get("file_hash") or row["record"], []).append(row)
    for batch in dict.fromkeys(str(item) for item in batches or () if item):
        mine = next((row for row in rows if row["record"] == batch), None)
        if not mine or mine.get("company") != company:
            continue
        work = frappe.db.get_value("Pearnly OCR Work", {"kind": chat_intake.CC_OCR_KIND, "reference": batch},
                                   ["status", "last_code"], as_dict=True, order_by="creation desc") or {}
        out[batch] = shapes.work_state(mine["status"], work.get("status"), work.get("last_code") or mine.get("reason") or "")
        if out[batch]["state"] == "done":
            out[batch]["documents"] = [_intake_row(row)["document"]
                                       for row in by_file.get(mine.get("file_hash") or batch, [])
                                       if row["status"] in (chat_intake.READY, chat_intake.CONFIRMED)]
    return out


def target(company) -> str:
    return "express"


def _intake_row(row) -> dict:
    document = {"doc_id": f"{INTAKE}:{row['record']}", "ref": row.get("doc_no") or "", "date": str(row.get("doc_date") or ""),
                "party": row.get("party") or "", "waiting": row["status"] != "confirmed"}
    return {"document": document, "kind": row.get("direction") or "purchase"}


INTAKE_DOCTYPE = "ERP LINE Intake"  # 同 `integrations.intake.DOCTYPE`；先查有没有，有才导入收单模块


def _intake_rows(company, first, last) -> list:
    """资料区里还等着提交的收单记录（本人、这家公司、单据日期在这个月）。"""
    names = frappe.get_all(INTAKE_DOCTYPE, filters={"source": "cc", "sent_by": frappe.session.user, "company": company,
                                                    "status": "ready"},
                           pluck="name", order_by="creation desc", limit_page_length=INTAKE_MAX)
    if not names:
        return []
    from pearnly_erp_portal.integrations import intake as chat_intake

    out = []
    for row in chat_intake.chat_records(names):
        if row["status"] != chat_intake.READY or not (first <= str(row.get("doc_date") or "") <= last):
            continue
        kind = row.get("direction") or "purchase"
        out.append({"doc_id": f"{INTAKE}:{row['record']}", "kind": kind, "group": kind, "date": str(row.get("doc_date") or ""),
                    "party": row.get("party") or "", "ref": row.get("doc_no") or "",
                    "total": str((row.get("totals") or {}).get("grand_total") or ""), "stopped": ""})
    return out


def _intake(company, doc_id):
    """``I:<记录>`` → 收单记录（本人、对话来的、这家公司的）；别的回 None。"""
    from pearnly_erp_portal.integrations import intake as chat_intake

    prefix, _sep, name = str(doc_id).partition(":")
    if prefix != INTAKE or not name:
        return None
    row = frappe.db.get_value(chat_intake.DOCTYPE, name, ["name", "source", "sent_by", "company", "direction", "status",
                                                          "purchase", "sales_document"], as_dict=True)
    if not row or row.source != chat_intake.SOURCE_CC or row.sent_by != frappe.session.user or row.company != company:
        return None
    if not guard.has(chat_intake.DIRECTION_CAPABILITY[row.direction]):
        return None
    return row


def _submitted(row):
    """收单记录提交出的 ERP 单据 ``(doctype, 名)``；还没提交回 None。"""
    name = row.purchase if row.direction == "purchase" else row.sales_document
    return ((push.PURCHASE if row.direction == "purchase" else push.SALES), name) if name else None


def pending_documents() -> list:
    """本人此刻在 ERP 版 CC「待处理」里列着的采购 / 销售单（``P:<名>`` / ``S:<名>``，与 ``express.pending`` 同一口径）：共用层
    「待处理」的代录一组按单据去重用。"""
    from pearnly_erp_portal.express import pending

    prefix = {doctype: key for key, (doctype, _capability) in KINDS.items()}
    return [f"{prefix[item['doctype']]}:{item['document']}" for item in pending._items(pending.visible())
            if item["doctype"] in prefix]


def _target(company, doc_id):
    """``P:<名>`` / ``S:<名>`` → ``(doctype, 名)``；不是这家公司的、本人没有这类单据开单权的回 None。"""
    prefix, _sep, name = str(doc_id).partition(":")
    if prefix not in KINDS or not name:
        return None
    doctype, capability = KINDS[prefix]
    if not guard.has(capability) or push.company_of(doctype, name) != company:
        return None
    owner = guard.scope_filters().get("owner")
    if owner and frappe.db.get_value(doctype, name, "owner") != owner:
        return None  # 员工只动本人录入的单（同单据页的记录范围）
    return doctype, name


def precheck(company, doc_ids) -> dict:
    """代录开录前逐张判一遍（写账前置判定 `express.precheck.collect_document`：与直写同一条准备，不写单据、不排写账件）：要人选的项
    （新商品、费用科目、对方档、账套、存货科目组、收银三项……连同可选值，商品与对方可选「新建」）一次收齐列出；准备不了的写明原因。
    回包形状不变（U1 / U2 调用方不用改），每张另带 ``precheck``（开始前确认卡片这一张的判定，共用层合成 ``card``）。"""
    from pearnly_erp_portal.express import precheck as write_precheck

    documents = []
    for doc_id in doc_ids:
        if str(doc_id).startswith(INTAKE + ":"):
            documents.append(_intake_precheck(company, doc_id))
            continue
        target = _target(company, doc_id)
        if target is None:
            documents.append({"doc_id": doc_id, "state": "blocked", "reason": "not_permitted", "message": _("Not Permitted")})
            continue
        doctype, name = target
        current = frappe.db.get_value(doctype, name, "ledger_job")
        if current and not _operator_held(current):
            documents.append({"doc_id": doc_id, "state": "blocked", "reason": "already_queued"})
            continue
        found = write_precheck.collect_document(doctype, name)
        row = with_menu(legacy_row(doc_id, found), write_precheck.menu_item(company, doctype, name))
        target = book_target(found["result"].get("book"))
        row = {**row, "target": target} if target else row
        documents.append({**row, "precheck": write_precheck.document_view(doc_id, doctype, name, found)})
    return {"documents": documents}


UNDECIDED_LINES_TEXT = "Some lines have no product yet"


def _intake_precheck(company, doc_id) -> dict:
    """一条待提交的收单记录：按它提交时的入参（`intake.build_payload`，同网页表单）判写进 Express 要人选的项（`collect_form`）。
    有定不了物料的行先停（开始前卡片问这几行在下一步加，A 批 1b）；已提交的按提交出的单据判。"""
    import json

    from pearnly_erp_portal.express import precheck as write_precheck
    from pearnly_erp_portal.integrations import intake as chat_intake

    row = _intake(company, doc_id)
    if row is None:
        return {"doc_id": doc_id, "state": "blocked", "reason": "not_permitted", "message": _("Not Permitted")}
    done = _submitted(row)
    if done:
        found = write_precheck.collect_document(*done)
        return {**legacy_row(doc_id, found), "precheck": write_precheck.document_view(doc_id, *done, found)}
    if row.status != chat_intake.READY:
        return {"doc_id": doc_id, "state": "blocked", "reason": "not_ready", "message": _("This document is no longer available")}
    doc = frappe.get_doc(chat_intake.DOCTYPE, row.name)
    result = json.loads(doc.result or "{}")
    party = result.get("supplier_name") if row.direction == "purchase" else result.get("buyer_name")
    view = {"doc_id": doc_id, "ref": result.get("doc_no") or row.name, "direction": row.direction,
            **({"party": party} if party else {})}
    asked = intake_items(company, doc, result)
    doctype = push.PURCHASE if row.direction == "purchase" else push.SALES
    found = write_precheck.collect_form(doctype, chat_intake.build_payload(doc), company)
    state, reason = write_precheck._state(found["result"])
    shown = book_target(found["result"].get("book"))
    view = {**view, **({"target": shown} if shown else {})}
    check = {**view, "state": state, **({"reason": reason} if reason else {}),
             **({"items": [*asked, *found["items"]]} if state == "check" else {})}
    legacy = legacy_row(doc_id, found)
    if legacy["state"] == "ready" and asked:
        legacy = {"doc_id": doc_id, "state": "choose", "reason": contracts.CHOICE + ":payment",
                  "needed": [{"kind": item["kind"], "subject": item["subject"], "label": item["label"],
                              "options": item.get("options") or [], "current": item.get("value") or ""} for item in asked]}
    return {**legacy, **({"target": shown} if shown else {}), "precheck": check}


# ---------------- 收单记录在开始前卡片里要人定的（A 批 1b）----------------
#
# 开始前一张卡片问完（主会话 11-08 定案）：付款（已付哪种方式 / 未付；预选网页默认「已付、现金」）、识别时定不了的行（选已有物料 /
# 新建 / 只记账；搜 ERP 物料）、有入库 / 出库行又没定仓时的仓库。归类键带记录名（``erp-intake:<记录>[:<行>]``），只管这一张，
# 不和 Express 那些项合并。答案在交单时写回收单记录（:func:`apply_intake_answers`），再用网页同一个入账函数提交。

INTAKE_SUBJECT = "erp-intake:"
UNPAID = "credit"
RECORD_ONLY = "@record"
METHOD_LABELS = {"cash": "Cash", "transfer": "Transfer", "promptpay": "PromptPay", "bank": "Bank", "cheque": "Cheque",
                 "other": "Other"}


def _methods(direction):
    from pearnly_erp_portal.integrations import line_flow

    return line_flow.PURCHASE_METHODS if direction == "purchase" else line_flow.SALES_METHODS


def _new_label(direction):
    return _("Add and receive") if direction == "purchase" else _("Add and issue")


def intake_items(company, doc, result) -> list:
    """这条收单记录在开始前卡片里的几项（卡片项的形状，共用层 `write_precheck.contracts.normalize_item`）。纯读，不写。"""
    import json

    from pearnly_core.write_precheck import contracts as card
    from pearnly_erp_portal.inventory import warehouses

    subject = INTAKE_SUBJECT + doc.name
    label = (result.get("supplier_name") if doc.direction == "purchase" else result.get("buyer_name")) \
        or result.get("doc_no") or doc.name
    value = (doc.payment_method or "cash") if doc.is_paid else UNPAID
    items = [{"kind": "payment", "subject": subject, "label": label, "value": value, "source": card.RULE,
              "direction": doc.direction,
              "options": [{"code": code, "name": _(METHOD_LABELS[code])} for code in _methods(doc.direction)]
              + [{"code": UNPAID, "name": _("Unpaid")}]}]
    chosen = json.loads(doc.line_choices or "null") or {}
    lines = result.get("lines") or []
    for index, line in enumerate(chosen.get("lines") or []):
        if not line or line.get("decided"):
            continue
        current = line.get("product") or (RECORD_ONLY if line.get("item_type") == "service" else card_new())
        name = (_new_label(doc.direction) if current == card_new() else _("Record only") if current == RECORD_ONLY
                else frappe.db.get_value("ERP Product", current, "product_name") or current)
        items.append({"kind": "item", "subject": f"{subject}:{index}", "direction": doc.direction,
                      "label": str((lines[index] if index < len(lines) else {}).get("description") or ""),
                      "value": current, "value_label": name, "source": card.RULE, "search": "own_products"})
    moving = any(line and line.get("item_type", "goods") == "goods" for line in chosen.get("lines") or [])
    if moving and not chosen.get("warehouse"):
        places = warehouses.names_of(company, include_inactive=False)
        options = [{"code": place, "name": frappe.db.get_value("ERP Warehouse", place, "warehouse_name") or place}
                   for place in places]
        only = options[0]["code"] if len(options) == 1 else ""
        items.append({"kind": "location", "subject": f"{subject}:warehouse", "label": _("Warehouse"), "value": only,
                      "source": card.RULE if only else "", "direction": doc.direction, "options": options})
    return items


def card_new():
    from pearnly_core.express_rules.push import choices as choice_rules

    return choice_rules.NEW


def apply_intake_answers(company, name, answers) -> list:
    """开始前卡片对这条收单记录答的（归类键 ``erp-intake:<记录>…``）写回记录：付款、各行物料、仓库。只认卡片给过的值（付款方式在
    可选范围里、物料是这家公司能用的、仓库是这家公司启用的）；其余答案（Express 要选的项）原样回给调用方，提交出单据后照旧记。"""
    import json

    from pearnly_erp_portal.integrations import intake as chat_intake
    from pearnly_erp_portal.inventory import warehouses

    doc = frappe.get_doc(chat_intake.DOCTYPE, name)
    subject = INTAKE_SUBJECT + name
    chosen = json.loads(doc.line_choices or "null") or {"lines": []}
    values, rest = {}, []
    for answer in answers or ():
        if not isinstance(answer, dict) or not str(answer.get("subject") or "").startswith(subject):
            rest.append(answer)
            continue
        kind, value, tail = answer.get("kind"), str(answer.get("value") or ""), str(answer["subject"])[len(subject):]
        if kind == "payment" and not tail:
            if value == UNPAID:
                values.update(is_paid=0)
            elif value in _methods(doc.direction):
                values.update(is_paid=1, payment_method=value)
            else:
                frappe.throw(_("Invalid request"))
        elif kind == "item" and tail[1:].isdigit():
            index = int(tail[1:])
            if index >= len(chosen.get("lines") or []) or not chosen["lines"][index]:
                frappe.throw(_("Invalid request"))
            if value == RECORD_ONLY:
                line = {"item_type": "service", "product": None, "new": False}
            elif value == card_new():
                line = {"item_type": "goods", "product": None, "new": True}
            elif frappe.db.exists("ERP Product", value):
                line = {"item_type": "goods", "product": value, "new": False}
            else:
                frappe.throw(_("Invalid request"))
            chosen["lines"][index] = {**chosen["lines"][index], **line, "decided": True}
        elif kind == "location" and tail == ":warehouse":
            if value not in warehouses.names_of(company, include_inactive=False):
                frappe.throw(_("Invalid request"))
            chosen["warehouse"] = value
        else:
            frappe.throw(_("Invalid request"))
    values["line_choices"] = json.dumps(chosen, ensure_ascii=False)
    frappe.db.set_value(chat_intake.DOCTYPE, name, values)
    return rest


def intake_search(company, doc_id, search, txt="", start=0, page_length=20) -> dict:
    """卡片里收单记录那几行的物料搜索（``own_products``）：这家公司能用的 ERP 物料（同物料页列表，服务端分页），第一页前面是「新建」
    「只记账」。Express 那些搜索（存货、科目、对方）按这条记录提交时会写进的账套搜（同已存的单，`precheck.search`）。"""
    from pearnly_erp_portal.express import precheck as write_precheck
    from pearnly_erp_portal.integrations import intake as chat_intake
    from pearnly_erp_portal.products import service as products

    row = _intake(company, doc_id)
    if row is None:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    start, size = max(0, int(start or 0)), max(1, min(int(page_length or 20), write_precheck.SEARCH_MAX))
    if search == "own_products":
        with companies.acting(company):
            rows = products.list_products(search=str(txt or "").strip() or None, is_active=1, start=start, limit=size + 1)
        options = [{"code": item["name"], "name": " ".join(str(part) for part in (item.get("code"), item.get("product_name"))
                                                            if part)} for item in rows]
        fixed = [{"code": card_new(), "name": _new_label(row.direction)}, {"code": RECORD_ONLY, "name": _("Record only")}] \
            if not start and not str(txt or "").strip() else []
        return {"options": fixed + options[:size], "more": len(options) > size}
    doctype = push.PURCHASE if row.direction == "purchase" else push.SALES
    doc = frappe.get_doc(chat_intake.DOCTYPE, row.name)
    found = write_precheck.collect_form(doctype, chat_intake.build_payload(doc), company)
    key = (found["result"].get("book") or {}).get("key") or ""
    return write_precheck.search_rows(key, row.direction, search, txt, start, size)


def legacy_needed(found) -> list:
    """收齐的项 → 原来「要人选的项」的形状 ``{kind, subject, label, options, current}``（单据页弹窗、代录预检用；``current`` 是预选）。"""
    from pearnly_erp_portal.express import precheck as write_precheck

    return [{"kind": item["kind"], "subject": item["subject"], "label": item.get("label") or "",
             "options": write_precheck.full_options(item, found["data"]), "current": item.get("value") or ""}
            for item in found["items"]]


def legacy_row(doc_id, found) -> dict:
    """一张单的代录预检行（改造前的形状）：单据本身不对 → blocked；收齐了要人选的项 → choose；主档还在读 → waiting；其余停下的原因
    → blocked；都没有 → ready。"""
    result = found["result"]
    hold = str(result.get("hold") or "")
    if "failed" in result:
        return {"doc_id": doc_id, "state": "blocked", "reason": result["failed"], "message": _(result["failed"])}
    if found["items"] or hold.startswith(contracts.CHOICE):
        kinds = ",".join(dict.fromkeys(item["kind"] for item in found["items"]))
        reason = contracts.CHOICE + ":" + kinds if kinds else hold
        return {"doc_id": doc_id, "state": "choose", "reason": reason, "needed": legacy_needed(found)}
    if hold == contracts.READING:
        return {"doc_id": doc_id, "state": "waiting", "reason": hold, "message": _(hold)}
    if hold:
        return {"doc_id": doc_id, "state": "blocked", "reason": hold, "message": _(hold)}
    return {"doc_id": doc_id, "state": "ready"}


def with_menu(row, menu) -> dict:
    """代录预检行加上停在子菜单那一项（评估 2026-11-05 修法 D）：还没定的列进 ``needed``（中途选那张卡片照它出下拉），原来 ready 的
    改成 choose；已记住且仍在候选里的不再问。单据本身不对、主档在读、停在别的原因的行不动。纯函数。"""
    if not menu or menu.get("value") or row.get("state") not in ("ready", "choose"):
        return row
    need = {"kind": menu["kind"], "subject": menu["subject"], "label": menu.get("label") or "",
            "options": list(menu.get("options") or []), "current": ""}
    if row["state"] == "ready":
        return {**row, "state": "choose", "reason": contracts.CHOICE + ":" + menu["kind"], "needed": [need]}
    reason = str(row.get("reason") or contracts.CHOICE)
    reason = reason + ("," if ":" in reason else ":") + menu["kind"]
    return {**row, "reason": reason, "needed": [*(row.get("needed") or []), need]}


def book_target(book) -> dict | None:
    """这张单交了会写进哪份账套（Code 版 CC B4）：准备那一步按税号、分店、单据日期选出的那份（`express.target.pick`，与交单
    同一条准备）；``writers`` 是报过能写它的电脑。没选出（没有账套、同期几份没选）回 None。纯函数。"""
    if not isinstance(book, dict) or not book.get("key"):
        return None
    return {"account_set": str(book["key"]), "account_set_title": str(book.get("label") or book["key"]),
            "account_set_path": str(book.get("path") or ""), "connection": "", "connection_title": "",
            "writers": list(book.get("writers") or [])}


def _operator_held(job_name) -> bool:
    job = jobs.find_by_name(job_name)
    return bool(job) and job.get("status") == "manual" and str(job.get("request_key") or "").startswith(push.OPERATOR_KEY)


def _remember(company, doctype, name, given, seen=None) -> None:
    """预检里人定的项按单据页「选一次」同一套核对记下（只认这一项能选的值，记在这家公司名下，`express.precheck.save_answers`；
    代录子菜单那项只认停下时的候选）。同一问题前一张已记下（例如这家公司这一年用哪套账，10-30 实测三张同问）的，这张不再问、
    不重复记。"""
    from pearnly_erp_portal.express import precheck as write_precheck

    if not isinstance(given, list):
        return
    write_precheck.save_answers(company, doctype, name, given, seen)


def submit(company, doc_ids, batch, computer, answers=None) -> list:
    results = []
    for index, doc_id in enumerate(doc_ids, start=1):
        given = (answers or {}).get(doc_id)
        if str(doc_id).startswith(INTAKE + ":"):
            target = _submit_intake(company, doc_id, given)
            if isinstance(target, dict):
                results.append(target)
                continue
            if target:
                target, given = target[:2], target[2]  # 收单记录那几项已写回记录；Express 的项记到提交出的单据上
        else:
            target = _target(company, doc_id)
        if target is None:
            results.append({"doc_id": doc_id, "status": "not_permitted", "reason": ""})
            continue
        doctype, name = target
        if given:
            _remember(company, doctype, name, given)
            frappe.db.commit()
        push.release_operator(doctype, name)
        result = push.send(doctype, name, operator={**batch, "index": index, "doc": doc_id, "company": company}) or {}
        results.append({"doc_id": doc_id, "status": result.get("status") or "", "reason": result.get("reason") or ""})
    return results


def _submit_intake(company, doc_id, answers=None):
    """开录时把收单记录提交成 ERP 单据（网页同一个入账函数，请求号按记录固定、只入一次），回 ``(doctype, 名, 其余答案)`` 交代录；
    开始前卡片对这条记录答的先写回记录（:func:`apply_intake_answers`），还有定不了的行不提交；提交不了回这一张的结果行（原因原样）。"""
    import json

    from pearnly_erp_portal.integrations import intake as chat_intake

    row = _intake(company, doc_id)
    if row is None:
        return {"doc_id": doc_id, "status": "not_permitted", "reason": ""}
    rest = answers or []
    if not _submitted(row):
        try:
            rest = apply_intake_answers(company, row.name, answers)
            chosen = json.loads(frappe.db.get_value(chat_intake.DOCTYPE, row.name, "line_choices") or "null") or {}
            if any(line and not line.get("decided") for line in chosen.get("lines") or []):
                frappe.db.commit()
                return {"doc_id": doc_id, "status": "failed", "reason": _(UNDECIDED_LINES_TEXT)}
            chat_intake.submit_chat(row.name)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"doc_id": doc_id, "status": "failed", "reason": str(error)[:300]}
        row = _intake(company, doc_id)
    done = _submitted(row) if row else None
    return (*done, rest) if done else None


def status(company, doc_ids) -> list:
    """CC 重启后恢复出来的任务，以站点为准收尾（共用层 ``status``）：按单据的 Express 写账状态——已写入 → 已入账（带单号）、
    排着写账件 → 仍在队列、停住 / 失败 / 对不上 → 未完成（带原因）、没排或已撤回、人标了手工处理 → 已取消；不是这家公司、
    本人没权限的 → unknown。"""
    out = []
    for doc_id in doc_ids:
        if str(doc_id).startswith(INTAKE + ":"):
            found = _intake(company, doc_id)
            target = _submitted(found) if found else None
            if found and not target:
                out.append({"doc_id": doc_id, "state": "cancelled", "docnum": "", "reason": ""})  # 还没提交、没交代录
                continue
        else:
            target = _target(company, doc_id)
        row = frappe.db.get_value(target[0], target[1], ["ledger_state", "ledger_docnum", "ledger_reason", "ledger_job"],
                                  as_dict=True) if target else None
        if not row:
            out.append({"doc_id": doc_id, "state": "unknown", "docnum": "", "reason": ""})
            continue
        if row.ledger_state == contracts.WRITTEN:
            state = "saved"
        elif row.ledger_state == contracts.QUEUED and row.ledger_job:
            state = "queued"
        elif row.ledger_state in (contracts.HELD, contracts.FAILED, contracts.MISMATCH):
            state = "not_done"
        else:
            state = "cancelled"
        out.append({"doc_id": doc_id, "state": state, "docnum": (row.ledger_docnum or "") if state == "saved" else "",
                    "reason": (row.ledger_reason or "") if state == "not_done" else ""})
    return out
