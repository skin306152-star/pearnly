"""Offline shell orchestration tests: no Docker, host changes, or tenant data."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name('activate-product.sh')
IMAGE = 'ghcr.io/skin306152-star/pearnly/frappe@sha256:' + 'a' * 64


class Activation(unittest.TestCase):
    def run_activation(self, failure=''):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = SOURCE.read_text().replace('/opt/pearnly', str(root))
            # Only the copied test script skips root identity; production retains it.
            source = source.replace('"$EUID" == 0', '1 == 1')
            (root / 'state').mkdir()
            script = root / 'activate.sh'
            script.write_text(source)
            mock = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as f:
    f.write(json.dumps({'args': args, 'image': os.environ.get('PEARNLY_IMAGE')})+'\\n')
if os.environ.get('FAIL_AT') and os.environ['FAIL_AT'] in args:
    sys.exit(7)
if args[:2] == ['image', 'inspect']:
    print('candidate-sha')
elif args and args[0] == 'inspect':
    print('sha256:old-running-image')
elif 'ps' in args:
    print('old-web-container')
'''
            for name in ('docker', 'flock'):
                target = root / name
                target.write_text(mock)
                target.chmod(0o755)
            # Capacity is independently tested; replace only this one copied call.
            source = source.replace('python3 ' + str(root) + '/deploy/infrastructure/production/capacity.py', 'true')
            script.write_text(source)
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       TRACE=str(root/'trace'), FAIL_AT=failure)
            result = subprocess.run(['bash', str(script), IMAGE, 'dms'], env=env,
                                    text=True, capture_output=True, timeout=10)
            import json
            trace = [json.loads(line) for line in (root/'trace').read_text().splitlines()]
            return result, trace

    def test_drain_and_old_version_backup_before_migration(self):
        result, trace = self.run_activation()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        find = lambda text: next(i for i, call in enumerate(calls) if text in call)
        self.assertLess(find('set-maintenance-mode on'), find('stop -t 130'))
        self.assertLess(find('stop -t 660'), find('backup --with-files'))
        self.assertLess(find('backup --with-files'), find('bench --site all migrate'))
        self.assertEqual(trace[find('backup --with-files')]['image'], 'sha256:old-running-image')
        self.assertLess(find('enable-scheduler'), find('up -d'))
        self.assertLess(find('up -d'), find('set-maintenance-mode off'))

    def test_migration_failure_keeps_maintenance_and_workers_stopped(self):
        result, trace = self.run_activation('migrate')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('maintenance remains enabled', result.stderr)
        calls = [' '.join(row['args']) for row in trace]
        self.assertFalse(any('set-maintenance-mode off' in call or 'up -d' in call for call in calls))
        self.assertFalse(any('start' in row['args'] for row in trace))

    def test_backup_failure_cannot_start_migration(self):
        result, trace = self.run_activation('backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('migrate' in row['args'] for row in trace))


if __name__ == '__main__':
    unittest.main()
