"""Cheap Linux headroom guard before document download/rendering.

Container hard limits remain the final boundary. Headroom prevents queued OCR
from consuming the last memory needed by HTTP, identity, database and Redis.
"""
from pathlib import Path
from .errors import Deferred

MIB = 1024 * 1024


def check_memory():
    try:
        lines = Path('/proc/meminfo').read_text().splitlines()
        available = next(int(line.split()[1]) * 1024 for line in lines if line.startswith('MemAvailable:'))
        if available < 1536 * MIB:
            raise Deferred('host_memory_pressure', 10)
    except (OSError, StopIteration, ValueError):
        pass  # Non-Linux development host; production runs in Linux containers.
    try:
        maximum = Path('/sys/fs/cgroup/memory.max').read_text().strip()
        current = int(Path('/sys/fs/cgroup/memory.current').read_text().strip())
        if maximum != 'max' and int(maximum) - current < 192 * MIB:
            raise Deferred('worker_memory_pressure', 10)
    except (OSError, ValueError):
        pass
