"""Read-only host budget check before starting another product group.

Use candidate Compose hard limits, not idle RSS, to reserve memory for existing
products. No tenant contents, environment variables or credentials are printed.

两道关（用户 2026-10-01 定，压测后放宽）：
- 上限之和：所有容器的内存上限加上宿主保留，不超过物理内存的 1.3 倍。上限是每个容器的"封顶"，
  不是常驻用量 —— 压测时 30 GiB 上限的整机实际只用约 7.4 GiB，按 1 倍算会把进程数卡在远低于机器能力的地方。
- 现在的余量：执行时整机 MemAvailable 至少 max(8 GiB, 物理内存 25%)，再加上这一步临时要的内存；
  超配靠这一条兜底 —— 真实用量涨上来时，开户 / 激活在启动前就被拒，而不是启动后被内核杀进程。
- 超配要有 swap 兜着（2026-10-17 加）：上限之和还不能超过物理内存 + swap。10-16 部署时上限合计 38.5 GiB、
  整机 32 GiB、没有 swap，激活间后台任务一起涨内存，整机挤满、生产约 10 分钟无响应（docs/work/2026-10-16-release-372dd6c4.md）。
  swap 由 scripts/production/ensure-swap.sh 建（激活前幂等执行）；没有 swap 的机器只能按 1 倍放行。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

MIB = 1024 * 1024
ROOT = Path(__file__).resolve().parent


def output(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE)


# 每个进程至少要分到的内存：进程数可在 runtime.env 里调，只加进程不加内存会让每个进程分到的变少、
# 被内核杀掉，所以在启动前拒绝。
# 识别 450：不 fork 运行（FRAPPE_BACKGROUND_WORKERS_NOFORK）时每个进程的内存停在它处理过的最大那一单。
#   合成发票约 210MB / 进程，但 2026-10-01 生产真实大文件回归时每进程 330–390MB，12 进程顶满 4g、OOM 杀了 2 个；
#   按实测高水位再留余量取 450（原 512 按 fork 模式定，09-30 一度按合成发票降到 320，偏低）。
# 对账 512：生产压测时 4 进程峰值 303MiB。网页 256：计费后台原 512m / 2 个（最小的一处）。
PROCESS_FLOOR = {'ocr': 450 * MIB, 'long': 512 * MIB, 'web': 256 * MIB}
# 识别进程开多线程（PEARNLY_OCR_THREADS，见 pearnly_core/execution/pool.py）时每多一个线程多要的内存：
# 默认 150MB（一份手机大图 / 多页 PDF 在本机跑时约 100MB 上下，与按内存回收的每线程余量一致）。识别交给 Cloud Run 的
# 产品（ADR-015）线程只带着原件等回复，用 PEARNLY_OCR_THREAD_MB 调小；本机识别由 pool.local_slot 限成每进程 1 份，
# 那一份由进程底线（PROCESS_FLOOR['ocr']）兜住。
OCR_THREAD_FLOOR = 150 * MIB
# 上限之和最多到物理内存的这么多倍（见模块头）。
OVERCOMMIT = 1.3
# 执行时整机至少要空出的内存：8 GiB 与物理内存 25% 取大。
AVAILABLE_FLOOR = 8192 * MIB
AVAILABLE_SHARE = 4


def available_floor(total_bytes):
    return max(AVAILABLE_FLOOR, total_bytes // AVAILABLE_SHARE)


# 原生 `bench worker-pool` 与先预导入模块再起原生 worker-pool 的入口（pearnly_core/execution/worker.py）一样记账。
POOL_COMMANDS = ('worker-pool', 'pearnly_core.execution.worker')


def is_pool(command):
    return any(name in command for name in POOL_COMMANDS)


def processes(row):
    """(lane, count) for a multi-process service, or None."""
    command = [str(part) for part in row.get('command') or []]
    for part in command:
        if part.startswith('--workers='):
            return 'web', int(part.split('=', 1)[1])
    if is_pool(command) and '--num-workers' in command:
        queue = command[command.index('--queue') + 1]
        return ('ocr' if queue == 'ocr' else 'long' if queue == 'long' else 'pool',
                int(command[command.index('--num-workers') + 1]))
    return None


def ocr_threads(row):
    environment = row.get('environment') or {}
    if isinstance(environment, list):  # compose 也接受 ["KEY=value"] 写法
        environment = dict(item.split('=', 1) for item in environment if '=' in item)
    value = str(environment.get('PEARNLY_OCR_THREADS') or '1').strip()
    if not value.isdigit() or not 1 <= int(value) <= 32:
        raise ValueError('PEARNLY_OCR_THREADS must be a whole number from 1 to 32')
    return int(value)


def ocr_thread_floor(row):
    environment = row.get('environment') or {}
    if isinstance(environment, list):
        environment = dict(item.split('=', 1) for item in environment if '=' in item)
    value = str(environment.get('PEARNLY_OCR_THREAD_MB') or '').strip()
    if not value:
        return OCR_THREAD_FLOOR
    if not value.isdigit() or not 10 <= int(value) <= 1024:
        raise ValueError('PEARNLY_OCR_THREAD_MB must be a whole number from 10 to 1024')
    return int(value) * MIB


def check_processes(services, names):
    for name in sorted(names):
        found = processes(services[name])
        if not found:
            continue
        lane, count = found
        if count < 1:
            raise ValueError('%s needs at least one process' % name)
        floor = PROCESS_FLOOR.get(lane, 0)
        if lane == 'ocr':
            floor += ocr_thread_floor(services[name]) * (ocr_threads(services[name]) - 1)
        if int(services[name]['mem_limit']) < floor * count:
            raise ValueError('%s: %d processes need at least %d MiB, limit is %d MiB'
                             % (name, count, floor * count // MIB, int(services[name]['mem_limit']) // MIB))


# 数据库连接核算（独立复查 2026-10-01：半量压测时 Max_used_connections 110 / 151，计费改成 2×16 线程后全量会顶到上限、网页直接 500）。
# 每个能连库的进程 / 线程按一个连接算：网页 workers × threads；识别进程 workers × 线程数，但线程等 Cloud Run 回复时先放掉连接
# （pearnly_core.execution.pool.release_db），按一半算；其余后台进程各一个；Keycloak 连接池按需开，留 KEYCLOAK_CONNECTIONS。
# 合计不得超过 max_connections 的 CONNECTION_SHARE。
CONNECTION_SHARE = 0.85
# 识别线程大部分时间在等 Cloud Run / 名额 / 模型，等的时候已经放掉连接，按"同时真正持有"算（半量压测实测：识别 176 个线程、
# 网页 72、计费 8 时 Max_used_connections 110）。
OCR_THREAD_CONNECTION_WEIGHT = 0.2
KEYCLOAK_CONNECTIONS = 20
# MariaDB 内存（压测窗口只读核算 2026-10-01，容器 4G、当天 00:49 OOM 过一次）：固定占用约 1.28G（buffer pool 1G + key buffer
# 128M + aria 128M + log 16M）；每个连接平均约 5M（sort 2M、join / read / stack 等），最坏带内存临时表约 19M。上限按平均算，
# 「固定 + max_connections × 每连接」不超过容器上限的 MARIADB_SHARE（4G × 0.7 ≈ 2.8G，压测窗口的警戒线）。
# 2026-10-02：缓冲池改由 runtime.env 设（PEARNLY_MARIADB_BUFFER_POOL），固定占用 = 缓冲池 + 其余约 286M。
MARIADB_FIXED_MIB = 1310
MARIADB_POOL_DEFAULT_MIB = 1024
MARIADB_CONNECTION_MIB = 5
MARIADB_SHARE = 0.7


def buffer_pool_mib(services):
    """MariaDB 命令行里的 --innodb-buffer-pool-size（如 1G / 6144M），换成 MiB；没写按默认 1G。"""
    for part in [str(item) for item in services['mariadb'].get('command') or []]:
        if part.startswith('--innodb-buffer-pool-size='):
            value = part.split('=', 1)[1].strip().upper()
            unit = {'G': 1024, 'M': 1}.get(value[-1:])
            return int(float(value[:-1]) * unit) if unit else int(value) // MIB
    return MARIADB_POOL_DEFAULT_MIB


def _flag(command, name, default):
    for part in command:
        if part.startswith(f'--{name}='):
            return int(part.split('=', 1)[1])
    if f'--{name}' in command:
        return int(command[command.index(f'--{name}') + 1])
    return default


def connections(services, names):
    """这些服务高峰时最多同时占的数据库连接（估算，见上）。"""
    total = 0.0
    for name in names:
        row = services[name]
        command = [str(part) for part in row.get('command') or []]
        if name == 'mariadb' or name.startswith('redis') or name.endswith(('-frontend', '-socketio')):
            continue
        if name == 'keycloak':
            total += KEYCLOAK_CONNECTIONS
        elif any(part.startswith('--workers=') for part in command):
            total += _flag(command, 'workers', 1) * _flag(command, 'threads', 1)
        elif is_pool(command):
            count = _flag(command, 'num-workers', 1)
            queue = command[command.index('--queue') + 1] if '--queue' in command else ''
            total += count * (1 + (ocr_threads(row) - 1) * OCR_THREAD_CONNECTION_WEIGHT if queue == 'ocr' else 1)
        else:
            total += 1
    return int(round(total))


def max_connections(services):
    command = [str(part) for part in services['mariadb'].get('command') or []]
    return _flag(command, 'max-connections', 151)


def calculate(services, containers, product, total_bytes, available_bytes, transient_bytes=0, swap_bytes=None):
    """swap_bytes：宿主 swap 总量；None 表示不核 swap（只用于离线检查里与 swap 无关的用例），生产 check() 总是传实际值。"""
    wanted = {name for name, row in services.items()
              if not row.get('profiles') or product in row['profiles']}
    other_bytes = 0
    for row in containers:
        if row['project'] == 'pearnly-production' and row['service'] in services:
            wanted.add(row['service'])
        else:
            if row['memory'] <= 0:
                raise ValueError('A running container has no memory limit; establish its budget first')
            other_bytes += row['memory']
    if any(not services[name].get('mem_limit') for name in wanted):
        raise ValueError('Every enabled production service needs a memory limit')
    check_processes(services, wanted)
    reserved = sum(int(services[name]['mem_limit']) for name in wanted) + other_bytes
    host_reserve = max(1536 * MIB, total_bytes // 10)
    budget_limit = int(total_bytes * OVERCOMMIT)
    if swap_bytes is not None:
        budget_limit = min(budget_limit, total_bytes + swap_bytes)
    report = {'product': product, 'services': sorted(wanted),
              'host_mib': total_bytes // MIB, 'container_budget_mib': reserved // MIB,
              'host_reserve_mib': host_reserve // MIB,
              'budget_limit_mib': budget_limit // MIB,
              'swap_mib': None if swap_bytes is None else swap_bytes // MIB,
              'available_mib': available_bytes // MIB,
              'available_floor_mib': available_floor(total_bytes) // MIB,
              'transient_mib': transient_bytes // MIB}
    every = {name for name, row in services.items() if not row.get('profiles')} | {
        row['service'] for row in containers if row['project'] == 'pearnly-production' and row['service'] in services} | wanted
    report['db_connections'] = connections(services, every)
    report['db_max_connections'] = max_connections(services)
    mariadb_limit = int(services['mariadb'].get('mem_limit') or 0)
    pool = buffer_pool_mib(services)
    report['db_buffer_pool_mib'] = pool
    report['db_memory_mib'] = (MARIADB_FIXED_MIB - MARIADB_POOL_DEFAULT_MIB + pool
                               + report['db_max_connections'] * MARIADB_CONNECTION_MIB)
    # 只在把连接数提到默认（151）以上、或把缓冲池提到默认（1G）以上时把关：提这两样都必须先有内存（生产已把 MariaDB 提到 4G）。
    if (mariadb_limit and (report['db_max_connections'] > 151 or pool > MARIADB_POOL_DEFAULT_MIB)
            and report['db_memory_mib'] * MIB > mariadb_limit * MARIADB_SHARE):
        raise ValueError('max_connections or the buffer pool would push MariaDB past %d%% of its memory limit: %s'
                         % (MARIADB_SHARE * 100, json.dumps(report)))
    if report['db_connections'] > report['db_max_connections'] * CONNECTION_SHARE:
        raise ValueError('Database connections would exceed %d%% of max_connections: %s'
                         % (CONNECTION_SHARE * 100, json.dumps(report)))
    if reserved + host_reserve > budget_limit:
        raise ValueError('Product memory budgets exceed this host: ' + json.dumps(report))
    if available_bytes < available_floor(total_bytes) + transient_bytes:
        raise ValueError('Insufficient current memory headroom: ' + json.dumps(report))
    return report


def check(product, transient_mib=0):
    if product not in ('dms', 'erp', 'cowork') or not os.environ.get('PEARNLY_IMAGE'):
        raise ValueError('A product and verified candidate image are required')
    sizing = Path('/opt/pearnly/state/sizing.env')  # 按机器算的参数在 runtime.env 之前读（sizing.py）
    config = json.loads(output(['docker', 'compose', *(['--env-file', str(sizing)] if sizing.exists() else []), '--env-file',
        '/opt/pearnly/secrets/runtime.env', '-f', str(ROOT / 'compose.yml'),
        '--profile', '*', 'config', '--format', 'json']))
    ids = output(['docker', 'ps', '-q']).split()
    containers = []
    if ids:
        # Emit only limits/labels from Docker; never whole container Config.Env.
        template = '{{json .HostConfig.Memory}}\t{{json (index .Config.Labels "com.docker.compose.project")}}\t{{json (index .Config.Labels "com.docker.compose.service")}}'
        for line in output(['docker', 'inspect', '--format', template, *ids]).splitlines():
            memory, project, service = (json.loads(value) for value in line.split('\t'))
            containers.append({'memory': memory, 'project': project or '', 'service': service or ''})
    mem = {line.split(':')[0]: int(line.split()[1]) * 1024
           for line in Path('/proc/meminfo').read_text().splitlines()
           if line.startswith(('MemTotal:', 'MemAvailable:', 'SwapTotal:'))}
    return calculate(config['services'], containers, product, mem['MemTotal'],
                     mem['MemAvailable'], transient_mib * MIB, mem['SwapTotal'])


HEADROOM_ERROR = 'Insufficient current memory headroom'


def check_waiting(product, transient_mib=0, wait_seconds=0, interval=15, clock=time.monotonic, sleep=time.sleep):
    """只有「现在的余量不够」时才等（上一个产品刚激活、迁移后的后台任务还在涨内存），最多等 wait_seconds 秒，
    每 interval 秒重查一次；上限之和、连接数这类配置问题等也没用，立刻报错。"""
    deadline = clock() + wait_seconds
    while True:
        try:
            return check(product, transient_mib)
        except ValueError as error:
            if not str(error).startswith(HEADROOM_ERROR) or clock() >= deadline:
                raise
        sleep(interval)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', choices=('dms', 'erp', 'cowork'), required=True)
    parser.add_argument('--transient-mib', type=int, default=0)
    parser.add_argument('--wait-seconds', type=int, default=0,
                        help='余量不够时最多等这么久（秒）再放弃；激活脚本用它让产品之间等内存回落')
    args = parser.parse_args()
    print(json.dumps(check_waiting(args.product, args.transient_mib, args.wait_seconds)))
