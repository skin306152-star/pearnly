"""冒烟开的本机无头 Chrome 一律收干净（主会话 10-26：本机留下 9 个跑了 1 天半的孤儿 Chrome、三十多 GB 内存，102 个临时 profile）。

孤儿是这样来的：``drive.mjs`` 自己在 finally 里关 Chrome，但 ``run.py`` 以前用 ``subprocess.run(timeout=…)`` 跑它，一超时或被打断就
直接杀掉 node（SIGKILL），finally 来不及跑，Chrome（node 的子进程）就成了父进程为 1 的孤儿，临时 profile 也留下。

- :func:`run_driver`：驱动放进单独的进程组（新会话，Chrome 跟着在组里），超时、出错、被打断都整组先 SIGTERM（给驱动收尾、删 profile
  的机会）再 SIGKILL；
- :func:`reap_orphans`：启动时清掉以前留下的——只认自己这一类：命令行带 ``--headless`` 且 ``--user-data-dir`` 是临时目录下的
  ``smoke-*``、父进程已经不是冒烟驱动（多为 1）的 Chrome；临时目录下 1 小时以前的 ``smoke-*`` 目录（还被进程用着的不删）。
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

PROFILE_PREFIX = "smoke-"
OTHER_PREFIXES = ("scan-selftest-",)  # 相机自检的临时目录（以前也没删，本机攒了 118 个）
STALE_SECONDS = 3600
OWNER_FILE = ".owner-pid"  # 冒烟 / 自检自己的工作目录里记下进程号：跑超过 1 小时也不会被别的窗口启动时清掉（验收 10-26）
_PROFILE = re.compile(r"--user-data-dir=(\S*/" + PROFILE_PREFIX + r"[^\s/]+)")


def temp_root() -> Path:
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir())


def _processes():
    # -ww：命令行不截断（长的会把 --user-data-dir、drive.mjs 截掉，误判父子关系）
    out = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=False).stdout
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            yield int(parts[0]), int(parts[1]), parts[2]


def reap_orphans(root: Path | None = None, *, stale_seconds: int = STALE_SECONDS) -> dict:
    """清掉以前冒烟留下的孤儿 Chrome 与旧临时 profile → ``{"killed": [pid], "removed": [目录名]}``。"""
    root = Path(root or temp_root()).resolve()
    killed, busy = [], set()
    table = list(_processes())
    commands = {pid: command for pid, _ppid, command in table}
    for pid, ppid, command in table:
        found = _PROFILE.search(command)
        if not found:
            continue
        profile = Path(found.group(1))
        try:
            mine = profile.resolve().parent == root
        except OSError:
            mine = False
        if not mine:
            continue
        # 只认进程树的根：Chrome 的 GPU 等子进程命令行里也带 --headless 与同一个 profile，它的父进程是 Chrome 主进程（带同一个
        # profile）——那是别人正在跑的冒烟，不是孤儿（验收 10-26 实测会误杀另一个窗口的冒烟）
        parent = commands.get(ppid, "")
        if found.group(1) in parent:
            busy.add(profile.name)
            continue
        # 孤儿：根进程的父进程已不是冒烟驱动（macOS 上被 1 号进程接管，Linux 上也可能被别的收养进程接走）
        if "--headless" in command and (ppid == 1 or "drive.mjs" not in parent):
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
        else:
            busy.add(profile.name)  # 正在跑的冒烟还在用
    removed = []
    cutoff = time.time() - stale_seconds

    def owned(path: Path) -> bool:
        try:
            pid = int((path / OWNER_FILE).read_text().strip())
        except (OSError, ValueError):
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    for path in (found for prefix in (PROFILE_PREFIX, *OTHER_PREFIXES) for found in root.glob(prefix + "*")):
        try:
            if path.is_dir() and path.name not in busy and not owned(path) and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path.name)
        except OSError:
            continue
    return {"killed": killed, "removed": removed}


def claim(path: Path) -> Path:
    """在自己的工作目录里记下进程号（:data:`OWNER_FILE`）：清理时还活着的就不删。"""
    Path(path, OWNER_FILE).write_text(str(os.getpid()))
    return Path(path)


def _end_group(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """整组先 SIGTERM（驱动收尾、删 profile），等一会儿还在就 SIGKILL。"""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, grace)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def run_driver(cmd: list, timeout: float, env: dict | None = None) -> tuple:
    """跑驱动 → ``(returncode, stdout, stderr)``；超时抛 ``subprocess.TimeoutExpired``。不管怎么结束，驱动和它开的 Chrome 整组收掉。"""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                            env=env)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    finally:
        if proc.poll() is None:
            _end_group(proc)
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except OSError:
                    pass
        else:
            try:  # 驱动退了、组里还剩的（Chrome 的子进程）也收掉
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def exit_on_signals() -> None:
    """SIGTERM / SIGHUP 也按退出处理（跑 finally，收掉驱动组），不让 Python 直接被杀、留下 Chrome。"""
    def stop(signum, _frame):
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)


__all__ = ["PROFILE_PREFIX", "claim", "exit_on_signals", "reap_orphans", "run_driver", "temp_root"]
