"""Native Express mapping/profile inputs; no external IO or transaction commit."""
import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document
from pearnly_core.shell import entitlements

from pearnly_cowork.access import scope
from pearnly_cowork.masters import supplier_posting as posting_rules

TYPES = ('Cowork ERP Mapping', 'Cowork Supplier Posting Profile')
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_('Not Permitted'), frappe.PermissionError)


def reject_share(doc, method=None):
    if doc.share_doctype in TYPES:
        deny()


def key(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def persist(doc):
    doc.flags.cowork_input_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.notify_update = False
    doc.flags.ignore_version = True
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


class InputDocument(Document):
    def check_permission(self, *args, **kwargs):
        if self.flags.cowork_input_write is not _WRITE:
            deny()

    def autoname(self):
        self.check_permission()
        self.name = self.input_key

    def before_validate(self):
        self.check_permission()

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        deny()

    def before_rename(self, *args, **kwargs):
        deny()


def _manager():
    require_actor('master_data', 'Cowork Admin')


def require_actor(feature, role):
    actor = frappe.session.user
    enabled = frappe.db.sql('SELECT enabled FROM `tabUser` WHERE name=%s LOCK IN SHARE MODE', (actor,))
    roles = frappe.db.sql("SELECT role FROM `tabHas Role` WHERE parent=%s AND parenttype='User' LOCK IN SHARE MODE", (actor,))
    if (actor == 'Guest' or frappe.conf.get('pearnly_product') != 'cowork'
            or feature not in entitlements() or not enabled or not enabled[0][0]
            or not {role, 'Cowork Admin'}.intersection(r[0] for r in roles)):
        deny()


def _workspace(workspace):
    rows = frappe.db.sql('SELECT enabled FROM `tabCowork Workspace` WHERE name=%s FOR UPDATE', (workspace,))
    if not rows or not rows[0][0]:
        deny()


def _text(value, limit=140, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        frappe.throw(_('connection.config_invalid'))
    return value.strip()


def save_mapping(kind, mapping_key, erp_code, *, expected_revision=0):
    """Site-wide account mapping, or exact native Client mapping, as in source.

    Stable natural identity + revision makes repeated equal writes idempotent.
    An empty code removes its effect while preserving the auditable native row.
    """
    _manager()
    if kind not in ('account', 'client'):
        frappe.throw(_('connection.config_invalid'))
    mapping_key, erp_code = _text(mapping_key), _text(erp_code, 40, empty=True)
    if kind == 'client':
        client = frappe.get_doc('Cowork Client', mapping_key, for_update=True)
        _workspace(client.workspace)
        if not client.enabled:
            deny()
    name = key('express', kind, mapping_key)
    # A site-wide role row serializes first inserts for the site-wide map too.
    frappe.db.get_value('Role', 'Cowork Admin', 'name', for_update=True)
    existing = frappe.db.exists(TYPES[0], name)
    doc = frappe.get_doc(TYPES[0], name, for_update=True) if existing else frappe.get_doc({
        'doctype': TYPES[0], 'input_key': name, 'kind': kind, 'mapping_key': mapping_key,
        'erp_type': 'express', 'record_revision': 0})
    if existing and doc.erp_code == erp_code:
        return {'name': name, 'revision': doc.record_revision}
    if isinstance(expected_revision, bool) or str(expected_revision) != str(doc.record_revision):
        frappe.throw(_('connection.revision_conflict'))
    doc.erp_code, doc.record_revision = erp_code, doc.record_revision + 1
    persist(doc)
    return {'name': name, 'revision': doc.record_revision}


def save_profile(workspace, seller_tax_id, *, default_payment=None, default_item_type=None,
        source='user_rule', allow_empty=True):
    """Save a supplier's posting defaults; ``None`` on a field means "leave it alone".

    The rules live in ``masters/supplier_posting.py`` (ported verbatim from the previous
    product): the user-facing route refuses an empty string, the internal correction
    learning may write one, and a learned correction never overwrites a user rule.
    """
    _manager()
    _workspace(workspace)
    tax = posting_rules.clean_seller_tax_id(seller_tax_id)
    incoming = {'default_payment': default_payment, 'default_item_type': default_item_type}
    if not tax or posting_rules.input_error(source, incoming, allow_empty=allow_empty):
        frappe.throw(_('connection.config_invalid'))
    frappe.db.get_value('Cowork Workspace', workspace, 'name', for_update=True)
    name = key(workspace, tax)
    existing = frappe.db.exists(TYPES[1], name)
    doc = frappe.get_doc(TYPES[1], name, for_update=True) if existing else frappe.get_doc({
        'doctype': TYPES[1], 'input_key': name, 'workspace': workspace, 'seller_tax_id': tax,
        'default_payment': '', 'default_item_type': ''})
    decision = posting_rules.merge({'source': doc.source} if existing else None, incoming, source)
    if decision['preserved_user_rule']:
        return {'name': name, 'preserved_user_rule': True}
    for field, value in decision['fields'].items():
        doc.set(field, value)
    doc.source = decision['source']
    persist(doc)
    return {'name': name, 'preserved_user_rule': False}


def list_profiles(workspace, limit=None):
    """A company's effective posting profiles, newest first, with the deployed limit.

    Native input rows are auditable and may not be removed, so "removed" is expressed by
    clearing both defaults: such a row has no effect and is not part of this projection
    (the deployed delete dropped the row outright).
    """
    if not scope.allowed_any((*scope.READ_FEATURES, 'master_data')):
        deny()
    _workspace(workspace)
    scope.require_scope(workspace)
    limit = posting_rules.MAX_LIMIT if limit in (None, '') else int(limit)
    limit = max(1, min(limit, posting_rules.MAX_LIMIT))
    rows = frappe.db.get_all(TYPES[1], filters={'workspace': workspace},
        fields=['name', 'seller_tax_id', 'default_payment', 'default_item_type', 'source', 'modified'],
        order_by='modified desc, name desc', limit_page_length=limit)
    return [dict(row) for row in rows if posting_rules.has_effect(row)]


def clear_profile(workspace, seller_tax_id):
    """Remove a profile's effect; ``deleted`` says whether it had one.

    Idempotent like the deployed delete: a tax id with no profile is a no-op answer, not
    a failure. The row itself stays (native input rows are auditable and refuse ``on_trash``).
    """
    _manager()
    _workspace(workspace)
    scope.require_scope(workspace)
    tax = posting_rules.clean_seller_tax_id(seller_tax_id)
    if not tax:
        return {'deleted': False}
    frappe.db.get_value('Cowork Workspace', workspace, 'name', for_update=True)
    name = key(workspace, tax)
    if not frappe.db.exists(TYPES[1], name):
        return {'deleted': False}
    doc = frappe.get_doc(TYPES[1], name, for_update=True)
    deleted = posting_rules.has_effect(doc)
    if deleted:
        for field, value in posting_rules.cleared().items():
            doc.set(field, value)
        persist(doc)
    return {'deleted': deleted}


def mappings(client, erp_type='express'):
    """Internal scoped projection. The caller has already authorized the History."""
    if erp_type not in ('express', 'mrerp'):
        deny()
    rows = frappe.db.sql('''SELECT kind,mapping_key,erp_type,erp_code FROM `tabCowork ERP Mapping`
        WHERE erp_type=%s AND (kind='account' OR (kind='client' AND mapping_key=%s))
        ORDER BY kind,mapping_key LOCK IN SHARE MODE''', (erp_type, client or ''), as_dict=True)
    return {'accounts': [{'erp_type': r.erp_type, 'pearnly_category': r.mapping_key, 'erp_code': r.erp_code}
                         for r in rows if r.kind == 'account' and r.erp_code],
            'clients': [{'erp_type': r.erp_type, 'client_id': r.mapping_key, 'erp_code': r.erp_code}
                        for r in rows if r.kind == 'client' and r.erp_code]}


def profiles(workspace, seller_tax):
    rows = frappe.db.sql('''SELECT seller_tax_id,default_payment,default_item_type,source
        FROM `tabCowork Supplier Posting Profile` WHERE workspace=%s AND seller_tax_id=%s
        LOCK IN SHARE MODE''', (workspace, seller_tax), as_dict=True)
    return {r.seller_tax_id: dict(r) for r in rows}
