"""Local-only ADR-009 verification. Requires the dedicated disposable lab.

Adds control/DMS fixture Sites in its own volume, no credentials or external IO.
Exercises actual catalog sync, installed-app hooks, sidebar projections and role
boundaries. Remove the task-owned Compose project/volumes after verification.
"""
import json
import os
from pathlib import Path
import subprocess
import frappe

ROOT = Path('/home/frappe/frappe-bench/sites')
ALLOWED = {'erp-pos-a.localhost', 'erp-pos-b.localhost', 'shared-control.localhost', 'shared-dms.localhost'}

def connect(site):
    os.chdir(ROOT)
    frappe.init(site=site, sites_path=str(ROOT)); frappe.connect(); frappe.set_user('Administrator')
    frappe.flags.mute_emails = True


def main():
    if os.environ.get('PEARNLY_SHARED_FOUNDATION_LAB') != '1' or not (ROOT/'.erp-pos-lab').is_file():
        raise SystemExit('Dedicated shared-foundation lab required')
    assert {p.parent.name for p in ROOT.glob('*/site_config.json')} <= ALLOWED
    assert json.loads((ROOT/'common_site_config.json').read_text())['db_host'] == 'db'
    from pearnly_control.features import codes_of, roles_payload
    from pearnly_core.provisioning import ensure_feature_roles, finalize_site
    from pearnly_core.desk import navigation
    from frappe.installer import update_site_config
    available = set((ROOT/'apps.txt').read_text().splitlines()) | {'pearnly_control','pearnly_dms'}
    (ROOT/'apps.txt').write_text('\n'.join(sorted(available))+'\n')
    os.chdir(ROOT.parent)
    for site,app in [('shared-control.localhost','pearnly_control'),('shared-dms.localhost','pearnly_dms')]:
        if not (ROOT/site/'site_config.json').exists():
            subprocess.run(['bench','new-site',site,'--db-host','db','--db-root-password','erp-pos-lab-only',
                            '--admin-password','shared-foundation-lab-only','--mariadb-user-host-login-scope','%'],check=True)
        subprocess.run(['bench','--site',site,'install-app','pearnly_core',app],check=True)
    for site,product in [('erp-pos-a.localhost','erp'),('shared-dms.localhost','dms')]:
        connect(site)
        for k,v in {'pearnly_product':product,'pearnly_features':codes_of(product),
                    'mute_emails':1,'pause_scheduler':1,'disable_scheduler':1}.items():
            update_site_config(k,v);frappe.conf[k]=v
        user='split-'+product+'@example.invalid'
        outsider='split-outsider-'+product+'@example.invalid'
        for email in [user,outsider]:
            if not frappe.db.exists('User',email):
                frappe.get_doc({'doctype':'User','email':email,'first_name':'Local fixture',
                    'send_welcome_email':0,'roles':[{'role':'Desk User'}]}).insert(ignore_permissions=True)
        ensure_feature_roles(json.dumps(roles_payload(product,codes_of(product))),admin_user=user)
        finalize_site();frappe.clear_cache();navigation.ensure_navigation();frappe.db.commit()
        assert navigation.products()==[product],navigation.products()
        assert navigation.available_pages('cowork')==set()
        shared=frappe.get_doc('Workspace Sidebar','Pearnly '+product.upper()).as_dict()
        frappe.set_user(user)
        pages=navigation.visible_pages(product)
        if product=='erp':
            assert {'pearnly-products','pearnly-stock-count','pearnly-sales','pearnly-cashier'} <= set(pages),pages
            assert frappe.has_permission('ERP Stock Count','write')
            perm=frappe.db.get_value('Custom DocPerm',{'parent':'ERP Product','role':'ERP Stock','permlevel':1},['read','write'],as_dict=True)
            assert perm and perm.read==1 and perm.write==0,perm
        else:
            assert set(pages)=={'pearnly-home','pearnly-connect','pearnly-records','pearnly-roster'},pages
        navigation.save_sidebar_order(list(reversed(pages)))
        assert navigation._personal_order(product,pages)==list(reversed(pages))
        frappe.set_user(outsider)
        assert navigation.visible_pages(product)==['pearnly-home']
        assert frappe.get_doc('Workspace Sidebar','Pearnly '+product.upper()).as_dict()['items']==shared['items']
        frappe.set_user('Guest')
        try:navigation.save_sidebar_order([])
        except frappe.PermissionError:pass
        else:raise AssertionError('Guest could save navigation')
        frappe.db.commit();frappe.destroy()
    connect('shared-control.localhost')
    assert frappe.get_installed_apps()==['frappe','pearnly_core','pearnly_control']
    from pearnly_control.features import sync_features
    sync_features();frappe.db.commit();result=sync_features()
    assert result.get('created',0)==0,result
    assert frappe.db.exists('Pearnly Feature', 'erp-stocktake')
    assert frappe.db.get_value('Pearnly Feature','erp-stocktake','role_name')=='ERP Stock'
    frappe.destroy()
    print('PASS: control-only catalog sync; ERP/DMS installed-app isolation; native groups and page permissions; cost field grant; personal order; outsider and guest rejection')

if __name__=='__main__':main()
