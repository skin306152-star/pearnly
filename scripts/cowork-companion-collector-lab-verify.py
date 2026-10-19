"""Synthetic collector/lease checks in the dedicated internal-network lab.

No ERP, Companion device, provider, frontend or production state is contacted.
The caller validates credentials; only local native rows and fixtures change.

Several computers per connection (ADR-021, evidence v2): reports go through the real heartbeat entry
(`heartbeat._receive`), never rewrite the connection's configuration or revision, keep masters in the
account set's Account Facts and only this computer's reach; the directory is the union of every
computer's reach. Directory reads are per computer (`refresh`: whole-directory child leases, one lease
per computer, a request for an account set nobody reaches fails).
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

FIXTURE = runpy.run_path(str(Path(__file__).with_name('cowork-companion-credentials-lab-verify.py')))
BASE = FIXTURE['NativeCredentials']
connect = FIXTURE['connect']
BOOK, ROOT = r'C:\BOOK', r'D:\APP'
OTHER = r'E:\OTHER'


class NativeCollector(unittest.TestCase):
    setUpClass = classmethod(BASE.setUpClass.__func__)
    tearDownClass = classmethod(BASE.tearDownClass.__func__)
    tearDown = BASE.tearDown
    issue = BASE.issue

    def setUp(self):
        BASE.setUp(self)
        from pearnly_cowork.connections import collector, model, refresh, reports, state
        self.collector, self.model, self.refresh, self.reports, self.state = collector, model, refresh, reports, state
        self.value = self.issue()
        self.context = self.service.authenticate(self.value['token'])
        self.now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.report(self.body())

    def body(self, **changes):
        # 1.7.0 起的 CC 桌面版：报版本（能读所选以外的账套，`refresh.reads_any_account_set`）
        value = {'account_set': BOOK, 'account_dir': BOOK, 'express_root': ROOT, 'companion_version': '1.7.0',
                 'account_sets': [{'code': 'BOOK', 'path': BOOK, 'root': ROOT, 'name': 'Synthetic', 'writable': True}],
                 'accounts': [{'code': '100', 'name': 'Cash'}],
                 'catalog': {'products': [{'code': 'P', 'name': 'Product'}], 'customers': [{'code': 'C', 'name': 'Customer'}]}}
        value.update(changes)
        return value

    def doc(self):
        return self.state.read(self.state.CONNECTION, self.connection['name'])

    def report(self, body, now=None):
        # 心跳的真实入口（`heartbeat._receive`：这台电脑的可达行、账套资料、电脑行，ADR-021），只把「此刻」换成用例给的时间
        from pearnly_cowork.companion import heartbeat
        now = now or self.now
        clock = type('Clock', (), {'now': staticmethod(lambda tz=None: now.replace(tzinfo=timezone.utc))})
        ingest = self.collector.ingest
        with patch.object(heartbeat, 'datetime', clock), \
                patch.object(self.collector, 'ingest', lambda *args, **kwargs: ingest(*args, **{**kwargs, 'now': now})):
            return heartbeat._receive(self.context, body)

    def request(self, scope=None):
        return self.connections.request_refresh(self.connection['name'], uuid4().hex, scope)

    def row(self, request):
        return self.state.read(self.state.REFRESH, request['request_id'])

    def head(self, scope=None):
        return self.state.heads(self.doc()).get(self.state.scope(scope, 'express')[2], {})

    def snapshot(self, scope=None):
        return self.state.read(self.state.SNAPSHOT, self.head(scope)['snapshot_id'])

    def lease(self, scope=None):
        request = self.request(scope)
        now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1)
        result = self.report(self.body(), now=now)
        self.now = now
        return request, result['master_refresh']

    def facts(self, account=BOOK):
        from pearnly_cowork.connections import pool
        return pool.facts(self.connection['name'], account) or {}

    def both(self):
        # 这台电脑够得到两份账套（读别的账套的请求才有电脑可交）
        body = self.body()
        body['account_sets'].append({'code': 'OTHER', 'path': OTHER, 'root': ROOT, 'name': 'Other', 'writable': True})
        return body

    def child(self, lease):
        return frappe.get_doc(self.state.REFRESH, frappe.db.get_value(self.state.REFRESH, {'lease_handle': lease['request_id']}))

    def completion(self, lease, **changes):
        return self.body(master_refresh_request_id=lease['request_id'], master_refresh_scope=lease['scope_kind'],
                         master_refresh_account_set=lease['account_set_key'], **changes)

    def test_report_stores_raw_and_canonical_without_preparation_and_reuses_equal_snapshot(self):
        before = self.snapshot(BOOK).name; revision = self.doc().record_revision
        self.report(self.body(), now=self.now + timedelta(seconds=10))
        self.assertEqual(self.snapshot(BOOK).name, before)
        self.assertEqual(self.doc().record_revision, revision)
        self.assertIsNone(self.model.parse(self.snapshot(BOOK).preparation))
        # 证据 v2 不绑密钥：上报不再改连接的上报绑定；主档进这份账套的资料，连接这一行不存
        self.assertFalse(self.doc().report_credential)
        self.assertEqual(self.facts()['reported_accounts'][0]['code'], '100')
        self.assertNotIn('reported_accounts', self.model.parse(self.doc().report_json, {}))
        self.assertTrue(self.connections.targets(self.connection['name'])['collector_available'])

    def test_omitted_catalog_retains_but_empty_catalog_clears_projection_and_raw_caches_separately(self):
        before = self.snapshot(BOOK).name
        self.report({'device': 'Synthetic'})
        self.assertEqual(self.snapshot(BOOK).name, before)
        self.report({'account_set': BOOK, 'catalog': {}})
        raw = self.facts()  # 账套资料：没报科目表留着上一次的，报了空商品目录就清空
        self.assertEqual(raw['reported_accounts'][0]['code'], '100')
        self.assertEqual(raw['reported_products'], [])
        masters = self.model.parse(self.snapshot(BOOK).body)['masters']
        self.assertEqual(masters['accounts'], [])
        self.assertEqual(masters['products'], [])

    def test_empty_account_sets_clears_only_this_computers_reach_without_marking_the_directory(self):
        # ADR-021：空名单只清这台电脑自己的可达行（池目录是各台的并集），不把整个目录标错（v1 留旧目录并记 ERR_ACCOUNT_SET_EMPTY）
        from pearnly_cowork.connections import pool
        device = self.service.device_id(self.value['credential_id'])
        self.assertTrue(pool.local_paths(self.connection['name'], device))
        self.report({'account_sets': []})
        self.assertEqual(pool.local_paths(self.connection['name'], device), {})
        self.assertIsNone(self.head().get('error_code'))
        self.assertEqual(self.connections.targets(self.connection['name'])['account_sets'], [])
        self.assertNotIn('reported_account_sets', self.model.parse(self.doc().report_json, {}))

    def test_offline_marks_last_seen_without_disabling_or_leasing_or_erasing_catalog(self):
        request = self.request(); before = self.snapshot().name
        result = self.report({'offline': True, 'account_sets': []})
        self.assertFalse(result['connected']); self.assertNotIn('master_refresh', result)
        self.assertEqual(self.row(request).status, 'requested')
        self.assertTrue(self.doc().enabled)
        self.assertEqual(self.snapshot().name, before)
        # 报到时间记在密钥行（心跳入口 `credentials.record_seen`），上报记录里不再存；下线只把准备凭据标成离线。
        report = self.model.parse(self.doc().report_json)
        self.assertNotIn('agent_last_seen_at', report)
        self.assertIsNot((report.get('_preparation_binding') or {}).get('online'), True)

    def test_human_request_machine_lease_completion_and_no_duplicate_replay(self):
        request, lease = self.lease()
        self.assertNotEqual(lease['request_id'], request['request_id'])
        self.assertEqual(self.row(request).status, 'leased')
        self.assertEqual(self.head()['status'], 'pending')
        self.report(self.completion(lease))
        self.assertEqual(self.row(request).status, 'succeeded')
        self.assertIsNone(self.row(request).lease_handle)
        self.assertEqual(self.head()['status'], 'fresh')
        with self.assertRaises(frappe.ValidationError): self.report(self.completion(lease))

    def test_ordinary_report_does_not_complete_active_refresh(self):
        request, lease = self.lease(BOOK)
        self.assertNotIn('master_refresh', self.report(self.body()))
        self.assertEqual(self.row(request).status, 'leased')
        self.assertEqual(self.head(BOOK)['status'], 'pending')

    def test_error_completion_can_publish_independent_directory_but_request_fails(self):
        request, lease = self.lease(BOOK)
        changed = self.body(master_refresh_error='ERR_SYNTHETIC')
        changed['account_sets'][0]['name'] = 'Updated directory'
        self.report(self.completion(lease, account_sets=changed['account_sets'], master_refresh_error='ERR_SYNTHETIC'))
        self.assertEqual(self.row(request).status, 'failed')
        self.assertEqual(self.row(request).error_code, 'ERR_SYNTHETIC')
        self.assertEqual(self.head(BOOK)['status'], 'error')
        self.assertEqual(self.connections.refresh_status(self.connection['name'], request['request_id'])['error_code'], 'ERR_SYNTHETIC')
        self.assertEqual(self.connections.targets(self.connection['name'])['account_sets'][0]['label'], 'Updated directory')

    def test_new_refresh_clears_old_error_from_human_directory_status(self):
        # 整目录读：池里唯一一台电脑交回的是错误 → 这一轮失败，目录状态记那台报的原因
        request, lease = self.lease()
        self.report(self.completion(lease, master_refresh_error='ERR_SYNTHETIC'), now=self.now + timedelta(seconds=1))
        self.assertEqual(self.row(request).status, 'failed')
        self.assertEqual(self.connections.targets(self.connection['name'])['error_code'], 'ERR_SYNTHETIC')
        self.request()
        directory = self.connections.targets(self.connection['name'])
        self.assertEqual(directory['status'], 'pending')
        self.assertIsNone(directory['error_code'])

    def test_wrong_scope_or_unknown_handle_rejected_before_any_write(self):
        request, lease = self.lease()
        before = self.doc().as_dict()
        for body in (self.completion({**lease, 'request_id': str(uuid4())}),
                     self.completion({**lease, 'scope_kind': 'account_set', 'account_set_key': BOOK})):
            with self.assertRaises(frappe.ValidationError): self.report(body)
            self.assertEqual(self.doc().as_dict(), before)
        self.assertEqual(self.row(request).status, 'leased')

    def test_reported_mapping_never_rewrites_connection_config_or_invalidates_leases(self):
        # ADR-021 方案 2.3 第 1 条：心跳不写连接配置、不升版本（所选与 6 个科目是这台电脑自己的）。v1 这里升版本、读请求作废
        request, lease = self.lease()
        before, config = self.doc().record_revision, self.doc().config_json
        self.report(self.completion(lease, mapping={'revenue_acc': '400'}))
        self.assertEqual((self.doc().record_revision, self.doc().config_json), (before, config))
        self.assertEqual(self.row(request).status, 'succeeded')
        self.assertEqual(self.snapshot().connection_revision, before)
        self.assertIsNone(self.model.parse(self.snapshot().preparation))

    def test_new_request_supersedes_leased_but_requested_coalesces(self):
        first = self.request(); second = self.request()
        self.assertEqual(first['request_id'], second['request_id'])
        self.now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1)
        old = self.report({})['master_refresh']
        third = self.request()
        self.assertNotEqual(third['request_id'], first['request_id'])
        self.assertEqual(self.row(first).error_code, 'ERR_REFRESH_SUPERSEDED')
        # 整目录读的子租约照常交回（只刷新这台电脑自己的可达行）；每台电脑同时只有一份租约，交回后才领新一轮
        self.assertNotIn('master_refresh', self.report({}))
        child = self.child(old)
        self.assertEqual(child.status, 'leased')
        result = self.report(self.completion(old), now=self.now + timedelta(seconds=1))
        self.assertEqual(frappe.db.get_value(self.state.REFRESH, child.name, 'status'), 'succeeded')
        self.assertNotEqual(result['master_refresh']['request_id'], old['request_id'])
        self.assertEqual(result['master_refresh']['scope_kind'], 'endpoint')

    def test_expiry_marks_failure_and_rerun_uses_new_handle(self):
        request, lease = self.lease()
        self.report({}, now=self.now + timedelta(seconds=901))
        self.assertEqual(self.row(request).error_code, 'ERR_REFRESH_LEASE_EXPIRED')
        request, lease = self.lease()
        row = self.row(request); row.requested_at = self.now + timedelta(seconds=1)
        self.connections.access.write(row)
        next_result = self.report({}, now=self.now + timedelta(seconds=901))
        self.assertNotEqual(next_result['master_refresh']['request_id'], lease['request_id'])
        self.assertEqual(self.row(request).status, 'leased')

    def test_success_with_a_newer_request_requeues_and_fences_old_handle(self):
        request, lease = self.lease()
        row = self.row(request); row.requested_at = self.now + timedelta(seconds=1)
        self.connections.access.write(row)
        result = self.report(self.completion(lease), now=self.now + timedelta(seconds=2))
        self.assertNotEqual(result['master_refresh']['request_id'], lease['request_id'])
        self.assertEqual(self.row(request).status, 'leased')
        with self.assertRaises(frappe.ValidationError): self.report(self.completion(lease), now=self.now + timedelta(seconds=3))

    def test_rotation_cannot_finish_old_lease_and_new_key_joins_with_its_own(self):
        # 旧槽位换钥：新钥是另一台「旧电脑」（`devices.for_old_slot`），旧钥的租约它交不回；它在同一轮整目录读里领自己的子租约，
        # 不去判别的电脑的租约失败（ADR-021：租约属于电脑，只有同一台电脑换了钥才判失败）
        request, lease = self.lease()
        new = self.service.issue(self.connection['name'], uuid4().hex, reset=True, expected_generation=1)
        self.context = self.service.authenticate(new['token'])
        with self.assertRaises(frappe.ValidationError): self.report(self.completion(lease))
        joined = self.report(self.body())['master_refresh']
        self.assertNotEqual(joined['request_id'], lease['request_id'])
        self.assertEqual(joined['scope_kind'], 'endpoint')
        self.assertEqual((self.child(lease).status, self.child(lease).error_code), ('leased', None))
        self.assertEqual(self.row(request).status, 'leased')

    def test_missing_selection_can_lease_other_account_despite_configured_default(self):
        # 1.3.0 起的电脑读它够得到的任一份账套（不只是所选的那份）；池里没有电脑够得到的账套，读请求失败、不一直排队
        self.report(self.both())
        nowhere = self.request(r'F:\NOWHERE')
        request = self.request(OTHER)
        result = self.report({}, now=self.now + timedelta(seconds=1))
        self.assertEqual(result['account_set'], BOOK)
        self.assertEqual(result['master_refresh']['account_set_key'], r'e:\other')
        self.assertEqual(self.row(request).status, 'leased')
        self.assertEqual((self.row(nowhere).status, self.row(nowhere).error_code), ('failed', 'ERR_ACCOUNT_SET_UNAVAILABLE'))

    def test_account_dir_only_filters_current_report_instead_of_configured_default(self):
        self.report(self.both())
        current = self.request(BOOK); other = self.request(OTHER)
        result = self.report({'account_dir': r'E:\OTHER'})
        self.assertEqual(result['account_set'], BOOK)
        self.assertEqual(result['master_refresh']['account_set_key'], r'e:\other')
        self.assertEqual(self.row(current).status, 'requested')
        self.assertEqual(self.row(other).status, 'leased')

    def test_two_database_processes_offer_only_one_lease_from_stale_snapshots(self):
        request = self.request()
        frappe.db.commit()
        workers = [subprocess.Popen([sys.executable, __file__, '--lease-worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for worker in workers:
                worker.stdin.write(json.dumps({'token': self.value['token'], 'body': self.body()}) + '\n')
                worker.stdin.flush()
            for worker in workers:
                self.assertEqual(worker.stdout.readline().strip(), 'READY')
            for worker in workers:
                worker.stdin.write('go\n'); worker.stdin.flush()
            outcomes = []
            for worker in workers:
                output, error = worker.communicate(timeout=30)
                self.assertEqual(worker.returncode, 0, error)
                outcomes.append(json.loads(output))
            self.assertEqual(sum(result['leased'] for result in outcomes), 1)
            # 整目录读：父请求只记这一轮开始，租约在这台电脑的那一份子租约上（同一台电脑同时只有一份）
            row = self.row(request)
            self.assertEqual(row.status, 'leased')
            children = frappe.get_all(self.state.REFRESH, filters={'parent_request': row.name, 'status': 'leased'},
                                      fields=['lease_owner'])
            self.assertEqual([child.lease_owner for child in children], [self.value['credential_id']])
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill(); worker.communicate()

    def test_endpoint_first_selection_match_and_single_outstanding_lease(self):
        self.report(self.both())
        other = self.request(OTHER); selected = self.request(BOOK); endpoint = self.request()
        self.now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1)
        lease = self.report(self.both())['master_refresh']
        self.assertEqual(lease['scope_kind'], 'endpoint')
        result = self.report(self.completion(lease, account_sets=self.both()['account_sets']), now=self.now + timedelta(seconds=1))
        self.assertEqual(result['master_refresh']['account_set_key'], 'c:\\book')
        self.assertEqual(self.row(other).status, 'requested')
        self.assertEqual(self.row(selected).status, 'leased')
        self.assertEqual(self.row(endpoint).status, 'succeeded')


def lease_worker():
    from unittest.mock import patch
    from werkzeug.test import Client
    from werkzeug.wrappers import Response
    from frappe.app import application
    from pearnly_cowork.companion import credentials, http_auth
    data = json.loads(sys.stdin.readline())
    site = frappe.local.site
    original = credentials.authenticate
    first = True
    def authenticate(token):
        nonlocal first
        frappe.conf.pearnly_cowork_erp_push_enabled = True
        frappe.conf.pearnly_features = ['intake', 'master_data', 'push_log']
        if first:
            first = False
            frappe.db.count('Cowork ERP Connection')  # Both HTTP requests establish an old RR snapshot.
            print('READY', flush=True)
            if sys.stdin.readline().strip() != 'go': raise AssertionError('Missing concurrency barrier')
        return original(token)
    frappe.destroy()
    try:
        with patch.object(credentials, 'authenticate', authenticate):
            response = Client(application, Response).post(http_auth.PATH, base_url='http://' + site,
                headers={'Authorization': 'Bearer ' + data['token']}, json=data['body'], buffered=True)
        if response.status_code != 200:
            raise AssertionError('Machine request returned ' + str(response.status_code))
        print(json.dumps({'leased': 'master_refresh' in response.get_json()['message']}), flush=True)
    finally:
        connect(site)


if __name__ == '__main__':
    connect()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--lease-worker':
            lease_worker()
        else:
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeCollector))
            raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:
        frappe.db.rollback(); frappe.destroy()
