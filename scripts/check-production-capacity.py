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

# The dispatcher initializes native Frappe and each Site even without OCR work.
# Production cold-start evidence: 128 MiB caused OOM/137, not a business failure.
for product in ('dms', 'erp', 'cowork'):
    assert int(services[product + '-ocr-dispatch']['mem_limit']) >= 256 * M
checks.append('All OCR dispatchers retain measured cold-start memory headroom')
assert services['dms-sync-worker']['command'] == ['bench', 'worker', '--queue', 'dms_sync']
# 8g 是实测出来的下限，不是随手填的数：整组客户快照要一次性持在内存里再落库，
# rayong 租户 15,135 个客户的 Python 对象会膨胀到 3.5GB 左右。1g 被 OOM 杀过
# （oom=true / anon-rss 835MB），4g 太贴边。故意改这个值时必须同时改这一行。
assert int(services['dms-sync-worker']['mem_limit']) == 8192 * M
assert not services['dms-sync-worker'].get('ports')
assert services['dms-sync-worker']['profiles'] == ['dms']
assert services['dms-worker']['command'][3] == 'short,default,long'
assert services['dms-ocr-worker']['command'][3] == 'ocr'
assert not any(name in services for name in ('erp-sync-worker', 'cowork-sync-worker'))
checks.append('Dedicated DMS sync lane never consumes interactive or OCR queues')


# 真实的"当前宿主"是 SSD Nodes 迁移后那台：8 vCPU / 32095 MiB
# （docs/work/2026-09-20-ssdnodes-migration.md）。`total` 的默认值 11960 是**迁移前的
# 历史机器**，下面留着当"12 GiB 组合必须被拒绝"的负例，不是现在的机器。
HOST_MIB = 32095


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


test('DMS fits current host', [], 'dms', total=HOST_MIB)
test('DMS plus ERP exceeds the pre-migration 12 GiB host', running('dms'), 'erp', reject=True)
test('DMS dedicated sync lane plus ERP fits the current host', running('dms'), 'erp', total=HOST_MIB)
test('All three exceed the pre-migration 12 GiB host before starting',
     running('dms')+running('erp'), 'cowork', reject=True)
# 含 8g 同步道的候选上限：DMS 15616、加上 ERP 18496、再加上 Cowork 21376 MiB，
# 连同宿主保留 3209 MiB 共 24585 MiB，仍然装得下 32095 MiB 的现机器（余量约 7.5 GiB）。
test('All three products fit the current host', running('dms')+running('erp'), 'cowork',
     total=HOST_MIB)
test('Idle products still reserve full candidate limits', running('dms'), 'erp', total=9000, reject=True)
test('Current pressure defers even one product', [], 'dms', available=1000, reject=True)
test('Transient site process needs additional headroom', [], 'dms', available=2000, transient=1024, reject=True)
test('Unbounded unrelated container requires accounting', [{'project':'other','service':'other','memory':0}], 'dms', reject=True)
test('Unrelated bounded container is included', [{'project':'other','service':'other','memory':5000*M}], 'dms', reject=True)
print(json.dumps({'passed': len(checks), 'checks': checks}))
