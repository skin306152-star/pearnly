"""MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7 条）：专用实验站点上的真实 WSGI / 数据库 / Redis 验证，合成账号密码从不打印。

只在 `infrastructure/compose/cowork-backend-lab.yml` 的实验站点上跑（复用连接与凭据验证的站点守卫与夹具）；不连 MR.ERP、
不连外网、不迁移站点。覆盖：

- 会话发票：本人在使用名单上才拿得到票，名单外的人拒绝；票只给认得出的电脑；
- 机器方法：只认机器令牌的精确 POST——带人的 API 密钥、GET、错路径都拒绝，密码不落库；
- 收下之后：连接的 Password 字段变成新密码、连接版本不变、授权记录（谁、哪台电脑、什么时候）写上，这台电脑的已授权清单
  有这条连接号；页面（detail / records）看得到「用户名 · 电脑 · 时间」、看不到密码；同一张票再用回 ticket_invalid；
- 老连接：网页上存的密码（configure 交的）在 CC 授权之前照样被直写取凭据的入口拿到；授权之后拿到新的；
- 回复与错误里都没有密码；Cowork 站点上共用通道的同名方法不认 Cowork 的令牌。

运行（站点建好之后）：
  docker compose -p <项目> -f infrastructure/compose/cowork-backend-lab.yml exec -T -w /home/frappe/frappe-bench/sites \\
    bench ../env/bin/python /workspace/scripts/cowork-mrerp-login-lab-verify.py
"""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import unittest
from uuid import uuid4

import frappe
from frappe.config import clear_site_config_cache
from frappe.utils.password import get_decrypted_password
from werkzeug.test import Client
from werkzeug.wrappers import Response

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-companion-credentials-lab-verify.py')))
BASE = FIXTURE['NativeCredentials']
connect = FIXTURE['connect']
METHOD = 'pearnly_cowork.companion.credential_api.mrerp_login'
PATH = '/api/method/' + METHOD
CORE_PATH = '/api/method/pearnly_core.cc_desktop.machine.mrerp_login'
OLD_PASSWORD = 'old-synthetic-' + uuid4().hex[:8]
NEW_PASSWORD = 'new-synthetic-' + uuid4().hex[:8]
DEVICE_KEY = 'K' * 43
FEATURES = ['intake', 'master_data', 'push_log']


class MrErpLogin(unittest.TestCase):
    setUpClass = classmethod(BASE.setUpClass.__func__)
    tearDownClass = classmethod(BASE.tearDownClass.__func__)

    def setUp(self):
        from frappe.app import application
        from pearnly_cowork.companion import credentials
        from pearnly_cowork.connections import service
        self.old_conf = deepcopy(dict(frappe.conf))
        self.site = frappe.local.site
        self.base = 'http://' + self.site
        self.config_path = Path(frappe.get_site_path('site_config.json'))
        self.original_config = self.config_path.read_text()
        config = json.loads(self.original_config)
        self.assertTrue(config['pearnly_backend_lab'])
        config['pearnly_cowork_erp_push_enabled'] = True
        config['pearnly_features'] = FEATURES
        self.config_path.write_text(json.dumps(config))
        clear_site_config_cache()
        self.enable()
        frappe.set_user(self.manager)
        # 老连接：网页上（configure）存的账号密码；名单上有录入员 actor
        self.mrerp = service.configure(self.workspace, 'mrerp', uuid4().hex, 'Synthetic MR.ERP', {},
                                       users=[self.manager, self.actor],
                                       credentials={'username': 'old-user', 'password': OLD_PASSWORD})
        express = service.configure(self.workspace, 'express', uuid4().hex, 'Synthetic Express', {},
                                    users=[self.manager, self.actor])
        frappe.set_user(self.actor)
        issued = credentials._register_device(express['name'], 'express', self.actor, uuid4().hex, DEVICE_KEY,
                                              'SYN-PC', False)
        self.token, self.device = issued['token'], issued['device']
        frappe.set_user('Administrator')
        manager = frappe.get_doc('User', self.manager)
        manager.api_key, secret = uuid4().hex, uuid4().hex
        manager.api_secret = secret
        manager.save()
        self.human_headers = {'Authorization': f'token {manager.api_key}:{secret}'}
        frappe.db.commit()
        frappe.destroy()
        self.client = Client(application, Response, use_cookies=False)

    def tearDown(self):
        self.config_path.write_text(self.original_config)
        clear_site_config_cache()
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()
        connect(self.site)
        frappe.conf.clear()
        frappe.conf.update(self.old_conf)
        frappe.set_user('Administrator')

    @staticmethod
    def enable():
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        frappe.conf.pearnly_features = FEATURES

    def db(self, function, *, user=None, commit=False):
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()
        connect(self.site)
        self.enable()
        frappe.set_user(user or self.manager)
        try:
            result = function()
            if commit:
                frappe.db.commit()
            return result
        finally:
            frappe.db.rollback()
            frappe.destroy()

    def ticket(self, user=None, key=DEVICE_KEY):
        from pearnly_cowork.companion import api
        return self.db(lambda: api.mrerp_login_ticket(self.mrerp['name'], key), user=user or self.actor, commit=True)

    def post(self, body, *, headers=None, path=PATH, method='POST'):
        response = self.client.open(path, base_url=self.base, method=method, buffered=True,
                                    headers={'Authorization': 'Bearer ' + self.token} if headers is None else headers,
                                    **({'json': body} if method == 'POST' else {}))
        text = response.get_data(as_text=True)
        self.assertNotIn(NEW_PASSWORD, text)
        self.assertNotIn(OLD_PASSWORD, text)
        self.assertEqual(response.headers.getlist('Set-Cookie'), [])
        return response

    def stored(self):
        def read():
            doc = frappe.get_doc('Cowork ERP Connection', self.mrerp['name'])
            return {'password': get_decrypted_password('Cowork ERP Connection', doc.name, 'credential_password',
                                                       raise_exception=False),
                    'username': get_decrypted_password('Cowork ERP Connection', doc.name, 'credential_username',
                                                       raise_exception=False),
                    'revision': doc.record_revision, 'by': doc.credential_authorized_by,
                    'device': doc.credential_device, 'at': doc.credential_authorized_at,
                    'logins': frappe.db.get_value('Cowork CC Desktop Device', self.device, 'mrerp_logins')}
        return self.db(read)

    def for_push(self):
        from pearnly_cowork.connections import service

        def read():
            loaded = service.read_for_push(self.mrerp['name'], self.workspace, None, actor=self.actor)
            return service.credentials_for_use(loaded)
        return self.db(read, user=self.actor)

    def test_old_connection_logs_in_with_its_stored_password_and_cc_authorization_replaces_it(self):
        self.assertEqual(self.for_push(), {'username': 'old-user', 'password': OLD_PASSWORD, 'token': ''})
        before = self.stored()
        ticket = self.ticket()
        self.assertTrue(ticket['ok'])
        response = self.post({'ticket': ticket['ticket'], 'username': 'test01', 'password': NEW_PASSWORD})
        self.assertEqual(response.status_code, 200)
        message = response.json['message']
        self.assertTrue(message['ok'])
        self.assertEqual((message['connection'], message['authorized']['username'], message['authorized']['device']),
                         (self.mrerp['name'], 'test01', 'SYN-PC'))
        after = self.stored()
        self.assertEqual((after['username'], after['password']), ('test01', NEW_PASSWORD))
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual((after['by'], after['device']), (self.actor, 'SYN-PC'))
        self.assertTrue(after['at'])
        self.assertIn(self.mrerp['name'], after['logins'].split(','))
        self.assertEqual(self.for_push(), {'username': 'test01', 'password': NEW_PASSWORD, 'token': ''})
        # 页面看得到「用户名 · 电脑 · 时间」，看不到密码
        from pearnly_cowork.connections import service
        for view in (self.db(lambda: service.detail(self.mrerp['name']), user=self.actor),
                     self.db(lambda: service.records(self.workspace), user=self.actor)):
            text = json.dumps(view, default=str)
            self.assertNotIn(NEW_PASSWORD, text)
            self.assertIn('"username": "test01"', text)
            self.assertIn('SYN-PC', text)
        # 同一张票再用：作废了
        again = self.post({'ticket': ticket['ticket'], 'username': 'test01', 'password': NEW_PASSWORD})
        self.assertEqual(again.json['message'], {'ok': False, 'code': 'ticket_invalid'})

    def test_people_off_the_list_and_unknown_computers_get_no_ticket(self):
        with self.assertRaises(frappe.PermissionError):
            self.ticket(user=self.outsider)
        self.assertEqual(self.ticket(key='Z' * 43), {'ok': False, 'code': 'device_unknown'})

    def test_only_the_exact_machine_post_is_accepted(self):
        ticket = self.ticket()['ticket']
        body = {'ticket': ticket, 'username': 'test01', 'password': NEW_PASSWORD}
        refused = [self.post(body, headers=self.human_headers), self.post(body, method='GET'),
                   self.post(body, headers={}), self.post(body, path='/api/v2/method/' + METHOD),
                   self.post(body, headers={'Authorization': 'Bearer exp_' + self.mrerp['name'] + '_forged'}),
                   self.post(body, path=CORE_PATH)]
        for response in refused:
            self.assertIn(response.status_code, (400, 401, 403, 404, 405))
        self.assertEqual(self.stored()['password'], OLD_PASSWORD)
        # 票没被这些请求用掉：正当的那一次照样成
        self.assertTrue(self.post(body).json['message']['ok'])

    def test_bad_bodies_answer_a_code_without_echoing_anything(self):
        ticket = self.ticket()['ticket']
        for body in ({'ticket': ticket, 'username': 'test01'},
                     {'ticket': ticket, 'username': 'test01', 'password': NEW_PASSWORD, 'extra': 1},
                     {'ticket': 'mrl_' + 'x' * 43, 'username': 'test01', 'password': NEW_PASSWORD}):
            response = self.post(body)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json['message']['ok'])
        self.assertEqual(self.stored()['password'], OLD_PASSWORD)


if __name__ == '__main__':
    connect()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MrErpLogin))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()
