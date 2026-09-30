"""Read-only host budget check before starting another product group.

Use candidate Compose hard limits, not idle RSS, to reserve memory for existing
products. No tenant contents, environment variables or credentials are printed.
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


# 每个进程至少要分到的内存（按现有实测定的原值折算）：进程数可在 runtime.env 里调，
# 只加进程不加内存会让每个进程分到的变少、被内核杀掉，所以在启动前拒绝。
# 识别：DMS 原 1g / 2 个；对账：原 1536m / 2 个；网页：计费后台原 512m / 2 个（最小的一处）。
PROCESS_FLOOR = {'ocr': 512 * MIB, 'long': 768 * MIB, 'web': 256 * MIB}


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
              'available_mib': available_bytes // MIB,
              'transient_mib': transient_bytes // MIB}
    if reserved + host_reserve > total_bytes:
        raise ValueError('Product memory budgets exceed this host: ' + json.dumps(report))
    if available_bytes < host_reserve + transient_bytes:
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
