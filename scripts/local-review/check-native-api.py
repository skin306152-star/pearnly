from unittest.mock import patch
import frappe
frappe.init(site='auth-preview.localhost');frappe.connect();frappe.set_user('preview@example.test')
from pearnly_dms.operators import roster
for method, target, args in [(roster.api_create_operator,'create_operator',{}),(roster.api_update_operator,'update_operator',{'name':'fixture'})]:
 for value, expected in [('0',0),('1',1),(0,0),(1,1)]:
  with patch.object(roster,target,return_value={}) as action:
   method(**args,can_query_dms=value)
   assert action.call_args.kwargs['can_query_dms']==expected
from pearnly_erp_portal import purchase
for lines in ('[{"description":"Fixture","qty":1,"unit_price":100,"vat_rate":7}]',
              [{"description":"Fixture","qty":1,"unit_price":100,"vat_rate":7}]):
 with patch.object(purchase,'create_purchase',return_value='LOCAL-TRANSPORT-CHECK') as action:
  assert purchase.api_create_purchase(cmd='ignored',lines=lines)['name']=='LOCAL-TRANSPORT-CHECK'
  assert action.call_args.kwargs=={'lines':[{'description':'Fixture','qty':1,'unit_price':100,'vat_rate':7}]}
assert 'System Manager' not in frappe.get_roles()
assert not frappe.has_permission('Role','write')
assert not frappe.has_permission('User Permission','create')
assert not frappe.has_permission('System Settings','write')
from pearnly_core.desk.navigation import extend_boot
boot=frappe._dict(navbar_settings=frappe._dict(settings_dropdown=[]))
extend_boot(boot)
assert boot.pearnly_pages==['pearnly-home','pearnly-connect','pearnly-roster']
# Build the real native boot so framework sidebar key casing and hook order are exercised.
from frappe.boot import get_bootinfo
native_boot = get_bootinfo()
assert len(native_boot.workspace_sidebar_item['pearnly dms']['items']) == 3
with patch('frappe.get_roles', return_value=['DMS Operator','Desk User','All']):
    extend_boot(native_boot)
    assert native_boot.pearnly_pages == ['pearnly-home']
    assert [i['link_to'] for i in native_boot.workspace_sidebar_item['pearnly dms']['items']] == ['pearnly-home']
    from pearnly_dms.connections.api import _require_admin as connection_admin
    for check in (connection_admin, roster._require_admin):
        try:
            check()
        except frappe.PermissionError:
            pass
        else:
            raise AssertionError('Ordinary operator reached an admin endpoint')
# Root paths must not expose framework Desktop on product sites.
from pearnly_core.login_redirect import send_root_to_home, _Redirect
from types import SimpleNamespace
for path in ('/', '/desk', '/desk/', '/app'):
    with patch.object(frappe.local, 'request', SimpleNamespace(path=path), create=True):
        try:
            send_root_to_home()
        except _Redirect as result:
            assert result.location == '/desk/pearnly-home'
        else:
            raise AssertionError('Product root did not redirect: '+path)
print('PASS real sidebar projection, ordinary-role endpoint denial, product root redirects')
print('PASS native form boolean transport, purchased Page projection, no platform manager/Role-write/User-Permission-create/System-settings-write grant')
frappe.db.rollback();frappe.destroy()
