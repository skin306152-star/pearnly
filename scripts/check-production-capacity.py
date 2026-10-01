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

# 抢 CPU 时网页 / 数据库先跑（2026-10-01 半量压测：后台与网页抢同一台机器，页面跟着变慢）。只给后台降权重，
# 不限核数；底座服务（数据库、Redis、Keycloak）不改配置，激活产品时不会连带重建。
BACKGROUND = ('-worker', '-scheduler', '-ocr-dispatch')
for name, service in services.items():
    shares = service.get('cpu_shares')
    if name.endswith(BACKGROUND):
        assert shares == 256, (name, shares)
    else:
        assert shares in (None, 1024), (name, shares)
        assert not service.get('cpus') and not service.get('cpu_quota'), name
checks.append('Background services yield CPU to web, realtime and database under contention, without core limits')

# 识别任务不 fork 只开在三个识别容器上（2026-10-01）：改锚点时既不能漏掉，也不能扩散到别的 worker。
NOFORK = 'FRAPPE_BACKGROUND_WORKERS_NOFORK'
for name, service in services.items():
    value = (service.get('environment') or {}).get(NOFORK)
    if name in ('dms-ocr-worker', 'erp-ocr-worker', 'cowork-ocr-worker'):
        assert value == '1', name
        assert service['command'][:2] == ['bench', 'worker-pool'], name
    else:
        assert value is None, name
checks.append('Only the three OCR worker pools run jobs without forking')


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
        assert report['container_budget_mib'] + report['host_reserve_mib'] <= total * capacity.OVERCOMMIT
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
# 识别 5 × 450 > 2048、网页 6 × 256 > 1024、对账 4 × 512 > 1536；12 进程 4g（旧目标值）也拒绝。
for knobs in ({'PEARNLY_COWORK_OCR_WORKERS': '5'},
              {'PEARNLY_COWORK_OCR_WORKERS': '12', 'PEARNLY_COWORK_OCR_MEM': '4g'}, {'PEARNLY_ERP_WEB_WORKERS': '6'},
              {'PEARNLY_COWORK_LONG_WORKERS': '4'}, {'PEARNLY_DMS_OCR_WORKERS': '0'}):
    product = next(iter(knobs)).split('_')[1].lower()
    try:
        capacity.calculate(rendered(**knobs), [], product, HOST_MIB * M, 20000 * M)
    except ValueError:
        pass
    else:
        raise AssertionError(knobs)
checks.append('More processes without more memory are refused before start')
huge = rendered(PEARNLY_DMS_OCR_WORKERS='12', PEARNLY_DMS_OCR_MEM='20g',
                PEARNLY_ERP_OCR_WORKERS='12', PEARNLY_ERP_OCR_MEM='20g')
try:
    capacity.calculate(huge, [{'project': 'pearnly-production', 'service': name,
                               'memory': int(huge[name]['mem_limit'])}
                              for name, row in huge.items() if 'dms' in row.get('profiles', [])],
                       'erp', HOST_MIB * M, 30000 * M)
except ValueError:
    pass
else:
    raise AssertionError('40 GiB of OCR workers must not fit the current host even at 1.3x')
checks.append('Raised limits still have to fit the host budget')

# 2026-10-01 生产现值（用户选「放宽容量检查」；真实大文件 OOM 后 cowork / erp 识别提到 8g）：上限之和超过物理内存、
# 但在 1.3 倍以内，且整机可用内存充足时放行；可用内存跌破 max(8 GiB, 25%) 时拒绝。
# 生产现值（runtime.env，2026-10-01 晚：识别改成少进程多线程、MariaDB 4G）：
REAL = dict(PEARNLY_COWORK_OCR_WORKERS='4', PEARNLY_COWORK_OCR_THREADS='24', PEARNLY_ERP_OCR_WORKERS='4',
            PEARNLY_ERP_OCR_THREADS='16', PEARNLY_DMS_OCR_WORKERS='2', PEARNLY_DMS_OCR_THREADS='8',
            PEARNLY_COWORK_LONG_WORKERS='4', PEARNLY_COWORK_WEB_WORKERS='6', PEARNLY_ERP_WEB_WORKERS='4',
            PEARNLY_ERP_WEB_THREADS='8', PEARNLY_DMS_WEB_WORKERS='4', PEARNLY_COWORK_OCR_MEM='7g',
            PEARNLY_ERP_OCR_MEM='8g', PEARNLY_DMS_OCR_MEM='2g', PEARNLY_COWORK_LONG_MEM='2g', PEARNLY_COWORK_WEB_MEM='1536m',
            PEARNLY_ERP_WEB_MEM='1536m', PEARNLY_DMS_WEB_MEM='1g', PEARNLY_COWORK_OCR_THREAD_MB='30',
            PEARNLY_ERP_OCR_THREAD_MB='80', PEARNLY_DMS_OCR_THREAD_MB='60', PEARNLY_MARIADB_MEM='4g')
target = rendered(**REAL, PEARNLY_MARIADB_MAX_CONNECTIONS='210')
live = [{'project': 'pearnly-production', 'service': name, 'memory': int(row['mem_limit'])}
        for name, row in target.items() if set(row.get('profiles', [])) & {'dms', 'erp'}]
report = capacity.calculate(target, live, 'cowork', HOST_MIB * M, 24000 * M, 1024 * M)
# 生产现值 36736 + 3209 = 39945：超过物理内存 32095，在 1.3 倍（41724）以内。
assert HOST_MIB < report['container_budget_mib'] + report['host_reserve_mib'] <= HOST_MIB * capacity.OVERCOMMIT, report
checks.append('Current production limits overcommit the host within 1.3x')
# 余下约 1.7 GiB：再加一个 1 GiB 的临时容器放行，2 GiB 就拒绝。
capacity.calculate(target, live + [{'project': 'other', 'service': 'x', 'memory': 1024 * M}], 'cowork',
                   HOST_MIB * M, 24000 * M)
try:
    capacity.calculate(target, live + [{'project': 'other', 'service': 'x', 'memory': 2048 * M}], 'cowork',
                       HOST_MIB * M, 24000 * M)
except ValueError:
    pass
else:
    raise AssertionError('Limits above 1.3x of the host must be refused')
checks.append('Limits may overcommit the host up to 1.3x and no further')
try:
    capacity.calculate(target, live, 'cowork', HOST_MIB * M, 8500 * M, 1024 * M)
except ValueError:
    pass
else:
    raise AssertionError('Less than 8 GiB plus the transient step must refuse to start')
assert capacity.available_floor(HOST_MIB * M) == 8192 * M
assert capacity.available_floor(64 * 1024 * M) == 16 * 1024 * M
checks.append('Overcommit is only admitted while the host has max(8 GiB, 25%) available')
assert '--max-connections=151' in services['mariadb']['command']
assert '--max-connections=300' in rendered(PEARNLY_MARIADB_MAX_CONNECTIONS='300')['mariadb']['command']
assert int(services['mariadb']['mem_limit']) == 2048 * M
assert int(rendered(PEARNLY_MARIADB_MEM='3g')['mariadb']['mem_limit']) == 3072 * M
checks.append('MariaDB connections and memory are set from runtime.env, default 151 / 2g')
# 识别进程开多线程（PEARNLY_*_OCR_THREADS，pool.py）：默认 1 不改任何行为；每多一个线程每进程多要 150 MiB。
for name in ('dms-ocr-worker', 'erp-ocr-worker', 'cowork-ocr-worker'):
    assert services[name]['environment']['PEARNLY_OCR_THREADS'] == '1', name
    assert capacity.ocr_threads(services[name]) == 1
threaded = rendered(PEARNLY_COWORK_OCR_WORKERS='6', PEARNLY_COWORK_OCR_MEM='8g', PEARNLY_COWORK_OCR_THREADS='6')
assert capacity.ocr_threads(threaded['cowork-ocr-worker']) == 6
capacity.check_processes(threaded, {'cowork-ocr-worker'})  # 6 × (450 + 5 × 150) = 7200 MiB ≤ 8 GiB
for knobs in ({'PEARNLY_COWORK_OCR_WORKERS': '12', 'PEARNLY_COWORK_OCR_MEM': '8g', 'PEARNLY_COWORK_OCR_THREADS': '6'},
              {'PEARNLY_COWORK_OCR_THREADS': 'many'}, {'PEARNLY_COWORK_OCR_THREADS': '0'}):
    try:
        capacity.check_processes(rendered(**knobs), {'cowork-ocr-worker'})
    except ValueError:
        pass
    else:
        raise AssertionError(knobs)
assert services['cowork-ocr-worker']['environment']['PEARNLY_OCR_PAGE_THREADS'] == '1'
assert rendered(PEARNLY_COWORK_OCR_PAGE_THREADS='4')['cowork-ocr-worker']['environment']['PEARNLY_OCR_PAGE_THREADS'] == '4'
checks.append('OCR threads default to 1 and each extra thread needs 150 MiB per process')

# 识别交给 Cloud Run（ADR-015）：Cowork 4 进程 × 24 线程、每线程 30MB，8g 放得下；同样的线程数按默认 150MB 放不下。
remote = rendered(PEARNLY_COWORK_OCR_WORKERS='4', PEARNLY_COWORK_OCR_THREADS='24', PEARNLY_COWORK_OCR_THREAD_MB='30',
                  PEARNLY_COWORK_OCR_MEM='8g')
capacity.check_processes(remote, ['cowork-ocr-worker'])
try:
    capacity.check_processes(rendered(PEARNLY_COWORK_OCR_WORKERS='4', PEARNLY_COWORK_OCR_THREADS='24',
                                      PEARNLY_COWORK_OCR_MEM='8g'), ['cowork-ocr-worker'])
except ValueError:
    pass
else:
    raise AssertionError('24 local threads at 150MB must not fit 8g')
for bad in ('5', '2000', 'x'):
    try:
        capacity.check_processes(rendered(PEARNLY_COWORK_OCR_THREADS='4', PEARNLY_COWORK_OCR_THREAD_MB=bad),
                                 ['cowork-ocr-worker'])
    except ValueError:
        pass
    else:
        raise AssertionError(bad)
checks.append('Remote-waiting OCR threads are sized by PEARNLY_OCR_THREAD_MB')
# 数据库连接核算（独立复查 2026-10-01）：生产实际的进程 / 线程数（runtime.env）在默认 151 个连接下必须被拒，
# 210 时放行；识别线程等云端回复时放掉连接，按 0.2 算。上限也要过 MariaDB 内存（固定 + 每连接 5M ≤ 4G × 0.7）。
narrow = rendered(**REAL)
running = [{'project': 'pearnly-production', 'service': name, 'memory': int(row['mem_limit'])}
           for name, row in narrow.items() if set(row.get('profiles', [])) & {'dms', 'erp'}]
try:
    capacity.calculate(narrow, running, 'cowork', 64 * 1024 * M, 64 * 1024 * M)
    raise AssertionError('production thread counts must not fit 151 database connections')
except ValueError as error:
    assert 'max_connections' in str(error), error
wide = rendered(**REAL, PEARNLY_MARIADB_MAX_CONNECTIONS='210')
estimate = capacity.connections(wide, set(wide))
assert estimate <= 210 * capacity.CONNECTION_SHARE, estimate
assert capacity.connections(wide, {'billing-web'}) == 16  # 计费 2 × 8
capacity.calculate(wide, running, 'cowork', 64 * 1024 * M, 64 * 1024 * M)
try:
    capacity.calculate(rendered(**REAL, PEARNLY_MARIADB_MAX_CONNECTIONS='400'), running, 'cowork', 64 * 1024 * M, 64 * 1024 * M)
    raise AssertionError('400 connections must not fit a 4G MariaDB')
except ValueError as error:
    assert 'memory limit' in str(error), error
assert capacity.connections(wide, {'cowork-ocr-worker'}) == 4 + round(4 * 23 * capacity.OCR_THREAD_CONNECTION_WEIGHT)
checks.append('Database connections stay within 85% of max_connections and max_connections fits MariaDB memory (production 210)')

print(json.dumps({'passed': len(checks), 'checks': checks}))
