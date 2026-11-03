"""ERP 接 MR.ERP 导入直写（评估 §13 第 6 步）在回归实验室 ERP 站点上的真实验证：真 MariaDB / Redis / WSGI，不连 MR.ERP。

只在回归实验室（`infrastructure/compose/regression-lab.yml`，容器环境 ``PEARNLY_REGRESSION_LAB=1``）的 ``erp.regression.localhost``
上跑；共用层的对外导入（`pearnly_core.mrerp.push.execute.execute`）一律换成假的，不发任何请求。合成连接、公司、客户、单据留在这个
一次性实验室里（实验室跑完整个拆掉）。

ERP 代码常量 ``pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED`` 在产品里保持 False：「开着」只在本验证进程里临时打补丁；WSGI 请求在
同一进程里跑，补丁对入口也生效。实验室自己的后台进程没有补丁，排进去的导入任务在那里一律直接返回（关着的口径）。

- 关着：管理员经 WSGI 调 MR.ERP 入口一律 403；公司就算标了 MR.ERP 账本，提交的单也不进 MR.ERP（``handles`` 假）。
- 开着：管理员经 WSGI 建连接、选账套当账本；销售单提交时排上（``ledger_target = mrerp``、排队中）；后台任务导入一张（假 MR.ERP 回
  受理并回读到单号）→ 单据账本栏记「已写」与 MR.ERP 单号；代录占着这个账号（真 Redis）时不导、照旧排队（``mrerp.account_busy``），
  放开后导入。

运行（回归实验室建好站之后）：
  docker compose -p <项目> -f infrastructure/compose/regression-lab.yml exec -T -w /home/frappe/frappe-bench/sites bench \\
    ../env/bin/python /workspace/scripts/erp-mrerp-direct-lab-verify.py
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from werkzeug.test import Client
from werkzeug.wrappers import Response

SITE = 'erp.regression.localhost'
SITE_ROOT = Path('/home/frappe/frappe-bench/sites')
ADMIN = 'admin@erp.regression'
API = '/api/method/pearnly_erp_portal.mrerp.api.'
PASSWORD = 'synthetic-' + uuid4().hex[:10]
SALES = 'ERP Sales Document'


def connect():
    if os.environ.get('PEARNLY_REGRESSION_LAB') != '1':
        raise SystemExit('Regression lab container required')
    os.chdir(SITE_ROOT)
    frappe.init(site=SITE, sites_path=str(SITE_ROOT))
    frappe.connect()
    if frappe.conf.get('db_host') not in ('db', None) or not frappe.db.exists('User', ADMIN):
        raise SystemExit('Refusing a non-lab Site')
    frappe.set_user('Administrator')
    frappe.flags.mute_emails = True


def lab_tax(name) -> str:
    """合成税号（过泰国税号校验位），同 `scripts/regression/datasets.py::lab_tax`。"""
    import hashlib
    body = '0105' + str(int(hashlib.sha256(name.encode()).hexdigest(), 16))[:8]
    return body + str((11 - sum(int(d) * (13 - i) for i, d in enumerate(body)) % 11) % 10)


@contextmanager
def ledger_on():
    from pearnly_erp_portal import mrerp
    with patch.object(mrerp, 'MRERP_LEDGER_ENABLED', True):
        yield


class ErpMrErpDirectLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pearnly_erp_portal.masters import company, customers
        cls.suffix = uuid4().hex[:8]
        frappe.set_user(ADMIN)
        made = company.create_company(company_name='บริษัท MR.ERP ทดสอบ ' + cls.suffix,
                                      tax_id=lab_tax('erp-mrerp-lab-' + cls.suffix),
                                      payment_methods=['cash'])
        cls.company = made['name']
        cls.customer = customers.create_customer(customer_name='ลูกค้า MR.ERP ' + cls.suffix)['name']
        frappe.set_user('Administrator')
        admin = frappe.get_doc('User', ADMIN)
        admin.api_key, secret = uuid4().hex, uuid4().hex
        admin.api_secret = secret
        admin.save(ignore_permissions=True)
        cls.headers = {'Authorization': f'token {admin.api_key}:{secret}'}
        frappe.db.commit()
        frappe.destroy()

    def setUp(self):
        from frappe.app import application
        self.client = Client(application, Response, use_cookies=False)

    def tearDown(self):
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()

    def db(self, function, *, user=ADMIN, commit=False):
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()
        connect()
        frappe.set_user(user)
        try:
            result = function()
            if commit:
                frappe.db.commit()
            return result
        finally:
            frappe.db.rollback()
            frappe.destroy()

    def call(self, method, **body):
        response = self.client.open(API + method, base_url='http://' + SITE, method='POST', buffered=True,
                                    headers=self.headers, json=body)
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))
        return response

    def sale(self):
        from pearnly_erp_portal.masters import company
        from pearnly_erp_portal.sales import entry

        def post():
            company.set_active_company(self.company)
            return entry.post({'company': self.company, 'customer': self.customer, 'doc_date': '2026-10-05',
                               'has_vat': 1, 'vat_rate': 7, 'source': 'manual', 'doc_kind': 'receipt',
                               'paid_amount': 107, 'payment_method': 'cash', 'payment_date': '2026-10-05',
                               'lines': [{'item_type': 'service', 'description': 'บริการทดสอบ MR.ERP', 'qty': 1,
                                          'unit': 'ครั้ง', 'unit_price': 100, 'vat_rate': 7, 'vat_applicable': 1}]},
                              request_id='mrerp-lab-' + uuid4().hex)['name']
        return self.db(post, commit=True)

    def ledger(self, name):
        return self.db(lambda: frappe.db.get_value(SALES, name, ['ledger_state', 'ledger_target', 'ledger_reason',
                                                                 'ledger_docnum'], as_dict=True), user='Administrator')

    def run_import(self, name, docnum):
        """后台任务导入一次（假 MR.ERP）；回假导入被调的次数。"""
        from pearnly_core.mrerp.push import execute as executing
        from pearnly_erp_portal.express import push
        calls = []

        def fake(payload, target, secrets, system_url, today):
            calls.append((target['account_set'], secrets['username'], len(payload['histories'])))
            return {'status': 'success', 'docnum': docnum, 'meta': {'doc_type': 'sales_cash'}}
        with patch.object(executing, 'execute', fake):
            self.db(lambda: push.mrerp_run(SALES, name), user='Administrator')
        return calls

    def test_1_with_the_constant_off_the_entries_refuse_and_documents_stay_out_of_mrerp(self):
        from pearnly_erp_portal import mrerp
        self.assertFalse(mrerp.MRERP_LEDGER_ENABLED)
        for method, body in (('configure', {'system_url': 'https://www.mrerp4sme.com'}),
                             ('choose_ledger', {'company': self.company, 'connection_name': 'X', 'account_set': '6:1'}),
                             ('read_choices', {'company': self.company})):
            self.assertEqual(self.call(method, **body).status_code, 403, method)
        # 就算公司上标了 MR.ERP 账本（只有开着时的入口能标，这里直接写库模拟）：提交的单不进 MR.ERP
        self.db(lambda: frappe.db.set_value('ERP Company', self.company, {'mrerp_enabled': 1, 'express_enabled': 0,
                                                                          'mrerp_linked_at': frappe.utils.now_datetime()}),
                user='Administrator', commit=True)
        try:
            row = self.ledger(self.sale())
            self.assertNotEqual(row.ledger_target, 'mrerp', row)
        finally:
            self.db(lambda: frappe.db.set_value('ERP Company', self.company, {'mrerp_enabled': 0,
                                                                              'mrerp_linked_at': None}),
                    user='Administrator', commit=True)

    def test_2_with_the_constant_on_a_sale_imports_into_fake_mrerp_and_waits_for_an_operator_held_account(self):
        from pearnly_core import session_lock
        from pearnly_core.mrerp.session import lock_key
        username = 'erp-user-' + self.suffix
        with ledger_on():
            response = self.call('configure', system_url='https://www.mrerp4sme.com')
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            view = response.json['message']
            self.assertEqual((view['system_url'], view['enabled']), ('https://www.mrerp4sme.com', True))
            self.assertNotIn('credential', view)
            connection = view['connection']

            def seed():
                # 实验室专用：账套目录（产品里由采集任务读 MR.ERP 写入）与 CC 桌面版授权写入的账号密码
                doc = frappe.get_doc('ERP MRERP Connection', connection)
                doc.catalog_json = json.dumps([{'source_id': '6:1', 'label': 'Synthetic 6:1'}])
                doc.credential_username = username
                doc.credential_password = PASSWORD
                doc.save(ignore_permissions=True)
            self.db(seed, user='Administrator', commit=True)
            chosen = self.call('choose_ledger', company=self.company, connection_name=connection, account_set='6:1')
            self.assertEqual(chosen.status_code, 200, chosen.get_data(as_text=True))
            company = self.db(lambda: frappe.db.get_value('ERP Company', self.company, [
                'mrerp_enabled', 'express_enabled', 'mrerp_connection', 'mrerp_account_set', 'mrerp_linked_at'],
                as_dict=True))
            self.assertEqual((company.mrerp_enabled, company.mrerp_connection, company.mrerp_account_set),
                             (1, connection, '6:1'))
            self.assertTrue(company.mrerp_linked_at)
            name = self.sale()
            row = self.ledger(name)
            self.assertEqual((row.ledger_state, row.ledger_target), ('queued', 'mrerp'), row)
            # 代录占着这个账号（真 Redis，同一把锁）：不导、照旧排队、等账号
            key = lock_key('https://www.mrerp4sme.com', username)
            holder = 'erp-lab:batch-' + self.suffix
            self.assertTrue(self.db(lambda: session_lock.lease_account(key, holder), user='Administrator'))
            try:
                self.assertEqual(self.run_import(name, 'SE690912-000001'), [])
                row = self.ledger(name)
                self.assertEqual((row.ledger_state, row.ledger_reason), ('queued', 'mrerp.account_busy'), row)
            finally:
                self.assertTrue(self.db(lambda: session_lock.release_account(key, holder), user='Administrator'))
            # 放开后导入一张：账本栏记已写与 MR.ERP 单号
            self.assertEqual(self.run_import(name, 'SE690912-000001'), [('6:1', username, 1)])
            row = self.ledger(name)
            self.assertEqual((row.ledger_state, row.ledger_target, row.ledger_docnum),
                             ('written', 'mrerp', 'SE690912-000001'), row)
            # 已写的单再排一次：不再导入
            self.assertEqual(self.run_import(name, 'SE690912-000002'), [])
            self.assertEqual(self.ledger(name).ledger_docnum, 'SE690912-000001')


if __name__ == '__main__':
    connect()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ErpMrErpDirectLab))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()
