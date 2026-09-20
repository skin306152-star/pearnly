"""Validate actual Compose budgets without starting containers or reading secrets."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capacity', ROOT / 'infrastructure/production/capacity.py')
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)
env = {**os.environ, 'PEARNLY_IMAGE': 'example.invalid/local-check:never-pulled',
       'DB_ROOT_PASSWORD': 'unused', 'KEYCLOAK_DB_PASSWORD': 'unused',
       'KEYCLOAK_ADMIN_PASSWORD': 'unused', 'AUTH_HOST': 'example.invalid'}
config = json.loads(subprocess.check_output(['docker', 'compose', '-f',
    str(ROOT / 'infrastructure/production/compose.yml'), '--profile', '*',
    'config', '--format', 'json'], env=env, text=True))
services = config['services']
M = capacity.MIB
checks = []


def test(label, containers, product, total=11960, available=9000, transient=0, reject=False):
    try:
        report = capacity.calculate(services, containers, product, total*M, available*M, transient*M)
    except ValueError:
        assert reject, label
    else:
        assert not reject, label
        assert report['container_budget_mib'] + report['host_reserve_mib'] <= total
    checks.append(label)


def running(group):
    return [{'project':'pearnly-production', 'service':name, 'memory':int(row['mem_limit'])}
            for name,row in services.items() if group in row.get('profiles',[])]


test('DMS fits current host', [], 'dms')
test('Adding one other product fits hard budgets', running('dms'), 'erp')
test('All three exceed current host before starting', running('dms')+running('erp'), 'cowork', reject=True)
test('All three fit 16 GiB hard budgets', running('dms')+running('erp'), 'cowork', total=16384)
test('Idle products still reserve full candidate limits', running('dms'), 'erp', total=9000, reject=True)
test('Current pressure defers even one product', [], 'dms', available=1000, reject=True)
test('Transient site process needs additional headroom', [], 'dms', available=2000, transient=1024, reject=True)
test('Unbounded unrelated container requires accounting', [{'project':'other','service':'other','memory':0}], 'dms', reject=True)
test('Unrelated bounded container is included', [{'project':'other','service':'other','memory':5000*M}], 'dms', reject=True)
print(json.dumps({'passed': len(checks), 'checks': checks}))
