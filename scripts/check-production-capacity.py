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


def rendered(**knobs):
    """Compose services as production renders them with these runtime.env capacity knobs."""
    return json.loads(subprocess.check_output(['docker', 'compose', '-f',
        str(ROOT / 'infrastructure/production/compose.yml'), '--profile', '*',
        'config', '--format', 'json'], env={**env, **knobs}, text=True))['services']


services = rendered()
M = capacity.MIB
checks = []

# 可调容量默认即原值：runtime.env 里什么都不写时，上线不改任何进程数和内存上限。
DEFAULTS = {'dms-ocr-worker': (2, 1024), 'erp-ocr-worker': (2, 2048), 'cowork-ocr-worker': (2, 2048),
            'cowork-long-worker': (2, 1536), 'dms-web': (2, 1024), 'erp-web': (2, 1024),
            'cowork-web': (2, 768), 'billing-web': (2, 512)}
for name, (count, mib) in DEFAULTS.items():
    assert capacity.processes(services[name])[1] == count, name
    assert int(services[name]['mem_limit']) == mib * M, name
checks.append('Capacity knobs default to the previous process counts and memory limits')

# The dispatcher initializes native Frappe and each Site even without OCR work.
# Production cold-start evidence: 128 MiB caused OOM/137, not a business failure.
for product in ('dms', 'erp', 'cowork'):
    assert int(services[product + '-ocr-dispatch']['mem_limit']) >= 256 * M
checks.append('All OCR dispatchers retain measured cold-start memory headroom')
assert services['dms-sync-worker']['command'] == ['bench', 'worker', '--queue', 'dms_sync']
# 2g 按实测定：整组客户快照要一次性持在内存里再落库。1g 被 OOM 杀过（2026-09-25，
# anon-rss 835MB）；2026-09-28 生产 cgroup memory.peak 738MB，期间完整读完并发布了
# ris 15,155 个客户（含无头浏览器）。原 8g 按"膨胀到 3.5GB"估算，实测从未用到。
# 故意改这个值时必须同时改这一行。
assert int(services['dms-sync-worker']['mem_limit']) == 2048 * M
assert not services['dms-sync-worker'].get('ports')
assert services['dms-sync-worker']['profiles'] == ['dms']
assert services['dms-worker']['command'][3] == 'short,default,long'
assert services['dms-ocr-worker']['command'][3] == 'ocr'
assert not any(name in services for name in ('erp-sync-worker', 'cowork-sync-worker'))
checks.append('Dedicated DMS sync lane never consumes interactive or OCR queues')
# Cowork 对账任务在 long 队列里等识别（最长 25 分钟）：单独两个进程，short / default（含定时任务）不再排在它后面。
assert services['cowork-worker']['command'] == ['bench', 'worker', '--queue', 'short,default']
assert services['cowork-long-worker']['command'] == ['bench', 'worker-pool', '--queue', 'long', '--num-workers', '2']
assert int(services['cowork-long-worker']['mem_limit']) == 1536 * M
assert services['cowork-long-worker']['profiles'] == ['cowork']
assert not services['cowork-long-worker'].get('ports')
assert services['cowork-ocr-worker']['command'][3] == 'ocr'
checks.append('Cowork reconciliation lane never holds scheduled or interactive jobs')


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
# 候选上限（2026-09-28，含 2g 同步道、各识别进程 1g/2g）：公共 4032 + DMS 5952 = 9984、
# 加上 ERP 14400、再加上 Cowork 18816 MiB，连同宿主保留 3209 MiB 共 22025 MiB，
# 装得下 32095 MiB 的现机器（余量约 9.8 GiB）。
test('All three products fit the current host', running('dms')+running('erp'), 'cowork',
     total=HOST_MIB)
test('Idle products still reserve full candidate limits', running('dms'), 'erp', total=9000, reject=True)
test('Current pressure defers even one product', [], 'dms', available=1000, reject=True)
test('Transient site process needs additional headroom', [], 'dms', available=2000, transient=1024, reject=True)
test('Unbounded unrelated container requires accounting', [{'project':'other','service':'other','memory':0}], 'dms', reject=True)
test('Unrelated bounded container is included', [{'project':'other','service':'other','memory':5000*M}], 'dms', reject=True)

# runtime.env 调大：进程数和内存一起改才放行；只加进程不加内存在启动前拒绝。
tuned = rendered(PEARNLY_COWORK_OCR_WORKERS='6', PEARNLY_COWORK_OCR_MEM='4g',
                 PEARNLY_COWORK_WEB_WORKERS='4', PEARNLY_COWORK_WEB_MEM='1536m')
assert capacity.processes(tuned['cowork-ocr-worker']) == ('ocr', 6)
assert capacity.processes(tuned['cowork-web']) == ('web', 4)
assert capacity.processes(tuned['erp-ocr-worker']) == ('ocr', 2)
capacity.calculate(tuned, [], 'cowork', HOST_MIB * M, 20000 * M)
checks.append('Raised process counts with matching memory are admitted')
for knobs in ({'PEARNLY_COWORK_OCR_WORKERS': '6'}, {'PEARNLY_ERP_WEB_WORKERS': '6'},
              {'PEARNLY_COWORK_LONG_WORKERS': '4'}, {'PEARNLY_DMS_OCR_WORKERS': '0'}):
    product = next(iter(knobs)).split('_')[1].lower()
    try:
        capacity.calculate(rendered(**knobs), [], product, HOST_MIB * M, 20000 * M)
    except ValueError:
        pass
    else:
        raise AssertionError(knobs)
checks.append('More processes without more memory are refused before start')
huge = rendered(PEARNLY_DMS_OCR_WORKERS='12', PEARNLY_DMS_OCR_MEM='12g',
                PEARNLY_ERP_OCR_WORKERS='12', PEARNLY_ERP_OCR_MEM='12g')
try:
    capacity.calculate(huge, [{'project': 'pearnly-production', 'service': name,
                               'memory': int(huge[name]['mem_limit'])}
                              for name, row in huge.items() if 'dms' in row.get('profiles', [])],
                       'erp', HOST_MIB * M, 30000 * M)
except ValueError:
    pass
else:
    raise AssertionError('24 GiB of OCR workers must not fit the current host')
checks.append('Raised limits still have to fit the host budget')
print(json.dumps({'passed': len(checks), 'checks': checks}))
