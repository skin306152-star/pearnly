"""测试机上 CC 桌面版的配置切换（本机这一侧）：域名在那台测试机上解析稳定之后才改配置 / 重启。

    python3 scripts/acceptance/cc_switch.py apply   --host testpc2 --channel core --domain testerp.pearnly.com   # lab-start 之后
    python3 scripts/acceptance/cc_switch.py restore --host testpc2 --domain testerp.pearnly.com                  # lab-stop 之前
    python3 scripts/acceptance/cc_switch.py settle  --host testpc2 --domain testerp.pearnly.com                  # lab-stop 之后：解析回线上再重启一次

为什么要等（11-14 示范 1 首跑）：切域名后上游解析缓存约 5 分钟，测试机在这期间新旧地址来回跳。CC 桌面版重启时把整份账套名单报给
「那一刻解析到的站点」——切完 1 分钟就改配置重启，只含验收账套的名单报到了线上；切回线上后解析没稳又白重启了两次。
所以：apply / restore 要求连续 NEED 次（每次先清解析缓存）都解析到实验栈；settle 要求连续 NEED 次都解析到线上地址才重启。
等不到（LIMIT 秒）就抛 Infra，不改任何东西。顺序见验收测试标准 §5.1「验收专用数据根」。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEED, GAP, LIMIT = 5, 15, 600
PRODUCTION = os.environ.get("PEARNLY_ACC_PROD_IP", "107.155.65.74")
ROOT_DIR = os.environ.get("PEARNLY_ACC_ROOT_DIR", r"C:\PearnlyAcceptance\root")
LEDGER = os.environ.get("PEARNLY_ACC_LEDGER", r"\\accserver\ACCOUNT\69EXP\TEST2")


def _lib(name: str):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
        sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules[name])
    return sys.modules[name]


Infra = _lib("acc_errors").Infra


def points_at(addresses: list[str]) -> str:
    """一次解析结果指向哪里：全是线上地址 → production；一个线上地址都没有 → lab；空的或混着 → mixed。"""
    found = [item.strip() for item in addresses if item.strip()]
    if found and all(item == PRODUCTION for item in found):
        return "production"
    if found and PRODUCTION not in found:
        return "lab"
    return "mixed"


def resolve_on(host: str, domain: str, runner=subprocess.run) -> list[str]:
    """在那台测试机上清掉解析缓存后解析一次（CC 桌面版用的就是系统的解析缓存）。"""
    command = f'powershell -NoProfile -Command "ipconfig /flushdns | Out-Null; (Resolve-DnsName {domain} -Type A -DnsOnly -ErrorAction SilentlyContinue).IPAddress -join \',\'"'
    done = runner(["ssh", "-o", "BatchMode=yes", host, command], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if done.returncode == 255:
        raise Infra(f"{host}：ssh 连不上（{(done.stderr or '').strip()[-200:]}）")
    return [item for item in (done.stdout or "").strip().split(",") if item]


def settled(host: str, domain: str, expect: str, resolve=resolve_on, sleep=time.sleep, clock=time.monotonic,
            need: int = NEED, gap: int = GAP, limit: int = LIMIT) -> list[str]:
    """等到连续 need 次都指向 expect（lab / production）；中间有一次不是就从头数。等不到抛 Infra。返回每次看到的指向。"""
    seen, streak, deadline = [], 0, clock() + limit
    while True:
        where = points_at(resolve(host, domain))
        seen.append(where)
        streak = streak + 1 if where == expect else 0
        if streak >= need:
            return seen
        if clock() >= deadline:
            raise Infra(f"{host}：{domain} 等了 {limit} 秒也没稳定指向 {expect}（看到：{' '.join(seen[-12:])}）")
        sleep(gap)


def remote(host: str, *args: str) -> dict:
    code, out = _lib("targets").Windows(host).python(HERE / "win" / "cc_root.py", *args, timeout=180)
    lines = [line for line in out.splitlines() if line.strip().startswith("{")]
    if code or not lines:
        raise Infra(f"{host}：cc_root.py {args[0]} 没成功（退出码 {code}）：{out[-300:]}")
    return json.loads(lines[-1])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="域名解析稳定后才切换测试机上 CC 桌面版的配置")
    parser.add_argument("action", choices=("apply", "restore", "settle", "show"))
    parser.add_argument("--host", default="testpc2")
    parser.add_argument("--domain", default="")
    parser.add_argument("--channel", choices=("core", "cowork"), default="core")
    args = parser.parse_args(argv)
    if args.action != "show" and not args.domain:
        parser.error("--domain 必填：CC 桌面版连的那个站点域名")
    if args.action == "apply":
        print("解析：" + " ".join(settled(args.host, args.domain, "lab")))
        result = remote(args.host, "apply", args.channel, ROOT_DIR, LEDGER)
    elif args.action == "restore":
        print("解析：" + " ".join(settled(args.host, args.domain, "lab")))
        result = remote(args.host, "restore")
    elif args.action == "settle":
        print("解析：" + " ".join(settled(args.host, args.domain, "production")))
        result = remote(args.host, "restart")
    else:
        result = remote(args.host, "show")
    print(json.dumps({key: result[key] for key in ("backup", "restored_from", "after", "cc_pids") if key in result}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
