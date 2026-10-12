"""Local preview only; transactional fixtures and no external network calls."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
import frappe
from frappe.utils.password import get_decrypted_password
frappe.init(site='auth-preview.localhost'); frappe.connect()
assert frappe.local.site == 'auth-preview.localhost'
from pearnly_dms.operators import roster, advisors
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_core import line_binding
from pearnly_core.account import preferences, oidc_session
from pearnly_core.desk import permissions
frappe.set_user('preview@example.test')
baseline = frappe.db.count('DMS Operator')
try:
    with patch.object(frappe.db, 'commit'), patch.object(roster.line_account, 'public_account', return_value={'basic_id':'@fixture'}):
        args = dict(display_name='LOCAL-ROSTER-REGRESSION', dms_username='fixture', dms_password='fixture-only')
        with patch.object(frappe.db, 'exists', wraps=frappe.db.exists) as exists:
            original = exists._mock_wraps
            exists.side_effect = lambda dt, *a, **kw: False if dt == 'Pearnly ERP Endpoint' else original(dt,*a,**kw)
            try: roster.create_operator(**args)
            except frappe.ValidationError: pass
            else: raise AssertionError('missing DMS accepted')
        assert frappe.db.count('DMS Operator') == baseline
        # A read that cannot resolve must reach the dialog as a code, never as a toast.
        assert roster.api_advisors() == {'items': [], 'error': 'roster.advisor.noConnection'}
        ep = frappe.get_doc(dict(doctype='Pearnly ERP Endpoint', label='LOCAL-ROSTER-REGRESSION', adapter='mrerp_dms', enabled=1, system_url='https://dms.invalid', username='fixture', password='fixture-only'))
        ep.insert(ignore_permissions=True)
        frappe.clear_messages()
        op = roster.create_operator(**args)
        user = frappe.get_doc('User',op['user'])
        assert user.user_type == 'Website User' and not user.roles
        assert not any('No Roles Specified' in str(m) or 'Add Roles' in str(m) for m in frappe.message_log)
        assert not frappe.db.exists('__Auth',{'doctype':'User','name':user.name,'encrypted':0})
        assert permissions.user_permission(user) is False
        assert permissions.user_permission(frappe.get_doc('User','preview@example.test')) is None
        assert not frappe.has_permission('User','read',user)
        assert not frappe.has_permission('User','write',user)
        assert frappe.has_permission('User','write',frappe.get_doc('User','preview@example.test'))
        # This legacy fixture is deliberately uncommitted and rolled back below.
        # Use its own connection for the final scope read; cross-connection/RR
        # credential changes are covered in the dedicated snapshot lab instead.
        live_reads = []
        def live_advisors(endpoint, callback, **kwargs):
            assert endpoint == ep.name and kwargs == {'authoritative_read': True}
            def advisor_rows():
                live_reads.append('txtusers')
                return [['A','C','Verified Name','private-phone']]
            return callback(SimpleNamespace(_advisor_rows=advisor_rows), None)
        cached = {'data': {'masters': {'advisors': [['A','C','Cached Name','private-phone']]}}}
        with patch.object(advisors.store, 'read', return_value=cached) as cache, \
                patch.object(advisors, 'run_logged_in', side_effect=live_advisors) as live, \
                patch.object(advisors.store, '_cache_connection', side_effect=lambda: nullcontext(frappe.db)):
            row = roster.update_operator(op['name'],display_name='Updated', dms_role='admin',can_query_dms=1,advisor_id='A',advisor_name='forged')
            assert row['advisor_name']=='Verified Name' and row['role']=='admin' and row['can_query_dms']
            choices = advisors.options()
            assert choices == [{'id':'A','code':'C','name':'Cached Name'}]
            assert not any('private-phone' in str(o) for o in choices)
            cache.assert_called_once_with(ep.name, 'company')
            try: roster.update_operator(op['name'],advisor_id='missing')
            except frappe.ValidationError: pass
            else: raise AssertionError('invalid advisor accepted')
            assert live.call_count == 2 and live_reads == ['txtusers', 'txtusers']
        with patch.object(advisors.store, 'read', side_effect=DmsClientError('No snapshot', 'ERR_DMS_SNAPSHOT_PENDING')), \
                patch.object(advisors, 'run_logged_in', return_value={'ok':False,'error_code':'ERR_DMS_AUTH'}) as live, \
                patch.object(advisors.store, '_cache_connection', side_effect=lambda: nullcontext(frappe.db)):
            assert roster.api_advisors() == {'items': [], 'error': 'roster.advisor.loadError'}
            live.assert_not_called()
            try: roster.update_operator(op['name'],advisor_id='A')
            except frappe.ValidationError: pass
            else: raise AssertionError('failed refresh accepted')
            assert live.call_count == 1 and live.call_args.kwargs == {'authoritative_read':True}
        password = get_decrypted_password('DMS Operator',op['name'],'dms_password')
        roster.update_operator(op['name'],dms_password='')
        assert get_decrypted_password('DMS Operator',op['name'],'dms_password') == password
        # Missing OA config must not mint/revoke a code or change an operator.
        with patch.object(roster.line_account, 'public_account', side_effect=frappe.ValidationError), patch.object(line_binding, 'generate_bind_code') as generate:
            try: roster.api_operator_bind_code(op['name'])
            except frappe.ValidationError: pass
            else: raise AssertionError('unverified OA accepted')
            generate.assert_not_called()
        code = roster.api_operator_bind_code(op['name'])['code']
        line_binding.bind('LOCAL-ROSTER-LINE',user.name,'Fixture LINE')
        roster.api_unbind_operator(op['name'])
        assert not line_binding.user_of('LOCAL-ROSTER-LINE')
        # Without a LINE sender consume_bind_code always returns None; name one so the code really is gone.
        assert not line_binding.consume_bind_code(code, line_user_id='LOCAL-ROSTER-LINE')
        assert frappe.db.exists('DMS Operator',op['name'])
        code = roster.api_operator_bind_code(op['name'])['code']
        roster.set_status(op['name'],'inactive')
        assert not line_binding.consume_bind_code(code, line_user_id='LOCAL-ROSTER-LINE')
        try: roster.api_operator_bind_code(op['name'])
        except frappe.ValidationError: pass
        else: raise AssertionError('inactive bind accepted')
        roster.set_status(op['name'],'active')
        assert not line_binding.user_of('LOCAL-ROSTER-LINE')
        log = frappe.get_doc(dict(doctype='Pearnly Push Log',status='success',trigger='line',actor=user.name,request_body='{"adapter":"mrerp_dms"}')).insert(ignore_permissions=True)
        roster.delete_operator(op['name'])
        assert frappe.db.exists('Pearnly Push Log',log.name)
        assert frappe.db.count('DMS Operator') == baseline
        with patch.object(oidc_session,'update_locale',return_value=False), patch.object(frappe.local,'cookie_manager',create=True) as cookie:
            doc=frappe.get_doc('User','preview@example.test'); doc.language='th';doc.save()
            assert frappe.db.get_value('User',doc.name,'language')=='th'
            assert frappe.defaults.get_user_default('pearnly_pending_locale')=='th'
            with patch.object(oidc_session,'update_locale',return_value=True):
                preferences.finish_login_locale(doc.name,'zh-CN')
            assert frappe.db.get_value('User',doc.name,'language')=='th'
            assert not frappe.defaults.get_user_default('pearnly_pending_locale')
        print('PASS create/no platform roles, endpoint prerequisite, private credentials, advisor validation, permissions, LINE bind/unbind/disable/enable/delete, retained ledger, deferred language sync')
finally:
    frappe.db.rollback()
    frappe.clear_cache(user='preview@example.test')
    frappe.destroy()
