"""Apply a verified legacy ACK delta to its exact reported account cache.

Does not invent directory evidence or write ERP stock. The push ledger owns
idempotency and transaction rollback. A new machine scan remains authoritative.
"""
from copy import deepcopy
import frappe
from . import access, model, state
from pearnly_cowork.targets.identity import normalize_express_account_key as identity


def apply(connection, target, count):
    if not count:
        return {'count': 0, 'state': 'not_applicable'}
    doc = state.read(state.CONNECTION, connection)
    report = model.parse(doc.report_json, {})
    config = model.parse(doc.config_json, {})
    binding = report.get('_preparation_binding') or {}
    credential = (target.get('preparation_evidence') or {}).get('credential')
    expected = {**(credential or {}), 'account_set': identity(target.get('account_set')),
                'root_key': identity(target.get('root_key'))}
    actual = {k: binding.get(k) for k in expected}
    if (not credential or actual != expected or doc.adapter != 'express' or doc.binding_generation
            or identity(config.get('account_set') or config.get('account_dir')) != expected['account_set']
            or identity(config.get('express_root')) != expected['root_key']
            or doc.report_credential != credential['credential_id']
            or doc.report_generation != credential['generation']):
        return {'count': count, 'state': 'target_changed'}
    origins = report.get('_preparation_origins') or {}
    fingerprint = report.get('catalog_fingerprint')
    if origins.get('catalog_fingerprint') != expected or not isinstance(fingerprint, dict):
        return {'count': count, 'state': 'unavailable'}
    before = fingerprint.get('stock_master_count', 0)
    if type(before) is not int or before < 0:
        return {'count': count, 'state': 'unavailable'}
    report['catalog_fingerprint'] = {**deepcopy(fingerprint), 'stock_master_count': before + count}
    doc.report_json = model.dumps(report)
    # This derived cache update is not a new machine report or a target revision.
    access.write(doc)
    return {'count': count, 'state': 'applied', 'before': before, 'after': before + count}
