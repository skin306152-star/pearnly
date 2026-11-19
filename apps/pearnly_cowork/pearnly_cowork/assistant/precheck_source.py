"""写账前置判定的 Cowork 这一侧（助手域，与代录来源 `operator_source` 同层；共用层 ``pearnly_core.write_precheck``，``hooks.pearnly_write_precheck`` 声明；评估
`docs/work/2026-11-04-precheck-card.md` 第 3 节、7.2 W2）。

- :func:`document`：一张单写之前要人定的项**一次收齐**：结果不明（先核对）、录不录（转账凭条 / 预扣税凭证）、方向（判不出时按公司
  记住的分组）、待选账套（多份同期）、商品单入账方式、写账规则要人选的项（对方档、存货、费用科目、现 / 赊、抵不抵、收款科目……）。
  以前 `prepare` 遇到录不录、方向、入账方式先报错退出，后面的项算不到，人要被问好几轮；这里都变成项收进同一份结果。
  写入路径（`service.prepare`）不变：照原顺序退出，只是人已经一次答完了。
- 每项带现在的值与来源：记住的、公司规则、Express 历史是已定；共用预选规则（`write_precheck.preselect`）、科目建议是预选；没有就未定。
  只收「缺了就没法正确写入」的项（Express、MR.ERP 都不拦的不收，用户 11-04）。定时任务默认处理定下的不进卡片（改了只对本次生效，
  不进账套记忆，10-08 口径）。
- :func:`remember`：卡片答案按各类现有的记法记下：账套记忆（`service.save_choices`）、租户口径（`history.document_policy`）、
  公司入账方式（`goods_posting.choose`）、公司分组（`operator_source.regroup`）、现用账套（`companies.pending`）、代录子菜单
  （``operator_form``，直接记进选择记忆 `push.choices.record`，记在停下那条推送记录的连接与账套；不经 `save_choices`：那条路
  只认写账规则的类）。
- 代录停在定制菜单的子菜单（原因 menu_choice，推送记录存下的回执 ``meta.operator`` 带单种与候选）：判定里收一项 ``operator_form``
  （归类键 = 单据种类，选项 = 候选菜单文字），代录预检原来的回包也列进 ``needed``；记住后再交时载荷带 ``operator.form_choice``。
- :func:`resume`：待处理里停着的单记下答案后按原来的写入路径重推（同待处理页「当场选」）。

只看不写：判定里记付款方式的那一笔整笔回滚（同 `choices_view`）；只有按公司记住的分组照代录预检的做法先落到单据上。
"""
import json

import frappe

from pearnly_core.transaction import snapshot
from frappe import _

from pearnly_core.assistant import guided as assistant_guided
from pearnly_core.express_rules.push import posting_profile

from pearnly_core.write_precheck import contracts as card
from pearnly_core.write_precheck import preselect
from pearnly_cowork.access import scope

# 写账规则问的类（记在账套记忆，`service.save_choices` 认得的那几类）
CHOICE_KINDS = ("party", "item", "account", "receipt", "department", "payment", "vat", "service_vat", "location", "detail")
DIRECTION = "direction"
DOCUMENT_POLICY = "document_policy"
GOODS_POSTING = "goods_posting"
CURRENT_BOOK = "current_book"
PAYMENT = "payment"
OPERATOR_FORM = "operator_form"  # 代录停在定制菜单的子菜单（原因 menu_choice）：这家这本账套这种单走哪个命令（评估 2026-11-05 修法 D）
# 写账规则记下的来源（`push.express.decide` 的 ``decided_by``）→ 卡片的来源
DECIDED_BY = {"remembered": card.REMEMBERED, "company_rule": card.COMPANY_RULE,
              "express_history": card.EXPRESS_HISTORY}
SHOWN_DECIDED = ("account", "payment", "vat")  # 已定区列出、可改的类（定时任务默认处理定下的不列）
# 大列表的类 → 搜索类型（共用接口 ``write_precheck.api.search``）：超过 ``contracts.SMALL_LIST`` 条时 item 里只给预选值
SEARCH_OF = {"account": "accounts", "receipt": "accounts", "item": "products", "party": "parties"}


def _row(doc) -> dict:
    return doc.as_dict() if hasattr(doc, "as_dict") else dict(vars(doc))


def _party(doc, fields) -> str:
    sales = str(fields.get("direction") or "") == "sales"
    return str(getattr(doc, "buyer_name" if sales else "seller_name", "") or "")


def _aimed(doc):
    """CC 对话里判定按「目标 = 本对话账套」算（A 线 11-12，主会话三条）：只在内存里换掉这张单的连接与账套、票不写（改投只在
    真推送那一刻落到票上，`tools._submit_one`）；原来指着同公司另一本的列进 ``retargeted``（``{from, to}``，带名称），原来是别家
    公司的不改投（回写入闸的拒绝，列在不能写）。回 (拒绝或 None, retargeted 或 None, 是否已按本对话账套算)。"""
    local = getattr(frappe, "local", None)
    if not (getattr(local, "pearnly_assistant", None) or {}).get("session_id"):
        return None, None, False  # 不在 CC 对话里（页面上的判定卡）：照票上的目标
    from pearnly_cowork.assistant import write_scope

    aimed = write_scope.aim(None, None, doc.push_connection, doc.push_account_set,
                            day=str(getattr(doc, "invoice_date", "") or "")[:10])
    if not aimed:
        return None, None, False
    if aimed.get("reason"):
        return aimed, None, False
    before = (str(doc.push_connection or ""), str(doc.push_account_set or ""))
    after = (aimed["connection"], aimed["key"])
    moved = None
    if before[1] and (before[0], write_scope.key_of(before[1])) != (after[0], write_scope.key_of(after[1])):
        moved = {"from": {"connection": before[0], "account_set": before[1],
                          "label": write_scope.label_of(before[0], before[1])},
                 "to": {"connection": after[0], "account_set": after[1], "label": aimed.get("label") or after[1]}}
    doc.push_connection, doc.push_account_set = after
    return None, moved, True


def document(history, posting_kind=None, hint=None, conversation=False) -> dict:
    """一张单的判定：``{"doc_id", "state", "reason"?, "message"?, "record"?, "target"?, "direction"?, "ref", "party",
    "items", "planned"?, "legacy"}``。``hint``：用户在对话里说的费用类别（CC 传，只影响科目建议，`service._suggest`）；``planned``：
    什么都不用定时此刻推出去会怎么写（`push.express.pushed.planned`，与入账后的 written 同一个函数），CC 推前照它说现 / 赊、科目
    （2026-10-20 教程录制：推前说赊购，写进去是现购）。``state`` / ``items`` 是共用卡片（``contracts.merge``）的输入；``legacy`` 是代录开始前预检原来的回包
    （:func:`operator_check`，形状与改造前逐字相同）。"""
    # 下面几处回滚「只是看」：调用方此前有没提交的写就拒绝，不随之冲掉（11-19 回滚审计，`transaction.snapshot`）
    snapshot.require_clean("precheck_source.document")
    from pearnly_core.express_rules.contracts.history import primary_page_fields
    from pearnly_cowork.companies import pending
    from pearnly_cowork.connections import service as connections
    from pearnly_cowork.contracts import document_policy as policy_rules
    from pearnly_cowork.history import storage
    from pearnly_cowork.push import lifecycle
    from pearnly_cowork.push.application import service
    from pearnly_cowork.push.application.provenance import original_history

    scope.require("intake")
    doc = storage.read(history)
    fields = primary_page_fields(json.loads(doc.pages or "[]"))
    base = {"doc_id": doc.name, "ref": str(getattr(doc, "invoice_no", "") or ""), "party": _party(doc, fields), "items": []}
    refused, moved, pinned = _aimed(doc) if conversation else (None, None, False)
    if refused:
        # 账套未定、票上是别家公司的去向：不能写（原因同写入闸），不改投
        return {**base, "state": card.NOT_WRITABLE, "reason": refused["reason"],
                "legacy": {"state": "blocked", "message": refused["reason"]}}
    if moved:
        base["retargeted"] = moved
    if lifecycle.result_unknown(doc.last_push_status, doc.last_push_reason):
        # 上次领单途中断了、结果不明：先在 Express 里核对（流程锁，防重复入账），不问别的
        legacy = {"state": "blocked", "reason": service.RESULT_UNKNOWN, "record": doc.last_push_record or "",
                  "message": _("intake.error.ERP_CONFIRMED_PUSH_RESULT_UNKNOWN")}
        return {**base, "state": card.UNKNOWN, "reason": service.RESULT_UNKNOWN, "record": legacy["record"],
                "legacy": legacy}
    items, target, found, loaded, legacy = [], None, None, None, None
    menu = _menu_item(doc)
    try:
        if not doc.push_connection:
            frappe.throw(_("cowork.company.pending.no_target"))
        blocked = service._document_block(doc)
        if blocked and blocked.startswith(policy_rules.SKIPPED_PREFIX):
            frappe.throw(_(blocked))  # 这类单据这个租户选了不录：不是要人定的事，列在「不能写」
        if blocked:
            # 录不录还没选：代录预检照旧在这一步停下（原来的回包），卡片里收成一项、接着把后面的项算完
            legacy = {"state": "blocked", "message": str(_(blocked))[:300]}
            items.append(_policy_item(policy_rules.document_class(fields)))
        original_history(doc)
        loaded = connections.read_for_push(doc.push_connection, doc.workspace, doc.client, lock=True)
        if loaded.endpoint.get("adapter") != "express":
            # MR.ERP 的导入菜单与默认值按 MR.ERP 自己的准备问（本批不进卡片，评估 B5 随 MR.ERP 代录那批）：直写照写，
            # 代录预检照旧停下（代录只录 Express）
            frappe.db.rollback()
            legacy = legacy or {"state": "blocked", "message": str(_("push.adapter_not_connected"))[:300]}
            return {**base, "state": card.CHECK, "items": items, "legacy": legacy}
        found = service._express_context(doc, loaded, doc.push_account_set or None)
        target = service._operator_target(doc, loaded, found)
        service._require_matching_account(loaded, found["target"], found["fields"], found["invoice_date"])
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        message = str(error)[:300]
        legacy = legacy or {"state": "blocked", "message": message, **({"target": target} if target else {})}
        return {**base, "state": card.NOT_WRITABLE, "reason": message, "legacy": legacy,
                **({"target": target} if target else {})}
    frappe.db.rollback()  # 只是看
    # 同原来的预检：回滚后重新锁上连接再取要选的项（读主档的请求同一时刻只排一个）
    loaded = connections.read_for_push(doc.push_connection, doc.workspace, doc.client, lock=True)
    row = _row(doc)
    if not pinned and pending.account_set_stop(row):  # 按本对话账套算的不再问哪份
        items.append(_book_item(pending.account_set_options(row)))
    fixed = service._fixed(doc.name, loaded.doc.name, found["target"].account_set, None)  # 定时任务默认处理
    view, decision, (kind, posting_from, today) = service._choices(doc, loaded, found, posting_kind or None, fixed=fixed,
                                                                   hint=hint or None)
    if legacy is None:
        legacy = _legacy(view, target)
    # 对方按推送判出的方向说（票上没声明方向的销项票，对方是买方，11-09）
    out = {**base, "party": _party(doc, {"direction": found["direction"] or fields.get("direction")}), "target": target,
           "direction": found["direction"] or "", "legacy": legacy}
    if view.get("wait"):
        # 要选的科目还没有可选值（科目表还没读到）：读到后重判，不问人
        return {**out, "state": card.WAITING, "reason": str(view["wait"].get("message") or "")}
    if not found["direction"]:
        # 判不出进项还是销项：先定方向（按对方记住，同代录资料区的分组），后面的项按方向才算得出
        from pearnly_cowork.assistant.operator_source import party_key

        subject, name = party_key(found["fields"], "purchase")
        if subject:
            items.append({"kind": DIRECTION, "subject": subject, "label": name,
                          "options": [{"code": code} for code in ("purchase", "expense", "sales")]})
        return _with_menu({**out, "state": card.CHECK, "items": items}, menu)
    goods = _goods_item(doc, loaded, found, decision, kind, posting_from, today)
    asked_before = None
    if goods:
        items.append(goods)
        # 预选「走库存」时还要定的项（存货对照等）一并列出：按预选的入账类型重新取数再判一次（只看不记）
        asked_before = {(item.get("kind"), item.get("subject")) for item in view.get("needed") or ()}
        found = service._express_context(doc, loaded, doc.push_account_set or None)
        view, decision, _posting = service._choices(doc, loaded, found, goods["value"], fixed=fixed, hint=hint or None)
        frappe.db.rollback()
    asked = choice_items(view, found["fields"], found["direction"])
    if asked_before is not None:
        # 按预选重判才多出来的项（存货对照、出库仓）只在入账方式答成预选值时才问、才算数（测试机 11-09：选了按服务录入还问出库仓）
        when = {"kind": GOODS_POSTING, "subject": goods["subject"], "value": goods["value"]}
        asked = [{**item, "when": dict(when)} if (item["kind"], item["subject"]) not in asked_before else item
                 for item in asked]
    items.extend(asked)
    items.extend(decided_items(decision, found["fields"], found["direction"]))
    own = document_payment_item(doc, found["fields"], found["direction"])
    if own:
        items.append(own)
    if view.get("planned") and all(card.state_of(item) == card.DECIDED for item in items) \
            and getattr(doc, "last_push_status", None) not in ("pending", "success"):
        # 什么都不用定（全部已定）才给：还有预选 / 未定的项时写法要等人答完、重判后再看
        out["planned"] = view["planned"]
    return _with_menu({**out, "state": card.CHECK, "items": items}, menu)


def _menu_stop(doc) -> dict:
    """这张单停在定制菜单的子菜单（最近一次推送转人工，那条推送记录存下的回执 ``meta.operator`` 带单种与候选，
    `menu_choice.stopped_at`）：``{doc_type, codes, connection, account_set}``（连接与账套是那条推送记录写的，交单时载荷按同一份
    读记住的值）；不是回空 dict。"""
    from pearnly_core.express_operator import menu_choice

    record = str(getattr(doc, "last_push_record", "") or "")
    if str(getattr(doc, "last_push_status", "") or "") != "manual" or not record:
        return {}
    found = frappe.db.get_value("Cowork Push Record", record, ["connection", "account_key", "response_json"], as_dict=True)
    if not isinstance(found, dict):
        return {}
    try:
        response = json.loads(found.get("response_json") or "{}")
    except ValueError:
        return {}
    doc_type, codes = menu_choice.stopped_at(response)
    if not doc_type or not found.get("connection") or not found.get("account_key"):
        return {}
    return {"doc_type": doc_type, "codes": codes, "connection": str(found["connection"]),
            "account_set": str(found["account_key"])}


def _menu_item(doc):
    """停在子菜单的单要人定的那一项（没有回 None）：选项是 CC 读到的候选菜单文字；这份账套这种单记过、且仍在候选里的是已定。"""
    stop = _menu_stop(doc)
    if not stop:
        return None
    from pearnly_cowork.push import choices as choice_store

    remembered = choice_store.operator_form(stop["connection"], stop["account_set"], stop["doc_type"])
    value = remembered if remembered in stop["codes"] else ""
    return {"kind": OPERATOR_FORM, "subject": stop["doc_type"], "label": stop["doc_type"], "value": value,
            "source": card.REMEMBERED if value else "", "options": [{"code": code, "name": ""} for code in stop["codes"]]}


def _with_menu(found, menu) -> dict:
    """判定结果里加上子菜单那一项；还没定的同时列进代录预检原来的回包（``needed``，中途选那张卡片照它出下拉）。"""
    if not menu or found.get("state") != card.CHECK:
        return found
    found["items"] = [*(found.get("items") or []), {**menu, "direction": found.get("direction") or ""}]
    legacy = dict(found.get("legacy") or {})
    if not menu["value"]:
        found.pop("planned", None)  # 「会怎么写」只在什么都不用定时给（同 :func:`document`），子菜单还没定就不给
        need = {key: menu[key] for key in ("kind", "subject", "label", "options")}
        if legacy.get("state") == "ready":
            legacy = {"state": "choose", "needed": [need], **({"target": legacy["target"]} if "target" in legacy else {})}
        elif legacy.get("state") == "choose":
            legacy["needed"] = [*(legacy.get("needed") or []), need]
        found["legacy"] = legacy
    return found


def _remember_menu(stop, subject, value, label) -> int:
    """记下这家这本账套这种单走哪个子菜单命令：值须是这张单停下时 CC 读到的候选之一（没停下、单种不是这张的由 :func:`remember`
    先跳过）。直接记进选择记忆（不经 `service.save_choices`：那条路的可选值只认写账规则的类）。``stop``：`_menu_stop` 的结果。"""
    from pearnly_cowork.push import choices as choice_store

    if not stop or str(subject or "").strip().upper() != stop["doc_type"] or value not in stop["codes"]:
        frappe.throw(_("cowork.choice.invalid"))
    return int(bool(choice_store.record(stop["connection"], stop["account_set"], OPERATOR_FORM, stop["doc_type"], value,
                                        label or stop["doc_type"])))


def operator_check(history, posting_kind=None) -> dict:
    """代录开录前预检一张（主会话 10-27：预检走产品的准备，要人选的一次问完，显示此刻的结果、不读单上旧原因）：与推送准备
    同一套挡单判断（单据口径、原件引用、单据日期在不在账套期间里）加同一套「要人选的项」，不建推送记录、不改单据。
    回 ``{"state": ready / choose / waiting / blocked, "reason"?, "message"?, "needed"?, "target"?}``；``needed`` 每项带可选值，
    商品、对方可选「在 Express 新建」（``NEW``，按直写现有建档、带查重）。``posting_kind``：交单时明传的入账类型（代录资料区的分组）。
    原在推送服务里（`push.application.service.operator_check`），11-05 随写账前置判定挪到这里：同一次判定（:func:`document`）里
    原来那份回包，形状不变。"""
    return document(history, posting_kind)["legacy"]


def _legacy(view, target) -> dict:
    """代录开始前预检原来的回包（:func:`operator_check` 改造前的形状，U1 / U2 的调用方不用改）。"""
    from pearnly_core.express_rules.push.choices import EXPENSE, NEW

    if view.get("wait"):
        return {"state": "waiting", "reason": view["wait"].get("state") or "",
                "message": view["wait"].get("message") or "", "target": target}
    if not view.get("needed"):
        return {"state": "ready", "target": target}
    needed = []
    for item in view["needed"]:
        rows = _options(item, view.get("options") or {})
        if item.get("kind") in ("item", "party"):
            rows.insert(0, {"code": NEW, "name": ""})  # 在 Express 新建（按直写现有建档、带查重，主会话 10-27）
        if item.get("kind") == "item" and item.get("expense"):
            rows.insert(1, {"code": EXPENSE, "name": ""})  # 这一行记费用（11-06 按行选）
        needed.append({**item, "options": rows})
    return {"state": "choose", "needed": needed, "target": target}


def _options(item, shared) -> list:
    kind = item.get("kind")
    products = shared.get("products_all") if kind == "item" and item.get("expense") else shared.get("products")
    options = item.get("options") or (products if kind == "item" else
                                      shared.get("accounts") if kind in ("account", "receipt") else []) or []
    return [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")} for row in options
            if isinstance(row, dict)]


def _policy_item(kind) -> dict:
    from pearnly_cowork.contracts import document_policy as policy_rules

    return {"kind": DOCUMENT_POLICY, "subject": kind, "label": kind, "value": preselect.document_policy(kind),
            "source": card.RULE, "options": [{"code": code} for code in policy_rules.VALUES]}


def _book_item(found) -> dict:
    """停在「待选账套」：可换的几份账套里选一份；这家公司已记住且仍算数的现用账套是已定。同一组候选的单问一次。"""
    books = found.get("account_sets") or []
    current = str(found.get("current") or "")
    return {"kind": CURRENT_BOOK, "subject": "|".join(sorted(item["key"] for item in books))[:card.SUBJECT_MAX],
            "value": current, "source": card.REMEMBERED if current else "",
            "options": [{"code": item["key"], "name": item.get("label") or item["key"]} for item in books]}


def _goods_item(doc, loaded, found, decision, kind, posting_from, today):
    """永续 / 混合库存账套里没声明入账类型的商品单，这家公司还没选「走库存 / 记费用」：预选走库存（评估 I3）。"""
    from pearnly_cowork.push.application import service
    from pearnly_cowork.push.express import goods_posting

    config = found["selected"]["config"]
    # A4（引导式录入第 1 期，覆盖表 G-A4）：CC 对话里（引导模式）这本账套还没报记账画像时，有商品行的单不再静默按非库存——同样问一次，
    # 预选「记费用」（就是原来的默认，点了才算）。其余入口照旧
    unknown = (assistant_guided.active() and not kind and found["direction"] != "sales"
               and posting_profile.profile_from_config(config).inventory_usage == "unknown"
               and any(isinstance(item, dict) for item in found["fields"].get("items") or []))
    if not unknown and (kind or not goods_posting.blocked(found["fields"], direction=found["direction"], config=config,
                                                          posting_kind=None, today=today,
                                                          vat_choice=(decision.get("applied") or {}).get("vat"))):
        return None
    if any(item.get("kind") == "item" and item.get("expense") for item in decision.get("needed") or ()):
        return None  # 已按行问存货对照（存货 / 新建存货 / 记费用，11-06）：不再整张问「走库存 / 记费用」
    if _lines_settled(found["fields"], (decision.get("applied") or {}).get("kinds")):
        return None  # 每一行都已按行定下（记过的、Express 同名的库存品 / 非库存品）：整张问「走库存 / 记费用」没有意义（测试机 11-09）
    company = goods_posting.company_key(loaded.doc.name, service._account_row(loaded, found["target"]))
    if not company:
        return None
    return {"kind": GOODS_POSTING, "subject": company, "value": "service" if unknown else "stock", "source": card.RULE,
            "options": [{"code": "stock"}, {"code": "service"}]}


def _lines_settled(fields, kinds) -> bool:
    """这张单每一行的过账类型都已按行定下（写账规则的 ``applied["kinds"]``）。"""
    from pearnly_core.express_rules.push import choices as choice_rules

    rows = [item for item in (fields or {}).get("items") or () if isinstance(item, dict)]
    return bool(rows and kinds) and all(choice_rules.line_kinds(rows, None, kinds))


def _preselect(item, rows, fields, direction, shared):
    """没有记忆时的预选 → (值, 来源)；没把握回 ("", "")。"""
    from pearnly_core.express_rules.contracts import pos as pos_contract
    from pearnly_core.express_rules.push.choices import NEW

    kind = item.get("kind")
    side = "buyer" if direction == "sales" else "seller"
    if kind == "party":
        value = preselect.party(item.get("options") or (), branch=fields.get(f"{side}_branch"))
    elif kind == "item":
        value = NEW
    elif kind == "account":
        suggested = item.get("suggested") or []
        value = str(suggested[0].get("code") or "") if suggested and isinstance(suggested[0], dict) else ""
        return (value, card.SUGGESTED) if value else ("", "")
    elif kind == "payment":
        value = preselect.payment(fields, direction=direction)
    elif kind == "vat":
        value = preselect.vat()
    elif kind == pos_contract.RECEIPT_KIND:
        value = preselect.receipt_account(item.get("label"), shared.get("accounts") or rows)
    elif kind == pos_contract.REFUND_KIND:
        value = preselect.refund_account(shared.get("accounts") or rows)
    elif kind == "location":
        value = str(item.get("current") or "")  # 多仓账套的默认仓：预选主仓（`choices.resolve`）
    else:
        value = ""
    return (value, card.RULE) if value else ("", "")


def choice_items(view, fields, direction) -> list:
    """写账规则要人选的项（`choices_view` 的 ``needed``）→ 卡片项，带可选值与预选。没声明过账类型的行（``expense``）
    多一个「记费用」，大列表按 ``products_all`` 搜（库存品与非库存品都列）。"""
    from pearnly_core.express_rules.push.choices import EXPENSE, NEW

    shared = view.get("options") or {}
    out = []
    for item in view.get("needed") or ():
        rows = _options(item, shared)
        if item.get("kind") in ("item", "party"):
            rows.insert(0, {"code": NEW, "name": ""})
        if item.get("kind") == "item" and item.get("expense"):
            rows.insert(1, {"code": EXPENSE, "name": ""})
        value, source = _preselect(item, rows, fields, direction, shared)
        notes = {str(row.get("code") or ""): str(row.get("reason") or "") for row in item.get("suggested") or ()
                 if isinstance(row, dict) and row.get("reason")}
        entry = {"kind": item["kind"], "subject": item["subject"], "label": str(item.get("label") or ""),
                 "value": value, "source": source, "direction": direction}
        if len(rows) > card.SMALL_LIST and item["kind"] in SEARCH_OF:
            # 大列表（科目表、存货）：只给预选值与名称，其余经 `search` 服务端分页取
            entry["search"] = SEARCH_OF[item["kind"]] + ("_all" if item["kind"] == "item" and item.get("expense") else "")
            entry["value_label"] = next((row["name"] for row in rows if row["code"] == value), "")
        else:
            entry["options"] = [{**row, "note": notes[row["code"]]} if notes.get(row["code"]) else row for row in rows]
        out.append(entry)
    return out


def search(company, doc_id, kind, txt, start, page_length) -> dict:
    """大列表的可选值（共用接口 ``write_precheck.api.search``）：这张单写进的那份账套的科目表 / 存货 / 对方主档，按编码或名称
    包含 ``txt`` 筛，服务端分页；存货、对方第一页最前面是「在 Express 新建」；``products_all``（没声明过账类型的行）库存品与
    非库存品都列、第二项是「记费用」。读不到这张单（不是本人能看的）照抛。只看不写。"""
    from pearnly_core.express_rules.push.choices import EXPENSE, NEW
    from pearnly_cowork.connections import service as connections
    from pearnly_cowork.history import storage
    from pearnly_cowork.push.application import service

    scope.require("intake")
    doc = storage.read(doc_id)
    if not doc.push_connection:
        frappe.throw(_("cowork.company.pending.no_target"))
    # 读推送目标要先锁连接（同推送准备）；只是看，取完回滚放锁
    loaded = connections.read_for_push(doc.push_connection, doc.workspace, doc.client, lock=True)
    found = service._express_context(doc, loaded, doc.push_account_set or None)
    frappe.db.rollback()
    config = found["selected"]["config"]
    if kind == "accounts":
        rows = config.get("reported_accounts") or []
    elif kind == "products":
        rows = [row for row in config.get("reported_products") or []
                if isinstance(row, dict) and str(row.get("kind") or "stock") == "stock"]
    elif kind == "products_all":
        rows = [row for row in config.get("reported_products") or [] if isinstance(row, dict)]  # 没声明过账类型的行
    else:
        rows = config.get("reported_customers" if found["direction"] == "sales" else "reported_suppliers") or []
    needle = str(txt or "").strip().casefold()
    matched = [{"code": str(row.get("code") or ""), "name": str(row.get("name") or "")} for row in rows
               if isinstance(row, dict) and row.get("code")
               and (not needle or needle in str(row.get("code")).casefold() or needle in str(row.get("name") or "").casefold())]
    if kind in ("products", "products_all", "parties") and not needle:
        matched.insert(0, {"code": NEW, "name": ""})
    if kind == "products_all" and not needle:
        matched.insert(1, {"code": EXPENSE, "name": ""})  # 这一行记费用（11-06 按行选）
    return {"options": matched[start:start + page_length], "more": len(matched) > start + page_length}


DOCUMENT_SUBJECT = "document:"  # 这张单自己定的那一项的归类键前缀（只管这一张，不和同一家别的单合并）


def document_payment_item(doc, fields, direction):
    """这张进项单自己的现 / 赊（单据上人定的 ``posting_payment_manual``，同复核页改付款方式；付款判定的第一级，压过账套记忆）：
    单独一项、已定（来源「这张单上人定的」）、能再改；没有回 None。已推出去的不列（同 planned）。"""
    from pearnly_core.express_rules.push import choices as choice_rules

    value = str(fields.get("posting_payment_manual") or "").strip()
    if direction != "purchase" or value not in choice_rules.PAYMENT_VALUES \
            or getattr(doc, "last_push_status", None) in ("pending", "success"):
        return None
    return {"kind": PAYMENT, "subject": DOCUMENT_SUBJECT + doc.name, "label": str(fields.get("seller_name") or ""),
            "value": value, "source": card.DOCUMENT, "direction": direction,
            "options": [{"code": code} for code in choice_rules.PAYMENT_VALUES]}


def _set_document_payment(doc, value) -> int:
    """只写这一张单自己的现 / 赊（不记进账套记忆、不改同一家别的单）：同复核页改付款方式（`service._document_payment`）。"""
    from pearnly_core.express_rules.contracts.history import primary_page_fields
    from pearnly_core.express_rules.push import choices as choice_rules
    from pearnly_cowork.push.application import service

    fields = primary_page_fields(json.loads(doc.pages or "[]"))
    direction = str(fields.get("direction") or "")
    if direction != "purchase" or value not in choice_rules.PAYMENT_VALUES:
        frappe.throw(_("cowork.choice.invalid"))
    subject = choice_rules.supplier_subject(direction, fields.get("seller_tax"), fields.get("seller_name"))
    return int(bool(service._document_payment(doc, fields, direction,
                                              [{"kind": PAYMENT, "subject": subject, "value": value}])))


def decided_items(decision, fields, direction) -> list:
    """已定、可改的项（费用科目、现 / 赊、抵不抵）：记住的、公司规则、Express 历史定的列进「已定」，定时任务默认处理定下的不列。"""
    from pearnly_core.express_rules.push import choices as choice_rules

    applied, decided_by = decision.get("applied") or {}, decision.get("decided_by") or {}
    side = "buyer" if direction == "sales" else "seller"
    name, tax = fields.get(f"{side}_name"), fields.get(f"{side}_tax")
    subject = choice_rules.supplier_subject(direction, tax, name)
    out = []
    for kind in SHOWN_DECIDED:
        value, source = str(applied.get(kind) or ""), DECIDED_BY.get(str(decided_by.get(kind) or ""))
        if not value or not source or not subject:
            continue
        item = {"kind": kind, "subject": subject, "label": str(name or ""), "value": value, "source": source,
                "direction": direction}
        if kind == "payment":
            item["options"] = [{"code": code} for code in choice_rules.PAYMENT_VALUES]
        elif kind == "vat":
            item["options"] = [{"code": code} for code in choice_rules.VAT_VALUES]
        else:
            item["search"] = "accounts"
        out.append(item)
    return out


# ── 产品钩子（``hooks.pearnly_write_precheck``）──


def check(company, doc_ids, hint=None) -> dict:
    """这家公司这批单的判定（共用层 ``write_precheck.api.check``）：按公司记住的分组先落到单据上（同代录预检），再逐张判；本人读不到的
    单不回、也不报存在与否。``hint``：用户说的费用类别（只影响科目建议）。"""
    from pearnly_cowork.assistant import operator_source
    from pearnly_cowork.companies import rules

    scope.require("intake")
    memory = rules.groups(company)
    documents = []
    for name in doc_ids:
        try:
            posting = operator_source._settle(name, memory) or None
            found = document(name, posting, hint, conversation=True)
        except (frappe.DoesNotExistError, frappe.PermissionError):
            frappe.db.rollback()
            frappe.clear_messages()
            continue
        found.pop("legacy", None)
        documents.append(found)
    title = frappe.db.get_value("Cowork Company", company, "title") if company else ""
    return {"company_title": title or "", "documents": documents}


def remember(company, answers) -> dict:
    """卡片答案（``{doc_id: [{kind, subject, value, label, remember}]}``）按各类现有的记法记下；同一项影响几张单只记一次。
    ``remember`` 为假的不记。只记、不推（待处理里重推见 :func:`resume`，代录由交单那一步录）。"""
    from pearnly_cowork.assistant import operator_source
    from pearnly_cowork.history import document_policy, storage
    from pearnly_cowork.push.application import goods_posting
    from pearnly_cowork.push.application import service

    scope.require("intake")
    done, saved = set(), 0
    for name, rows in (answers or {}).items():
        # ``document``：只答给这一张的（现 / 赊例外，主会话 11-05），不记住、只写这张单自己的
        rows = [row for row in rows or () if isinstance(row, dict) and (row.get("remember", True) or row.get("document"))]
        if not rows:
            continue
        doc = storage.read(name)
        choices = []
        for row in rows:
            kind, value = str(row.get("kind") or ""), str(row.get("value") or "").strip()
            once = (kind, str(row.get("subject") or ""))
            if row.get("document"):
                if kind != PAYMENT:
                    frappe.throw(_("cowork.choice.invalid"))
                saved += _set_document_payment(doc, value)
                continue
            if kind == OPERATOR_FORM:
                stop = _menu_stop(doc)
                if not stop or str(row.get("subject") or "").strip().upper() != stop["doc_type"]:
                    # 这张没停在子菜单、或停的不是这种单（旧前端把同一份答案带给批里每一张）：不是这张的，跳过、不拒、不占「记过」
                    continue
                key = (*once, stop.get("connection"), stop.get("account_set"))
                if key not in done:
                    done.add(key)
                    saved += _remember_menu(stop, row.get("subject"), value, str(row.get("label") or ""))
                continue
            if kind in CHOICE_KINDS:
                key = (*once, doc.push_connection, doc.push_account_set)
                if key not in done:
                    done.add(key)
                    choices.append({key_: row.get(key_) for key_ in ("kind", "subject", "value", "label")})
                continue
            if kind == DIRECTION:
                operator_source.regroup(company, name, value)  # 每张单都改方向；按公司记住同一对方
                saved += 1
                continue
            if once in done:
                continue
            done.add(once)
            if kind == DOCUMENT_POLICY:
                document_policy.choose(name, value)
            elif kind == GOODS_POSTING:
                goods_posting.choose(name, value)
            elif kind == CURRENT_BOOK:
                _choose_book(doc, value)
            else:
                frappe.throw(_("connection.config_invalid"))
            saved += 1
        if choices:
            saved += int(service.save_choices(name, doc.push_connection, doc.push_account_set or None,
                                              json.dumps(choices, ensure_ascii=False)).get("saved") or 0)
    return {"saved": saved}


def _choose_book(doc, value) -> None:
    """待选账套选了一份：多份同期时这家公司记住现用的那份（同待处理页「选账套」`pending_api.push_to_account_set` 记的那条）；
    这张单的去向换成选的这份，不推。"""
    from pearnly_cowork.assistant import pending_api
    from pearnly_cowork.companies import pending
    from pearnly_cowork.history import push_state

    row = _row(doc)
    picked = pending.account_set_pick(row, value)
    if not picked:
        frappe.throw(_("cowork.choice.invalid"))
    found = pending.current_book_choice(row, picked["key"])
    if found:
        pending_api._remember(found)
    push_state.retarget(doc.name, doc.push_connection, picked["key"])


def resume(company, doc_ids) -> list:
    """待处理里停着等人定的单，记下答案后按原来的写入路径重推（同待处理页「当场选」`pending_api.answer`）；归代录的、已推出去 /
    在途的不动。在 CC 对话里不推（B1：写入只走计划，共用层同样不调这里）。"""
    from pearnly_cowork.assistant import pending_api, tools
    from pearnly_cowork.history import storage
    from pearnly_cowork.push.application import service

    scope.require("intake")
    if (getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id"):
        return []
    results = []
    for name in doc_ids:
        doc = storage.read(name)
        if str(doc.get("last_push_status") or "") in ("success", "pending") or service.operator_owned(doc):
            continue
        result = tools._push_one(name, route_device=pending_api._reader(name, doc.push_connection))
        frappe.db.commit()
        results.append({"doc_id": name, "status": str(result.get("status") or ""),
                        "reason": str(result.get("reason") or "")[:300]})
    return results
