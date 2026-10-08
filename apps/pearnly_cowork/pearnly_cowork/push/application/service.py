"""Authorized native preparation. No worker registration or external dispatch."""
from copy import deepcopy
import json
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import service as connections
from pearnly_cowork.connections import preparation as preparation_sources
from pearnly_cowork.targets import stock_groups
from pearnly_cowork.contracts import document_policy as policy_rules
from pearnly_cowork.contracts.history import primary_page, primary_page_fields
from pearnly_cowork.contracts.push import clean_tax_id, explicit_direction, detect_by_tax, resolve_posting_kind
from pearnly_cowork.history import document_policy, push_state, storage
from pearnly_cowork.push import choices as choice_store
from pearnly_cowork.push.application.provenance import original_history
from pearnly_cowork.push.express import choices as choice_rules
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
from pearnly_cowork.targets.identity import normalize_express_account_key
from pearnly_cowork.targets.selection import resolve_account_choice


def _context(doc, loaded, account=""):
    return ledger.ReservationContext(actor=frappe.session.user, workspace=doc.workspace,
        client=doc.client or "", history=doc.name, history_version=int(doc.record_version),
        connection=loaded.doc.name, account_key=account,
        branch="managed" if int(loaded.endpoint.get("binding_generation") or 0) else "legacy",
        binding_generation=int(loaded.endpoint.get("binding_generation") or 0),
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
            *, confirm_duplicate=False):
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
    direction = explicit_direction(flat, history_value) or detect_by_tax(flat, own_tax)
    _require_matching_account(loaded, target, direction, fields, invoice_date)
    preparation, evidence = preparation_sources.collect(loaded, target,
        seller_tax=seller_tax if direction == 'purchase' else '',
        invoice_date=invoice_date.isoformat() if invoice_date else None)
    # Choice clears other-account caches before applying attributed observations.
    selected['config'].update(deepcopy(preparation['reported_config']))
    _apply_stock_group(selected['config'], loaded.doc, target.account_set)
    decision = _decisions(loaded.doc.name, target.account_set, flat, fields, direction, posting_kind,
                          selected['config'], preparation)
    if decision["needed"]:
        # 拿不准的项（对方档 / 存货 / 费用科目 / 付款 / 抵进项 / 部门）还没人选：不发小助手，停在这里等人选一次。
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
        context=ExpressContext(enabled=True, today=today, own_tax_id=own_tax,
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
}


def _require_matching_account(loaded, target, direction, fields, invoice_date):
    """用户选的账套与票面确定对不上就不推（规则见 `targets/account_match.py`）：本方税号不同、日期不在会计期间里。"""
    rows = (loaded.evidence.snapshot or {}).get("account_sets") or []
    if isinstance(rows, str):
        rows = json.loads(rows)
    key = normalize_express_account_key(target.account_set)
    account = next((row for row in rows if isinstance(row, dict)
                    and normalize_express_account_key(row.get("source_id")) == key), None)
    facts = {**fields, "date": invoice_date.isoformat() if invoice_date else fields.get("date")}
    reason = account_match.conflict("express", account, direction=direction, fields=facts)
    if reason:
        frappe.throw(_(_ACCOUNT_CONFLICTS[reason]))


def _decisions(connection, account_set, flat, fields, direction, posting_kind, config, preparation):
    """这张票在这份账套里的选择（规则见 `push/express/choices.py`）：记过的套用，缺的列出来要人选。

    票上会计明确改过的付款方式按对方记下（「按对方记住会计第一次的选择」，用户 2026-09-28）。"""
    category = str(flat.get("category") or (flat.get("fields") or {}).get("category") or "").strip()
    _, source = resolve_account_sourced((preparation.get("mappings") or {}).get("accounts") or [], category, None)
    remembered = choice_store.remembered(connection, account_set)
    kind = resolve_posting_kind(posting_kind, flat)
    decision = choice_rules.resolve(fields, direction=direction, posting_kind=kind, config=config,
                                    remembered=remembered, account_resolved=source == SRC_CATEGORY)
    applied = decision["applied"]
    remember = applied.pop("remember_payment", None)
    if remember:
        name, tax = ((fields.get("seller_name"), fields.get("seller_tax")) if direction == "purchase"
                     else (fields.get("buyer_name"), fields.get("buyer_tax")))
        choice_store.record(connection, account_set, "payment",
                            choice_rules.supplier_subject(direction, tax, name), remember, name)
    return decision


def choices_view(history, connection, account_set_key=None, posting_kind=None):
    """复核页提交前 / 退回后要人选的项与可选值（与准备同一套判定，不建推送记录）。"""
    scope.require("intake")
    doc = storage.read(history)
    loaded = connections.read_for_push(connection, doc.workspace, doc.client, lock=True)
    if loaded.endpoint.get("adapter") != "express" or _document_block(doc):
        # 这类单据录不录还没定（或定了不录）：先不问对方档、科目这些，提交时停在口径那一步
        return {"needed": [], "options": {}}
    target, flat, fields, direction, preparation, config = _express_facts(doc, loaded, account_set_key)
    decision = _decisions(loaded.doc.name, target.account_set, flat, fields, direction, posting_kind,
                          config, preparation)
    frappe.db.rollback()  # 只是看：记付款方式的那一笔不在这里落
    kinds = {item["kind"] for item in decision["needed"]}
    options = {}
    if "item" in kinds:
        options["products"] = [{"code": row["code"], "name": row.get("name") or ""}
                               for row in config.get("reported_products") or []
                               if isinstance(row, dict) and str(row.get("kind") or "stock") == "stock"][:5000]
    if "account" in kinds:
        options["accounts"] = [{"code": row["code"], "name": row.get("name") or ""}
                               for row in config.get("reported_accounts") or [] if isinstance(row, dict)][:2000]
    return {"needed": decision["needed"], "options": options, "account_set": target.account_set}


def save_choices(history, connection, account_set_key, items):
    """记下会计在复核页选的（每项 `{kind, subject, value, label}`）；值须在这份账套的主档里（或「新建」）。"""
    scope.require("intake")
    doc = storage.read(history)
    loaded = connections.read_for_push(connection, doc.workspace, doc.client, lock=True)
    target, _flat, _fields, _direction, _preparation, config = _express_facts(doc, loaded, account_set_key)
    items = json.loads(items) if isinstance(items, str) else items
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        frappe.throw(_("connection.config_invalid"))
    allowed = {
        "party": None, "item": {r.get("code") for r in config.get("reported_products") or [] if isinstance(r, dict)},
        "account": {r.get("code") for r in config.get("reported_accounts") or [] if isinstance(r, dict)},
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
    direction = explicit_direction(flat, history_value) or detect_by_tax(flat, own_tax)
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
           *, confirm_duplicate=False):
    """复核页「提交」的一张票：准备 →（MR.ERP）确认发送并排执行任务；Express 的确认由小助手确认入口做。

    失败不抛给页面（用户 2026-09-26：只在列表状态图标上看原因）：整笔回滚后把原因记到这张票上
    （`push_state.record_failure`），回 `{"status": "failed", "reason"}`。推送状态本身仍只由账本投影。
    """
    try:
        result = prepare(history, connection, request_key, expected_history_version, expected_connection_revision,
            account_set_key, catalog_request_id, catalog_revision, posting_kind, confirm_duplicate=confirm_duplicate)
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
