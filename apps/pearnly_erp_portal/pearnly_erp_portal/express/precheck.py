"""ERP 这一侧的写账前置判定（开始前确认卡片，共用层 `pearnly_core.write_precheck`，`hooks.pearnly_write_precheck` 声明；评估
`docs/work/2026-11-04-precheck-card.md` 7.2 的 W3）。

一张单写进 Express 前要人定的项，一次收齐：账套（同期几份）、写账规则的选择项（对方档、存货、费用科目、小票税额、服务品口径、
收银的收款 / 退货科目）、存货科目组、收银的增值税登记户与免税商品写法、库存单科目。

- **收齐的做法**（:func:`collect`）：照写入同一条准备（`push._prepare_body`）判一遍；停在要人选的项上时，把这些项的预选值（或人刚答
  的值；都没有的临时按一个可选值试，`_probe`）当作选过（`choices.assuming`，只在这次判定里、不写库），再判一遍，直到准备走完或不再
  多出新项。真正写入（`push.send`）不经这里，顺序、判据、载荷都不变。
- **只问缺了就没法正确写入的**：已经按公司记住的不出题（写账准备本来就照记住的写），Express 与 MR.ERP 都不拦的不收（用户 11-04）。
- **每项**：小列表（≤ :data:`SMALL` 条，同共用层 ``contracts.SMALL_LIST``）直接给选项，预选的选项排第一、带一句依据 ``note``（「推荐」
  由共用层按预选值标）；科目表、存货、整张对方表不给选项，只给预选值、``value_label`` 与搜索类型（:func:`search` 服务端分页）。
  预选只是预选：要人在卡片上答了才记住（主会话 11-04 Q2）。
- **单号**：``P:`` 采购、``S:`` 销售（同代录）、``D:`` 收银日汇总 / 退货单、``T:`` 出入库单、``C:`` 盘点。
- **权限**：这家公司在本人可见公司里、有这类单据的开单权（同单据页「选一次」`api.answer`），员工只看本人录入的；别人的单不回、
  不报存在与否。记下的值按单据页同一套核对（只认这一项能选的值）。
- **代录停在定制菜单的子菜单**（原因 menu_choice；这张单当前那件代录写账件转了人工，回执 ``meta.operator`` 带单种与候选，评估
  2026-11-05 修法 D）：判定里多一项 ``operator_form``（归类键 = 单据种类，选项 = 候选菜单文字，:func:`menu_item`），代录预检原来的
  回包也列进 ``needed``；记住（:func:`save_answers`）单开一支：值须是候选之一，记进这家公司的选择（对象 = 那件写账件的账套 | 单种），
  再交代录时载荷带 ``operator.form_choice``（`push.send`）。写账准备与写入路径不经这一项。
"""
import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_core.cc_desktop import jobs
from pearnly_core.express_operator import menu_choice
from pearnly_core.express_rules.contracts import codes as code_rules
from pearnly_core.express_rules.contracts import pos as pos_contract
from pearnly_core.express_rules.push import common as receipt_rules
from pearnly_core.express_rules.push import choices as choice_rules
from pearnly_core.express_rules.push import service_vat as service_vat_rules
from pearnly_core.express_rules.targets import account_match
from pearnly_core.write_precheck import contracts as card
from pearnly_core.write_precheck import preselect

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.express import choices, contracts, conversation, facts, pending, pos_day, printed, push, stock_entry, target
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.mrerp import choices as mrerp_choices, contracts as mrerp_contracts, push as mrerp_push

PREFIXES = pending.PRECHECK_PREFIXES  # 单号前缀（待处理也按它给要人选的行带单号）
WRITE = {push.PURCHASE: "purchase.write", push.SALES: "sales.write", push.POS_DAY: "cashier.write",
         push.STOCK: "stock.write", push.COUNT: "count.write"}  # 同 `api.CAPABILITIES` 的开单权
ROUNDS = 6        # 收齐最多判几遍：账套 → 收银登记户 → 免税写法 → 选择项 → 存货科目组，留余量
SMALL = 20        # 选项不多于这么多条直接给，多了走搜索（AGENTS「列表、选择与搜索」）
SEARCH_MAX = 50   # 搜索一页最多几条
SEARCH = {"item": "products", "account": "accounts", "receipt": "accounts", pos_contract.REFUND_KIND: "accounts",
          "party": "parties"}
NOT_PERMITTED = "not_permitted"
ALREADY_QUEUED = "already_queued"
WRITTEN = "already_written"
NO_ACCOUNT_SET = "no_account_set"  # 这家没有能写这张单的账套（`contracts.NO_BOOK`）：如实报，不归「等电脑」（A 线 11-12 A9）
# 单据日期不在本对话账套的会计期间里（`contracts.PERIOD_OUTSIDE`，Express 会拒）：如实报，码同 Cowork 推送
PERIOD_MISMATCH = account_match.PERIOD_MISMATCH

NOTE_SAME_BRANCH = "express.precheck.note.sameBranch"    # 同税号、同分店的档
NOTE_ONLY_RECORD = "express.precheck.note.onlyRecord"    # 这个税号只有这一个档
NOTE_NOT_IN_BOOK = "express.precheck.note.notInBook"     # 账套里还没有
NOTE_VAT_EXPENSE = "express.precheck.note.vatExpense"    # 小票、简易税票的税额一般不抵进项
NOTE_WRITABLE = "express.precheck.note.writable"         # 有电脑能写的那份
NOTE_ACCOUNT_NAME = "express.precheck.note.accountName"  # 科目名称对得上
NOTE_MOST_USED = "express.precheck.note.mostUsedGroup"   # 账套里挂得最多的合格组
NOTE_BOOK_BASIS = "express.precheck.note.bookBasis"      # 这本账套各家供应商的口径
NOTE_VAT_SALES = "express.precheck.note.vatSales"        # 税号 13 位，卖过带税的商品
NOTE_SPLIT = "express.precheck.note.splitExempt"         # 免税商品按行不计税


# ---------------- 单号与权限 ----------------


doc_id_of = pending.precheck_id


def _company_ok(company, user=None) -> bool:
    return bool(company) and any((row.get("name") if isinstance(row, dict) else row) == company
                                 for row in companies.allowed_companies(user or frappe.session.user))


def allowed(user, company) -> bool:
    """共用层三个接口（check / answer / search）先过的守卫：这家公司在本人可见公司里，且有任一类写账本单据的开单权（采购、销售、
    收银、出入库、盘点）。每张单另在 :func:`resolve_target` 按这类单据核。"""
    if _collection().allowed(user, company):
        return True  # 收款员在 CC 里录的收款也进写入计划（`collection.plan`，收款第 4 步）
    return bool(user) and user not in ("Guest",) and _company_ok(company, user) \
        and any(guard.has(capability, user) for capability in WRITE.values())


def _collection():
    """收款的判定（``R:`` 单号，`collection.plan`）。用到时才导入：收款域依赖本包，反过来不在导入时依赖。"""
    from pearnly_erp_portal.collection import plan

    return plan


# 写账判定里本产品认的编号种类（:func:`target_of`）：单据（采购 ``P:`` / 销售 ``S:`` / 收银日汇总 ``D:`` / 出入库 ``T:`` / 盘点 ``C:``）、
# 还没提交的收单记录（``I:``：CC 代录放进来的与 LINE 收单出卡前提问的，`operator.judged_intake`）、收款（``R:``，`collection.plan`）。新加一种在这里登记，
# check / remember / search / resume 都经 :func:`target_of`，不在各函数里各判一次（11-16 H3：``I:`` 只接进了代录钩子）。
INTAKE_PREFIX = "I"
DOCUMENT, INTAKE, COLLECTION, MISSING, UNKNOWN = "document", "intake", "collection", "missing", "unknown"
# 编号种类本产品不认（契约缺口）：写账判定回这个原因（给模型、写入计划的跳过原因）并记错误日志，与「不是本人的单」（不回、不报存在与否）分开
UNKNOWN_DOCUMENT, UNKNOWN_LOG = contracts.UNKNOWN_DOCUMENT, contracts.UNKNOWN_DOCUMENT_LOG


def target_of(company, doc_id) -> tuple:
    """一张单的编号 → ``(种类, 值)``：``(DOCUMENT, (doctype, 名))``、``(INTAKE, 收单记录行)``、``(COLLECTION, 编号)``；本人不能写的、
    找不到的 ``(MISSING, None)``；编号种类本产品不认 ``(UNKNOWN, None)``。"""
    text = str(doc_id or "")
    prefix, _sep, name = text.partition(":")
    if _collection().mine(text):
        return COLLECTION, text
    if prefix == INTAKE_PREFIX and name:
        from pearnly_erp_portal.express import operator

        row = operator.judged_intake(company, text)  # CC 对话放进来的，与 LINE 收单出卡前提问的（11-19）
        return (INTAKE, row) if row else (MISSING, None)
    if prefix in PREFIXES and name:
        found = resolve_target(company, text)
        return (DOCUMENT, found) if found else (MISSING, None)
    return UNKNOWN, None


def _unknown(company, doc_id) -> None:
    from pearnly_erp_portal.express import operator

    operator.log_unknown(company, doc_id)


def resolve_target(company, doc_id):
    """``P:<名>`` 等 → ``(doctype, 名)``；不是这家公司的、本人没有这类单据开单权的、员工不是本人录入的回 None。"""
    prefix, _sep, name = str(doc_id or "").partition(":")
    doctype = PREFIXES.get(prefix)
    if not doctype or not name or not guard.has(WRITE[doctype]) or push.company_of(doctype, name) != company:
        return None
    owner = guard.scope_filters().get("owner")
    if owner and frappe.db.get_value(doctype, name, "owner") != owner:
        return None
    return doctype, name


# ---------------- 收齐 ----------------


def collect(doctype, company, make_body, given=None, name="") -> dict:
    """照写入同一条准备判这张单，要人定的项一次收齐。``make_body()`` 每遍出一份新的单据内容（收银日汇总的内容随登记户、免税写法
    变，要在临时选过的范围里重出）；``given``：``{kind: {subject: value}}``，人刚答的值（优先于预选）；``name``：已存的单的单据名
    （收银日汇总按它看卖过的商品）。

    回 ``{"result", "items", "data"}``：``result`` 是最后一遍准备的结论（同 `push.prepare_only`：``payload`` / ``hold`` /
    ``failed``）；``items`` 是收齐的项（卡片项的形状，带 ``value`` / ``source`` / 选项或搜索类型）；``data`` 是账套资料。"""
    company_row = push._company(company)
    given = {kind: dict(values) for kind, values in (given or {}).items() if isinstance(values, dict)}
    assumed = {}  # 只在一项冒出来时才按人答的 / 预选 / 试探的值往下算：先放进去的值会让这一项不再被问，也就核不了
    items, data, result = {}, None, {}
    for _round in range(ROUNDS):
        with choices.assuming(company, assumed):
            body = make_body()
            if doctype == push.POS_DAY and not body.get("hold") and (body.get("net_negative") or body.get("empty")):
                # 同 `push.send`：冲减后为负数停下交人；一天全退（全部为 0）Express 里什么都不用写
                stop = {"hold": pos_day.NET_NEGATIVE} if body.get("net_negative") else {}
                return {"result": stop, "items": list(items.values()), "data": data}
            result, _vat_fields, unmapped = push._prepare_body(doctype, company, copy.deepcopy(body))
        hold = str(result.get("hold") or "")
        if not hold.startswith(contracts.CHOICE):
            if unmapped and "failed" not in result and "hold" not in result:
                result = {"hold": contracts.WAREHOUSE_UNMAPPED, "book": result.get("book") or {}}
            break
        book = result.get("book") or {}
        data = facts.load(book["key"]) if book.get("key") else data
        progressed = False
        for raw in result.get("needed") or []:
            key = (raw.get("kind"), raw.get("subject"))
            if not key[0] or not key[1] or key in items:
                continue
            item = _item(raw, data, company_row, body, doctype, name)
            items[key] = item
            value = str((given.get(key[0]) or {}).get(key[1]) or item["value"] or _probe(item, raw, data))
            if value:
                assumed.setdefault(key[0], {})[key[1]] = value
                progressed = True
        if not progressed:
            break
    return {"result": result, "items": list(items.values()), "data": data}


def collect_document(doctype, name, given=None) -> dict:
    """一张已存的单（同 `push.prepare_only` 的准备）。票面合计对不上之类停在单据本身的，不往下判。"""
    stop = printed.hold(doctype, name)
    if stop:
        return {"result": {"hold": stop}, "items": [], "data": None}
    company = push.company_of(doctype, name)
    found = collect(doctype, company, lambda: push.document(doctype, name), given, name)
    if found["items"]:
        return found
    return _with_blocked(doctype, name, company, found)


def _with_blocked(doctype, name, company, found) -> dict:
    """写账规则这遍判不出要选的、但上次电脑写的时候以「要人选一次」拦下了（对方档疑似重复 / 分店认不准、存货科目组、服务品进项
    口径，`api.blocked_needed`）：把那一项照单据页同一份选项列进卡片，选了记住、再交（11-16 真机 H5：代录对话里只剩「重试」）。"""
    from pearnly_erp_portal.express import api

    blocked = api.blocked_needed(doctype, name)
    if not blocked:
        return found
    book = (found.get("result") or {}).get("book") or {}
    data = found.get("data") or (facts.load(book["key"]) if book.get("key") else None)
    body = push.document(doctype, name)
    company_row = push._company(company)
    items = [_unset(_item(raw, data, company_row, body, doctype, name))  # loop-query: 电脑拦下的只有一项（对方档 / 存货组 / 服务口径），有界
             for raw in blocked]
    return {**found, "data": data, "items": items}


def _unset(item) -> dict:
    """被拦的题不带当前值（推荐项照旧排第一、带依据）：带了，对话里的写入计划按「预选项没答也照预选写」放行，可这张单要人点一次
    记下（`api._unanswered` 认停下之后答的）才会再写，直写出口照旧不交、又卡住（11-26 实验室流程 erp.cc.blocked-answered-once）。
    人点了就记下，下一遍不再出这道题。"""
    return {**{key: value for key, value in item.items() if key != "value_label"}, "value": "", "source": ""}


def collect_form(doctype, payload, company, given=None) -> dict:
    """提交前表单此刻的内容（不存，`api.preflight`）：出入库单按库存单准备，其余按发票准备。"""
    if doctype == push.STOCK:
        body = stock_entry.form_body(payload)
    else:
        body = push.form_document(doctype, payload, company)
    return collect(doctype, company, lambda: copy.deepcopy(body), given)


# ---------------- 一项：选项与预选 ----------------


def _probe(item, raw, data) -> str:
    """没有预选的项，只为看清后面几道还要问什么，判定时临时按一个可选值往下算（不当预选给人看、不记）。是不是增值税登记户按
    「不是」试：「是」才会多出免税商品写法那一问，不该先问。"""
    if item["kind"] == pos_day.VAT_REGISTERED:
        return companies.VAT_NO
    rows = item.get("options") or full_options(raw, data)
    return next((row["code"] for row in rows if row.get("code")), "")


def _direction(doctype, body, raw) -> str:
    if raw.get("kind") == "party" or str(raw.get("subject") or "").startswith(("sales|", "purchase|")):
        return "sales" if str(raw.get("subject") or "").startswith("sales|") else "purchase"
    return "sales" if push.DIRECTIONS.get(doctype) == "sales" else "purchase"


def full_options(raw, data) -> list:
    """一项能选的全部值（同单据页「选一次」的范围，`api._options`）。"""
    from pearnly_erp_portal.express import api

    return api._options(raw, data)


def _option(row, note="") -> dict:
    out = {"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
    if row.get("branch"):
        out["branch"] = row["branch"]
    if note:
        out["note"] = _(note)
    return out


def _item(raw, data, company_row, body, doctype, name="") -> dict:
    kind, subject = raw["kind"], raw["subject"]
    rows = full_options(raw, data)
    value, source, note = _preselect(raw, rows, data, company_row, body, doctype, name)
    if value and value not in {row["code"] for row in rows}:
        value, source, note = "", "", ""
    item = {"kind": kind, "subject": subject, "label": str(raw.get("label") or ""), "value": value, "source": source,
            "direction": _direction(doctype, body, raw)}
    chosen = next((row for row in rows if row["code"] == value), None) if value else None
    if chosen:
        item["value_label"] = chosen.get("name") or ""
    if kind in SEARCH and len(rows) > SMALL:
        item["search"] = SEARCH[kind]
        return item
    first = [_option(chosen, note)] if chosen else []
    item["options"] = first + [_option(row) for row in rows if row is not chosen]
    return item


def _accounts(data) -> list:
    return facts.reported(data).get("reported_accounts") or []


def _preselect(raw, rows, data, company_row, body, doctype="", name=""):
    """没有记忆时的预选 → ``(值, 来源, 依据词条)``；没把握回空。规则在共用层（`write_precheck.preselect`）。"""
    kind, subject = raw["kind"], raw["subject"]
    if kind == choices.ACCOUNT_SET:
        found = target.current(company_row, body.get("date") or "", conversation.wanted(company_row)) \
            if body.get("date") else None
        codes = {row["code"] for row in rows}
        if found and found.get("key") in codes and found.get("writable"):
            return found["key"], card.RULE, NOTE_WRITABLE
        return "", "", ""
    if kind == "party":
        branch = (str(subject).split("|") + ["", "", ""])[2]
        same = [row for row in raw.get("options") or () if row.get("code")]
        value = preselect.party(same, None, branch)
        if value == choice_rules.NEW:
            return value, card.RULE, NOTE_NOT_IN_BOOK
        exact = [row for row in same if choice_rules.branch5(row.get("branch")) == choice_rules.branch5(branch)]
        return (value, card.RULE, NOTE_SAME_BRANCH if len(exact) == 1 else NOTE_ONLY_RECORD) if value else ("", "", "")
    if kind == "item":
        return choice_rules.NEW, card.RULE, NOTE_NOT_IN_BOOK
    if kind == "vat":
        return preselect.vat(), card.RULE, NOTE_VAT_EXPENSE
    if kind == "receipt":
        value = preselect.receipt_account(raw.get("label"), _accounts(data))
        return (value, card.RULE, NOTE_ACCOUNT_NAME) if value else ("", "", "")
    if kind == pos_contract.REFUND_KIND:
        value = preselect.refund_account(_accounts(data))
        return (value, card.RULE, NOTE_ACCOUNT_NAME) if value else ("", "", "")
    if kind == service_vat_rules.KIND:
        suppliers = facts.reported(data).get("reported_suppliers") or []
        return preselect.service_vat(row.get("srv") for row in suppliers if row.get("srv")), card.RULE, NOTE_BOOK_BASIS
    if kind == choices.STOCK_GROUP:
        groups = facts.reported(data).get("reported_stock_acc_groups") or []
        counts = {str(row.get("acccod") or "").strip(): row.get("used_by") or 0 for row in groups if isinstance(row, dict)}
        value = preselect.most_used(counts, [row["code"] for row in rows])
        return (value, card.HISTORY, NOTE_MOST_USED) if value else ("", "", "")
    if kind == pos_day.VAT_REGISTERED:
        value = preselect.vat_registered(company_row.get("tax_id"), doctype == push.POS_DAY and _taxed_sales(name))
        return (value, card.RULE, NOTE_VAT_SALES) if value else ("", "", "")
    if kind == pos_day.EXEMPT_MODE:
        return pos_day.EXEMPT_SPLIT, card.RULE, NOTE_SPLIT
    return "", "", ""


def _taxed_sales(name) -> bool:
    """这张收银日汇总里卖过勾了「คิด VAT」的商品（判是不是增值税登记户的依据之一）。"""
    if not name:
        return False
    return bool(frappe.db.sql("""SELECT 1 FROM `tabERP Sale Line` l JOIN `tabERP Sale` s ON s.name = l.parent
        AND l.parenttype = 'ERP Sale' JOIN `tabERP Product` p ON p.name = l.product
        WHERE s.express_summary = %s AND p.vat_applicable = 1 LIMIT 1""", (name,)))


# ---------------- 一张单 → 卡片的输入 ----------------


def _state(result):
    """准备的结论 → 卡片里这张单的状态与原因。"""
    hold = str(result.get("hold") or "")
    if "failed" in result:
        return card.NOT_WRITABLE, str(result["failed"])
    if hold == contracts.NO_BOOK:
        return card.NOT_WRITABLE, NO_ACCOUNT_SET
    if hold == contracts.PERIOD_OUTSIDE:
        return card.NOT_WRITABLE, PERIOD_MISMATCH
    if hold and not hold.startswith(contracts.CHOICE):
        return (card.WAITING if hold in contracts.RETRY_REASONS else card.NOT_WRITABLE), hold
    return card.CHECK, ""


def _head(doctype, name) -> dict:
    kind = {push.PURCHASE: ("system_no", "supplier_name"), push.SALES: ("system_no", "buyer_name"),
            push.POS_DAY: ("name", ""), push.STOCK: ("doc_no", ""), push.COUNT: ("title", "")}[doctype]
    fields = ["name", "ledger_state", "ledger_job", kind[0]] + ([kind[1]] if kind[1] else [])
    row = frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
    return {"ref": str(row.get(kind[0]) or name), "party": str(row.get(kind[1]) or "") if kind[1] else "",
            "ledger_state": row.get("ledger_state") or "", "ledger_job": row.get("ledger_job") or ""}


def document_view(doc_id, doctype, name, found=None) -> dict:
    """卡片的一张单（`write_precheck.contracts.merge` 的输入）。已写进去的、已在别的写账件里的不再判；上次结果不明的要人核对。"""
    head = _head(doctype, name)
    view = {"doc_id": doc_id, "ref": head["ref"], "direction": "sales" if push.DIRECTIONS[doctype] == "sales" else "purchase"}
    if head["party"]:
        view["party"] = head["party"]
    if head["ledger_state"] in (contracts.WRITTEN, contracts.HANDLED):
        return {**view, "state": card.NOT_WRITABLE, "reason": WRITTEN}
    job = jobs.find_by_name(head["ledger_job"]) if head["ledger_job"] else None
    if job and job.get("status") == "manual" and job.get("reason") == jobs.LOST:
        return {**view, "state": card.UNKNOWN, "reason": jobs.LOST}
    if job and job.get("status") not in ("manual", "cancelled"):
        return {**view, "state": card.NOT_WRITABLE, "reason": ALREADY_QUEUED}
    found = found if found is not None else collect_document(doctype, name)
    state, reason = _state(found["result"])
    book = found["result"].get("book") or {}
    from pearnly_erp_portal.express import operator

    shown = operator.book_target(book)
    if shown:
        view["target"] = shown
    if state != card.CHECK:
        return {**view, "state": state, "reason": reason}
    # 停在子菜单的只有代录交过、转了人工的采购 / 销售单（代录只录这两类）
    menu = menu_item(push.company_of(doctype, name), doctype, name) \
        if doctype in (push.PURCHASE, push.SALES) and job else None
    dialog = dialog_item(push.company_of(doctype, name), doctype, name) \
        if doctype in (push.PURCHASE, push.SALES) and job else None
    items = [*found["items"], *[{**extra, "direction": view["direction"]} for extra in (menu, dialog) if extra]]
    return {**view, "state": card.CHECK, "items": items}


# ---------------- 代录停在定制菜单的子菜单 ----------------


OPERATOR_FORM = choices.OPERATOR_FORM


def menu_stop(doctype, name) -> dict:
    """这张单停在定制菜单的子菜单：单据当前那件写账件是代录交的、转了人工，电脑存下的回执 ``meta.operator`` 带单种与候选（共用写账
    通道原样存回执，读时过同一个净化 `express_rules.push.common.clean_operator_meta`，再按 `menu_choice.stopped_at` 取）。
    回 ``{doc_type, codes, book}``（账套是那件写账件的）；不是回空 dict。"""
    current = frappe.db.get_value(doctype, name, "ledger_job") if name else None
    row = frappe.db.get_value(jobs.JOB, current, ["status", "request_key", "book_key", "meta_json"], as_dict=True) \
        if current else None
    if not row or row.get("status") != "manual" or not str(row.get("request_key") or "").startswith(push.OPERATOR_KEY) \
            or not row.get("book_key"):
        return {}
    try:
        meta = json.loads(row.get("meta_json") or "{}")
    except ValueError:
        return {}
    operator = receipt_rules.clean_operator_meta(meta.get("operator")) if isinstance(meta, dict) else {}
    doc_type, codes = menu_choice.stopped_at({"meta": {"operator": operator}})
    return {"doc_type": doc_type, "codes": codes, "book": str(row["book_key"])} if doc_type else {}


def menu_item(company, doctype, name, stop=None):
    """停在子菜单的单要人定的那一项（不是回 None）：选项是 CC 读到的候选菜单文字；这家公司这份账套这种单记过、且仍在候选里的是已定。"""
    stop = stop if stop is not None else menu_stop(doctype, name)
    if not stop:
        return None
    remembered = choices.operator_form(company, stop["book"], stop["doc_type"])
    value = remembered if remembered in stop["codes"] else ""
    return {"kind": OPERATOR_FORM, "subject": stop["doc_type"], "label": stop["doc_type"], "value": value,
            "source": card.REMEMBERED if value else "", "options": [{"code": code, "name": ""} for code in stop["codes"]]}


# ---------------- 代录中途配方外的业务选择框（CC 桌面版 1.12.23）----------------

OPERATOR_DIALOG = choices.OPERATOR_DIALOG


def _operator_job(doctype, name):
    """这张单当前那件写账件：代录交的、转了人工的才回 ``(行, 回执 meta)``，否则 ``(None, {})``。"""
    current = frappe.db.get_value(doctype, name, "ledger_job") if name else None
    row = frappe.db.get_value(jobs.JOB, current, ["status", "request_key", "book_key", "meta_json", "written_by_device",
                                                   "lease_device", "route_device", "last_device"], as_dict=True) if current else None
    if not row or row.get("status") != "manual" or not str(row.get("request_key") or "").startswith(push.OPERATOR_KEY):
        return None, {}
    try:
        meta = json.loads(row.get("meta_json") or "{}")
    except ValueError:
        return None, {}
    return row, (meta if isinstance(meta, dict) else {})


def dialog_stop(doctype, name) -> dict:
    """这张单停在 Express 中途一个配方外的业务选择框：回执 ``meta.operator.dialog``（过 `clean_operator_meta`）加上留着框的那台电脑与
    批次 ``{digest, title, text, buttons, stage, device, batch, doc}``；不是回空 dict。"""
    row, meta = _operator_job(doctype, name)
    raw = meta.get("operator") if isinstance(meta.get("operator"), dict) else {}
    dialog = receipt_rules.clean_operator_meta(raw).get("dialog") if row else None
    if not dialog:
        return {}
    # 留着框的那台：最后领这件的电脑（`last_device`，领单时记、转人工不清）；转人工时租约与派给谁都已清空，只认它会找不到电脑、
    # 对话里的选择就发不下去（11-22 测试机1 实测：答了框 4 分钟没人点）
    device = row.get("last_device") or row.get("written_by_device") or row.get("lease_device") or row.get("route_device") or ""
    return {**dialog, "device": str(device), "batch": str(raw.get("batch") or ""), "doc": str(raw.get("doc") or "")}


def dialog_item(company, doctype, name, stop=None):
    """停在业务选择框的单要人定的那一项（不是回 None）：问的是框的原文，选项是框上的按钮；这家公司答过同一个框（指纹）、那个钮
    仍在框上的是已定。"""
    stop = stop if stop is not None else dialog_stop(doctype, name)
    if not stop:
        return None
    remembered = choices.chosen(company, OPERATOR_DIALOG, stop["digest"])
    value = remembered if remembered in stop["buttons"] else ""
    label = " · ".join(part for part in (stop.get("title"), stop.get("text")) if part)[:200]
    return {"kind": OPERATOR_DIALOG, "subject": stop["digest"], "label": label, "value": value,
            "source": card.REMEMBERED if value else "", "options": [{"code": button, "name": button} for button in stop["buttons"]]}


def _dialog_answers(company, doctype, name, answers, seen) -> int:
    """人答的业务选择框：值须是这张单停下时框上的按钮、指纹对得上（属于批里别的单的跳过、不拒）；记下（按公司 + 指纹），再给留着框的
    那台电脑发一条「点这个钮」（`cc_desktop.operator_controls`，下一次领单带下去，电脑核框没变才点）。回记了几项。"""
    rows = [answer for answer in answers if answer.get("remember") is not False]
    stop = dialog_stop(doctype, name) if rows else {}
    if not stop:
        return 0
    from pearnly_core.cc_desktop import operator_controls

    saved = 0
    for answer in rows:
        value = str(answer.get("value") or "").strip()
        if str(answer.get("subject") or "").strip().lower() != stop["digest"]:
            continue
        if value not in stop["buttons"]:
            frappe.throw(_("Invalid request"))
        key = (OPERATOR_DIALOG, stop["digest"])
        if key in seen:
            continue
        seen.add(key)
        choices.remember(company, OPERATOR_DIALOG, stop["digest"], value, stop.get("text") or stop.get("title") or "")
        if stop["device"] and stop["batch"] and stop["doc"]:
            operator_controls.send(stop["device"], stop["batch"], stop["doc"], "answer", stop["digest"], value)
        saved += 1
    return saved


def _menu_answers(doctype, name, answers, seen) -> list:
    """人答的子菜单项核一遍（单种对上的，值须是这张单停下时 CC 读到的候选，不是整批拒）→ ``[(对象, 值, 单种)]``，同一项只记一次。
    这张单没停在子菜单、或答案的单种不是这张单停下的那种（属于批里别的单），跳过、不拒：代录交单逐张记，旧前端把同一份答案带给
    批里每一张（10-30 实机），一张停、别的没停或停在别的单种时不能整批「请求无效」。"""
    rows = [answer for answer in answers if answer.get("remember") is not False]
    stop = menu_stop(doctype, name) if rows else {}
    if not stop:
        return []
    checked = []
    for answer in rows:
        value = str(answer.get("value") or "").strip()
        if str(answer.get("subject") or "").strip().upper() != stop["doc_type"]:
            continue
        if value not in stop["codes"]:
            frappe.throw(_("Invalid request"))
        key = (OPERATOR_FORM, choices.operator_subject(stop["book"], stop["doc_type"]))
        if key in seen:
            continue
        seen.add(key)
        checked.append((key[1], value, stop["doc_type"]))
    return checked


def save_answers(company, doctype, name, answers, seen=None) -> int:
    """一张单人答的值全部记下（卡片 :func:`remember`、代录交单前 `operator._remember` 同一条）：子菜单那项单独核（:func:`_menu_answers`），
    其余照写账准备收齐后核（:func:`save`）；都核过才记。回记了几项。"""
    seen = seen if seen is not None else set()
    if not isinstance(answers, list) or any(not isinstance(answer, dict) for answer in answers):
        frappe.throw(_("Invalid request"))
    menu = [answer for answer in answers if answer.get("kind") == OPERATOR_FORM]
    boxes = [answer for answer in answers if answer.get("kind") == OPERATOR_DIALOG]
    rest = [answer for answer in answers if answer.get("kind") not in (OPERATOR_FORM, OPERATOR_DIALOG)]
    checked = _menu_answers(doctype, name, menu, seen)
    saved = save(company, collect_document(doctype, name, given_of(rest)), rest, seen) if rest else 0
    for subject, value, label in checked:
        choices.remember(company, OPERATOR_FORM, subject, value, label)
    return saved + len(checked) + _dialog_answers(company, doctype, name, boxes, seen)


# ---------------- MR.ERP 账本（公司选了 MR.ERP 账套、总开关开着，`mrerp.push.handles`） ----------------

MRERP_KINDS = (mrerp_choices.MENU, mrerp_choices.DEFAULT)


def mrerp_picked(doctype, name):
    """这张 MR.ERP 单写进哪本（`mrerp.push.picked`：对话账套 → 单据上记过的 → 按单据日期）；MR.ERP 不导的单据类型回 None。"""
    return mrerp_push.picked(doctype, name) if mrerp_push.INVOICES.get(doctype) else None


def mrerp_needed(company, doctype, picked=None):
    """这家公司这类单导入 MR.ERP 前此刻要人选的导入菜单（``mrerp_menu`` / 单据类型）与主档默认值（``mrerp_default`` / 主档字段），
    卡片项的形状；选一次记在公司上那本账套名下，大家共用（同单据上「选一次」`mrerp.push.answer`）。``picked``：这张单选账套的结论
    （:func:`mrerp_picked`）——同期两本没选过只问选哪本（同 Express 那项、同一个记忆），选中了按那本的菜单与默认值问；没给时是公司上
    记的那本。MR.ERP 不导的单据类型回 None。"""
    direction = mrerp_push.INVOICES.get(doctype)  # 库存单在单据上停下问（`mrerp.stock.needed`），不进 CC 预选卡
    row = mrerp_choices.company_row(company) if direction else None
    if not row:
        return None
    if picked and picked["reason"] == contracts.SEVERAL_BOOKS:
        return [{"kind": choices.ACCOUNT_SET, "subject": picked["subject"], "label": row.company_name or row.name,
                 "value": "", "source": "", "options": [_option(option) for option in picked["options"]]}]
    from pearnly_erp_portal.express import api

    menu_view, master_view = mrerp_choices.views(row, (picked or {}).get("book"))
    # 题目用单据弹窗同一套项目名（`api.MRERP_SUBJECTS`，原来直接放了单据类型 / 字段键，卡上显示 purchase）；选项「代码 · 名称」
    # （同 Cowork `guided.push_setup`、记住的选择的显示）
    return [{"kind": raw["kind"], "subject": raw["subject"], "label": _(api.MRERP_SUBJECTS.get(raw["subject"], raw["subject"])),
             "value": "", "source": "",
             "options": [_option({**option, "name": _coded(option)}) for option in raw.get("options") or ()]}
            for raw in mrerp_contracts.needed(menu_view, master_view, direction)]


def _coded(option) -> str:
    """选项显示「代码 · 名称」（两者相同或缺一只显示一个）。"""
    return " · ".join(str(part) for part in dict.fromkeys((option.get("code"), option.get("name"))) if part)


def mrerp_view(doc_id, doctype, name) -> dict:
    """MR.ERP 账本的一张单（卡片输入）：已导入的、在导的不再判，上次结果不明的要人核对；MR.ERP 不导的单据类型写不了。"""
    head = _head(doctype, name)
    view = {"doc_id": doc_id, "ref": head["ref"], "direction": "sales" if push.DIRECTIONS[doctype] == "sales" else "purchase"}
    if head["party"]:
        view["party"] = head["party"]
    reason = str(frappe.db.get_value(doctype, name, "ledger_reason") or "")
    if head["ledger_state"] in (contracts.WRITTEN, contracts.HANDLED, contracts.MISMATCH):
        return {**view, "state": card.NOT_WRITABLE, "reason": WRITTEN}
    if reason.startswith(mrerp_contracts.UNKNOWN):
        return {**view, "state": card.UNKNOWN, "reason": mrerp_contracts.UNKNOWN}
    if head["ledger_state"] == contracts.QUEUED:
        return {**view, "state": card.NOT_WRITABLE, "reason": ALREADY_QUEUED}
    picked = mrerp_picked(doctype, name)
    book = (picked or {}).get("book") or {}
    if book.get("key"):
        # 写进哪本：按单据日期选出的那本年度账套（同 Express 单的 `operator.book_target` 形状）；不给时写入计划说「没有指定账套」
        # （11-16 testerp T11）
        view["target"] = {"account_set": str(book["key"]), "account_set_title": str(book.get("label") or book["key"]),
                          "account_set_path": "", "connection": str(book.get("connection") or ""), "connection_title": "",
                          "writers": []}
    if picked and picked["reason"] == contracts.NO_BOOK:
        return {**view, "state": card.NOT_WRITABLE, "reason": NO_ACCOUNT_SET}  # 没有覆盖单据日期的年度账套（同 Express）
    items = mrerp_needed(push.company_of(doctype, name), doctype, picked)
    if items is None:
        return {**view, "state": card.NOT_WRITABLE, "reason": contracts.DISCONNECTED}
    return {**view, "state": card.CHECK, "items": items}


def save_mrerp(company, doctype, answers, seen, name=None, picked=None) -> int:
    """记 MR.ERP 的导入菜单 / 主档默认值：只认此刻要选的那几项、最近一次读到的可用值（不是就整批拒），记在公司上这张单那本账套名下
    （`mrerp.choices.choose`）；同期两本选哪本记进与 Express 同一个记忆（`express.choices`）。同一项（``seen``）只记一次。
    ``picked``：还没存成单据的（LINE 收单记录，`operator.remember_intake_mrerp`）按票面日期选好的那本；给了 ``name`` 按单据选。"""
    picked = mrerp_picked(doctype, name) if name else picked
    book = (picked or {}).get("book")
    asked = {(item["kind"], item["subject"]): item for item in mrerp_needed(company, doctype, picked) or ()}
    checked = []
    for answer in answers or ():
        if not isinstance(answer, dict):
            frappe.throw(_("Invalid request"))
        key = (answer.get("kind"), answer.get("subject"))
        if key in seen or answer.get("remember") is False:
            continue
        item = asked.get(key)
        offered = [option["code"] for option in item["options"]] if item is not None else []
        value = code_rules.choose(answer.get("value"), offered)
        if item is None or value not in offered:
            frappe.throw(_("Invalid request"))
        seen.add(key)
        checked.append((key, value))
    for (kind, subject), value in checked:
        if kind == choices.ACCOUNT_SET:
            label = next((option["name"] for option in asked[(kind, subject)]["options"] if option["code"] == value), "")
            choices.remember(company, kind, subject, value, label)  # loop-query: 一张卡片答的几项，同一项只记一次
            continue
        if mrerp_choices.choose(company, kind, subject, value, book) is None:
            frappe.throw(_("Invalid request"))
    return len(checked)


# ---------------- 钩子 ----------------



UNSUPPORTED = "unsupported"  # 这条路此刻办不了（共用层出口 `assistant.stop_exits`、文字 ``guide.reason.unsupported``）


def _operator_conversation(record=None) -> bool:
    """这张收单记录在代录对话里（会话挂着代录任务，同共用引擎认模式的判据 `assistant.guide.run._conversation`）。先看记录自己
    放进来的那段对话（``session_id``，不随调用方的环境变：代录交单的清单、后台任务里也一样），没有再看此刻的对话；都没有
    （LINE 卡片、网页）算不是。"""
    session = str(getattr(record, "session_id", "") or "") or conversation.session()
    return bool(session) and bool(frappe.db.get_value("Pearnly Assistant Session", {"session_id": session}, "operator_task"))


def check(company, doc_ids) -> dict:
    """共用层 ``write_precheck.api.check`` 调的钩子：这家公司这批单的判定。本人不能写的单不回、也不报存在与否；这家公司断开了
    Express 的，每张都写不了。"""
    if not _company_ok(company):
        return {"company_title": "", "documents": []}
    title = frappe.db.get_value(push.COMPANY, company, "company_name") or company
    linked, ledger_mrerp = push.enabled(company), mrerp_push.handles(company)  # MR.ERP 总开关关着时 handles 回假
    collections = _collection()
    documents = collections.check(company, [doc_id for doc_id in doc_ids if collections.mine(doc_id)])["documents"]
    for doc_id in doc_ids:
        kind, found = target_of(company, doc_id)
        if kind in (COLLECTION, MISSING):
            continue  # 收款（``R:``）上面已判；本人不能写的不回、也不报存在与否
        if kind == UNKNOWN:
            _unknown(company, doc_id)
            documents.append({"doc_id": doc_id, "state": card.NOT_WRITABLE, "reason": UNKNOWN_DOCUMENT})
            continue
        if kind == INTAKE:
            # 还没提交的收单记录：与代录预检同一份判定（CC 代录开录时才提交成单据，`operator.submit`；LINE 卡片「保存」时提交）
            from pearnly_erp_portal.express import operator

            if not linked:
                # 没接 Express：按「这家公司接的账本 + 对话方式」判，不按记录的来源（11-24 主会话：CC 对话放进来的与 LINE 收单的同一判据；
                # 原来只有 LINE 来源走下面这条，ERP 直写 · MR.ERP 的 CC 收单一律报 express.disconnected）。
                # 代录对话：代录写的是 Express（CC 桌面版录），这家没接 Express——账本是 MR.ERP 的报「不支持」（如实），没有账本的报未连接；
                # 直写（和 LINE 卡片）：问记录自己要人定的（物料、仓库），账本是 MR.ERP 的再加 MR.ERP 导入前要人定的（导入菜单、主档默认值、
                # 同期两本选哪本；同已存单据 :func:`mrerp_view`，用户 11-19 出卡前问完）
                if _operator_conversation(found):  # loop-query: 收单记录逐张判，一批有上限（同下一行）
                    documents.append({"doc_id": doc_id, "state": card.NOT_WRITABLE,
                                      "reason": UNSUPPORTED if ledger_mrerp else contracts.DISCONNECTED})
                    continue
                view = operator.intake_mrerp_view if ledger_mrerp else operator.intake_own_view
                documents.append(view(company, doc_id, found))  # loop-query: 收单记录逐张判，一批有上限
                continue
            view = operator.intake_view(company, doc_id)  # loop-query: 收单记录逐张判（同单据 document_view），一批有上限
            if view:
                documents.append(view)
            continue
        if ledger_mrerp:
            documents.append(mrerp_view(doc_id, *found))
            continue
        if not linked:
            documents.append({"doc_id": doc_id, "state": card.NOT_WRITABLE, "reason": contracts.DISCONNECTED})
            continue
        documents.append(document_view(doc_id, *found))
    return {"company_title": title, "documents": documents}


def given_of(answers) -> dict:
    given = {}
    for answer in answers or ():
        if not isinstance(answer, dict):
            frappe.throw(_("Invalid request"))
        kind, subject = str(answer.get("kind") or ""), str(answer.get("subject") or "")
        value = str(answer.get("value") or "").strip()
        if kind and subject and value:
            given.setdefault(kind, {})[subject] = value
    return given


def save(company, found, answers, seen=None) -> int:
    """按一张单收齐的项记下人答的值（``answers`` = ``[{kind, subject, value, label?, remember?}]``）：只认这一项能选的值（不是就整批
    拒），``remember`` 为假的不记。增值税登记户记在公司上（收银小票按它拆税额），其余记进这家公司的选择。同一项（``seen``）只记一次
    （前一张已记下的跳过）。这张单此刻不再问的项（出题与记答案不同口径，或卡片过期：换了账套后不再问）整批拒、回「没记下」哪几题，
    不静默跳过（11-17 回归 erp.cc.operator.intake.sales）。回记了几项。"""
    seen = seen if seen is not None else set()
    asked = {(item["kind"], item["subject"]): item for item in found["items"]}
    checked, missed = [], []
    for answer in answers or ():  # 先全部核完再记：有一项不对，一项都不记
        if not isinstance(answer, dict):
            frappe.throw(_("Invalid request"))
        key = (answer.get("kind"), answer.get("subject"))
        item = asked.get(key)
        if key in seen or answer.get("remember") is False:
            continue
        if item is None:
            # 这一题此刻不再问：已经记成同一个值的（同一批前一张记下了，10-30）照算记下；否则是卡上出了这一题、这里重判却没有
            # （出题与记答案不同口径，11-17 回归）——不能当记下了，回明确的「没记下」，调用方报给人
            remembered = choices.chosen(company, str(key[0] or ""), str(key[1] or ""))  # loop-query: 一张卡上此刻不再问的那几题（有界），只读一行
            if str(remembered or "") != str(answer.get("value") or ""):
                missed.append(str(answer.get("label") or answer.get("subject") or answer.get("kind") or ""))
            continue
        options = {row["code"]: row.get("name") or "" for row in full_options(item, found["data"])}
        value = code_rules.choose(answer.get("value"), options)  # 复述时空白变了的编码换回主档原样
        if value not in options:
            frappe.throw(_("Invalid request"))
        seen.add(key)
        checked.append((item, value, options[value] or item.get("label") or ""))
    if missed:
        frappe.throw(_("Not saved: {0}").format(", ".join(dict.fromkeys(missed))))
    for item, value, label in checked:
        if item["kind"] == pos_day.VAT_REGISTERED:
            companies.set_vat_registered(company, value)
        else:
            choices.remember(company, item["kind"], item["subject"], value, label)
    return len(checked)


def remember(company, answers) -> dict:
    """共用层 ``write_precheck.api.answer`` 调的钩子：``answers`` = ``{doc_id: [{kind, subject, value, label, remember}]}``
    （``contracts.expand`` 的结果）。逐张核权限，按这张单（带着人答的值）重新收齐后核值、记下（:func:`save`）。"""
    if not _company_ok(company) or not isinstance(answers, dict):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    seen, saved, ledger_mrerp = set(), 0, mrerp_push.handles(company)
    for doc_id, given in answers.items():
        kind, found = target_of(company, doc_id)
        if kind == UNKNOWN:
            _unknown(company, doc_id)
            frappe.throw(_("Invalid request"))
        if kind == COLLECTION:
            frappe.throw(_("Invalid request"))  # 收款的判定没有要人答的项（`collection.plan`）
        if kind == MISSING:
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
        if kind == INTAKE:
            from pearnly_erp_portal.express import operator

            # 同 :func:`check`：按账本 + 对话方式，不按来源（代录对话里 MR.ERP 公司的收单不出题，不会走到这里记 MR.ERP 那几项）
            on_mrerp = ledger_mrerp and not _operator_conversation(found)  # loop-query: 一张卡片答到的几条记录，逐条判
            remember_row = operator.remember_intake_mrerp if on_mrerp else operator.remember_intake
            saved += remember_row(company, found, given, seen)  # loop-query: 一张卡片答到的几条记录，逐条写回
            continue
        doctype, name = found
        if ledger_mrerp:
            saved += save_mrerp(company, doctype, given, seen, name)
            continue
        saved += save_answers(company, doctype, name, given, seen)  # loop-query: 一张开始卡上答到的几张单，逐张记（同一项 seen 去重）
    return {"saved": saved}


DATES = {push.PURCHASE: "doc_date", push.SALES: "doc_date", push.POS_DAY: "day", push.STOCK: "posting_date",
         push.COUNT: "posted_at"}  # 单据日期（同 `pending.KINDS`）：没记写进哪份账套时按它选账套


def _book_of(doctype, name, company):
    """这张单写进的账套：CC 对话 / 代录里是本对话账套（`conversation.wanted`，记过的 ``ledger_book`` 也以它为准）；否则记过的
    （``ledger_book``）就是它，没记按单据日期选（同写入，`target.pick`；同期几份时按选过的或今天该写的）。"""
    company_row = push._company(company)
    wanted = conversation.wanted(company_row) if company_row else None
    if wanted is not None:
        return wanted
    row = frappe.db.get_value(doctype, name, ["ledger_book", DATES[doctype]], as_dict=True) or {}
    if row.get("ledger_book"):
        return row["ledger_book"]
    day = str(row.get(DATES[doctype]) or "")[:10] or datetime.now(ZoneInfo("Asia/Bangkok")).date().isoformat()
    found = target.current(company_row, day)
    return (found or {}).get("key") or ""


def search(company, doc_id, search, txt="", start=0, page_length=20) -> dict:
    """大列表的项服务端分页搜索（共用层 ``write_precheck.api.search`` 调的钩子）：``search`` 是项上给的搜索类型（``accounts`` /
    ``products`` / ``parties``），``doc_id`` 定账套（这张单写进的那份）；按编码或名称包含 ``txt``，回 ``{options: [{code, name}], more}``，
    存货、对方第一条是「新建」。这张单本人要能写（同 :func:`check`）。"""
    kind, found = target_of(company, doc_id) if _company_ok(company) else (MISSING, None)
    if kind == UNKNOWN:
        _unknown(company, doc_id)
        frappe.throw(_("Invalid request"))
    if kind == INTAKE:
        # 代录放文件识别出、还没提交的收单记录（A 批 1b）：物料按 ERP 物料搜，Express 那些按它会写进的账套搜
        from pearnly_erp_portal.express import operator

        if search not in {*SEARCH.values(), "own_products"}:
            frappe.throw(_("Invalid request"))
        return operator.intake_search(company, doc_id, search, txt, start, page_length)
    if kind != DOCUMENT:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    try:
        start, page_length = max(0, int(start or 0)), max(1, min(int(page_length or 20), SEARCH_MAX))
    except (TypeError, ValueError):
        frappe.throw(_("Invalid request"))
    if search not in set(SEARCH.values()):
        frappe.throw(_("Invalid request"))
    doctype, name = found
    key = _book_of(doctype, name, company)
    return search_rows(key, "sales" if push.DIRECTIONS[doctype] == "sales" else "purchase", search, txt, start, page_length)


def search_rows(key, direction, search, txt="", start=0, page_length=20) -> dict:
    """账套 ``key`` 里能选的值按 ``txt`` 过滤、分页（单据与代录的收单记录同一条）。"""
    data = facts.load(key) if key else None
    rows = _search_rows(search, direction, data)
    needle = " ".join(str(txt or "").split()).casefold()
    if needle:
        rows = [row for row in rows if row["code"] != choice_rules.NEW
                and (needle in row["code"].casefold() or needle in row["name"].casefold())]
    return {"options": rows[start:start + page_length], "more": len(rows) > start + page_length}


def _search_rows(search, direction, data) -> list:
    """搜索类型 → 这份账套里能选的全部值（同单据页「选一次」的范围）：对方是这一边的整张档案表、存货是库存品、科目是整张科目表
    （这一边常用的大类在前）。"""
    reported = facts.reported(data)
    new = [{"code": choice_rules.NEW, "name": ""}]
    if search == "parties":
        side = "reported_customers" if direction == "sales" else "reported_suppliers"
        return new + [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
                      for row in reported.get(side) or [] if row.get("code")]
    if search == "products":
        return new + [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
                      for row in reported.get("reported_products") or []
                      if row.get("code") and str(row.get("kind") or "stock") == "stock"]
    accounts = facts.accounts_first(reported.get("reported_accounts") or [], f"{direction}|")
    return [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")} for row in accounts if row.get("code")]


def resume(company, doc_ids) -> list:
    """共用层 ``write_precheck.api.answer(resume=1)`` 调的钩子（待处理尾部的卡片）：记下答案后，停在要人选、还没交写账件的单按原来的
    写入路径重新准备（同单据页「选一次」后 `api.answer` 的做法：清掉停下的原因，`push.send`）。归代录的、在途的、转了人工的不动；
    这家公司断开了 Express 的不重排。回 ``[{doc_id, status, reason}]``。在 CC 对话里不推（B1：写入只走计划，共用层同样不调这里）。"""
    if not _company_ok(company) or conversation.session():
        return []
    if mrerp_push.handles(company):
        return _resume_mrerp(company, doc_ids)
    if not push.enabled(company):
        return []
    results = []
    for doc_id in doc_ids:
        found = _resumable(company, doc_id)
        if found is None:
            continue
        doctype, name = found
        row = frappe.db.get_value(doctype, name, ["ledger_state", "ledger_reason", "ledger_job"], as_dict=True) or {}
        if row.get("ledger_state") != contracts.HELD or row.get("ledger_job") \
                or not str(row.get("ledger_reason") or "").startswith(contracts.CHOICE) or push.operator_owned(doctype, name):
            continue
        push._set(doctype, name, {"ledger_reason": "", "ledger_needed": None})
        frappe.db.commit()
        result = push.send(doctype, name) or {}
        results.append({"doc_id": doc_id, "status": str(result.get("status") or ""),
                        "reason": str(result.get("reason") or "")[:300]})
    return results


def _resumable(company, doc_id):
    """待处理里能按原来写入路径重排的单 ``(doctype, 名)``：只有已存的单据；收单记录（代录交单时才提交）、收款不在这里重推，
    认不出的种类记日志。"""
    kind, found = target_of(company, doc_id)
    if kind == UNKNOWN:
        _unknown(company, doc_id)
    return found if kind == DOCUMENT else None


def _resume_mrerp(company, doc_ids) -> list:
    """MR.ERP 账本：停在要人选导入菜单 / 默认值（``choice_needed:mrerp``）的单，记下后排一次导入（同单据上「选一次」后
    `mrerp.push.answer` 的 `send`）；归代录的不动。"""
    results = []
    for doc_id in doc_ids:
        found = _resumable(company, doc_id)
        if found is None:
            continue
        doctype, name = found
        row = frappe.db.get_value(doctype, name, ["ledger_state", "ledger_reason"], as_dict=True) or {}
        if row.get("ledger_state") != contracts.HELD or row.get("ledger_reason") != mrerp_contracts.CHOICE \
                or push.operator_owned(doctype, name):
            continue
        result = mrerp_push.send(doctype, name) or {}
        results.append({"doc_id": doc_id, "status": str(result.get("status") or ""),
                        "reason": str(result.get("reason") or "")[:300]})
    return results


__all__ = ["COLLECTION", "DOCUMENT", "INTAKE", "INTAKE_PREFIX", "MISSING", "OPERATOR_FORM", "PREFIXES", "UNKNOWN",
           "UNKNOWN_DOCUMENT", "UNKNOWN_LOG", "check", "collect", "collect_document", "collect_form", "doc_id_of", "document_view",
           "allowed", "dialog_item", "dialog_stop", "full_options", "given_of", "menu_item", "menu_stop", "mrerp_needed", "mrerp_view", "remember",
           "resolve_target", "resume", "save", "save_answers", "search", "target_of"]
