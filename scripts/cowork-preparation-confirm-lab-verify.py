"""Scoped local collection/confirmation verification. Synthetic Site writes only.

No scheduler, external network, ERP/LINE writer, browser or paid provider. Uses
native machine authentication + real report ingestion and human WSGI requests.

Preparation evidence v2 (ADR-021): the computer reports through `heartbeat._receive` (account set rows carry
their six mappings), confirmation needs a writer in the pool (`has_writer`), binds no key, compares the whole
evidence of the day; bank evidence is the entering user's; a refused confirmation returns ``failed``.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from pymysql.err import ProgrammingError

BASE = runpy.run_path(str(Path(__file__).with_name('cowork-push-application-lab-verify.py')))
P = BASE['NativePreparation']
MAPPING_KEYS = ('fallback_acc', 'vat_input_acc', 'ap_acc', 'revenue_acc', 'vat_output_acc', 'ar_acc')
connect = BASE['connect']


class PreparationConfirmation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not frappe.db.exists('Role', 'Cowork Push Log'):
            frappe.get_doc({'doctype': 'Role', 'role_name': 'Cowork Push Log'}).insert()
        P.setUpClass.__func__(cls)
    tearDown, install, prepare = P.tearDown, P.install, P.prepare

    def setUp(self):
        P.setUp(self)
        from pearnly_cowork.companion import credentials, submission, transport
        from pearnly_cowork.connections import collector, reports, preparation
        from pearnly_cowork.masters import push_inputs
        from pearnly_cowork.reconciliation import bank_evidence
        self.credentials, self.submission, self.transport = credentials, submission, transport
        self.collector, self.reports, self.sources = collector, reports, preparation
        self.inputs, self.bank = push_inputs, bank_evidence
        frappe.set_user(self.manager)
        self.issued = credentials.issue(self.connection['name'], uuid4().hex)
        self.machine = credentials.authenticate(self.issued['token'])
        self.report()
        self.choose()
        frappe.set_user(self.actor)

    def choose(self):
        # 现在的推送要先定付款方式与科目（复核页选一次记在账套记忆里）；这里像会计在复核页选过一样记下
        from pearnly_cowork.push import choices as choice_store
        subject = 'purchase|' + BASE['FIELDS']['seller_tax']
        choice_store.record(self.connection['name'], 'DATAT', 'payment', subject, 'credit')
        choice_store.record(self.connection['name'], 'DATAT', 'account', subject, 'A')

    def report(self, **changes):
        body = {'account_set': 'DATAT', 'express_root': BASE['CONFIG']['express_root'],
            # 账套行带这份账套的 6 个科目（CC 桌面版 `account_probe` 同样报在每一行上）：多台电脑之后连接配置不再存它们
            'account_sets': [{'code': 'DATAT', 'path': 'DATAT', 'root': BASE['CONFIG']['express_root'], 'writable': True,
                              'mapping': {key: BASE['CONFIG'][key] for key in MAPPING_KEYS}}],
            'accounts': [{'code': code} for code in ('A','V','P','S','O','R')],
            'catalog': {'products': [], 'customers': []}, 'max_payload_version': 1}
        body.update(changes)
        # 心跳的真实入口（证据 v2 / 多台电脑：这台电脑的可达行、账套资料、电脑行，`heartbeat._receive`）
        from pearnly_cowork.companion import heartbeat
        return heartbeat._receive(self.machine, body)

    def confirm(self, prepared, key='confirm'):
        return self.submission.confirm(prepared['name'], prepared['revision'], key)

    def refused(self, prepared, key='confirm'):
        # 确认失败不抛给页面（2026-09-26 起）：整笔回滚、原因记在票上、回 failed。合成夹具不提交（各用例互不影响）：
        # 这一次的「整笔回滚」只退到确认前的保存点，夹具还在，之后照样断言记录仍是 prepared
        point = 'lab_refused_' + uuid4().hex[:12]
        frappe.db.savepoint(point)
        rollback = frappe.db.rollback
        with patch.object(frappe.db, 'rollback', lambda save_point=None, **kwargs: rollback(save_point=save_point or point)):
            result = self.confirm(prepared, key)
        self.assertEqual(result['status'], 'failed', result)
        return result

    def issuer_enabled(self, enabled):
        # 密钥签发人（管理员）停用 / 恢复：池里「能写的电脑」按签发人此刻能不能用这条连接核（`pool.writers`）
        frappe.db.set_value('User', self.manager, 'enabled', int(enabled), update_modified=False)
        frappe.clear_cache(user=self.manager)

    def row(self, prepared):
        return frappe.get_doc('Cowork Push Record', prepared['name'])

    def test_prepare_confirm_lease_exact_payload_and_idempotency(self):
        prepared = self.prepare()
        self.assertEqual(prepared['status'], 'prepared')
        self.assertTrue(prepared['has_reported_credential'])
        self.assertEqual(self.transport.lease(self.machine, {})['jobs'], [])
        frozen = self.row(prepared).payload_json
        pending = self.confirm(prepared)
        self.assertEqual(pending['status'], 'pending')
        replay = self.confirm(prepared)
        self.assertTrue(replay['replayed'])
        job = self.transport.lease(self.machine, {})['jobs'][0]
        self.assertEqual(job['payload'], json.loads(frozen))
        self.assertEqual(self.confirm(prepared)['status'], 'leased')
        self.assertEqual(frappe.db.count('Cowork Push Attempt', {'push_record': prepared['name']}), 1)
        with self.assertRaises(frappe.ValidationError): self.confirm(prepared, 'different')
        self.assertFalse({'frozen_payload', 'frozen_target', 'payload', 'credential'}.intersection(pending))
        self.assertNotIn(self.issued['token'], json.dumps(pending))

    def test_mapping_and_optional_profile_bank_are_real_native_reads(self):
        frappe.set_user(self.manager)
        self.inputs.save_mapping('account', 'accounts_payable', 'P')
        self.inputs.save_profile(self.workspace, BASE['FIELDS']['seller_tax'], default_payment='credit')
        self.inputs.save_profile(self.workspace, BASE['FIELDS']['seller_tax'], default_payment='cash', source='correction')
        frappe.set_user(self.actor)
        # 准备证据 v2：银行窗口按录入人取（以前按连接上报的那个人）
        self.bank.record(uuid4().hex, '2026-09-21', 'OUT', '107.00')
        prepared = self.prepare()
        self.assertEqual(prepared['preparation_states'], {'mappings': 'present',
            'supplier_profiles': 'present', 'bank_index': 'present', 'reported_config': 'present'})
        self.assertEqual(self.inputs.profiles(self.workspace, BASE['FIELDS']['seller_tax'])[
            BASE['FIELDS']['seller_tax']]['default_payment'], 'credit')
        # Sources freeze into payload; later editable inputs cannot rewrite it.
        frozen = self.row(prepared).payload_json
        frappe.set_user(self.manager)
        self.inputs.save_mapping('account', 'accounts_payable', 'CHANGED', expected_revision=1)
        frappe.set_user(self.actor)
        self.refused(prepared)
        self.assertEqual(self.row(prepared).payload_json, frozen)
        self.assertEqual(self.row(prepared).status, 'prepared')

    def test_optional_missing_table_differs_from_empty_and_deadlock_is_not_swallowed(self):
        with patch.object(self.inputs, 'profiles', side_effect=ProgrammingError(1146, 'synthetic missing')):
            prepared = self.prepare()
        self.assertEqual(prepared['status'], 'prepared')
        self.assertEqual(prepared['preparation_states']['supplier_profiles'], 'unavailable')
        with patch.object(self.inputs, 'profiles', side_effect=frappe.QueryDeadlockError):
            with self.assertRaises(frappe.QueryDeadlockError):
                self.sources._read_optional(self.inputs.profiles, {}, self.workspace, '')

    def test_legacy_complete_flag_is_not_collection_authority(self):
        frappe.set_user(self.manager)
        self.install(complete=False)
        frappe.set_user(self.actor)
        self.report()  # 目录是池里各台电脑的并集（ADR-021），旧的整份快照不是推送依据；电脑再报到一次
        self.assertEqual(self.prepare()['status'], 'prepared')
        with patch.object(self.inputs, 'mappings', side_effect=RuntimeError('synthetic collector failure')):
            with self.assertRaises(RuntimeError): self.prepare(request_key=uuid4().hex)

    def test_no_writer_cannot_confirm(self):
        # 证据 v2：能不能确认看池里有没有够得到这份账套、能写的电脑（`has_writer`），不再看连接上报的那把密钥。
        # 唯一一台够得到这份账套的电脑，它的密钥签发人此刻用不了这条连接 → 目录里还有、池里没有能写的电脑
        self.issuer_enabled(False)
        prepared = self.prepare()
        self.assertFalse(prepared['has_writer'])
        self.assertFalse(prepared['has_reported_credential'])  # 旧名同值保留
        self.refused(prepared)

    def test_a_preparation_that_can_no_longer_be_confirmed_is_replaced_on_push_again(self):
        # 2026-09-28 录制：小助手重扫账套的两分钟里推的票，恢复后重推一直「推送目标已变更」
        # 付款 / 科目这类「选一次」与本用例无关：当作都已选过。
        chosen = patch('pearnly_cowork.push.application.service._decisions',
                       return_value={'needed': [], 'applied': {}})
        chosen.start(); self.addCleanup(chosen.stop)
        stale = self.prepare()
        self.assertTrue(stale['has_writer'])
        # 小助手重扫账套后这份账套的科目表变了：准备时的事实对不上，这份准备确认不了（证据 v2 比整份事实）
        self.report(accounts=[{'code': code} for code in ('A', 'V', 'P', 'S', 'O', 'R', 'NEW')])
        fresh = self.prepare(request_key=uuid4().hex)
        self.assertNotEqual(fresh['name'], stale['name'])
        self.assertEqual((fresh['status'], fresh['has_writer']), ('prepared', True))
        self.assertEqual((self.row(stale).status, self.row(stale).reason), ('cancelled', 'push.preparation_superseded'))
        self.assertEqual(frappe.db.count('Cowork Push Attempt', {'push_record': stale['name']}), 0)
        # 还能确认的准备照旧挡住重复准备，不被替换
        again = self.prepare(request_key=uuid4().hex)
        self.assertEqual((again['name'], again.get('blocked_reason')), (fresh['name'], 'active'))
        self.assertEqual(self.row(fresh).status, 'prepared')

    def test_other_actor_and_revoked_scope_cannot_confirm(self):
        prepared = self.prepare()
        frappe.set_user(self.manager)
        self.refused(prepared)
        # Cowork 不按人分公司（用户 2026-09-26）：谁能用 Express 看连接自己的使用名单（ADR-021 D2）。把录入人移出名单
        self.connection = self.connections.configure(self.workspace, 'express', uuid4().hex, 'Synthetic Express',
            BASE['CONFIG'], users=[self.manager], name=self.connection['name'],
            expected_revision=self.connection['revision'])
        frappe.set_user(self.actor)
        self.refused(prepared)
        self.assertEqual(self.row(prepared).status, 'prepared')

    def rotate(self):
        frappe.set_user(self.manager)
        fresh = self.credentials.issue(self.connection['name'], uuid4().hex, reset=True, expected_generation=1)
        old, self.machine = self.machine, self.credentials.authenticate(fresh['token'])
        frappe.set_user(self.actor)
        return old

    def test_rotated_key_does_not_quarantine_pending_work(self):
        # 证据 v2 不绑密钥（ADR-021 第 3 批）：换钥前排着的单不再转人工；旧钥领不了，新钥报到后照常领到同一张
        prepared = self.prepare(); self.confirm(prepared)
        old = self.rotate()
        with self.assertRaises(frappe.AuthenticationError): self.transport.lease(old, {})
        self.report()
        job = self.transport.lease(self.machine, {})['jobs'][0]
        self.assertEqual(self.row(prepared).status, 'leased')
        self.assertEqual(frappe.get_doc('Cowork Companion Delivery', job['log_id']).credential_id,
                         self.machine.credential_id)
        self.assertEqual(frappe.db.count('Cowork Push Attempt', {'push_record': prepared['name']}), 1)

    def test_rotation_before_confirmation_still_confirms(self):
        # 准备证据 v2 只认 `route` 与本单用到的主档，不认上报的那把密钥：换钥后照常确认
        prepared = self.prepare()
        self.rotate(); self.report()
        self.assertEqual(self.confirm(prepared)['status'], 'pending')

    def test_day_expiry_rejects_confirmation_but_pending_refresh_does_not(self):
        # 准备证据 v2：人确认的是当天这份（过了一天确认不了）；这份账套正在重读主档不再挡确认——证据只比
        # 准备时的事实与 `route`，没有刷新状态（v1 的「刷新中不许确认」随证据 v2 去掉）
        prepared = self.prepare()
        with patch('pearnly_cowork.connections.preparation.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 1, tzinfo=timezone.utc)
            self.refused(prepared)
        self.assertEqual(self.row(prepared).status, 'prepared')
        self.connections.request_refresh(self.connection['name'], uuid4().hex, 'DATAT')
        self.assertEqual(self.confirm(prepared)['status'], 'pending')

    def test_other_owner_bank_and_other_workspace_profile_do_not_leak(self):
        # 银行窗口按录入人（准备证据 v2）：别人记的流水，日期在窗口里也不算；别的工作空间的档案不算
        frappe.set_user(self.manager)
        self.inputs.save_profile(self.foreign, BASE['FIELDS']['seller_tax'], default_payment='cash')
        self.bank.record(uuid4().hex, '2026-09-21', 'OUT', '107')
        frappe.set_user(self.actor)
        prepared = self.prepare()
        self.assertEqual(prepared['preparation_states']['bank_index'], 'empty')
        self.assertEqual(prepared['preparation_states']['supplier_profiles'], 'empty')

    def test_partial_report_does_not_relabel_other_account_caches(self):
        # 主档进那份账套的账套资料（`pool.put_facts`），连接这一行不再存主档与来源；所选是这台电脑够不到的账套时不记到任何账套名下
        from pearnly_cowork.connections import pool
        before = pool.facts(self.connection['name'], 'datat')
        self.report(account_set='OTHER', account_sets=None, catalog=None, accounts=[{'code': 'X'}])
        self.assertEqual(pool.facts(self.connection['name'], 'datat'), before)
        self.assertIsNone(pool.facts(self.connection['name'], 'other'))
        report = json.loads(frappe.get_doc('Cowork ERP Connection', self.connection['name']).report_json or '{}')
        self.assertFalse({'_preparation_origins', '_preparation_binding', 'reported_accounts'} & set(report))

    def test_late_failure_rolls_back_pending_and_receipt(self):
        from pearnly_cowork.push.native import service
        prepared = self.prepare(); persist = service.persist
        def fail_after(doc):
            result = persist(doc)
            if doc.doctype == service.RECORD and doc.status == 'pending':
                raise RuntimeError('synthetic after-save')
            return result
        with patch.object(service, 'persist', side_effect=fail_after), self.assertRaises(RuntimeError):
            self.confirm(prepared)
        row = self.row(prepared)
        self.assertEqual(row.status, 'prepared'); self.assertFalse(row.confirmation_request)
        self.assertEqual(self.confirm(prepared)['status'], 'pending')

    def test_native_fact_crud_and_share_are_denied(self):
        frappe.set_user(self.manager)
        value = self.inputs.save_mapping('account', 'accounts_payable', 'P')
        doc = frappe.get_doc('Cowork ERP Mapping', value['name'])
        with self.assertRaises(frappe.PermissionError): doc.save()
        with self.assertRaises(frappe.PermissionError): frappe.get_list('Cowork ERP Mapping')
        from frappe import share
        with self.assertRaises(frappe.PermissionError): share.add(doc.doctype, doc.name, self.actor, read=1)
        frappe.set_user(self.actor)
        with self.assertRaises(frappe.PermissionError): self.inputs.save_mapping('account', 'x', 'x')

    def test_history_edit_rejects_confirmation(self):
        prepared = self.prepare()
        pages = deepcopy(self.history['pages']); pages[0]['fields']['invoice_number'] = 'changed'
        self.storage.update_pages(self.history['name'], pages, self.history['record_version'])
        self.refused(prepared)
        self.assertEqual(self.row(prepared).status, 'prepared')

    def test_client_id_is_server_derived_for_mapping(self):
        from pearnly_cowork.masters import api as masters
        frappe.set_user(self.manager)
        client = masters.create_client(self.workspace, 'Synthetic mapped buyer')['name']
        frappe.set_user('Administrator')
        BASE['grant'](self.actor, 'Cowork Client', client)
        frappe.set_user(self.manager)
        self.inputs.save_mapping('client', client, 'NATIVE-SUPPLIER')
        self.connection = self.connections.configure(self.workspace, 'express', uuid4().hex,
            'Synthetic client connection', BASE['CONFIG'], client=client, users=[self.manager, self.actor])
        self.issued = self.credentials.issue(self.connection['name'], uuid4().hex)
        self.machine = self.credentials.authenticate(self.issued['token']); self.report(); self.choose()
        frappe.set_user(self.actor)
        pages = [{'fields': {**BASE['FIELDS'], 'client_id': 'FORGED'}}]
        self.history = self.storage.create_recognized(self.workspace, pages, uuid4().hex,
            client=client, source='manual', confidence='high', ai_raw=deepcopy(pages))
        prepared = self.prepare()
        self.assertEqual(json.loads(self.row(prepared).payload_json)['supplier']['code'], 'NATIVE-SUPPLIER')

    def test_two_process_confirm_is_one_revision_and_one_receipt(self):
        prepared = self.prepare(); frappe.db.commit()
        workers = [subprocess.Popen([sys.executable, __file__, '--confirm-worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in range(2)]
        try:
            for worker in workers:
                worker.stdin.write(json.dumps({'actor': self.actor, 'prepared': prepared})+'\n'); worker.stdin.flush()
            for worker in workers: self.assertEqual(worker.stdout.readline().strip(), 'READY')
            for worker in workers: worker.stdin.write('go\n'); worker.stdin.flush()
            values = []
            for worker in workers:
                output, error = worker.communicate(timeout=30)
                self.assertEqual(worker.returncode, 0, error); values.append(json.loads(output))
            self.assertEqual(sum(v['replayed'] for v in values), 1)
            row = frappe.get_doc('Cowork Push Record', prepared['name'], for_update=True)
            self.assertEqual(row.status, 'pending'); self.assertEqual(row.revision, prepared['revision']+1)
            self.assertEqual(frappe.db.count('Cowork Push Attempt', {'push_record': row.name}), 0)
        finally:
            for worker in workers:
                if worker.poll() is None: worker.kill(); worker.communicate()

    def test_real_wsgi_post_confirmation_get_guest_and_internal_denied(self):
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        from frappe.app import application
        prepared = self.prepare(); site = frappe.local.site
        frappe.set_user('Administrator')
        actor = frappe.get_doc('User', self.actor)
        actor.api_key, secret = uuid4().hex, uuid4().hex
        actor.api_secret = secret; actor.save()
        headers = {'Authorization': f'token {actor.api_key}:{secret}'}
        # WSGI loads its own Site configuration; temporarily persist only this lab gate.
        path = Path(frappe.get_site_path('site_config.json')); original = path.read_text()
        config = json.loads(original); config['pearnly_cowork_erp_push_enabled'] = True
        path.write_text(json.dumps(config)); frappe.config.clear_site_config_cache()
        frappe.db.commit(); frappe.destroy()
        client = Client(application, Response)
        url = '/api/method/pearnly_cowork.companion.submission.confirm'
        body = {'name': prepared['name'], 'expected_revision': prepared['revision'], 'request_key': 'http'}
        try:
            self.assertEqual(client.post(url, base_url='http://'+site, json=body).status_code, 403)
            self.assertEqual(client.get(url, base_url='http://'+site, headers=headers, query_string=body).status_code, 403)
            result = client.post(url, base_url='http://'+site, headers=headers, json=body)
            self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
            self.assertEqual(result.json['message']['status'], 'pending')
            for internal in ('pearnly_cowork.push.native.service.confirm', 'pearnly_cowork.connections.preparation.collect', 'pearnly_cowork.reconciliation.bank_evidence.record'):
                self.assertEqual(client.post('/api/method/'+internal, base_url='http://'+site,
                    headers=headers, json={}).status_code, 403)
        finally:
            path.write_text(original); frappe.config.clear_site_config_cache(); connect(site)


if __name__ == '__main__':
    connect()
    try:
        if '--confirm-worker' in sys.argv:
            from pearnly_cowork.companion.submission import confirm
            data = json.loads(sys.stdin.readline())
            frappe.conf.pearnly_cowork_erp_push_enabled = True
            frappe.set_user(data['actor']); frappe.db.count('Cowork Push Record')
            print('READY', flush=True)
            assert sys.stdin.readline().strip() == 'go'
            prepared = data['prepared']
            value = confirm(prepared['name'], prepared['revision'], 'concurrent')
            frappe.db.commit(); print(json.dumps({'replayed': bool(value.get('replayed'))}), flush=True)
            raise SystemExit(0)
        suite = (unittest.TestSuite(PreparationConfirmation(name) for name in sys.argv[1:]) if len(sys.argv)>1
                 else unittest.defaultTestLoader.loadTestsFromTestCase(PreparationConfirmation))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback(); frappe.destroy()
