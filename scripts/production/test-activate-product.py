"""Offline shell orchestration tests: no Docker, host changes, or tenant data."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name('activate-product.sh')
CHECKER = Path(__file__).with_name('check-deploy-image.py')
IMAGE = 'ghcr.io/skin306152-star/pearnly/frappe@sha256:' + 'a' * 64
LEGACY_IMAGE = 'ghcr.io/skin306152-star/pearnly/frappe@sha256:' + 'b' * 64
DMS_IMAGE = 'ghcr.io/skin306152-star/pearnly/frappe@sha256:' + 'c' * 64
COWORK_IMAGE = 'ghcr.io/skin306152-star/pearnly/frappe@sha256:' + 'd' * 64


def activated_services(product):
    """Containers `activate-product.sh` recreates for one product, in order.

    DMS runs an extra queue worker for the manual snapshot synchronization; the
    other products must not get it. Cowork runs its long queue (reconciliation jobs
    that wait on recognition) in a separate worker pool.
    """
    suffixes = ['web', 'worker', 'ocr-worker', 'ocr-dispatch', 'scheduler', 'socketio', 'frontend']
    if product == 'dms':
        suffixes.insert(3, 'sync-worker')
    if product == 'cowork':
        suffixes.insert(3, 'long-worker')
    return [f'{product}-{suffix}' for suffix in suffixes]


class DeployToolingMatch(unittest.TestCase):
    """The activation gate that compares the server's deploy copy with the image's copy."""

    def run_checker(self, mutate=lambda image_copy, deploy_root: None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image_copy, deploy_root = root / 'image', root / 'deploy'
            for relative in ('infrastructure/production', 'scripts/production'):
                (image_copy / relative).mkdir(parents=True)
                (deploy_root / relative).mkdir(parents=True)
                for name, body, mode in (('compose.yml', 'services: {}\n', 0o644),
                                         ('provision-agent.py', '#!/usr/bin/env python3\n', 0o755)):
                    for base in (image_copy, deploy_root):
                        target = base / relative / name
                        target.write_text(body)
                        target.chmod(mode)
            fake = root / 'docker'
            fake.write_text('#!/bin/sh\ncase "$1" in\n'
                            '  cp) mkdir -p "$3" && cp -a "$FAKE_IMAGE_COPY/." "$3/" ;;\n'
                            'esac\nexit 0\n')
            fake.chmod(0o755)
            mutate(image_copy, deploy_root)
            result = subprocess.run(['python3', str(CHECKER), IMAGE, str(deploy_root)],
                                    env=dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                                             FAKE_IMAGE_COPY=str(image_copy)),
                                    text=True, capture_output=True, timeout=10)
            return result

    def test_same_tooling_passes(self):
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_edited_or_extra_file_fails(self):
        def missing(image_copy, deploy_root):
            (deploy_root / 'scripts/production/provision-agent.py').unlink()

        def edited(image_copy, deploy_root):
            (deploy_root / 'infrastructure/production/compose.yml').write_text('services: {changed: 1}\n')

        def lost_executable_bit(image_copy, deploy_root):
            (deploy_root / 'scripts/production/provision-agent.py').chmod(0o644)

        def extra(image_copy, deploy_root):
            (deploy_root / 'scripts/production/hand-edited.sh').write_text('echo hi\n')

        for name, mutate in (('missing', missing), ('edited', edited),
                             ('lost executable bit', lost_executable_bit), ('extra', extra)):
            with self.subTest(case=name):
                result = self.run_checker(mutate)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('not the copy that ships with this image', result.stderr)


class Activation(unittest.TestCase):
    def run_activation(self, failure='', source_file=SOURCE, product='dms', records=None, themes=('pearnly', 'pearnly-ai'),
                       themes_record=None, containers=None, sites=('a.example.test', 'b.example.test'), unfinished=(),
                       fail_site='', lose_site='', uncountable=''):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = source_file.read_text().replace('/opt/pearnly', str(root))
            # Only the copied test script skips root identity; production retains it.
            source = source.replace('"$EUID" == 0', '1 == 1')
            (root / 'state').mkdir()
            (root / 'deploy').mkdir()
            # 主机那份 doctype_guard.py 做前后比较（真脚本）；容器里数 DocType 由下面的假 docker 回答
            (root / 'deploy/scripts/production').mkdir(parents=True)
            for name in ('doctype_guard.py', 'doctype-removals.txt'):
                (root / 'deploy/scripts/production' / name).write_text((SOURCE.parent / name).read_text())
            records = records if records is not None else {'active-image.json': [LEGACY_IMAGE]}
            for name, value in records.items():
                (root / 'deploy' / name).write_text(json.dumps(value) + '\n')
            self.records_before = {name: (root / 'deploy' / name).read_bytes() for name in records}
            for theme in themes:
                target = root / 'deploy/infrastructure/keycloak/themes' / theme / 'login/theme.properties'
                target.parent.mkdir(parents=True)
                target.write_text('parent=keycloak\n')
            if themes_record is not None:
                (root / 'state/keycloak-themes.sha256').write_text(themes_record + '\n')
            (root / 'secrets').mkdir()
            (root / 'secrets/member-identity.json').write_text('{}')
            script = root / 'activate.sh'
            script.write_text(source)
            mock = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as f:
    f.write(json.dumps({'args': args, 'image': os.environ.get('PEARNLY_IMAGE'),
                        'program': os.path.basename(sys.argv[0])})+'\\n')
joined = ' '.join(args)
# 迁移前后数 DocType（doctype_guard.py snapshot <站点>）：FAKE_LOSE_SITE 那站迁移后少两个；FAKE_UNCOUNTABLE 那站数不了
if 'snapshot' in args and any(a.endswith('doctype_guard.py') for a in args):
    site = args[-1]
    if os.environ.get('FAKE_UNCOUNTABLE') == site:
        sys.exit(1)
    names = ['DocType', 'ERP Product', 'ERP Stock Entry']
    migrated = json.dumps(['bench', '--site', site, 'migrate'])[1:-1] in open(os.environ['TRACE']).read()
    if os.environ.get('FAKE_LOSE_SITE') == site and migrated:
        names = names[:1]
    print('Linking fresh assets to volume...')
    print('DOCTYPES ' + json.dumps(names))
    sys.exit(0)
# 产品的站点清单（逐站迁移先列站点）与「开户没做完」（站点配置里没有 pearnly_features）
if 'site_config.json' in joined and 'for d in' in joined:
    print('Linking fresh assets to volume...')  # 容器启动时的输出不能混进站点清单（10-28 续做脚本踩过）
    print(os.environ.get('FAKE_SITES', ''))
    sys.exit(0)
if 'pearnly_features' in joined:
    unfinished = [s for s in os.environ.get('FAKE_UNFINISHED', '').split() if s]
    sys.exit(0 if any(s + '/site_config.json' in joined for s in unfinished) else 1)
if os.environ.get('FAIL_SITE') and 'migrate' in args and os.environ['FAIL_SITE'] in args:
    sys.exit(7)
if os.environ.get('FAIL_AT') and os.environ['FAIL_AT'] in args:
    sys.exit(7)
# 宿主上现有的维护类容器：{名字: {"labels": "role/group", "top": "进程列表"}}；不在里面的名字按「没有这个容器」处理
containers = json.loads(os.environ.get('FAKE_CONTAINERS') or '{}')
if args[:2] == ['image', 'inspect']:
    print('candidate-sha')
elif args and args[0] == 'inspect' and 'pearnly.role' in ' '.join(args):
    if args[-1] not in containers:
        sys.exit(1)
    print(containers[args[-1]]['labels'])
elif args and args[0] == 'inspect':
    print('sha256:old-running-image')
elif args and args[0] == 'top':
    if args[1] not in containers:
        sys.exit(1)
    print(containers[args[1]].get('top', 'PID COMMAND\\n4242 /bin/sh -c sleep infinity\\n4251 sleep infinity'))
elif args and args[0] == 'rm' and args[-1] not in containers:
    sys.exit(1)
elif 'ps' in args:
    print('old-web-container')
'''
            for name in ('docker', 'flock', 'mark'):
                target = root / name
                target.write_text(mock)
                target.chmod(0o755)
            if failure == 'publish-record':
                target = root / 'mv'
                target.write_text('#!/bin/sh\nexit 7\n')
                target.chmod(0o755)
            # Capacity is independently tested; replace only this one copied call (traced as `capacity`, to check its order).
            source = source.replace('python3 ' + str(root) + '/deploy/infrastructure/production/capacity.py', 'mark capacity')
            # 宿主 swap 由 ensure-swap.sh 幂等建（要 root、动宿主），测试里不跑，只记下它在哪一步
            source = source.replace('bash ' + str(root) + '/deploy/scripts/production/ensure-swap.sh', 'mark ensure-swap')
            # The deploy-vs-image comparison is tested where it runs (server side); stub it here.
            source = source.replace('python3 ' + str(root) + '/deploy/scripts/production/check-deploy-image.py', 'true')
            source = source.replace('python3 "$root/scripts/production/check-deploy-image.py"', 'true')
            script.write_text(source)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       TRACE=str(root/'trace'), FAIL_AT=failure, FAKE_CONTAINERS=json.dumps(containers or {}),
                       FAKE_SITES='\n'.join(sites), FAKE_UNFINISHED=' '.join(unfinished), FAIL_SITE=fail_site,
                       FAKE_LOSE_SITE=lose_site, FAKE_UNCOUNTABLE=uncountable)
            result = subprocess.run(['bash', str(script), IMAGE, product], env=env,
                                    text=True, capture_output=True, timeout=10)
            trace = [json.loads(line) for line in (root/'trace').read_text().splitlines()]
            listed = root / f'state/unfinished-sites-{product}.txt'
            self.unfinished_listed = listed.read_text().split() if listed.exists() else None
            self.records_after = {path.name: path.read_bytes() for path in (root / 'deploy').glob('active-image*.json')}
            self.record_temporaries = list((root / 'deploy').glob('.active-image-*'))
            record = root / 'state/keycloak-themes.sha256'
            self.themes_record = record.read_text().strip() if record.exists() else None
            return result, trace

    def test_drain_and_old_version_backup_before_migration(self):
        result, trace = self.run_activation()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        find = lambda text: next(i for i, call in enumerate(calls) if text in call)
        self.assertLess(find('set-maintenance-mode on'), find('stop -t 130'))
        self.assertLess(find('stop -t 660'), find('backup --with-files'))
        self.assertLess(find('backup --with-files'), find('bench --site a.example.test migrate'))
        self.assertLess(find('bench --site a.example.test migrate'), find('bench --site b.example.test migrate'))
        self.assertEqual(trace[find('backup --with-files')]['image'], 'sha256:old-running-image')
        self.assertLess(find('enable-scheduler'), find('up -d'))
        self.assertLess(find('up -d'), find('set-maintenance-mode off'))

    @staticmethod
    def maintenance(group, role='maintenance', top=None):
        """ensure_maintenance 建的那种容器：名字 pearnly-maintenance-<组>，标签 pearnly.role / pearnly.group。"""
        container = {'labels': f'{role}/{group}'}
        if top is not None:
            container['top'] = top
        return {f'pearnly-maintenance-{group}': container}

    def order(self, calls):
        """收维护容器 → ensure-swap → 容量检查 → 停机前的维护模式：各自第一次出现的位置。"""
        find = lambda match: next((i for i, call in enumerate(calls) if match(call)), None)
        return (find(lambda call: call[:2] == ['rm', '-f']),
                find(lambda call: call[:1] == ['ensure-swap']),
                find(lambda call: call[:1] == ['capacity']),
                find(lambda call: 'set-maintenance-mode' in call))

    def test_leftover_maintenance_container_is_removed_before_swap_and_capacity(self):
        # 开户代理的维护容器跑的是旧镜像、占容量额度：激活时先收掉（持着开户锁），再建 swap、再做容量等待
        for product in ('dms', 'erp', 'cowork'):
            with self.subTest(product=product):
                result, trace = self.run_activation(product=product, containers=self.maintenance(product))
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [row['args'] for row in trace]
                self.assertIn(['rm', '-f', f'pearnly-maintenance-{product}'], calls)
                removal, swap, capacity, maintenance_mode = self.order(calls)
                self.assertLess(removal, swap)
                self.assertLess(swap, capacity)
                self.assertLess(capacity, maintenance_mode)
                self.assertEqual(calls[capacity], ['capacity', '--product', product, '--wait-seconds', '600'])
                # 收之前已持有开户代理同一把 provision.lock（此刻没有开户工单在跑）
                self.assertLess(next(i for i, row in enumerate(trace) if row['program'] == 'flock'), removal)
                self.assertEqual([call for call in calls if call[:2] == ['rm', '-f']],
                                 [['rm', '-f', f'pearnly-maintenance-{product}']])

    def test_other_groups_maintenance_containers_are_left_alone(self):
        containers = {**self.maintenance('erp'), **self.maintenance('cowork')}
        result, trace = self.run_activation(product='dms', containers=containers)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [row['args'] for row in trace]
        self.assertFalse([call for call in calls if call[:1] == ['rm']])
        self.assertFalse([call for call in calls if any(name in call for name in containers)])

    def test_container_in_use_or_not_a_maintenance_container_is_kept(self):
        busy = 'PID COMMAND\n4242 sleep infinity\n5120 /home/frappe/frappe-bench/env/bin/python /run/site-step.py'
        cases = (('running a provisioning step', self.maintenance('dms', top=busy), 'still running a provisioning step'),
                 ('labelled for another group', {'pearnly-maintenance-dms': {'labels': 'maintenance/erp'}},
                  'not a provisioning maintenance container'),
                 ('without the maintenance labels', {'pearnly-maintenance-dms': {'labels': '/'}},
                  'not a provisioning maintenance container'))
        for name, containers, message in cases:
            with self.subTest(case=name):
                result, trace = self.run_activation(product='dms', containers=containers)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(message, result.stderr)
                calls = [row['args'] for row in trace]
                self.assertFalse([call for call in calls if call[:1] == ['rm']])
                _, swap, capacity, maintenance_mode = self.order(calls)
                self.assertLess(swap, capacity)
                self.assertLess(capacity, maintenance_mode)

    def test_no_leftover_maintenance_container_activates_as_before(self):
        result, trace = self.run_activation(product='dms')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [row['args'] for row in trace]
        self.assertFalse([call for call in calls if call[:1] in (['rm'], ['top'])])
        _, swap, capacity, maintenance_mode = self.order(calls)
        self.assertLess(swap, capacity)
        self.assertLess(capacity, maintenance_mode)
        # 收不掉（docker rm 失败）也不能挡住激活：只告警，容量检查照做
        result, trace = self.run_activation('rm', product='dms', containers=self.maintenance('dms'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('could not be removed', result.stderr)
        self.assertIsNotNone(self.order([row['args'] for row in trace])[2])

    def test_warmup_runs_after_reopening_and_cannot_fail_activation(self):
        result, trace = self.run_activation(product='cowork')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        find = lambda text: next(i for i, call in enumerate(calls) if text in call)
        self.assertLess(find('set-maintenance-mode off'), find('pearnly_core.desk.warmup.run'))
        self.assertIn('run --rm --no-deps cowork-web bench --site all execute', calls[find('warmup.run')])
        result, _ = self.run_activation('pearnly_core.desk.warmup.run', product='cowork')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Desk warm-up failed', result.stderr)
        self.assertEqual(json.loads(self.records_after['active-image-cowork.json']), [IMAGE])

    def test_cowork_backfills_result_views_detached_after_reopening(self):
        result, trace = self.run_activation(product='cowork')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        backfill = next(i for i, call in enumerate(calls) if 'view_store.backfill' in call)
        self.assertLess(next(i for i, call in enumerate(calls) if 'set-maintenance-mode off' in call), backfill)
        self.assertIn('run -d --rm --no-deps cowork-worker', calls[backfill])
        result, trace = self.run_activation(product='erp')
        self.assertFalse(any('view_store.backfill' in ' '.join(row['args']) for row in trace))
        result, _ = self.run_activation('pearnly_cowork.recon_center.view_store.backfill', product='cowork')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stale views refresh in the background', result.stderr)

    def test_migration_failure_keeps_maintenance_and_workers_stopped(self):
        result, trace = self.run_activation('migrate')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('inspect maintenance status, migration and backups', result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        self.assertFalse(any('set-maintenance-mode off' in call or 'up -d' in call for call in calls))
        self.assertFalse(any('start' in row['args'] for row in trace))

    def test_doctypes_are_counted_before_and_after_each_site_migration(self):
        result, trace = self.run_activation()
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = []
        for row in trace:
            args = row['args']
            if 'snapshot' in args:
                steps.append('count ' + args[-1])
            elif 'migrate' in args and '--site' in args:
                steps.append('migrate ' + args[args.index('--site') + 1])
        self.assertEqual(steps, ['count a.example.test', 'migrate a.example.test', 'count a.example.test',
                                 'count b.example.test', 'migrate b.example.test', 'count b.example.test'])
        self.assertIn('DocTypes kept', result.stdout)

    def test_lost_doctypes_stop_the_activation_with_maintenance_kept(self):
        result, trace = self.run_activation(lose_site='a.example.test')
        self.assertNotEqual(result.returncode, 0)
        calls = [' '.join(row['args']) for row in trace]
        self.assertIn('2 DocTypes disappeared during migration', result.stderr)
        self.assertIn('ERP Product, ERP Stock Entry', result.stderr)
        self.assertIn('a.example.test: DocTypes disappeared', result.stderr)
        self.assertFalse(any('b.example.test migrate' in call for call in calls))
        self.assertFalse(any('set-maintenance-mode off' in call for call in calls))
        self.assertFalse(any('up -d' in call for call in calls))

    def test_uncountable_site_stops_unless_its_provisioning_is_unfinished(self):
        result, trace = self.run_activation(uncountable='a.example.test')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('could not count DocTypes before migrating', result.stderr)
        self.assertFalse(any('migrate' in row['args'] for row in trace))
        result, trace = self.run_activation(uncountable='a.example.test', unfinished=('a.example.test',))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        self.assertTrue(any('bench --site b.example.test migrate' in call for call in calls))

    def test_unfinished_site_migration_failure_does_not_stop_other_sites(self):
        """10-28 发布：开户卡在半路的新租户（站点配置里还没有 pearnly_features）迁移失败，不能拖着已开户的站点停在维护里。"""
        sites = ('a.example.test', 'half.example.test', 'c.example.test')
        result, trace = self.run_activation(product='erp', sites=sites, unfinished=('half.example.test',), fail_site='half.example.test')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        for site in sites:
            self.assertTrue(any(f'bench --site {site} migrate' in call for call in calls), site)
        self.assertTrue(any('up -d' in call for call in calls))
        self.assertTrue(any('set-maintenance-mode off' in call for call in calls))
        self.assertIn('half.example.test: provisioning is unfinished', result.stderr)
        self.assertEqual(self.unfinished_listed, ['half.example.test'])

    def test_provisioned_site_migration_failure_still_stops_the_activation(self):
        sites = ('a.example.test', 'b.example.test', 'c.example.test')
        result, trace = self.run_activation(product='erp', sites=sites, fail_site='b.example.test')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('b.example.test: migration failed', result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        self.assertFalse(any('c.example.test migrate' in call for call in calls))
        self.assertFalse(any('set-maintenance-mode off' in call or 'up -d' in call for call in calls))

    def test_no_sites_listed_stops_before_reopening(self):
        result, trace = self.run_activation(sites=())
        self.assertNotEqual(result.returncode, 0)
        calls = [' '.join(row['args']) for row in trace]
        self.assertFalse(any('set-maintenance-mode off' in call for call in calls))

    def test_backup_failure_cannot_start_migration(self):
        result, trace = self.run_activation('backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('migrate' in row['args'] for row in trace))

    def test_dms_sync_is_drained_configured_and_recreated(self):
        result, trace = self.run_activation()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [row['args'] for row in trace]
        drain = next(i for i, args in enumerate(calls) if '660' in args and 'stop' in args)
        configure = next(i for i, args in enumerate(calls) if any('configure-dms-runtime.py' in arg for arg in args))
        migrate = next(i for i, args in enumerate(calls) if 'migrate' in args)
        start = next(i for i, args in enumerate(calls) if 'up' in args)
        self.assertIn('dms-sync-worker', calls[drain])
        self.assertIn('dms-sync-worker', calls[start])
        self.assertLess(drain, configure)
        self.assertLess(configure, migrate)
        self.assertLess(migrate, start)

    def test_cowork_reconciliation_lane_is_drained_and_recreated(self):
        result, trace = self.run_activation(product='cowork')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [row['args'] for row in trace]
        drain = next(i for i, args in enumerate(calls) if '660' in args and 'stop' in args)
        start = next(i for i, args in enumerate(calls) if 'up' in args)
        self.assertIn('cowork-long-worker', calls[drain])
        self.assertIn('cowork-long-worker', calls[start])

    def test_other_products_do_not_get_dms_queue_or_tick_changes(self):
        for product in ('erp', 'cowork'):
            with self.subTest(product=product):
                result, trace = self.run_activation(product=product)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any('dms-sync-worker' in row['args'] or
                    any('configure-dms-runtime.py' in arg for arg in row['args']) for row in trace))

    def test_erp_tick_is_configured_before_migration_and_only_for_erp(self):
        def configured(trace):
            return [i for i, row in enumerate(trace) if any('configure-erp-runtime.py' in arg for arg in row['args'])]
        result, trace = self.run_activation(product='erp')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [row['args'] for row in trace]
        migrate = next(i for i, args in enumerate(calls) if 'migrate' in args)
        self.assertEqual(len(configured(trace)), 1)
        self.assertLess(configured(trace)[0], migrate)
        for product in ('dms', 'cowork'):
            with self.subTest(product=product):
                result, trace = self.run_activation(product=product)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(configured(trace), [])

    def test_activation_pins_only_selected_product_and_preserves_other_versions(self):
        for product in ('erp', 'dms', 'cowork'):
            with self.subTest(product=product):
                records = {'active-image.json': [LEGACY_IMAGE],
                           'active-image-erp.json': [LEGACY_IMAGE],
                           'active-image-dms.json': [DMS_IMAGE],
                           'active-image-cowork.json': [COWORK_IMAGE]}
                result, trace = self.run_activation(product=product, records=records)
                self.assertEqual(result.returncode, 0, result.stderr)
                selected = f'active-image-{product}.json'
                self.assertEqual(json.loads(self.records_after[selected]), [IMAGE])
                for name in records:
                    if name != selected:
                        self.assertEqual(self.records_after[name], self.records_before[name], name)
                self.assertEqual(self.record_temporaries, [])
                recreate = [row['args'] for row in trace if '--force-recreate' in row['args']]
                self.assertEqual(len(recreate), 1)
                self.assertEqual(recreate[0][recreate[0].index('up'):],
                                 ['up', '-d', '--no-deps', '--force-recreate',
                                  *activated_services(product)])

    def test_first_product_activation_keeps_legacy_fallback(self):
        result, _ = self.run_activation(product='erp')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.records_after['active-image.json'], self.records_before['active-image.json'])
        self.assertEqual(json.loads(self.records_after['active-image-erp.json']), [IMAGE])
        self.assertEqual(set(self.records_after), {'active-image.json', 'active-image-erp.json'})

    def test_failed_activation_does_not_publish_new_product_pin(self):
        for failure in ('backup', 'migrate', 'enable-scheduler', '--force-recreate', 'off', 'publish-record'):
            for already_pinned in (False, True):
                with self.subTest(failure=failure, already_pinned=already_pinned):
                    records = {'active-image.json': [LEGACY_IMAGE], 'active-image-dms.json': [DMS_IMAGE]}
                    if already_pinned:
                        records['active-image-erp.json'] = [LEGACY_IMAGE]
                    result, _ = self.run_activation(failure, product='erp', records=records)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.records_after, self.records_before)
                    self.assertEqual(self.record_temporaries, [])



class FoundationActivation(unittest.TestCase):
    run_activation = Activation.run_activation

    def test_private_rpc_drains_and_backs_up_before_bootstrap_and_migration(self):
        result, trace = self.run_activation(source_file=SOURCE.with_name('activate-foundation.sh'))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        find = lambda text: next(i for i, call in enumerate(calls) if text in call)
        self.assertLess(find('stop -t 130 billing-web'), find('backup --with-files'))
        self.assertEqual(trace[find('backup --with-files')]['image'], 'sha256:old-running-image')
        self.assertLess(find('backup --with-files'), find('/run/bootstrap-billing.py'))
        self.assertLess(find('bench --site billing.internal migrate'), find('up -d --no-deps billing-web'))
        # billing 没有后台进程：迁移不排网站搜索索引任务（否则永远滞留在共用队列）
        self.assertIn('--skip-search-index', calls[find('bench --site billing.internal migrate')])

    def test_missing_login_theme_stops_before_keycloak_is_touched(self):
        """主题目录空着 Keycloak 会悄悄用默认登录页（2026-10-11 pearnly-ai）：缺了就不激活。"""
        result, trace = self.run_activation(source_file=SOURCE.with_name('activate-foundation.sh'), themes=('pearnly',))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('login theme pearnly-ai is missing', result.stderr)
        self.assertFalse(any('keycloak' in row['args'] and 'up' in row['args'] for row in trace))

    def test_keycloak_restarts_only_when_the_themes_changed(self):
        script = SOURCE.with_name('activate-foundation.sh')
        result, trace = self.run_activation(source_file=script)  # 第一次：只记下
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('restart' in row['args'] for row in trace))
        digest = self.themes_record
        self.assertRegex(digest, r'^[0-9a-f]{64}$')
        result, trace = self.run_activation(source_file=script, themes_record=digest)  # 没变：不重启
        self.assertFalse(any('restart' in row['args'] for row in trace))
        result, trace = self.run_activation(source_file=script, themes_record='0' * 64)  # 变了：清压缩缓存再重启
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        find = lambda text: next(i for i, call in enumerate(calls) if text in call)
        self.assertLess(find('rm -rf /opt/keycloak/data/tmp/kc-gzip-cache'), find('restart keycloak'))
        self.assertEqual(self.themes_record, digest)

    def test_failed_backup_or_migration_never_reopens_private_endpoint(self):
        for failure in ('backup', 'migrate'):
            with self.subTest(failure=failure):
                result, trace = self.run_activation(failure, SOURCE.with_name('activate-foundation.sh'))
                self.assertNotEqual(result.returncode, 0)
                calls = [' '.join(row['args']) for row in trace]
                self.assertFalse(any('up -d --no-deps billing-web' in call for call in calls))
                if failure == 'backup':
                    self.assertFalse(any('migrate' in row['args'] for row in trace))


if __name__ == '__main__':
    unittest.main()
