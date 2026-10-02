#!/usr/bin/env python3
"""按机器的核数与内存算容量参数（配置对照表的公式版；表、依据与实测见 docs/development/production-capacity.md）。

部署时 activate-* 先跑 ``sizing.py --write /opt/pearnly/state/sizing.env``，compose 依次读 sizing.env、runtime.env：runtime.env
里写了的照旧以它为准（后读覆盖），没写的跟着机器走——换机器或加内存后重新部署，没写死的参数自动跟上。算出的组合同样要过
``capacity.py`` 的把关（每进程内存底线、连接数、MariaDB 内存、整机 1.3 倍）。

公式按 2026-10-02 生产实测校准：在 8 核 / 32095 MiB 上与当时 runtime.env 逐项相同（scripts/check-production-capacity.py 守住）。
Cloud Run 实例上限、Cloud Tasks 并发、OCR 名额不随本机变化（按谷歌配额与模型限速定），不在这里算。
"""
import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capacity  # noqa: E402

ROOT = Path(__file__).resolve().parent
MIB = capacity.MIB
# 识别线程数与每线程内存（不随核数变）：Cowork / ERP 识别交给 Cloud Run，线程只带着原件等回复；DMS 本机读字。
OCR_THREADS = {"COWORK": (24, 30), "ERP": (16, 80), "DMS": (8, 60)}
ERP_WEB_THREADS = 8
POOL_SHARE = 0.13         # 缓冲池约占物理内存 13%（热数据 2026-10-02 约 1GB，留 4 倍增长）
# 最小支持的机器：三个产品全开、识别线程照满时，8 核 / 31 GiB（2026-10-02 生产机）才放得下；更小的机器公式会超出整机 1.3 倍
# 预算（容量检查会拒绝），这里直接说明，要么换机器，要么在 runtime.env 里手工写死较小的进程 / 线程数。
MIN_CORES, MIN_MEM_MIB = 8, 30 * 1024
CONNECTION_HEADROOM = 0.8  # max_connections = 估算连接数 / 0.8（容量检查的拦截线是 0.85）


def _ceil_gib(mib):
    return int(math.ceil(mib / 1024.0))


def base(cores, mem_mib):
    """不依赖渲染的那部分：进程数、线程、内存上限、缓冲池。"""
    half, quarter = max(2, cores // 2), max(1, cores // 4)
    cowork_web = max(2, round(cores * 0.75))
    knobs = {
        "PEARNLY_COWORK_WEB_WORKERS": cowork_web, "PEARNLY_COWORK_WEB_MEM": f"{256 * cowork_web}m",
        "PEARNLY_ERP_WEB_WORKERS": half, "PEARNLY_ERP_WEB_THREADS": ERP_WEB_THREADS, "PEARNLY_ERP_WEB_MEM": f"{384 * half}m",
        "PEARNLY_DMS_WEB_WORKERS": half, "PEARNLY_DMS_WEB_MEM": f"{256 * half}m",
        "PEARNLY_COWORK_LONG_WORKERS": half, "PEARNLY_COWORK_LONG_MEM": f"{512 * half}m",
    }
    for product, workers in (("COWORK", half), ("ERP", half), ("DMS", quarter)):
        threads, thread_mb = OCR_THREADS[product]
        floor = capacity.PROCESS_FLOOR["ocr"] // MIB + thread_mb * (threads - 1)  # 与 capacity.check_processes 同一底线
        knobs.update({f"PEARNLY_{product}_OCR_WORKERS": workers, f"PEARNLY_{product}_OCR_THREADS": threads,
                      f"PEARNLY_{product}_OCR_THREAD_MB": thread_mb,
                      f"PEARNLY_{product}_OCR_MEM": f"{_ceil_gib(floor * workers)}g"})
    knobs["PEARNLY_MARIADB_BUFFER_POOL"] = f"{max(1, int(mem_mib * POOL_SHARE // 1024))}G"
    return knobs


def render(knobs, env_file=None):
    """compose 按这些参数渲染出的服务（与 capacity.check 同一条 docker compose config）。"""
    args = ["docker", "compose"] + (["--env-file", env_file] if env_file else []) + [
        "-f", str(ROOT / "compose.yml"), "--profile", "*", "config", "--format", "json"]
    # 只看进程数、线程与内存上限：渲染必填的镜像与密码给占位值（与 check-production-capacity 相同），不读任何密钥。
    placeholders = {"PEARNLY_IMAGE": "example.invalid/sizing:never-pulled", "DB_ROOT_PASSWORD": "unused",
                    "KEYCLOAK_DB_PASSWORD": "unused", "KEYCLOAK_ADMIN_PASSWORD": "unused", "AUTH_HOST": "example.invalid"}
    env = {**placeholders, **os.environ, **{key: str(value) for key, value in knobs.items()}}
    return json.loads(subprocess.check_output(args, env=env, text=True))["services"]


def recommend(cores, mem_mib, render=render):
    if cores < MIN_CORES or mem_mib < MIN_MEM_MIB:
        raise ValueError(f"host {cores} cores / {mem_mib} MiB is below the supported minimum {MIN_CORES} cores / "
                         f"{MIN_MEM_MIB} MiB; pin smaller process and thread counts in runtime.env instead")
    knobs = base(cores, mem_mib)
    services = render({**knobs, "PEARNLY_MARIADB_MAX_CONNECTIONS": 151, "PEARNLY_MARIADB_MEM": "64g"})
    everything = {name for name, row in services.items()
                  if not row.get("profiles") or set(row["profiles"]) & {"dms", "erp", "cowork"}}
    connections = capacity.connections(services, everything)
    max_connections = max(151, int(math.ceil(connections / CONNECTION_HEADROOM / 10.0)) * 10)
    pool_mib = int(knobs["PEARNLY_MARIADB_BUFFER_POOL"][:-1]) * 1024
    mariadb_mib = (capacity.MARIADB_FIXED_MIB - capacity.MARIADB_POOL_DEFAULT_MIB + pool_mib
                   + max_connections * capacity.MARIADB_CONNECTION_MIB) / capacity.MARIADB_SHARE
    knobs.update(PEARNLY_MARIADB_MAX_CONNECTIONS=max_connections, PEARNLY_MARIADB_MEM=f"{_ceil_gib(mariadb_mib)}g")
    return knobs


def host():
    meminfo = {line.split(":")[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
               if line.startswith("MemTotal:")}
    return os.cpu_count() or 1, meminfo["MemTotal"] // 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cores", type=int)
    parser.add_argument("--mem-mib", type=int)
    parser.add_argument("--write", help="写成 compose 可读的 env 文件（在 runtime.env 之前读）")
    parser.add_argument("--compare", help="与这份 runtime.env 对比，列出写死了、与公式不同的项")
    args = parser.parse_args()
    cores, mem_mib = host() if args.cores is None else (args.cores, args.mem_mib)
    knobs = recommend(cores, mem_mib)
    if args.write:
        Path(args.write).write_text("".join(f"{key}={value}\n" for key, value in sorted(knobs.items())))
    report = {"cores": cores, "mem_mib": mem_mib, "knobs": knobs}
    if args.compare:
        pinned = dict(line.split("=", 1) for line in Path(args.compare).read_text().splitlines() if "=" in line)
        report["pinned_differs"] = {key: {"runtime.env": pinned[key], "formula": str(value)}
                                    for key, value in knobs.items() if key in pinned and pinned[key] != str(value)}
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
