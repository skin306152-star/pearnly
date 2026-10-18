# -*- coding: utf-8 -*-
"""打包后的 CC 桌面版启动冒烟（windows-latest，打包之后跑；只用标准库）。

照用户的用法走打出来的 ``dist/companion/companion.exe``（不是源码），每次都用一个全新的临时 %APPDATA%（没有成员、不连服务端）：

1. 后台常驻（``--background``，同开机自启 / 安装程序拉起）起来，日志有「托盘常驻」；
2. **再不带参数启动一次**（同双击桌面图标）：这一份要很快自己退出（把「打开窗口」交给已在跑的那份，不弹「已在运行」框），
   已在跑的那份要记「打开 CC 窗口（窗口进程）」；
3. 结束全部 CC，**不带参数直接启动**：要起来（「托盘常驻」）并且自己打开窗口（「打开 CC 窗口（窗口进程）」）。

失败时打出日志尾巴与原因，退出码 1。用法：``python packaging/smoke_launch.py dist/companion/companion.exe``
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STARTED = "托盘常驻"
OPENED = "打开 CC 窗口（窗口进程）"


class Smoke:
    def __init__(self, exe: Path) -> None:
        self.exe = exe.resolve()
        self.home = Path(tempfile.mkdtemp(prefix="cc-smoke-"))
        self.env = dict(os.environ)
        self.env.update(APPDATA=str(self.home / "Roaming"), LOCALAPPDATA=str(self.home / "Local"))
        self.env.pop("QT_QPA_PLATFORM", None)  # 真的 Windows 界面，不是测试用的 offscreen
        self.log = self.home / "Roaming" / "Pearnly" / "logs" / "companion.log"
        self.trays: list = []

    def lines(self) -> list:
        try:
            return self.log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

    def count(self, text: str) -> int:
        return sum(1 for line in self.lines() if text in line)

    def wait_for(self, text: str, at_least: int, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.count(text) >= at_least:
                return True
            time.sleep(0.25)
        return False

    def start(self, *args: str) -> subprocess.Popen:
        process = subprocess.Popen([str(self.exe), *args], env=self.env, cwd=str(self.exe.parent))
        self.trays.append(process)
        return process

    def kill_all(self) -> None:
        for process in self.trays:
            if process.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
        for process in self.trays:
            try:
                process.wait(20)
            except subprocess.TimeoutExpired:
                pass
        time.sleep(1.0)  # 单实例互斥量随进程退出由系统释放

    def fail(self, why: str) -> int:
        print(f"FAIL: {why}")
        print("---- companion.log (tail) ----")
        for line in self.lines()[-40:]:
            print(line)
        window_log = self.log.with_name("window.log")
        if window_log.exists():
            print("---- window.log (tail) ----")
            print("\n".join(window_log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]))
        return 1

    def run(self) -> int:
        try:
            # 1. 后台常驻
            self.start("--background")
            if not self.wait_for(STARTED, 1, 120):
                return self.fail("--background did not start (no tray line in the log)")
            time.sleep(2.0)  # 托盘的本地管道在托盘建好之后才开
            opened_before = self.count(OPENED)

            # 2. 再不带参数启动一次：交给已在跑的那份打开窗口，自己退出
            second = subprocess.Popen([str(self.exe)], env=self.env, cwd=str(self.exe.parent))
            try:
                code = second.wait(30)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(second.pid)], capture_output=True, check=False)
                return self.fail("second launch did not exit within 30 s (already-running dialog or hang)")
            if code != 0:
                return self.fail(f"second launch exited with {code}")
            if self.count(STARTED) != 1:
                return self.fail("second launch started another tray instead of handing over")
            if not self.wait_for(OPENED, opened_before + 1, 20):
                return self.fail("the running tray did not open the CC window after the second launch")
            print("ok: second launch handed over and the running tray opened the window")

            # 3. 结束全部，再不带参数直接启动
            self.kill_all()
            self.trays.clear()
            started_before, opened_before = self.count(STARTED), self.count(OPENED)
            self.start()
            if not self.wait_for(STARTED, started_before + 1, 120):
                return self.fail("plain launch with nothing running did not start the tray")
            if not self.wait_for(OPENED, opened_before + 1, 30):
                return self.fail("plain launch started the tray but did not open the CC window")
            print("ok: plain launch with nothing running started the tray and opened the window")
            return 0
        finally:
            self.kill_all()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # CI 的输出是管道、默认 cp1252，日志里有中文
    except (AttributeError, ValueError):
        pass
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    exe = Path(sys.argv[1])
    if not exe.is_file():
        print(f"FAIL: {exe} not found")
        return 1
    return Smoke(exe).run()


if __name__ == "__main__":
    sys.exit(main())
