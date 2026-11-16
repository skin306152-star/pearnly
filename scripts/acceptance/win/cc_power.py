"""测试机上 CC 桌面版的停 / 起（以桌面用户跑，经验收体系的一次性计划任务；本机由场景经 `targets.Windows.python` 调用）。
造「电脑离线 / 关机」现场（不正常使用 M07）：停掉 CC → 服务端看不到这台电脑 → 收款 / 单据停在等电脑 → 再起 CC → 补写。只用标准库。

    python cc_power.py state              CC 在不在跑
    python cc_power.py stop               停 CC（taskkill），等到进程没了
    python cc_power.py start [<秒>]       起 CC（--background），等 companion.log 里起来之后的 [heartbeat] INFO 行（默认 150 秒）

不改配置、不动账套；停 / 起的做法与 infrastructure/maclab/win/cfg_guard.py 相同（同一个程序路径、同一个日志格式）。
末行打印一行 JSON（ASCII 转义：计划任务里标准输出是系统代码页）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

APPDATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
LOG = APPDATA / "Pearnly" / "logs" / "companion.log"
EXE = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Programs" / "CC" / "companion.exe"
_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} \[(\w+)\] (\S+) :: (.*)$")


def beats_since(lines, since: datetime) -> list[str]:
    """日志里 ``since`` 之后的心跳 INFO 行（纯函数，单测用）。"""
    out = []
    for raw in lines:
        match = _LINE.match(raw.rstrip("\r\n"))
        if match and match.group(2) == "INFO" and "[heartbeat]" in match.group(4) \
                and datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S") >= since:
            out.append(raw.strip()[:160])
    return out


def running() -> bool:
    listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq companion.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return any(line.startswith('"companion') for line in listed.splitlines())


def stop() -> dict:
    was = running()
    subprocess.run(["taskkill", "/IM", "companion.exe", "/F"], capture_output=True)
    deadline = time.monotonic() + 30
    while running() and time.monotonic() < deadline:
        time.sleep(2)
    return {"was_running": was, "running": running()}


def start(limit: int) -> dict:
    if running():
        return {"was_running": True, "running": True, "beat": "already running"}
    since = datetime.now().replace(microsecond=0)
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process '{EXE}' -ArgumentList '--background'"], capture_output=True)
    deadline, beats = time.monotonic() + limit, []
    while time.monotonic() < deadline:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-3000:] if LOG.is_file() else []
        beats = beats_since(lines, since)
        if beats:
            break
        time.sleep(5)
    return {"was_running": False, "running": running(), "since": f"{since:%Y-%m-%d %H:%M:%S}", "beat": beats[0] if beats else ""}


def main(argv) -> int:
    import base64

    argv = [base64.b64decode(arg[4:]).decode("utf-8") if arg.startswith("b64:") else arg for arg in argv]
    mode = argv[0] if argv else "state"
    if mode == "stop":
        out = stop()
    elif mode == "start":
        out = start(int(argv[1]) if len(argv) > 1 else 150)
    elif mode == "state":
        out = {"running": running()}
    else:
        raise SystemExit(f"unknown mode {mode}")
    print(json.dumps({"ok": True, "mode": mode, **out}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
