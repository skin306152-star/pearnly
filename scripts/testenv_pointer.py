#!/usr/bin/env python3
"""测试站此刻指向哪里（线上 / 测试环境），以及 lab-start / lab-stop 后自动登记到实时表（主会话 11-23）。

为什么要有它：11-23 三次误判都因为测试站域名切在测试环境而没人察觉（窗口在「线上」testerp 做的验收，授权落进了测试环境）。
判断法（不要令牌）：线上那份测试租户走通配记录 ``*.pearnly.com`` 直连生产（不经 Cloudflare 代理）；切到测试环境后是
经 Cloudflare 隧道的代理记录，解析出来是 Cloudflare 的地址。所以拿一个没人用的子域（:data:`WILDCARD_PROBE`，只命中通配记录）
的解析结果当「线上地址」比对，仓库里不写生产地址。本机 DNS 缓存可能比 Cloudflare 慢几分钟，要准看
`scripts/testenv-cloudflare.sh status`（要令牌，`testenv.sh where` 两个都打）。

用法：
  python3 scripts/testenv_pointer.py                 # 三站指向，第一行是醒目的结论
  python3 scripts/testenv_pointer.py --record erp lab --src <工作树>   # testenv.sh 切换成功后调用
"""
from __future__ import annotations

import argparse
import datetime
import os
import socket
import subprocess
import sys
from pathlib import Path

WILDCARD_PROBE = "pointer-probe-unused.pearnly.com"  # 只命中通配记录：它解析到的就是线上地址
HOSTS = {"erp": "testerp.pearnly.com", "cowork": "testcowork.pearnly.com", "dms": "testdms.pearnly.com"}
POINTER_FILE = Path(os.environ.get("PEARNLY_TESTENV_POINTER_FILE")
                    or Path.home() / "pearnly-devtools" / "test-window" / "环境指向与占用-实时.md")
LAB, PROD, UNKNOWN = "测试环境", "线上", "解析不到"


def _addresses(host, resolve):
    try:
        return {item[4][0] for item in resolve(host, 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        return set()


def where(host: str, resolve=socket.getaddrinfo, production=None) -> str:
    """``production``：线上地址（缺省按 :data:`WILDCARD_PROBE` 现解析）。"""
    addresses = _addresses(host, resolve)
    if not addresses:
        return UNKNOWN
    production = production if production is not None else _addresses(WILDCARD_PROBE, resolve)
    if not production:
        return UNKNOWN
    return PROD if addresses <= production else LAB


def report(hosts=None, resolve=socket.getaddrinfo) -> list[str]:
    """第一行是结论（有测试站在测试环境就醒目标出），后面每站一行。"""
    hosts = list(hosts or HOSTS.values())
    production = _addresses(WILDCARD_PROBE, resolve)
    found = {host: where(host, resolve, production) for host in hosts}
    lab = [host for host, state in found.items() if state != PROD]
    head = (f"!!! 注意：{'、'.join(lab)} 此刻不在线上（{'、'.join(found[h] for h in lab)}），要线上该站的活先停 !!!"
            if lab else "测试站指向：全部线上")
    return [head] + [f"  {host}: {state}" for host, state in found.items()]


def banner(hosts=None, stream=None) -> None:
    """脚本开头调用：把指向打到标准错误（不混进脚本的正式输出）。"""
    print("\n".join(report(hosts)), file=stream or sys.stderr)


def _short_sha(src: str) -> str:
    try:
        return subprocess.run(["git", "-c", "core.quotepath=off", "-C", src, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def record(app: str, direction: str, src: str = "", path: Path = POINTER_FILE, now=None) -> bool:
    """lab-start（``lab``）/ lab-stop（``prod``）成功后：在实时表「切换流水」末尾追加一行，并改这个域名那一行的「当前 / 起始」。
    表不在就只提示、回 False（不挡测试环境本身）。"""
    host = HOSTS.get(app)
    if not host:
        return False
    if not path.exists():
        print(f"(没有实时表 {path}，切换没登记)", file=sys.stderr)
        return False
    stamp = (now or datetime.datetime.now()).strftime("%m-%d %H:%M")
    sha = _short_sha(src) if src else ""
    source = f"src={src}{' @ ' + sha if sha else ''}" if src else ""
    state = LAB if direction == "lab" else PROD
    flow = f"- {stamp} {host} → {state}（testenv.sh {'lab-start' if direction == 'lab' else 'lab-stop'} 自动登记{'；' + source if source else ''}）"
    lines = path.read_text(encoding="utf-8").split("\n")
    for index, line in enumerate(lines):
        if line.startswith(f"| {host} |"):
            cells = line.split("|")
            if len(cells) >= 6:
                cells[2] = f" **{LAB}（测试机1）** " if direction == "lab" else f" {PROD} "
                if direction != "lab":
                    cells[3] = " — "
                cells[4] = f" {stamp} 自动登记 "
                lines[index] = "|".join(cells)
            break
    while lines and lines[-1] == "":
        lines.pop()
    lines.append(flow)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", nargs=2, metavar=("APP", "DIRECTION"), help="APP=erp|cowork|dms，DIRECTION=lab|prod")
    parser.add_argument("--src", default="")
    parser.add_argument("hosts", nargs="*")
    args = parser.parse_args(argv)
    if args.record:
        app, direction = args.record
        if direction not in ("lab", "prod"):
            parser.error("DIRECTION 只能是 lab 或 prod")
        record(app, direction, args.src)
        return 0
    print("\n".join(report(args.hosts or None)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
