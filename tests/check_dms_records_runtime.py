"""Isolated auth-preview regression; fixture writes are rolled back, no external IO.
Run via the preview bench Python with sites as cwd and this file on stdin.
"""
import json
from unittest.mock import patch
import frappe

frappe.init(site='auth-preview.localhost')
frappe.connect()
assert frappe.local.site == 'auth-preview.localhost'
from pearnly_dms.query import records
from pearnly_dms.records import access, manage
from pearnly_dms.records import push_log
frappe.set_user('preview@example.test')
original_product = frappe.conf.get('pearnly_product')
try:
    assert 'DMS Admin' in frappe.get_roles()
    assert 'push_record' in access.entitlements()
    baseline = records.list_records()['total']
    names = []
    def add(status='success', adapter='mrerp_dms', trigger='line', error=''):
        doc = frappe.get_doc({'doctype':'Pearnly Push Log', 'status':status,
            'trigger':trigger, 'business_key':'LOCAL-RECORDS-CHECK', 'attempt':1,
            'actor':'preview@example.test', 'request_body':json.dumps({'adapter':adapter}),
            'error_msg':error})
        doc.insert(ignore_permissions=True)
        names.append(doc.name)
        return doc.name
    for i in range(26):
        add()
    failed = add(status='failed', error='ERR_TEST_FAILURE')
    private = add(status='failed', error='private request content must not be exposed')
    other = add(adapter='other_adapter')
    mirror = add(trigger='line_mirror')
    result = records.list_records()
    assert result['total'] == baseline + 28
    assert len(result['items']) == 20
    assert records.list_records(start=20)['items']
    assert len(records.list_records(page_length=100)['items']) == baseline + 28
    assert not set(r.name for r in result['items']) & set(r.name for r in records.list_records(start=20)['items'])
    failures = records.list_records(status='failed')['items']
    row = next(r for r in failures if r.name == failed)
    assert row.error_code == 'ERR_TEST_FAILURE'
    row = next(r for r in failures if r.name == private)
    assert row.error_code == '' and row.has_error_detail
    assert not {'request_body','response_body','error_msg'}.intersection(row)
    assert records.list_records(from_date='2099-01-01')['total'] == 0
    for args in ({'status':"success' OR 1=1"}, {'start':-1}, {'page_length':'bad'}, {'page_length':2501},
                 {'from_date':'2026-09-21','to_date':'2026-09-20'}):
        try:
            records.list_records(**args)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError(args)
    # Deletion never removes the authoritative ledger or alters push state.
    success = names[0]
    pending = add(status='pending')
    frappe.db.set_value('Pearnly Push Log', success, {
        'reference_doctype':'User','reference_name':'preview@example.test',
        'request_body':json.dumps({'adapter':'mrerp_dms','people_id_tail':'7591'}),
    })
    before = {name:frappe.get_doc('Pearnly Push Log',name).as_dict() for name in (success,pending)}
    state = push_log.status_of('User','preview@example.test')
    from pearnly_dms.ocr.ocr_service import recent_dms_customer_ids_by_tail
    assert 'LOCAL-RECORDS-CHECK' in recent_dms_customer_ids_by_tail('7591')
    for invalid in ([],None,{},'not json','"a string"',[None],[{}],[''],['x'*141],['x']*2501):
        try:
            manage.delete_records(invalid)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError(('accepted invalid selection',invalid))
    for invalid_name in (other,mirror,'OTHER-SITE-OR-MISSING',"' OR 1=1 --"):
        try:
            manage.delete_records([success,invalid_name])
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError(('accepted out-of-scope record',invalid_name))
        assert not frappe.db.get_value('Pearnly Push Log',success,'records_deleted_at')
    deleted = manage.delete_records(json.dumps([success,pending,success]))
    assert deleted == {'deleted':2}
    assert records.list_records()['total'] == baseline + 27
    for name in (success,pending):
        after = frappe.get_doc('Pearnly Push Log',name).as_dict()
        assert after.records_deleted_at and after.records_deleted_by == 'preview@example.test'
        for field,value in before[name].items():
            if field not in ('records_deleted_at','records_deleted_by'):
                assert after[field] == value, (field,value,after[field])
    assert push_log.status_of('User','preview@example.test') == state
    # The customer-lookup fallback still sees its source data after list removal.
    assert 'LOCAL-RECORDS-CHECK' in recent_dms_customer_ids_by_tail('7591')
    removed_at = frappe.db.get_value('Pearnly Push Log',success,'records_deleted_at')
    assert manage.delete_records([success,pending]) == {'deleted':0}
    assert frappe.db.get_value('Pearnly Push Log',success,'records_deleted_at') == removed_at
    assert all(row.name not in (success,pending) for row in records.list_records(page_length=2500)['items'])
    assert frappe.allowed_http_methods_for_whitelisted_func[manage.delete_records] == ['POST']
    for permission in ('role','feature','product','guest'):
        with patch.object(access, 'entitlements', return_value=[] if permission=='feature' else ['push_record']), \
             patch.object(frappe, 'get_roles', return_value=[] if permission=='role' else ['DMS Admin']):
            frappe.conf.pearnly_product = 'cowork' if permission=='product' else 'dms'
            frappe.session.user = 'Guest' if permission=='guest' else 'preview@example.test'
            for call in (records.list_records,lambda: manage.delete_records([failed])):
                try:
                    call()
                except frappe.PermissionError:
                    pass
                else:
                    raise AssertionError(permission)
    assert not frappe.db.get_value('Pearnly Push Log',failed,'records_deleted_at')
    print('PASS scoped reads, single/batch removal, atomic denial, idempotence, audit, retained business evidence, POST-only and read/write access denials')
finally:
    frappe.conf.pearnly_product = original_product
    frappe.db.rollback()
    frappe.destroy()
