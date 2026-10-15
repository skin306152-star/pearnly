"""Authorized native preparation. No worker registration or external dispatch."""
from copy import deepcopy
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import access as connection_access
from pearnly_cowork.connections import service as connections
from pearnly_cowork.connections import preparation as preparation_sources
from pearnly_cowork.targets import stock_groups
from pearnly_cowork.contracts import document_policy as policy_rules
from pearnly_cowork.contracts import pos as pos_contract
from pearnly_cowork.contracts.history import primary_page, primary_page_fields
from pearnly_cowork.contracts.push import clean_tax_id, explicit_direction, resolve_account_direction, resolve_posting_kind
from pearnly_cowork.history import document_policy, push_state, storage
from pearnly_cowork.push import choices as choice_store
from pearnly_cowork.push.application.provenance import original_history
from pearnly_cowork.push.express import account_suggest
from pearnly_cowork.push.express import choices as choice_rules
from pearnly_cowork.push.express import pushed as pushed_rules
from pearnly_cowork.push.express.common import SRC_CATEGORY, resolve_account_sourced
from pearnly_cowork.push.express.prepare import prepare_express
from pearnly_cowork.push.express.preflight import ExpressContext
from pearnly_cowork.push.express.history import flatten_history_for_mrerp
from pearnly_cowork.push.express.common import parse_invoice_date
from pearnly_cowork.push.mrerp import runner
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.push.receipts import derive_external_ref, express_prior_number
from pearnly_cowork.targets.choice import endpoint_with_account_choice
from pearnly_cowork.targets.contracts import TargetSelectionError
from pearnly_cowork.targets import account_match
from pearnly_cowork.targets.freeze import freeze_target
from pearnly_cowork.targets.identity import normalize_express_account_key, same_company_account_sets
from pearnly_cowork.targets.selection import resolve_account_choice


def _context(doc, loaded, account=""):
    return ledger.ReservationContext(actor=frappe.session.user, workspace=doc.workspace,
        client=doc.client or "", history=doc.name, history_version=int(doc.record_version),
        connection=loaded.doc.name, account_key=account,
        branch="legacy", binding_generation=0,
        connection_revision=loaded.revision)


def _view(row):
    """A preparation receipt, never credentials, transport payload or raw reply."""
    result = {key: row.get(key) for key in (
        "name", "status", "revision", "history", "connection", "history_version",
        "connection_revision", "reason", "result_unknown", "replayed", "blocked_by", "blocked_reason")}
    result["checks"] = deepcopy(row.get("checks") or [])
    evidence = (row.get('frozen_target') or {}).get('preparation_evidence') or {}
    result['preparation_states'] = deepcopy(evidence.get('states') or {})
    result['has_reported_credential'] = bool(evidence.get('credential'))
    result["external_reference"] = derive_external_ref(
        (row.get("frozen_target") or {}).get("adapter"), row.get("response_body"), row.get("status"))
    return result


def _document_block(doc):
    """转账凭条 / 预扣税凭证这类录不录还没选、或选了不录入 → 原因码（`contracts.document_policy`）；能推回 None。"""
    fields = primary_page_fields(json.loads(doc.pages or "[]"))
    return policy_rules.push_block(fields, document_policy.read(doc.workspace))


def prepare(history, connection, request_key, expected_history_version, expected_connection_revision,
            account_set_key=None, catalog_request_id=None, catalog_revision=None, posting_kind=None,
            *, confirm_duplicate=False, fixed=None):
    scope.require("intake")
    doc = storage.read(history)
    loaded = connections.read_for_push(connection, doc.workspace, doc.client, lock=True)
    # Same lock order as connection refresh and ledger: connection before history.
    doc = storage.read(history, for_update=True)
    context = _context(doc, loaded)
    intent = {"history": history, "connection": connection,
        "expected_history_version": storage.revision(expected_history_version),
        "expected_connection_revision": storage.revision(expected_connection_revision),
        "account_set_key": account_set_key or None, "catalog_request_id": catalog_request_id or None,
        "catalog_revision": storage.revision(catalog_revision) if catalog_revision is not None else None,
        "posting_kind": posting_kind or None}
    if confirm_duplicate:
        # 只在点了「仍然推送」时写进意图：老的请求键回放时意图照旧对得上。
        intent["confirm_duplicate"] = True
    if posting_kind not in (None, "", "service", "stock"):
        frappe.throw(_("push.posting_kind_invalid"))
    previous = ledger.lookup_request(context, request_key)
    if previous:
        if (previous.get("frozen_target") or {}).get("request_intent") != intent:
            frappe.throw(_("push.request_conflict"))
        # Resolve immutable original references again: revoking the old invoice
        # must not leave an idempotency key as an access capability to its ERP ID.
        original_history(doc)
        return _view(previous)
    if int(doc.record_version) != intent["expected_history_version"] or loaded.revision != intent["expected_connection_revision"]:
        storage.conflict()
    adapter = loaded.endpoint.get("adapter")
    if adapter not in ("express", "mrerp"):
        frappe.throw(_("push.adapter_not_connected"))
    if frappe.conf.get("pearnly_cowork_erp_push_enabled") not in (True, 1):
        return {"status": "disabled", "reason": "ERR_EXPRESS_DISABLED", "history": doc.name, "connection": connection}
    blocked = _document_block(doc)
    if blocked:  # 原因码由提交记在票上，复核页按码出文案与「录入 / 不录入」按钮
        frappe.throw(blocked)
    if adapter != "express" and pos_contract.present(primary_page(json.loads(doc.pages or "[]"))):
        # POS 日汇总票的付款方式拆分只按 Express 原生记法写（`contracts/pos.py`）：推往别的 ERP 会全额记成现金
        frappe.throw(pos_contract.EXPRESS_ONLY)
    if adapter == "mrerp":
        return _view(_prepare_mrerp(doc, loaded, request_key, intent, account_set_key,
                                    catalog_request_id, catalog_revision))
    try:
        target = freeze_target(loaded.endpoint, access=loaded.access, evidence=loaded.evidence,
            account_set_key=account_set_key, request_id=catalog_request_id, revision=catalog_revision)
        choice = resolve_account_choice(loaded.endpoint, tenant_id=loaded.access.tenant_id,
            user_id=loaded.access.user_id, account_set_key=account_set_key,
            state={"snapshot": loaded.evidence.snapshot or {}})
    except TargetSelectionError as error:
        frappe.throw(_(error.code), frappe.PermissionError if error.status_code == 403 else frappe.ValidationError)
    selected = endpoint_with_account_choice(deepcopy(loaded.endpoint), choice)
    fixed = _fixed(doc.name, loaded.doc.name, target.account_set, fixed)
    own_tax = frappe.db.get_value("Cowork Workspace", doc.workspace, "tax_id", for_update=True) or ""
    history_value = storage.project(doc)
    history_value.update(id=doc.name, client_id=doc.client or None,
        tenant_id=loaded.access.tenant_id, workspace_client_id=doc.workspace)
    prior = original_history(doc)
    context = _context(doc, loaded, target.account_set)
    receipt = ledger.read_success(context=context, history=prior)
    prior_docnum = None
    if receipt:
        prior_docnum = express_prior_number(receipt.get("response_body"))
        if not prior_docnum:
            frappe.throw(_("push.prior_document_number_missing"))
    pages = history_value.get("pages") or []
    primary = primary_page(pages)
    fields = primary.get("fields") or {}
    seller_tax = clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id"))
    flat = flatten_history_for_mrerp(history_value)
    invoice_date = parse_invoice_date(flat.get('invoice_date') or (flat.get('fields') or {}).get('date'))
    account_tax = _account_tax(_account_row(loaded, target))
    direction = resolve_account_direction(flat, history_value, own_tax_id=own_tax, account_tax_id=account_tax)
    _require_matching_account(loaded, target, direction, fields, invoice_date)
    if direction and not explicit_direction(flat, history_value):
        # 按税号判出的方向交给预检当明确方向（预检与这里同一套判定，账套税号也给它）
        history_value["direction"] = direction
    preparation, evidence = preparation_sources.collect(loaded, target,
        seller_tax=seller_tax if direction == 'purchase' else '',
        invoice_date=invoice_date.isoformat() if invoice_date else None)
    # Choice clears other-account caches before applying attributed observations.
    selected['config'].update(deepcopy(preparation['reported_config']))
    _apply_stock_group(selected['config'], loaded.doc, target.account_set)
    decision = _decisions(loaded.doc.name, target.account_set, flat, fields, direction, posting_kind,
                          selected['config'], preparation, fixed,
                          siblings=lambda: _same_company(loaded, target.account_set))
    if decision["needed"]:
        masters = connections.ensure_account_masters(loaded.doc.name, target.account_set)
        wait = masters_wait(masters, decision["needed"])
        if wait:
            # 要选的科目没有可选值（科目表还没读到）：不留一条「待人选」却没得选的记录，说清楚、读到后再推。
            # 读的请求先提交（下面抛错会整笔回滚）；没到这一步时请求随这次准备一起提交，中途不提交、不放锁。
            if masters.get("requested"):
                frappe.db.commit()
            frappe.throw(wait["message"])
        # 拿不准的项（对方档 / 存货 / 费用科目 / 付款 / 抵进项 / 部门）还没人选：不发小助手，停在这里等人选一次。
        # 费用科目带上建议（只看存着的历史，这里不请电脑读、不提交）
        _suggest(decision["needed"], loaded.doc.name, target.account_set, fields, selected['config'])
        kinds = ",".join(dict.fromkeys(item["kind"] for item in decision["needed"]))
        row = ledger.reserve(context=context, request_key=request_key, frozen_target={**asdict(target),
            "request_intent": intent, "connection_revision": loaded.revision, "original_history": prior,
            "preparation_date": datetime.now(ZoneInfo("Asia/Bangkok")).date().isoformat(),
            "own_tax_id": own_tax, "preparation_evidence": evidence},
            frozen_payload={"adapter": "express", "choice_needed": decision["needed"]},
            disposition="manual", reason=f"choice_needed:{kinds}", checks=[])
        return _view(row)
    selected['config']['choices'] = decision["applied"]
    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    prepared = prepare_express(selected, history_value, posting_kind=posting_kind,
        context=ExpressContext(enabled=True, today=today, own_tax_id=own_tax, account_tax_id=account_tax,
            tenant_id=loaded.access.tenant_id, mappings=preparation["mappings"],
            supplier_profile=preparation["supplier_profiles"].get(seller_tax),
            bank_index=preparation["bank_index"], prior_docnum=prior_docnum))
    frozen = asdict(target)
    frozen.update(request_intent=intent, connection_revision=loaded.revision,
        original_history=prior, preparation_date=today.isoformat(), own_tax_id=own_tax,
        preparation_evidence=evidence)
    payload = prepared.payload or {}
    if confirm_duplicate and payload:
        # 小助手的查重闸认这个键：Express 已有同号不同内容 / 上一版还在时，会计确认过就照写（`duplicate_gate`）。
        payload["duplicate_confirmed"] = True
    row = ledger.reserve(context=context, request_key=request_key, frozen_target=frozen,
        frozen_payload=payload,
        disposition="prepared" if prepared.disposition == "ready" else "manual",
        reason=prepared.reason or "", checks=prepared.preflight.checks_json())
    return _view(row)


def _apply_stock_group(config, connection, account_set):
    """存货科目组每个账套选一次（用户 2026-09-28）：这份账套选过就用它，新建库存品都挂它；没选过照旧。"""
    chosen = stock_groups.chosen(connection.get("stock_acc_groups"), normalize_express_account_key(account_set))
    if chosen:
        config["stock_acccod"] = chosen


_ACCOUNT_CONFLICTS = {
    account_match.TAX_MISMATCH: "The own tax ID on this document does not match the chosen account set. "
                                "Check the account set and send it again.",
    account_match.PERIOD_MISMATCH: "The document date is outside the fiscal period of the chosen account set. "
                                   "Check the year and send it again.",
    account_match.BRANCH_MISMATCH: "This document belongs to another branch than the chosen account set. "
                                   "Choose that branch's account set and send it again.",
}


def _account_row(loaded, target):
    """推往的这份账套在连接目录快照里的那一行（带 attributes：税号、会计期间）；目录里没有回 None。"""
    rows = (loaded.evidence.snapshot or {}).get("account_sets") or []
    if isinstance(rows, str):
        rows = json.loads(rows)
    key = normalize_express_account_key(target.account_set)
    return next((row for row in rows if isinstance(row, dict)
                 and normalize_express_account_key(row.get("source_id")) == key), None)


def _account_tax(account):
    attributes = (account or {}).get("attributes")
    return clean_tax_id(attributes.get("tax_id")) if isinstance(attributes, dict) else ""


def _require_matching_account(loaded, target, direction, fields, invoice_date):
    """用户选的账套与票面确定对不上就不推（规则见 `targets/account_match.py`）：本方税号不同、分店不同（同一法人有几个
    分店的账套时）、日期不在会计期间里。"""
    account = _account_row(loaded, target)
    facts = {**fields, "date": invoice_date.isoformat() if invoice_date else fields.get("date")}
    rows = (loaded.evidence.snapshot or {}).get("account_sets") or []
    if isinstance(rows, str):
        rows = json.loads(rows)
    reason = account_match.conflict("express", account, direction=direction, fields=facts, catalog=rows,
                                    work_branch=lambda: _work_branch(loaded.doc.name, target.account_set))
    if reason:
        frappe.throw(_(_ACCOUNT_CONFLICTS[reason]))


def _work_branch(connection, account_set):
    """票面没有本方分店时用的分店：CC 对话指定的（钩子 ``cowork_push_work_branch``，只在推往那家公司的账套时给）；
    push 域不导入 companies。取不到回空串、不挡推送。"""
    for path in frappe.get_hooks("cowork_push_work_branch") or []:
        try:
            found = str(frappe.get_attr(path)(connection, account_set) or "")
        except Exception:
            frappe.log_error(title="Cowork push work branch failed")
            continue
        if found:
            return found
    return ""


def _fixed(history, connection, account_set, fixed):
    """这张票已定的选择（`{kind: value}`，优先于账套记忆、不写进账套记忆）。

    调用方给了（文件夹后台推送，`{}` 也算给了）就照用；没给（None：网页复核页、手机、CC 重推）就按登记的提供者取
    （`cowork_push_fixed_choices`，定时任务的默认处理）。push 域不导入提供者所在的域；对方档不在此列。"""
    if fixed is not None:
        return fixed
    found = {}
    for path in frappe.get_hooks("cowork_push_fixed_choices") or []:
        found.update(frappe.get_attr(path)(history, connection, account_set) or {})
    return {kind: value for kind, value in found.items() if kind != "party"} or None


def _decisions(connection, account_set, flat, fields, direction, posting_kind, config, preparation, fixed=None,
               siblings=None):
    """这张票在这份账套里的选择（规则见 `push/express/choices.py`）：记过的套用，缺的列出来要人选。

    票上会计明确改过的付款方式按对方记下（「按对方记住会计第一次的选择」，用户 2026-09-28）。
    ``siblings``：取同一家公司其他账套的函数（`_same_company`；只有 POS 票才取），POS 收款科目按公司问一次
    （`_borrow_receipts`）。"""
    category = str(flat.get("category") or (flat.get("fields") or {}).get("category") or "").strip()
    _, source = resolve_account_sourced((preparation.get("mappings") or {}).get("accounts") or [], category, None)
    remembered = choice_store.remembered(connection, account_set)
    kind = resolve_posting_kind(posting_kind, flat)
    receipts = _pos_receipts(flat)
    _borrow_receipts(remembered, connection, siblings, receipts, config)
    decision = choice_rules.resolve(fields, direction=direction, posting_kind=kind, config=config,
                                    remembered=remembered, account_resolved=source == SRC_CATEGORY, fixed=fixed,
                                    receipts=receipts)
    settled = _settled_account(decision["needed"], connection, account_set, fields, config, fixed)
    if settled:
        # 历史很一致：当作这家已记住的科目用（只这一次判断，不写进账套记忆；主档页可见可删）
        subject = next(item["subject"] for item in decision["needed"] if item.get("kind") == "account")
        remembered = {**remembered, "account": {**(remembered.get("account") or {}), subject: settled["code"]}}
        decision = choice_rules.resolve(fields, direction=direction, posting_kind=kind, config=config,
                                        remembered=remembered, account_resolved=source == SRC_CATEGORY, fixed=fixed,
                                        receipts=receipts)
        decision["history"] = {"account": settled["code"], "docs": settled["docs"]}
    applied = decision["applied"]
    remember = applied.pop("remember_payment", None)
    if remember:
        name, tax = ((fields.get("seller_name"), fields.get("seller_tax")) if direction == "purchase"
                     else (fields.get("buyer_name"), fields.get("buyer_tax")))
        choice_store.record(connection, account_set, "payment",
                            choice_rules.supplier_subject(direction, tax, name), remember, name)
    return decision


def _settled_account(needed, connection, account_set, fields, config, fixed):
    """要人选费用科目、而这家供应商从没被人选过（也没有任务默认）时，历史很一致的科目（用户 2026-10-15：同一供应商按税号
    合并各分店，近 12 个月 ≥3 次且全记同一个科目，就直接用、不再问）；否则 None（仍作建议、问一次）。

    历史经钩子 ``cowork_push_account_history`` 只读存着的（不请电脑、不写库）；科目须还在这份账套的科目表里。"""
    wanted = [item for item in needed or () if item.get("kind") == "account"]
    if not wanted or wanted[0].get("current") or str((fixed or {}).get("account") or "").strip():
        return None
    seller = {"tax": fields.get("seller_tax") or fields.get("seller_tax_id") or "", "name": fields.get("seller_name") or ""}
    chart = {str(row.get("code") or "").strip() for row in config.get("reported_accounts") or [] if isinstance(row, dict)}
    for path in frappe.get_hooks("cowork_push_account_history") or []:
        found = (frappe.get_attr(path)(connection, account_set, seller, False) or {}).get("settled")
        if isinstance(found, dict) and found.get("code") and (not chart or found["code"] in chart):
            return found
    return None


def _pos_receipts(flat):
    """POS 日汇总票里非现金的付款方式 ``[(归类键, 写法)]``（每一种要一个收款科目）；不是 POS 票或形状不对回空
    （形状不对由映射器拦成 `pos_facts_invalid`）。"""
    try:
        found = pos_contract.facts(primary_page(flat.get("pages") or []))
    except pos_contract.PosFactsError:
        return []
    return [(payment.subject, payment.label) for payment in found.non_cash()] if found else []


def _same_company(loaded, account_set):
    """这条连接里与这份账套同属一家公司（目录里公司名相同、年度不同）的其他账套键；读不出公司名回空。
    与 POS 版式按公司记同一判据（`targets.identity.account_set_company`）。"""
    rows = (loaded.evidence.snapshot or {}).get("account_sets") or []
    if isinstance(rows, str):
        rows = json.loads(rows)
    return same_company_account_sets(rows, account_set)


def _borrow_receipts(remembered, connection, siblings, receipts, config):
    """POS 收款科目按公司问一次（用户 2026-10-13）：这份账套还没记过的付款方式，同一家公司别的年度账套最近记过、
    科目又在本账套科目表里的直接带出（`choices.borrow_receipts`）；只用于这一次判断，不写进本账套记忆。"""
    wanted = [subject for subject, _label in receipts or () if subject not in (remembered.get("receipt") or {})]
    if not wanted or siblings is None or not config.get("reported_accounts"):
        return
    others = siblings()
    if not others:
        return
    borrowed = choice_store.latest(connection, others, pos_contract.RECEIPT_KIND, wanted)
    choice_rules.borrow_receipts(remembered, receipts, borrowed, config.get("reported_accounts"))


def choices_view(history, connection, account_set_key=None, posting_kind=None, *, fixed=None, hint=None):
    """复核页提交前 / 退回后要人选的项与可选值（与准备同一套判定，不建推送记录）。

    要人选费用科目时那一项带 ``suggested``（最多 2 个，见 `_suggest`）；``hint`` 是会计员在对话里明说的费用类别（CC 传），
    命中的科目排第一。这本账套近 12 个月的常用科目正在由电脑读时回 ``history: "reading"``。"""
    scope.require("intake")
    doc = storage.read(history)
    loaded = connections.read_for_push(connection, doc.workspace, doc.client, lock=True)
    if loaded.endpoint.get("adapter") != "express" or _document_block(doc):
        # 这类单据录不录还没定（或定了不录）：先不问对方档、科目这些，提交时停在口径那一步
        return {"needed": [], "options": {}}
    target, flat, fields, direction, preparation, config = _express_facts(doc, loaded, account_set_key)
    fixed = _fixed(doc.name, loaded.doc.name, target.account_set, fixed)
    decision = _decisions(loaded.doc.name, target.account_set, flat, fields, direction, posting_kind,
                          config, preparation, fixed, siblings=lambda: _same_company(loaded, target.account_set))
    frappe.db.rollback()  # 只是看：记付款方式的那一笔不在这里落
    masters = account_masters(loaded.doc.name, target.account_set)
    kinds = {item["kind"] for item in decision["needed"]}
    options = {}
    if "item" in kinds:
        options["products"] = [{"code": row["code"], "name": row.get("name") or ""}
                               for row in config.get("reported_products") or []
                               if isinstance(row, dict) and str(row.get("kind") or "stock") == "stock"][:5000]
    if kinds & {"account", "receipt"}:
        options["accounts"] = [{"code": row["code"], "name": row.get("name") or ""}
                               for row in config.get("reported_accounts") or [] if isinstance(row, dict)][:2000]
    state, changed = _suggest(decision["needed"], loaded.doc.name, target.account_set, fields, config, hint=hint,
                              refresh=True)
    if changed:
        frappe.db.commit()  # 建了 / 了结了读历史的请求（网页取「要选的项」是 GET，不提交就被回滚掉）
    result = {"needed": decision["needed"], "options": options, "account_set": target.account_set,
              "masters": masters}
    if state == "reading":
        result["history"] = state
    wait = masters_wait(masters, decision["needed"])
    if wait:
        # 要选的科目还没有可选值（这份账套的科目表还没读到）：先等电脑读，不拿空的科目表请人选
        result["wait"] = wait
    if fixed:
        result["fixed"] = dict(fixed)  # 这张票已定的（定时任务默认处理），不再问
    return result


def _suggest(needed, connection, account_set, fields, config, *, hint=None, refresh=False):
    """要人选费用科目的项带上 ``suggested`` ``[{code, name, source, reason}]``（`push.express.account_suggest`：会计员说的
    类别、这家供应商近 12 个月的常用科目、名称与大类，确定性匹配）。回 (历史的状况, 要不要提交)。

    历史经钩子 ``cowork_push_account_history`` 取（`readback.history.for_push`；push 域不导入它，同 `_fixed`）：
    ``refresh`` 时缺了 / 旧了会请电脑读（建请求，调用方提交），否则只看存着的。没有提供者、取不到就只按名称与大类建议。"""
    wanted = [item for item in needed or () if item.get("kind") == "account"]
    if not wanted:
        return None, False
    seller = {"tax": fields.get("seller_tax") or fields.get("seller_tax_id") or "", "name": fields.get("seller_name") or ""}
    state, accounts, changed = "none", [], False
    for path in frappe.get_hooks("cowork_push_account_history") or []:
        found = frappe.get_attr(path)(connection, account_set, seller, refresh) or {}
        changed = changed or bool(found.get("changed"))
        if found.get("state") in ("ready", "reading"):
            state, accounts = found["state"], found.get("accounts") or accounts
    for item in wanted:
        item["suggested"] = account_suggest.suggest(fields, accounts=config.get("reported_accounts") or [],
                                                    history=accounts, hint=hint)
    return state, changed


# 可选值来自这份账套科目表的选择（费用科目、POS 收款科目）：科目表没读到时这几项没法选。对方档、存货只在主档读到时才问。
CHART_KINDS = ("account", "receipt")
MASTERS_TEXT = {"reading": "cowork.masters.reading", "offline": "cowork.masters.offline",
                "update_needed": "cowork.masters.update_needed", "failed": "cowork.masters.failed",
                "unavailable": "cowork.masters.unavailable"}


def account_masters(connection, account_set):
    """这份 Express 账套的主档在不在；缺的、读过很久的请电脑读（`connections.ensure_account_masters`，同一时刻只排一个）。
    建了请求就当场提交（网页取「要选的项」是 GET，不提交就被回滚掉）。"""
    found = connections.ensure_account_masters(connection, account_set)
    if found.pop("requested", False):
        frappe.db.commit()
    return found


def masters_wait(masters, needed):
    """要人选的项里有靠科目表的、而这份账套的科目表此刻不能用：回 ``{"state", "message", "error_code"?}``，否则 None。"""
    state = (masters or {}).get("state") or "ready"
    if state == "ready" or not any(item.get("kind") in CHART_KINDS for item in needed or []):
        return None
    wait = {"state": state, "message": _(MASTERS_TEXT.get(state, MASTERS_TEXT["reading"]))}
    if masters.get("error_code"):
        wait["error_code"] = masters["error_code"]
    return wait


def save_choices(history, connection, account_set_key, items):
    """记下会计在复核页选的（每项 `{kind, subject, value, label}`）；值须在这份账套的主档里（或「新建」）。"""
    scope.require("intake")
    doc = storage.read(history)
    loaded = connections.read_for_push(connection, doc.workspace, doc.client, lock=True)
    target, _flat, _fields, _direction, _preparation, config = _express_facts(doc, loaded, account_set_key)
    items = json.loads(items) if isinstance(items, str) else items
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        frappe.throw(_("connection.config_invalid"))
    # 定时任务默认处理定下的类别只管这张票：不写进账套记忆（要改就改任务），整批不记。
    covered = _fixed(doc.name, loaded.doc.name, target.account_set, None) or {}
    if any(isinstance(item, dict) and str(item.get("kind") or "") in covered for item in items):
        frappe.throw(_("cowork.choice.task_default"))
    allowed = {
        "party": None, "item": {r.get("code") for r in config.get("reported_products") or [] if isinstance(r, dict)},
        "account": {r.get("code") for r in config.get("reported_accounts") or [] if isinstance(r, dict)},
        "receipt": {r.get("code") for r in config.get("reported_accounts") or [] if isinstance(r, dict)},
        "department": {r.get("code") for r in config.get("reported_departments") or [] if isinstance(r, dict)},
        "payment": set(choice_rules.PAYMENT_VALUES), "vat": set(choice_rules.VAT_VALUES)}
    parties = {r.get("code") for key in ("reported_customers", "reported_suppliers")
               for r in config.get(key) or [] if isinstance(r, dict)}
    saved = 0
    for item in items:
        if not isinstance(item, dict) or item.get("kind") not in allowed:
            frappe.throw(_("connection.config_invalid"))
        kind, value = item["kind"], str(item.get("value") or "").strip()
        options = parties if kind == "party" else allowed[kind]
        if kind in ("party", "item") and value == choice_rules.NEW:
            pass
        elif not value or (options and value not in options) or (kind in ("payment", "vat") and value not in options):
            frappe.throw(_("cowork.choice.invalid"))
        saved += int(choice_store.record(loaded.doc.name, target.account_set, kind, str(item.get("subject") or ""),
                                         value, item.get("label")))
    return {"saved": saved}


def _express_facts(doc, loaded, account_set_key):
    """与准备同一套取数：冻结目标、推送准备主档、方向、主页面字段。"""
    try:
        target = freeze_target(loaded.endpoint, access=loaded.access, evidence=loaded.evidence,
                               account_set_key=account_set_key, request_id=None, revision=None)
        choice = resolve_account_choice(loaded.endpoint, tenant_id=loaded.access.tenant_id,
            user_id=loaded.access.user_id, account_set_key=account_set_key,
            state={"snapshot": loaded.evidence.snapshot or {}})
    except TargetSelectionError as error:
        frappe.throw(_(error.code), frappe.PermissionError if error.status_code == 403 else frappe.ValidationError)
    selected = endpoint_with_account_choice(deepcopy(loaded.endpoint), choice)
    own_tax = frappe.db.get_value("Cowork Workspace", doc.workspace, "tax_id") or ""
    history_value = storage.project(doc)
    history_value.update(id=doc.name, client_id=doc.client or None,
        tenant_id=loaded.access.tenant_id, workspace_client_id=doc.workspace)
    pages = history_value.get("pages") or []
    primary = primary_page(pages)
    fields = primary.get("fields") or {}
    flat = flatten_history_for_mrerp(history_value)
    invoice_date = parse_invoice_date(flat.get('invoice_date') or (flat.get('fields') or {}).get('date'))
    direction = resolve_account_direction(flat, history_value, own_tax_id=own_tax,
                                          account_tax_id=_account_tax(_account_row(loaded, target)))
    seller_tax = clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id"))
    preparation, _evidence = preparation_sources.collect(loaded, target,
        seller_tax=seller_tax if direction == 'purchase' else '',
        invoice_date=invoice_date.isoformat() if invoice_date else None)
    selected['config'].update(deepcopy(preparation['reported_config']))
    return target, flat, fields, direction, preparation, selected['config']


def _prepare_mrerp(doc, loaded, request_key, intent, account_set_key, catalog_request_id, catalog_revision):
    """MR.ERP：冻结目标账套 + 整理后的票面 + 已配置映射 + 本方税号，交服务端执行（`push.mrerp.runner`）。

    推什么由票面决定（旧 `push_mrerp_history` 同一组输入）：用户在入口选的进 / 销项与付款方式、
    复核改过的字段都已写在票面里（`direction` / `posting_payment_manual`），分流与建档在执行时按旧规则做。
    """
    # MR.ERP 没有打通现金采购（只有应付的采购 / 费用模块）：进项 + 现金在服务端挡下，不悄悄改成赊购
    # （旧线上 `mrerp_purchase_cash_unverified`；此前只靠入口不给这个选项）。
    fields = primary_page_fields(json.loads(doc.pages or "[]"))
    if fields.get("direction") == "purchase" and fields.get("posting_payment_manual") == "cash":
        frappe.throw(_("MR.ERP purchases can only be entered on credit."))
    try:
        target = freeze_target(loaded.endpoint, access=loaded.access, evidence=loaded.evidence,
            account_set_key=account_set_key, request_id=catalog_request_id, revision=catalog_revision)
    except TargetSelectionError as error:
        frappe.throw(_(error.code), frappe.PermissionError if error.status_code == 403 else frappe.ValidationError)
    own_tax = frappe.db.get_value("Cowork Workspace", doc.workspace, "tax_id", for_update=True) or ""
    history_value = storage.project(doc)
    history_value.update(id=doc.name, client_id=doc.client or None,
        tenant_id=loaded.access.tenant_id, workspace_client_id=doc.workspace)
    prior = original_history(doc)
    mappings = deepcopy(connections.mrerp_mappings(loaded))
    if own_tax:
        mappings["_own_tax_id"] = own_tax  # 匹配闸锚点：确认是别家的票就挡下，不推错账套
    # 这份账套各类单据选过的 MR.ERP 菜单（选一次记住，用户 2026-09-28）；推送时按导入页核对，失效就停下。
    mappings["_mrerp_menus"] = connections.mrerp_chosen_menus(loaded, target.account_set)
    # 主档默认值（部门 / 员工 / 科目等，选一次记住，用户 2026-10-08）；推送时按这次读到的主档核对，失效就停下。
    mappings["_mrerp_defaults"] = connections.mrerp_chosen_defaults(loaded, target.account_set)
    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    frozen = asdict(target)
    frozen.update(request_intent=intent, connection_revision=loaded.revision, original_history=prior,
        preparation_date=today.isoformat(), own_tax_id=own_tax)
    # 冻结进账本须是纯 JSON：库里读出的日期 / 时间 / Decimal 转成字符串（生成器按字符串解析日期）。
    payload = json.loads(json.dumps({"histories": [flatten_history_for_mrerp(history_value)], "mappings": mappings},
                                    ensure_ascii=False, default=str))
    return ledger.reserve(context=_context(doc, loaded, target.account_set), request_key=request_key,
        frozen_target=frozen, frozen_payload=payload, disposition="prepared")


def detail(name):
    # Native generic access remains denied. The purpose-built log view checks
    # the current log role and connection grant even for disabled connections.
    row = frappe.db.get_value("Cowork Push Record", name,
        ["connection", "workspace", "client"], as_dict=True)
    if not row:
        frappe.throw(_("push.record_not_found"), frappe.DoesNotExistError)
    connections.read_for_log(row.connection, row.workspace, row.client)
    doc = frappe.get_doc("Cowork Push Record", name, for_update=True)
    if (doc.connection, doc.workspace, doc.client or "") != (row.connection, row.workspace, row.client or ""):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return _view(ledger.project(doc))


def resolve_unknown(name, entered, docnum=None):
    """复核页「已在 ERP 核对」：结果不明的推送由人判定已入账 / 未入账（规则见 `ledger.resolve_unknown`）。"""
    scope.require("intake")
    row = frappe.db.get_value("Cowork Push Record", name,
        ["connection", "workspace", "client", "history"], as_dict=True)
    if not row:
        frappe.throw(_("push.record_not_found"), frappe.DoesNotExistError)
    loaded = connections.read_for_push(row.connection, row.workspace, row.client)
    history = storage.read(row.history, for_update=True)
    doc = frappe.get_doc("Cowork Push Record", name, for_update=True)
    context = _context(history, loaded, doc.account_key)
    return _view(ledger.resolve_unknown(name, context=context, entered=entered, docnum=docnum))


def cancel_prepared(name, expected_revision):
    scope.require("intake")
    row = frappe.db.get_value("Cowork Push Record", name,
        ["connection", "workspace", "client", "history"], as_dict=True)
    if not row:
        frappe.throw(_("push.record_not_found"), frappe.DoesNotExistError)
    loaded = connections.read_for_push(row.connection, row.workspace, row.client)
    history = storage.read(row.history, for_update=True)
    doc = frappe.get_doc("Cowork Push Record", name, for_update=True)
    context = _context(history, loaded, doc.account_key)
    return _view(ledger.cancel_prepared(name, context=context,
        expected_revision=storage.revision(expected_revision)))


def confirm(name, expected_revision, request_key, *, credential):
    """Called by authenticated Companion submission orchestration, never RPC."""
    scope.require('intake')
    row = frappe.db.get_value('Cowork Push Record', name,
        ['connection', 'workspace', 'client', 'history'], as_dict=True)
    if not row:
        frappe.throw(_('push.record_not_found'), frappe.DoesNotExistError)
    loaded = connections.read_for_push(row.connection, row.workspace, row.client)
    history = storage.read(row.history, for_update=True)
    original_history(history)
    doc = frappe.get_doc('Cowork Push Record', name, for_update=True)
    context = _context(history, loaded, doc.account_key)
    return _view(ledger.confirm(name, context=context, credential=credential,
        expected_revision=storage.revision(expected_revision), request_key=request_key))


def confirm_server(name, expected_revision, request_key):
    """MR.ERP：人工确认（prepared → pending）后排服务端执行任务。Express 走小助手确认入口。"""
    scope.require('intake')
    row = frappe.db.get_value('Cowork Push Record', name,
        ['connection', 'workspace', 'client', 'history', 'target_json'], as_dict=True)
    if not row:
        frappe.throw(_('push.record_not_found'), frappe.DoesNotExistError)
    if (frappe.parse_json(row.target_json or '{}') or {}).get('adapter') != 'mrerp':
        frappe.throw(_('push.native_target_changed'))
    result = confirm(name, expected_revision, request_key, credential=None)
    if result.get('status') == 'pending':
        runner.dispatch(name)
    return result


def submit(history, connection, request_key, expected_history_version, expected_connection_revision,
           account_set_key=None, catalog_request_id=None, catalog_revision=None, posting_kind=None,
           *, confirm_duplicate=False, fixed=None):
    """复核页「提交」的一张票：准备 →（MR.ERP）确认发送并排执行任务；Express 的确认由小助手确认入口做。

    失败不抛给页面（用户 2026-09-26：只在列表状态图标上看原因）：整笔回滚后把原因记到这张票上
    （`push_state.record_failure`），回 `{"status": "failed", "reason"}`。推送状态本身仍只由账本投影。
    """
    try:
        result = prepare(history, connection, request_key, expected_history_version, expected_connection_revision,
            account_set_key, catalog_request_id, catalog_revision, posting_kind, confirm_duplicate=confirm_duplicate,
            fixed=fixed)
        if result.get("status") == "disabled":
            return submission_failed(history, _("push.disabled"))
        if result.get("status") == "prepared" and connections.adapter_of(connection) == "mrerp":
            result = confirm_server(result["name"], result["revision"], "confirm:" + str(request_key))
        return result
    except (frappe.ValidationError, frappe.PermissionError) as error:
        return submission_failed(history, str(error))


def submission_failed(history, reason):
    """提交失败：整笔回滚，只把原因记在这张票上（小助手确认入口共用）。"""
    frappe.db.rollback()
    frappe.clear_messages()  # 原因记在票上，不再弹给页面
    storage.read(history)  # 仍要有这张票的读取范围才记
    push_state.record_failure(history, reason)
    return {"status": "failed", "reason": reason, "history": history}


OUTCOMES_MAX = 20  # 同 CC 一次推送的张数上限


def outcomes(records):
    """推送的人看自己这几次推送此刻的结论（CC 推送后等写账结果，用户 2026-10-13 第 9 条）：
    ``{记录名: {"status", "reason", "docnum", "result_unknown"}}``；status 是账本状态（pending / leased 还在路上，
    success 已入账，manual 要人处理）。docnum 只在入账成功时给：ERP 回报的单号（Express 由 CC 桌面版写完回读）。

    只认本人发起（actor）的记录，别人的、不存在的不回；只读、不加锁、不改任何东西。调用方要看别的请求刚提交的结论时，
    先自己 ``frappe.db.rollback()`` 换一份新快照。"""
    scope.require("intake")
    names = list(dict.fromkeys(str(name) for name in records or () if name))[:OUTCOMES_MAX]
    if not names:
        return {}
    rows = frappe.db.sql("""SELECT name, status, reason, external_docnum, result_unknown FROM `tabCowork Push Record`
        WHERE actor=%s AND name IN %s""", (frappe.session.user, tuple(names)), as_dict=True)
    return {row.name: {"status": row.status, "reason": row.reason or "",
                       "docnum": (row.external_docnum or "") if row.status == "success" else "",
                       "result_unknown": bool(row.result_unknown)} for row in rows}


def records(connection, start=0, limit=50):
    owner = connections.read_for_log(connection)
    try:
        start, limit = max(0, int(start)), max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        frappe.throw(_("push.request_invalid"))
    rows = frappe.db.sql("""SELECT name FROM `tabCowork Push Record`
        WHERE connection=%s AND workspace=%s AND COALESCE(client, '')=%s
        ORDER BY creation DESC, name DESC LIMIT %s OFFSET %s FOR UPDATE""",
        (connection, owner["workspace"], owner.get("client") or "", limit, start), as_dict=True)
    return {"records": [_view(ledger.project(frappe.get_doc("Cowork Push Record", row.name, for_update=True))) for row in rows]}


PUSHED_MAX = 3000
PUSHED_PAGE = 500
# 按期间找推送记录时只看月初前这么多天以后建立的：单据日在这个月的单，不会早三个月就推（日期只在载荷里，库里没有可筛的列）
PUSHED_EARLIER_DAYS = 92
_PUSHED_SELECT = """SELECT name, creation, history, external_docnum, payload_json, response_json
    FROM `tabCowork Push Record` WHERE connection=%s AND account_key=%s AND status='success'
    AND COALESCE(external_docnum, '') != ''"""


def _loads(value):
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


def pushed_express(connection, account_set_key, date_from=None, date_to=None, docnums=None):
    """Express 回读比对用（2026-10 CC 闭环 L1，`readback` 域）：这条连接推进这本账套、推送成功的单据，单据日期落在
    [date_from, date_to] 的，加上 Express 单号在 ``docnums`` 里的（不论日期）。同一单号推过几次取最后一次。

    判据同推送：录入权限 + 这条连接的使用授权。只读，回 `push.express.pushed.project` 的形状，按单号排，最多
    ``PUSHED_MAX`` 张。不整表扫：按期间的只取月初前 ``PUSHED_EARLIER_DAYS`` 天以后建立的记录，按单号的直接按单号查。"""
    scope.require("intake")
    connection_access.require_connection(frappe.get_doc("Cowork ERP Connection", connection))
    key = normalize_express_account_key(account_set_key)
    wanted = sorted({str(item or "").strip().upper() for item in docnums or () if str(item or "").strip()})
    rows = {}
    if key and date_from is not None and date_to is not None:
        since = datetime(date_from.year, date_from.month, date_from.day) - timedelta(days=PUSHED_EARLIER_DAYS)
        start = 0
        while True:
            page = frappe.db.sql(_PUSHED_SELECT + " AND creation >= %s ORDER BY creation, name LIMIT %s OFFSET %s",
                                 (connection, key, since, PUSHED_PAGE, start), as_dict=True)
            rows.update((row.name, row) for row in page)
            start += len(page)
            if len(page) < PUSHED_PAGE:
                break
    for offset in range(0, len(wanted) if key else 0, PUSHED_PAGE):
        part = wanted[offset:offset + PUSHED_PAGE]
        rows.update((row.name, row) for row in frappe.db.sql(
            _PUSHED_SELECT + f" AND external_docnum IN ({', '.join(['%s'] * len(part))})",
            (connection, key, *part), as_dict=True))
    named, found = set(wanted), {}
    for row in sorted(rows.values(), key=lambda row: (row.creation or datetime.min, row.name)):
        item = pushed_rules.project(row.name, row.history, row.external_docnum, _loads(row.payload_json),
                                    _loads(row.response_json))
        if item and (item["docnum"].upper() in named or pushed_rules.in_window(item, date_from, date_to)):
            found[item["docnum"].upper()] = item
    return [found[docnum] for docnum in sorted(found)][:PUSHED_MAX]
