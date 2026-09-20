"""Authorized native preparation. No worker registration or external dispatch."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import service as connections
from pearnly_cowork.contracts.push import clean_tax_id
from pearnly_cowork.history import storage
from pearnly_cowork.push.application.provenance import original_history
from pearnly_cowork.push.express.prepare import prepare_express
from pearnly_cowork.push.express.preflight import ExpressContext
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.push.receipts import derive_external_ref, express_prior_number
from pearnly_cowork.targets.choice import endpoint_with_account_choice
from pearnly_cowork.targets.contracts import TargetSelectionError
from pearnly_cowork.targets.freeze import freeze_target
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
    result["external_reference"] = derive_external_ref(
        (row.get("frozen_target") or {}).get("adapter"), row.get("response_body"), row.get("status"))
    return result


def prepare(history, connection, request_key, expected_history_version, expected_connection_revision,
            account_set_key=None, catalog_request_id=None, catalog_revision=None, posting_kind=None):
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
    if loaded.endpoint.get("adapter") != "express":
        frappe.throw(_("push.adapter_not_connected"))
    if frappe.conf.get("pearnly_cowork_erp_push_enabled") not in (True, 1):
        return {"status": "disabled", "reason": "ERR_EXPRESS_DISABLED", "history": doc.name, "connection": connection}
    try:
        target = freeze_target(loaded.endpoint, access=loaded.access, evidence=loaded.evidence,
            account_set_key=account_set_key, request_id=catalog_request_id, revision=catalog_revision)
        choice = resolve_account_choice(loaded.endpoint, tenant_id=loaded.access.tenant_id,
            user_id=loaded.access.user_id, account_set_key=account_set_key,
            state={"snapshot": loaded.evidence.snapshot or {}})
    except TargetSelectionError as error:
        frappe.throw(_(error.code), frappe.PermissionError if error.status_code == 403 else frappe.ValidationError)
    selected = endpoint_with_account_choice(deepcopy(loaded.endpoint), choice)
    preparation = connections.preparation_for_target(loaded, target)
    # Per-account observations must replace old caches only after the choice
    # cleared the previous account's mappings and catalogues.
    selected["config"].update(deepcopy(preparation.get("reported_config") or {}))
    own_tax = frappe.db.get_value("Cowork Workspace", doc.workspace, "tax_id", for_update=True) or ""
    history_value = storage.project(doc)
    history_value.update(id=doc.name, tenant_id=loaded.access.tenant_id, workspace_client_id=doc.workspace)
    prior = original_history(doc)
    context = _context(doc, loaded, target.account_set)
    receipt = ledger.read_success(context=context, history=prior)
    prior_docnum = None
    if receipt:
        prior_docnum = express_prior_number(receipt.get("response_body"))
        if not prior_docnum:
            frappe.throw(_("push.prior_document_number_missing"))
    pages = history_value.get("pages") or []
    primary = next((p for p in pages if not p.get("is_duplicate") and not p.get("is_copy")), pages[0] if pages else {})
    fields = primary.get("fields") or {}
    seller_tax = clean_tax_id(fields.get("seller_tax") or fields.get("seller_tax_id"))
    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    prepared = prepare_express(selected, history_value, posting_kind=posting_kind,
        context=ExpressContext(enabled=True, today=today, own_tax_id=own_tax,
            tenant_id=loaded.access.tenant_id, mappings=preparation["mappings"],
            supplier_profile=preparation["supplier_profiles"].get(seller_tax),
            bank_index=preparation["bank_index"], prior_docnum=prior_docnum))
    frozen = asdict(target)
    frozen.update(request_intent=intent, connection_revision=loaded.revision,
        original_history=prior, preparation_date=today.isoformat(), own_tax_id=own_tax)
    row = ledger.reserve(context=context, request_key=request_key, frozen_target=frozen,
        frozen_payload=prepared.payload or {},
        disposition="prepared" if prepared.disposition == "ready" else "manual",
        reason=prepared.reason or "", checks=prepared.preflight.checks_json())
    return _view(row)


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
