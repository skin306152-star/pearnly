"""MR.ERP 代录（ADR-026 第 3 步、11-03 选 A）在专用实验站点上的真实验证：真 MariaDB / Redis / WSGI，合成账号密码从不打印。

只在 `infrastructure/compose/cowork-backend-lab.yml` 的实验站点上跑（站点守卫、合成用户同 `cowork-backend-lab-verify.py`）；
不连 MR.ERP、不连外网、不迁移站点。直写执行器的对外导入（`runner.execute`）一律换成假的，不发任何请求。

全局常量 ``MRERP_OPERATOR_ENABLED`` 在产品代码里保持 False：「开着」的用例只在本验证进程里临时打补丁（`patch.object`），
WSGI 请求在同一进程里跑，所以补丁对认证、心跳、领单、回执都生效；「关着」的用例不打补丁，核这些路都不通。

覆盖（接线记录 §6 第 2 条、§9.5 第 1 条）：

- 开录：`application.submit` 带代录批次与 ``mrerp`` 块、选定电脑 → `_prepare_mrerp` → `ledger.reserve` → `ledger.confirm`（不是
  `confirm_server`、不排服务端执行任务），记录排着、带选定电脑、载荷是 MR.ERP 代录单；
- 领单查询：`mrerp_jobs.heads` 的真 SQL 取得到这台的、排掉别的电脑的与直写单（``own`` 两个方向）；
- MR.ERP 连接自己的密钥经真实机器认证：心跳记电脑行（目标、用户名）、领单拿到 MR.ERP 代录载荷、回执记 MR.ERP 单号并放开账号；
- 同一台电脑两把密钥（Express 连接与 MR.ERP 连接各一把）同时来领同一张：只有一把领到、只有一条投递；
- 真 Redis 上的账号互斥：代录租着时直写不领、记录照旧排队；租约过期（按 Redis 剩余时间）后直写恢复；直写短锁占着时代录领不到、
  单子照旧排着，放开后领到；
- 常量关着：MR.ERP 密钥心跳 / 领单 403、电脑行不变；开录前准备拒收；排着的 MR.ERP 代录单 Express 密钥也领不到。

运行（站点建好之后）：
  docker compose -p <项目> -f infrastructure/compose/cowork-backend-lab.yml exec -T -w /home/frappe/frappe-bench/sites \\
    bench ../env/bin/python /workspace/scripts/cowork-mrerp-operator-lab-verify.py
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import runpy
import secrets
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.config import clear_site_config_cache
from werkzeug.test import Client
from werkzeug.wrappers import Response

BASE = runpy.run_path(str(Path(__file__).with_name('cowork-backend-lab-verify.py')))
connect, user, grant = BASE['connect'], BASE['user'], BASE['grant']
FEATURES = ['intake', 'master_data', 'push_log']
PATHS = {'heartbeat': '/api/method/pearnly_cowork.companion.heartbeat.report',
         'lease': '/api/method/pearnly_cowork.companion.queue_api.lease',
         'ack': '/api/method/pearnly_cowork.companion.queue_api.acknowledge'}
FIELDS = {'direction': 'purchase', 'seller_name': 'Synthetic seller', 'seller_tax': '0107561000013',
          'buyer_tax': '0994000333444', 'buyer_name': 'Synthetic buyer', 'subtotal': '100',
          'vat': '7', 'total_amount': '107', 'date': '2026-09-21', 'document_type': 'tax_invoice'}
PASSWORD = 'synthetic-' + uuid4().hex[:10]
VERSION = '1.13.0'  # 认得出重发同一件（task_key）的版本


@contextmanager
def operator_on():
    """只在本进程里把全局常量打开（产品代码里仍是 False）。"""
    from pearnly_core.express_operator import contracts
    with patch.object(contracts, 'MRERP_OPERATOR_ENABLED', True):
        yield


def lock_of(account_key):
    from pearnly_core import session_lock
    return session_lock._account_lock_object(account_key, session_lock.OPERATOR_LEASE_SEC)


class MrErpOperatorLab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suffix = uuid4().hex[:10]
        cls.manager = f'cw-mop-admin-{suffix}@example.invalid'
        cls.actor = f'cw-mop-actor-{suffix}@example.invalid'
        user(cls.manager, ['Cowork Admin'])
        user(cls.actor, ['Cowork Intake', 'Cowork Push Log'])
        frappe.db.commit()
        cls.site = frappe.local.site
        cls.config_path = Path(frappe.get_site_path('site_config.json'))
        cls.original_config = cls.config_path.read_text()
        config = json.loads(cls.original_config)
        if not config.get('pearnly_backend_lab'):
            raise SystemExit('Marked lab Site required')
        config.update(pearnly_cowork_erp_push_enabled=True, pearnly_features=FEATURES)
        cls.config_path.write_text(json.dumps(config))
        clear_site_config_cache()
        frappe.destroy()

    @classmethod
    def tearDownClass(cls):
        cls.config_path.write_text(cls.original_config)
        clear_site_config_cache()

    def setUp(self):
        from frappe.app import application
        self.client = Client(application, Response, use_cookies=False)
        self.suffix = uuid4().hex[:10]
        self.username = 'op-user-' + self.suffix
        self.device_key = secrets.token_urlsafe(32)
        self.leases = []
        self.workspace = self.db(self._workspace, user='Administrator', commit=True)
        self.mrerp = self.db(lambda: self._connection('mrerp', 'Synthetic MR.ERP'), user=self.manager, commit=True)
        self.db(self._seed_login, user='Administrator', commit=True)
        issued = self.db(lambda: self._register(self.mrerp), user=self.actor, commit=True)
        self.token, self.device = issued['token'], issued['device']

    def tearDown(self):
        def clear():
            for key in self.leases:  # 只清本用例的合成账号锁
                lock = lock_of(key)
                lock.redis.delete(lock.name)
        self.db(clear, user='Administrator')
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()

    # ── 夹具 ────────────────────────────────────────────────────────────────────────────────
    def db(self, function, *, user=None, commit=False):
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()
        connect(self.site)
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        frappe.conf.pearnly_features = FEATURES
        frappe.set_user(user or self.actor)
        try:
            result = function()
            if commit:
                frappe.db.commit()
            return result
        finally:
            frappe.db.rollback()
            frappe.destroy()

    def _workspace(self):
        from pearnly_cowork.masters import api
        name = api.create_workspace('Synthetic MR.ERP operator ' + self.suffix)['name']
        grant(self.actor, 'Cowork Workspace', name)
        return name

    def _connection(self, adapter, label):
        from pearnly_cowork.connections import service
        return service.configure(self.workspace, adapter, uuid4().hex, label, {}, users=[self.manager, self.actor])

    def _seed_login(self):
        """站点上存的 MR.ERP 账号（同 `cowork-mrerp-login-lab-verify.seed_old_login`）：直写取凭据用。"""
        from pearnly_cowork.connections import access
        doc = frappe.get_doc('Cowork ERP Connection', self.mrerp['name'])
        doc.set('credential_username', self.username)
        doc.set('credential_password', PASSWORD)
        access.write(doc)

    def _register(self, connection):
        from pearnly_cowork.companion import credentials
        adapter = frappe.db.get_value('Cowork ERP Connection', connection['name'], 'adapter')
        return credentials._register_device(connection['name'], adapter, self.actor, uuid4().hex, self.device_key,
                                            'SYN-OP-PC', False)

    def express_key(self):
        self.express = self.db(lambda: self._connection('express', 'Synthetic Express'), user=self.manager, commit=True)
        issued = self.db(lambda: self._register(self.express), user=self.actor, commit=True)
        self.assertEqual(issued['device'], self.device)  # 同一台电脑，两条连接各一把
        return issued['token']

    def history(self):
        from pearnly_cowork.history import storage
        pages = [{'page_index': 1, 'fields': {**deepcopy(FIELDS), 'invoice_number': 'MOP-' + uuid4().hex[:12]}}]
        return storage.create_recognized(self.workspace, pages, uuid4().hex, source='manual', confidence='high',
                                         ai_raw=deepcopy(pages))

    def submit(self, *, operator=True, device=None, batch=None, index=1, total=1):
        """一张票交出去：代录（批次 + ``mrerp`` 块 + 选定电脑）或直写；回 (提交结果, 是否排了服务端执行任务)。
        票先建好、提交（同产品里：票早已在库里，提交只改推送）。"""
        from pearnly_cowork.push.application import service
        from pearnly_cowork.push.mrerp import runner
        found = self.db(self.history, commit=True)

        def run():
            revision = frappe.db.get_value('Cowork ERP Connection', self.mrerp['name'], 'record_revision')
            options = {}
            if operator:
                options = {'route_device': device or self.device,
                           'operator': {'id': batch or 'batch-' + self.suffix, 'index': index, 'total': total, 'start_at': 0,
                                        'mrerp': {'connection': self.mrerp['name'], 'doc_type': 'purchase_credit',
                                                  'lines': [{'code': 'SYN', 'qty': '1', 'price': '100'}]}}}
            with patch.object(runner, 'dispatch') as dispatched:
                result = service.submit(found['name'], self.mrerp['name'], uuid4().hex, found['record_version'],
                                        revision, **options)
            return result, dispatched.called
        return self.db(run, commit=True)

    def record(self, name):
        return self.db(lambda: frappe.get_doc('Cowork Push Record', name).as_dict(), user='Administrator')

    def post(self, operation, token, body=None):
        response = self.client.open(PATHS[operation], base_url='http://' + self.site, method='POST', buffered=True,
                                    headers={'Authorization': 'Bearer ' + token}, json=body or {})
        text = response.get_data(as_text=True)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(token, text)
        self.assertEqual(response.headers.getlist('Set-Cookie'), [])
        return response

    def heartbeat(self, token=None, *, users=None):
        return self.post('heartbeat', token or self.token, {
            'companion_version': VERSION, 'max_payload_version': 18, 'operator_targets': ['express', 'mrerp'],
            'mrerp_logins': [self.mrerp['name']], 'mrerp_users': {self.mrerp['name']: users or self.username}})

    def lease(self, token=None):
        response = self.post('lease', token or self.token)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['message']['jobs']

    def ack(self, log_id, token=None, **fields):
        response = self.post('ack', token or self.token, {'log_id': log_id, 'result': 'success',
                                                          'express_docnum': 'MR-' + self.suffix, **fields})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['message']

    def account(self, username=None):
        from pearnly_core.mrerp.session import lock_key
        key = lock_key('https://www.mrerp4sme.com', username or self.username)
        self.leases.append(key)
        return key

    def held(self, key=None):
        from pearnly_core import session_lock
        key = key or self.account()
        return self.db(lambda: session_lock.operator_holds(key), user='Administrator')

    def run_direct(self, name, docnum='MR-DIRECT'):
        """直写执行器跑一次（对外导入换成假的）；回假导入被调了几次。"""
        from pearnly_cowork.push.mrerp import runner
        calls = []

        def fake(payload, target, secrets_, system_url, today):
            calls.append(secrets_.get('username'))
            return {'status': 'success', 'docnum': docnum, 'meta': {}}

        def run():
            with patch.object(runner, 'execute', fake), patch.object(runner, 'dispatch'):
                runner.run(name)
        self.db(run, user='Administrator')
        return calls

    def queued(self):
        """常量开着：交一张代录单、心跳报到，回记录号。"""
        result, dispatched = self.submit()
        self.assertEqual(result['status'], 'pending', result)
        self.assertFalse(dispatched)
        response = self.heartbeat()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return result['name']

    # ── 常量开着 ─────────────────────────────────────────────────────────────────────────────
    def test_open_operator_job_is_confirmed_not_dispatched_and_heads_finds_only_this_computer(self):
        from pearnly_core.express_operator import contracts
        from pearnly_cowork.companion import credentials, mrerp_jobs
        with operator_on():
            result, dispatched = self.submit()
            self.assertEqual(result['status'], 'pending', result)
            self.assertFalse(dispatched)  # 只 confirm，不排服务端执行任务
            row = self.record(result['name'])
            payload = json.loads(row['payload_json'])
            self.assertEqual((row['route_device'], row['status']), (self.device, 'pending'))
            self.assertTrue(contracts.is_operator(payload))
            self.assertEqual(contracts.target_of(payload), 'mrerp')
            self.assertGreaterEqual(payload['payload_version'], 18)
            self.assertEqual(payload['mrerp']['connection'], self.mrerp['name'])
            # 直写同一连接：照旧排服务端执行任务、不带选定电脑
            direct, direct_dispatched = self.submit(operator=False)
            self.assertEqual(direct['status'], 'pending', direct)
            self.assertTrue(direct_dispatched)
            self.assertEqual(self.record(direct['name'])['route_device'] or '', '')
            # 派给别的电脑的一张
            from pearnly_cowork.companion import devices
            other_device = self.db(lambda: devices.create_claimed(
                workspace=self.workspace, owner=self.actor, label='SYN-OTHER', secret_digest=uuid4().hex * 2,
                signed_in_user=self.actor).name, commit=True)
            other, _ = self.submit(device=other_device)
            self.assertEqual(other['status'], 'pending', other)
            express_token = self.express_key()

            def heads():
                own = credentials.authenticate(self.token)
                via_express = credentials.authenticate(express_token)
                return ([r.name for r in mrerp_jobs.heads(own, self.device, own=True)],
                        [r.name for r in mrerp_jobs.heads(via_express, self.device)],
                        [r.name for r in mrerp_jobs.heads(own, self.device)],
                        [r.name for r in mrerp_jobs.heads(own, other_device, own=True)])
            mine, express, wrong_direction, others = self.db(heads, user='Administrator')
            self.assertEqual(mine, [result['name']])
            self.assertEqual(express, [result['name']])
            self.assertEqual(wrong_direction, [])  # MR.ERP 密钥按「别的连接」找：没有
            self.assertEqual(others, [other['name']])

    def test_mrerp_key_heartbeats_leases_and_acknowledges_through_machine_auth(self):
        from pearnly_cowork.companion import devices
        with operator_on():
            name = self.queued()
            device = self.db(lambda: frappe.get_doc(devices.DOCTYPE, self.device).as_dict(), user='Administrator')
            self.assertIn('mrerp', device['operator_targets'])
            self.assertEqual(json.loads(device['mrerp_users']), {self.mrerp['name']: self.username})
            self.assertIn(self.mrerp['name'], device['mrerp_logins'])
            self.assertFalse(self.held())
            jobs = self.lease()
            self.assertEqual(len(jobs), 1)
            payload = jobs[0]['payload']
            self.assertEqual(payload['operator']['target'], 'mrerp')
            self.assertEqual(payload['mrerp']['connection'], self.mrerp['name'])
            self.assertIn('task_key', jobs[0])
            self.assertEqual(self.record(name)['status'], 'leased')
            self.assertTrue(self.held())  # 领单时租上账号
            # 手上还有一件又来领 = 那件丢了（ADR-021 11-02）：重发同一件（同一 task_key），不领第二件
            again = self.lease()
            self.assertEqual([job['task_key'] for job in again], [jobs[0]['task_key']])
            self.assertEqual(self.record(name)['status'], 'leased')
            self.assertTrue(self.held())
            self.ack(again[0]['log_id'])
            row = self.record(name)
            self.assertEqual(row['status'], 'success')
            self.assertEqual(row['external_docnum'], 'MR-' + self.suffix)
            self.assertFalse(self.held())  # 这批最后一张回执：放开账号

    def test_an_express_key_of_the_chosen_computer_also_takes_and_acknowledges_it(self):
        """第 3 步原样的路：同一工作空间有 Express 连接时，这台电脑的 Express 密钥领到 MR.ERP 代录单（投递挂在 Express 连接上），
        回执按记录自己的 MR.ERP 连接记单号、放开账号。"""
        with operator_on():
            express_token = self.express_key()
            name = self.queued()
            jobs = self.lease(express_token)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['payload']['operator']['target'], 'mrerp')
            self.assertTrue(self.held())
            delivery = self.db(lambda: frappe.db.get_value('Cowork Companion Delivery', {'push_record': name},
                                                           'connection'), user='Administrator')
            self.assertEqual(delivery, self.express['name'])
            self.ack(jobs[0]['log_id'], token=express_token)
            row = self.record(name)
            self.assertEqual((row['status'], row['external_docnum']), ('success', 'MR-' + self.suffix))
            self.assertFalse(self.held())

    def test_two_keys_of_one_computer_racing_for_one_job_only_one_gets_it(self):
        """同一台电脑两把密钥（Express 连接、MR.ERP 连接各一把）同时来领同一张：只有一把领到、只有一条投递；输家那把交不了回执。
        两轮，线程先后对调（两把各先起一次）；领到的那把在领取事务里（账本已领、还没提交）多停 1 秒，核输家确实同时走到了账本领取、
        被记录行锁挡下（不是先后错开）。"""
        from pearnly_cowork.companion import transport
        real_lease, real_take, calls = transport.ledger.lease, transport.mrerp_jobs.take, []

        def lease(*args, **kwargs):
            started = time.monotonic()
            try:
                claim = real_lease(*args, **kwargs)
                calls.append((threading.current_thread().name, started, 'leased' if claim['leased'] else 'waiting'))
                return claim
            except Exception as error:
                # 输家撞上赢家还没提交的领取：账本按「不可领」回，或库回冲突（1020 / 死锁）→ 共用的整笔重来，重来时这台已忙
                calls.append((threading.current_thread().name, started, type(error).__name__))
                raise

        def take(*args, **kwargs):
            time.sleep(1)
            return real_take(*args, **kwargs)
        with operator_on(), patch.object(transport.ledger, 'lease', lease), \
                patch.object(transport.mrerp_jobs, 'take', take):
            express_token = self.express_key()
            tokens = {'mrerp': self.token, 'express': express_token}
            winners, outcomes = [], []
            for order in (('mrerp', 'express'), ('express', 'mrerp')):
                name = self.queued()
                barrier, found, errors = threading.Barrier(2), {}, []
                calls.clear()

                def race(label):
                    try:
                        from frappe.app import application
                        client = Client(application, Response, use_cookies=False)
                        barrier.wait(timeout=30)
                        response = client.open(PATHS['lease'], base_url='http://' + self.site, method='POST',
                                               buffered=True, headers={'Authorization': 'Bearer ' + tokens[label]},
                                               json={})
                        found[label] = (response.status_code, response.json)
                    except Exception as error:  # noqa: BLE001 — 线程里的失败带回主线程断言
                        errors.append(repr(error))
                threads = [threading.Thread(target=race, args=(label,), name=label) for label in order]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=120)
                self.assertEqual(errors, [])
                self.assertEqual({label: status for label, (status, _) in found.items()},
                                 {'mrerp': 200, 'express': 200})
                delivered = {label: body['message']['jobs'] for label, (_, body) in found.items()}
                self.assertEqual(sorted(len(jobs) for jobs in delivered.values()), [0, 1], delivered)
                self.assertEqual(self.record(name)['status'], 'leased')
                self.assertEqual(self.db(lambda: frappe.db.count('Cowork Companion Delivery', {'push_record': name}),
                                         user='Administrator'), 1)
                winner = next(label for label, jobs in delivered.items() if jobs)
                winners.append(winner)
                # 两把都走到了账本领取：赢家领到一次；输家的领取在赢家提交前开始（赢家停着 1 秒），没领到
                loser_label = 'express' if winner == 'mrerp' else 'mrerp'
                self.assertEqual([outcome for label, _, outcome in calls if label == winner], ['leased'], calls)
                lost = [(started, outcome) for label, started, outcome in calls if label == loser_label]
                self.assertTrue(lost, calls)
                self.assertNotIn('leased', [outcome for _, outcome in lost], calls)
                start = next(started for label, started, _ in calls if label == winner)
                self.assertLess(abs(lost[0][0] - start), 1.0, calls)
                outcomes.append(lost[0][1])
                log_id = delivered[winner][0]['log_id']
                loser = 'express' if winner == 'mrerp' else 'mrerp'
                refused = self.post('ack', tokens[loser], {'log_id': log_id, 'result': 'success', 'express_docnum': 'X'})
                self.assertNotEqual(refused.status_code, 200)
                self.ack(log_id, token=tokens[winner])
                self.assertEqual(self.record(name)['status'], 'success')
            print(f'  两把密钥同时领，两轮领到的：{winners}；输家在账本领取处：{outcomes}', flush=True)

    def test_operator_lease_holds_direct_write_back_until_it_expires(self):
        """一批两张：第一张回执后这批还有单、账号不放；这期间直写不领、照旧排着；电脑不在、租约过期后直写恢复；电脑回来领第二张时重新租上。"""
        with operator_on():
            direct, _ = self.submit(operator=False)
            batch = 'batch-two-' + self.suffix
            first, _ = self.submit(batch=batch, index=1, total=2)
            second, _ = self.submit(batch=batch, index=2, total=2)
            self.assertEqual((first['status'], second['status']), ('pending', 'pending'))
            self.assertEqual(self.heartbeat().status_code, 200)
            jobs = self.lease()
            self.assertEqual([job['history_id'] for job in jobs], [self.record(first['name'])['history']])
            self.ack(jobs[0]['log_id'])
            self.assertEqual(self.record(first['name'])['status'], 'success')
            self.assertTrue(self.held())  # 这批还有第二张：账号不放
            # 代录租着：直写不导入、照旧排着
            self.assertEqual(self.run_direct(direct['name']), [])
            self.assertEqual(self.record(direct['name'])['status'], 'pending')
            # 电脑不在：租约按 Redis 剩余时间过期（剩余时间缩到 1 秒，等同 300 秒没人续）
            key = self.account()

            def shorten():
                lock = lock_of(key)
                left = lock.redis.pttl(lock.name)
                lock.redis.pexpire(lock.name, 1000)
                return left
            self.assertGreater(self.db(shorten, user='Administrator'), 200_000)
            time.sleep(1.5)
            self.assertFalse(self.held())
            self.assertEqual(self.run_direct(direct['name']), [self.username])
            self.assertEqual(self.record(direct['name'])['status'], 'success')
            # 电脑回来：领第二张前重新租上，回执后这批录完、放开
            jobs = self.lease()
            self.assertEqual([job['history_id'] for job in jobs], [self.record(second['name'])['history']])
            self.assertTrue(self.held())
            self.ack(jobs[0]['log_id'])
            self.assertFalse(self.held())

    def test_direct_write_short_lock_keeps_the_operator_job_queued(self):
        from pearnly_core import session_lock
        with operator_on():
            name = self.queued()
            key = self.account()
            connect(self.site)  # 短锁要站点上下文找 Redis；锁着期间的请求各自另开
            with session_lock.account_lock(key, timeout_sec=1):  # 直写正在导入
                frappe.destroy()
                self.assertEqual(self.lease(), [])
                row = self.record(name)
                self.assertEqual((row['status'], row['active_attempt'] or ''), ('pending', ''))
                self.assertEqual(self.db(lambda: frappe.db.count('Cowork Companion Delivery', {'push_record': name}),
                                         user='Administrator'), 0)
                connect(self.site)
            frappe.destroy()
            jobs = self.lease()  # 放开后领到
            self.assertEqual(len(jobs), 1)
            self.assertTrue(self.held())
            self.ack(jobs[0]['log_id'])
            self.assertFalse(self.held())

    def test_site_username_is_used_when_the_computer_reports_none(self):
        with operator_on():
            result, _ = self.submit()
            self.assertEqual(result['status'], 'pending', result)
            response = self.post('heartbeat', self.token, {'companion_version': VERSION, 'max_payload_version': 18,
                                                           'operator_targets': ['express', 'mrerp']})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            jobs = self.lease()
            self.assertEqual(len(jobs), 1)
            self.assertTrue(self.held())  # 站点上存的用户名那把
            self.ack(jobs[0]['log_id'])
            self.assertFalse(self.held())

    # ── 常量关着（产品代码里的值）────────────────────────────────────────────────────────────
    def test_with_the_constant_off_none_of_these_paths_work(self):
        from pearnly_core.express_operator import contracts
        from pearnly_cowork.companion import devices
        self.assertFalse(contracts.MRERP_OPERATOR_ENABLED)
        before = self.db(lambda: frappe.get_doc(devices.DOCTYPE, self.device).as_dict(), user='Administrator')
        self.assertEqual(self.heartbeat().status_code, 403)
        self.assertEqual(self.post('lease', self.token).status_code, 403)
        after = self.db(lambda: frappe.get_doc(devices.DOCTYPE, self.device).as_dict(), user='Administrator')
        for field in ('operator_targets', 'mrerp_users', 'mrerp_logins', 'companion_version', 'max_payload_version'):
            self.assertEqual(after.get(field), before.get(field), field)
        # 开录前准备：拒收代录批次，不建记录
        refused, dispatched = self.submit()
        self.assertEqual(refused['status'], 'failed', refused)
        self.assertFalse(dispatched)
        self.assertEqual(self.db(lambda: frappe.db.count('Cowork Push Record', {'connection': self.mrerp['name']}),
                                 user='Administrator'), 0)
        # 开着时排上的一张，关了之后：Express 密钥领不到，MR.ERP 密钥 403
        with operator_on():
            name = self.queued()
        express_token = self.express_key()
        self.assertEqual(self.lease(express_token), [])
        self.assertEqual(self.post('lease', self.token).status_code, 403)
        self.assertEqual(self.record(name)['status'], 'pending')
        self.assertFalse(self.held())
        # 开着时领走的一张，关了之后回执：MR.ERP 密钥 403，记录还在电脑手上
        with operator_on():
            jobs = self.lease()
            self.assertEqual(len(jobs), 1)
        refused = self.post('ack', self.token, {'log_id': jobs[0]['log_id'], 'result': 'success', 'express_docnum': 'X'})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.record(name)['status'], 'leased')


if __name__ == '__main__':
    connect()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(MrErpOperatorLab))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        if getattr(frappe.local, 'db', None):
            frappe.db.rollback()
            frappe.destroy()
