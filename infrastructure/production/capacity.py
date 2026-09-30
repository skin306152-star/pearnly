"""Read-only host budget check before starting another product group.

Use candidate Compose hard limits, not idle RSS, to reserve memory for existing
products. No tenant contents, environment variables or credentials are printed.

两道关（用户 2026-10-01 定，压测后放宽）：
- 上限之和：所有容器的内存上限加上宿主保留，不超过物理内存的 1.3 倍。上限是每个容器的"封顶"，
  不是常驻用量 —— 压测时 30 GiB 上限的整机实际只用约 7.4 GiB，按 1 倍算会把进程数卡在远低于机器能力的地方。
- 现在的余量：执行时整机 MemAvailable 至少 max(8 GiB, 物理内存 25%)，再加上这一步临时要的内存；
  超配靠这一条兜底 —— 真实用量涨上来时，开户 / 激活在启动前就被拒，而不是启动后被内核杀进程。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

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
# 上限之和最多到物理内存的这么多倍（见模块头）。
OVERCOMMIT = 1.3
# 执行时整机至少要空出的内存：8 GiB 与物理内存 25% 取大。
AVAILABLE_FLOOR = 8192 * MIB
AVAILABLE_SHARE = 4


def available_floor(total_bytes):
    return max(AVAILABLE_FLOOR, total_bytes // AVAILABLE_SHARE)


def processes(row):
    """(lane, count) for a multi-process service, or None."""
    command = [str(part) for part in row.get('command') or []]
    for part in command:
        if part.startswith('--workers='):
            return 'web', int(part.split('=', 1)[1])
    if 'worker-pool' in command and '--num-workers' in command:
        queue = command[command.index('--queue') + 1]
        return ('ocr' if queue == 'ocr' else 'long' if queue == 'long' else 'pool',
                int(command[command.index('--num-workers') + 1]))
    return None


def check_processes(services, names):
    for name in sorted(names):
        found = processes(services[name])
        if not found:
            continue
        lane, count = found
        if count < 1:
            raise ValueError('%s needs at least one process' % name)
        floor = PROCESS_FLOOR.get(lane, 0)
        if int(services[name]['mem_limit']) < floor * count:
            raise ValueError('%s: %d processes need at least %d MiB, limit is %d MiB'
                             % (name, count, floor * count // MIB, int(services[name]['mem_limit']) // MIB))


def calculate(services, containers, product, total_bytes, available_bytes, transient_bytes=0):
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
    report = {'product': product, 'services': sorted(wanted),
              'host_mib': total_bytes // MIB, 'container_budget_mib': reserved // MIB,
              'host_reserve_mib': host_reserve // MIB,
              'budget_limit_mib': int(total_bytes * OVERCOMMIT) // MIB,
              'available_mib': available_bytes // MIB,
              'available_floor_mib': available_floor(total_bytes) // MIB,
              'transient_mib': transient_bytes // MIB}
    if reserved + host_reserve > total_bytes * OVERCOMMIT:
        raise ValueError('Product memory budgets exceed this host: ' + json.dumps(report))
    if available_bytes < available_floor(total_bytes) + transient_bytes:
        raise ValueError('Insufficient current memory headroom: ' + json.dumps(report))
    return report


def check(product, transient_mib=0):
    if product not in ('dms', 'erp', 'cowork') or not os.environ.get('PEARNLY_IMAGE'):
        raise ValueError('A product and verified candidate image are required')
    config = json.loads(output(['docker', 'compose', '--env-file',
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
           for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:'))}
    return calculate(config['services'], containers, product, mem['MemTotal'],
                     mem['MemAvailable'], transient_mib * MIB)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', choices=('dms', 'erp', 'cowork'), required=True)
    parser.add_argument('--transient-mib', type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(check(args.product, args.transient_mib)))
