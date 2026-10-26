"""冒烟开的本机无头 Chrome 一律收干净（主会话 10-26：本机留下 9 个跑了 1 天半的孤儿 Chrome、三十多 GB 内存，102 个临时 profile）。

孤儿是这样来的：``drive.mjs`` 自己在 finally 里关 Chrome，但 ``run.py`` 以前用 ``subprocess.run(timeout=…)`` 跑它，一超时或被打断就
直接杀掉 node（SIGKILL），finally 来不及跑，Chrome（node 的子进程）就成了父进程为 1 的孤儿，临时 profile 也留下。

- :func:`run_driver`：驱动放进单独的进程组（新会话，Chrome 跟着在组里），超时、出错、被打断都整组先 SIGTERM（给驱动收尾、删 profile
  的机会）再 SIGKILL；
- :func:`reap_orphans`：启动时清掉以前留下的——只认运行标记：每次运行在自己的临时目录里记下进程号（drive.mjs 的 profile、冒烟与
  自检的工作目录），标记的那次运行已经结束的才结掉它的 Chrome、删它的目录；没有标记、或那次运行还在跑的一律不碰。
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

PROFILE_PREFIX = "smoke-"
OTHER_PREFIXES = ("scan-selftest-",)  # 相机自检的临时目录（以前也没删，本机攒了 118 个）
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


def _owner_alive(path: Path):
    """目录里的运行标记（:data:`OWNER_FILE`）：标记的进程还在 → True，已经结束 → False，没有标记 → None（不是本工具这次机制留下的，不碰）。"""
    try:
        pid = int((Path(path) / OWNER_FILE).read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def reap_orphans(root: Path | None = None) -> dict:
    """清掉以前冒烟留下的 → ``{"killed": [pid], "removed": [目录名]}``。只认运行标记（主会话 10-26）：用临时目录下 ``smoke-*`` profile、
    profile 里标记的那次运行已经结束的 Chrome（主进程、GPU 等子进程一样）结掉；标记过、那次运行已经结束、也没有进程在用的目录删掉。
    没有标记的、标记的运行还在跑的，一律不碰（别的窗口正在跑的冒烟、跑超过 1 小时的冒烟都不受影响）。"""
    root = Path(root or temp_root()).resolve()
    killed, busy = [], set()
    for pid, _ppid, command in list(_processes()):
        found = _PROFILE.search(command)
        if not found:
            continue
        profile = Path(found.group(1))
        try:
            if profile.resolve().parent != root:
                continue
        except OSError:
            continue
        if _owner_alive(profile) is False:
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
        else:
            busy.add(profile.name)
    removed = []
    for path in (found for prefix in (PROFILE_PREFIX, *OTHER_PREFIXES) for found in root.glob(prefix + "*")):
        try:
            if path.is_dir() and path.name not in busy and _owner_alive(path) is False:
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
