#!/usr/bin/env python3
"""占用锁：一台测试机、一部手机、一本测试账套、一个会互踢的账号、一套实验栈，同一时间只有一个使用者（验收测试标准 §5.2）。

起因：11-12 一天撞车 8 次（两个窗口同写 69EXP\\TEST、TEST2 一本多用、两个 run.py 互相重启容器、三个窗口连生产致办公室 IP 被挡），
当时的占用只是两份人工 Markdown 表。

与流程运行器的实验栈锁（`scripts/flows/run.py` 的 `lab_lock`）是同一套机制：同一个目录（`~/.cache/pearnly-flows/`）、
同一种锁文件（`<名>.lock`）、同一个系统锁（flock）。在它之上多一种「约期占用」，给跨很多条命令的人工 / 真机工作用：

- **进程占用**（`hold`，运行器用）：拿着 flock 直到进程退出；进程死了锁自动没了。
- **约期占用**（`acquire` / `release`，窗口用）：把「谁、做什么、到几点、还原清单」写进锁文件后放开 flock；到期前别人拿不到。
  到期不自动作废：要接手必须 `--takeover` 并写原因，先看到上一位留下的还原清单。

资源名固定（见 RESOURCES）；实验栈 `lab:<栈名>` 的锁文件就是 `lab_lock` 用的 `<栈名>.lock`，两边互相看得见。
全部窗口都在同一台 Mac 上跑，所以锁放本机用户目录就能互斥；它管不了的（切测试域名、连生产、要用户在场的步骤）仍先发主会话。

用法：
    python3 scripts/acceptance/locks.py acquire testpc1 --holder <窗口名> --purpose "做什么" --minutes 60 [--restore "还原清单"]
    python3 scripts/acceptance/locks.py release testpc1 --holder <窗口名> [--note "还原情况"]
    python3 scripts/acceptance/locks.py status [--markdown]
    python3 scripts/acceptance/locks.py history [资源] [--limit 20]
退出码：0 = 成功；1 = 被占用或不是持有者；2 = 用法 / 资源名不对。只动本机锁目录，不连任何机器。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

# 资源名：固定的几个，加带参数的几类。新增一类资源先加在这里（并写进验收测试标准 §5.2）。
RESOURCES = {
    "testpc1": "测试机1（实验栈、WSL、文件）",
    "testpc1-desktop": "测试机1 的桌面会话（遥控 Express / CC 桌面版界面）",
    "testpc2": "测试机2",
    "testpc2-desktop": "测试机2 的桌面会话",
    "samsung": "三星测试手机",
    "prod-ssh": "连生产（一次只一个窗口，被挡后谁都不重试）",
}
PATTERNS = {
    "ledger": (r"[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)*", "Express 测试账套，如 ledger:69EXP/TEST2"),
    "mrerp": (r"[A-Za-z0-9][A-Za-z0-9_.-]*", "MR.ERP 测试账号，如 mrerp:test01（登录会踢掉别处同账号）"),
    "testenv-domain": (r"(erp|cowork|dms)", "测试域名切到测试机，如 testenv-domain:erp"),
    "lab": (r"[A-Za-z0-9][A-Za-z0-9_.-]*", "本机实验栈，如 lab:pearnly-regression-lab"),
    "e2e": (r"[A-Za-z0-9][A-Za-z0-9_.-]*", "e2e 账号，如 e2e:e2ecowork（连续登录会被拒、会把别处挤下线）"),
}
MAX_MINUTES = 8 * 60


class Busy(Exception):
    """资源被占用；`record` 是占用者的登记（进程占用且读不到登记时只有 pid）。"""

    def __init__(self, resource: str, record: dict):
        self.resource, self.record = resource, record
        super().__init__(describe(resource, record))


class NotHolder(Exception):
    pass


def folder() -> Path:
    # 固定放用户目录：各窗口的 TMPDIR 不同，放临时目录锁不住别的窗口（与 run.py 的 lab_lock 同一个目录）
    path = Path(os.environ.get("PEARNLY_LOCK_DIR") or Path.home() / ".cache" / "pearnly-flows")
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_name(resource: str) -> str:
    if resource in RESOURCES:
        return resource
    kind, _, rest = resource.partition(":")
    if kind in PATTERNS and rest and re.fullmatch(PATTERNS[kind][0], rest):
        return resource
    known = ", ".join(sorted(RESOURCES)) + "；" + "；".join(f"{k}:<…>（{v[1]}）" for k, v in sorted(PATTERNS.items()))
    raise ValueError(f"不认识的资源名 {resource!r}。可用：{known}")


def lock_path(resource: str) -> Path:
    check_name(resource)
    kind, _, rest = resource.partition(":")
    # 实验栈沿用 lab_lock 的文件名 <栈名>.lock；其余用资源名（: / 换成 __）
    name = rest if kind == "lab" else re.sub(r"[:/]", "__", resource)
    return folder() / f"{name}.lock"


def _read(handle) -> dict:
    handle.seek(0)
    text = handle.read().strip()
    if not text:
        return {}
    try:
        record = json.loads(text)
        return record if isinstance(record, dict) else {"pid": text}
    except ValueError:
        return {"pid": text}  # lab_lock 只写进程号


def _write(handle, record: dict) -> None:
    handle.seek(0)
    handle.truncate()
    if record:
        handle.write(json.dumps(record, ensure_ascii=False))
    handle.flush()
    os.fsync(handle.fileno())


def _log(event: str, resource: str, record: dict, **extra) -> None:
    line = {"at": _stamp(time.time()), "event": event, "resource": resource, **record, **extra}
    with open(folder() / "history.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps(line, ensure_ascii=False) + "\n")


def _stamp(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seconds))


def describe(resource: str, record: dict) -> str:
    if not record:
        return f"{resource}：空闲"
    if record.get("mode") != "lease":
        who = f"{record['holder']}（进程 {record.get('pid', '?')}）" if record.get("holder") else f"进程 {record.get('pid', '?')}"
        return f"{resource}：{who} 正在用" + (f"（{record['purpose']}）" if record.get("purpose") else "")
    left = record.get("until", 0) - time.time()
    state = f"还有 {int(left // 60)} 分钟" if left > 0 else f"已超时 {int(-left // 60)} 分钟（没释放）"
    text = f"{resource}：{record['holder']} 在用（{record.get('purpose', '')}；{_stamp(record.get('since', 0))} 起，{state}）"
    return text + (f"；还原清单：{record['restore']}" if record.get("restore") else "")


def _open(resource: str):
    """打开锁文件并拿到 flock；拿不到说明有活着的进程占着。"""
    handle = open(lock_path(resource), "a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        record = _read(handle)
        handle.close()
        raise Busy(resource, record or {"pid": "?"})
    return handle


def _lease_of(record: dict) -> dict:
    """锁文件里还作数的约期占用；进程号（进程已退出才拿得到 flock）与空内容都不算。"""
    return record if record.get("holder") and record.get("mode") == "lease" else {}


def acquire(resource: str, holder: str, purpose: str, minutes: int, restore: str = "",
            takeover: bool = False, reason: str = "") -> dict:
    """约期占用。别人占着（哪怕已超时）就抛 Busy；`takeover` + `reason` 可接手已超时的。自己已占着就是续期。"""
    if not holder.strip() or not purpose.strip():
        raise ValueError("holder、purpose 不能空")
    if not 0 < minutes <= MAX_MINUTES:
        raise ValueError(f"minutes 要在 1–{MAX_MINUTES} 之间；更久的分段续期")
    handle = _open(resource)
    try:
        current = _lease_of(_read(handle))
        if current and current["holder"] != holder:
            expired = current.get("until", 0) <= time.time()
            if not (takeover and expired and reason.strip()):
                raise Busy(resource, current)
            _log("takeover", resource, current, by=holder, reason=reason)
        now = time.time()
        record = {"mode": "lease", "holder": holder, "purpose": purpose, "restore": restore,
                  "since": current.get("since", now) if current.get("holder") == holder else now,
                  "until": now + minutes * 60}
        _write(handle, record)
        _log("acquire", resource, record)
        return record
    finally:
        handle.close()  # 关文件即放 flock；约期占用靠文件内容


def release(resource: str, holder: str, note: str = "") -> None:
    handle = _open(resource)
    try:
        current = _lease_of(_read(handle))
        if not current:
            return
        if current["holder"] != holder:
            raise NotHolder(f"{resource} 是 {current['holder']} 占着的，不是 {holder}")
        _write(handle, {})
        _log("release", resource, current, note=note)
    finally:
        handle.close()


class hold:
    """进程占用：`with hold("ledger:69EXP/TEST2", holder, purpose):`。别人有约期占用或有进程占着就抛 Busy；
    自己（同名 holder）事先约期占用的可以进。退出时放锁，约期占用原样留着。"""

    def __init__(self, resource: str, holder: str, purpose: str):
        self.resource, self.holder, self.purpose, self.handle, self.lease = resource, holder, purpose, None, {}

    def __enter__(self):
        handle = _open(self.resource)
        self.lease = _lease_of(_read(handle))
        if self.lease and self.lease["holder"] != self.holder:
            handle.close()
            raise Busy(self.resource, self.lease)
        self.handle = handle
        if not self.lease:  # 让被挡的人看得到是谁；自己的约期占用原样留着
            _write(handle, {"mode": "process", "holder": self.holder, "purpose": self.purpose, "pid": os.getpid()})
        _log("hold", self.resource, {"holder": self.holder, "purpose": self.purpose, "pid": os.getpid()})
        return self

    def __exit__(self, *exc):
        _log("unhold", self.resource, {"holder": self.holder, "pid": os.getpid()})
        if not self.lease:
            _write(self.handle, {})
        self.handle.close()
        return False


def hold_all(resources: list[str], holder: str, purpose: str) -> list[hold]:
    """按名字排序依次拿（固定顺序，两个运行器不会互相等死）；有一个拿不到就把已拿的都放掉再抛 Busy。"""
    held: list[hold] = []
    try:
        for resource in sorted(set(resources)):
            held.append(hold(resource, holder, purpose).__enter__())
    except Exception:
        for item in reversed(held):
            item.__exit__(None, None, None)
        raise
    return held


def status() -> list[tuple[str, dict]]:
    rows = []
    for path in sorted(folder().glob("*.lock")):
        name = path.name[: -len(".lock")]
        resource = name.replace("__", ":", 1).replace("__", "/") if "__" in name or name in RESOURCES else f"lab:{name}"
        try:
            handle = _open(resource)
        except Busy as busy:
            rows.append((resource, busy.record))
            continue
        except ValueError:
            continue
        with handle:
            lease = _lease_of(_read(handle))
        if lease:
            rows.append((resource, lease))
    return rows


def history(resource: str | None = None, limit: int = 20) -> list[dict]:
    path = folder() / "history.jsonl"
    if not path.exists():
        return []
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [line for line in lines if not resource or line.get("resource") == resource][-limit:]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("acquire")
    one.add_argument("resource")
    one.add_argument("--holder", default=os.environ.get("PEARNLY_LOCK_HOLDER", ""))
    one.add_argument("--purpose", required=True)
    one.add_argument("--minutes", type=int, required=True)
    one.add_argument("--restore", default="")
    one.add_argument("--takeover", action="store_true")
    one.add_argument("--reason", default="")
    two = sub.add_parser("release")
    two.add_argument("resource")
    two.add_argument("--holder", default=os.environ.get("PEARNLY_LOCK_HOLDER", ""))
    two.add_argument("--note", default="")
    three = sub.add_parser("status")
    three.add_argument("--markdown", action="store_true")
    four = sub.add_parser("history")
    four.add_argument("resource", nargs="?")
    four.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == "acquire":
            record = acquire(args.resource, args.holder, args.purpose, args.minutes, args.restore, args.takeover, args.reason)
            print(f"已占用 {describe(args.resource, record)}")
        elif args.command == "release":
            release(args.resource, args.holder, args.note)
            print(f"已释放 {args.resource}")
        elif args.command == "status":
            rows = status()
            if args.markdown:
                print("| 资源 | 谁 | 做什么 | 起 | 到 | 还原清单 |\n|---|---|---|---|---|---|")
                for resource, record in rows:
                    print(f"| {resource} | {record.get('holder') or '进程 ' + str(record.get('pid', '?'))} | {record.get('purpose', '')} "
                          f"| {_stamp(record['since']) if record.get('since') else ''} | {_stamp(record['until']) if record.get('until') else ''} "
                          f"| {record.get('restore', '')} |")
            else:
                print("\n".join(describe(resource, record) for resource, record in rows) or "没有占用")
        else:
            for line in history(args.resource, args.limit):
                print(json.dumps(line, ensure_ascii=False))
    except Busy as busy:
        print(f"被占用：{busy}", file=sys.stderr)
        return 1
    except NotHolder as error:
        print(str(error), file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
