# -*- coding: utf-8 -*-
"""打包后的 CC 桌面版启动冒烟（windows-latest，打包之后跑；只用标准库）。

照用户的用法走打出来的 ``dist/companion/companion.exe``（不是源码），每次都用一个全新的临时 %APPDATA%（没有成员、不连服务端）：

1. 后台常驻（``--background``，同开机自启 / 安装程序拉起）起来，日志有「托盘常驻」；
2. **再不带参数启动一次**（同双击桌面图标）：这一份要很快自己退出（把「打开窗口」交给已在跑的那份，不弹「已在运行」框），
   已在跑的那份要记「打开 CC 窗口（窗口进程）」；
3. 结束全部 CC，**不带参数直接启动**：要起来（「托盘常驻」）并且自己打开窗口（「打开 CC 窗口（窗口进程）」）；
4. 结束全部 CC，**云端模式** ``--cloud``（云端主机上那份，runtime_mode.py）：日志有「云端模式」、不起托盘；没有隧道与成员时
   **不退出**，记「云端等待隧道」后一直等（云端 Express 第二阶段 D3：它从启动文件夹起来，退出就要等到下次登录），由冒烟结束它。

只在 Windows 上跑（打包后的 exe；GitHub 的 windows-latest），macOS / Linux 开发机不跑这个脚本。

云端精简包（``dist/companion-cloud/companion.exe``，companion-cloud.spec，没有托盘与窗口）只跑第 4 步，加 ``--cloud-only``：
``python packaging/smoke_launch.py --cloud-only dist/companion-cloud/companion.exe``。

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
CLOUD_STARTED = "云端模式"
CLOUD_WAITING = "云端等待隧道"
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

    def run(self, cloud_only: bool = False) -> int:
        try:
            if cloud_only:
                return self.cloud_step()
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

            # 4. 云端模式
            self.kill_all()
            self.trays.clear()
            return self.cloud_step()
        finally:
            self.kill_all()

    def cloud_step(self) -> int:
        """云端模式：不起托盘；全新目录没有隧道、没有成员 → 记「云端等待隧道」并一直等着（D3：不再以 2 退出）。"""
        started_before, cloud_before = self.count(STARTED), self.count(CLOUD_STARTED)
        waiting_before = self.count(CLOUD_WAITING)
        cloud = subprocess.Popen([str(self.exe), "--cloud"], env=self.env, cwd=str(self.exe.parent))
        try:
            if not self.wait_for(CLOUD_WAITING, waiting_before + 1, 60):
                code = cloud.poll()
                return self.fail("--cloud did not wait for the tunnel within 60 s"
                                 + (f" (exited with {code})" if code is not None else ""))
            time.sleep(3.0)
            if cloud.poll() is not None:
                return self.fail(f"--cloud with no member exited with {cloud.returncode}; it must keep waiting")
        finally:
            if cloud.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(cloud.pid)], capture_output=True, check=False)
        if self.count(CLOUD_STARTED) != cloud_before + 1:
            return self.fail("--cloud did not log the cloud mode line")
        if self.count(STARTED) != started_before:
            return self.fail("--cloud started the tray")
        print("ok: --cloud ran without the tray and kept waiting for the tunnel when nothing is paired")
        return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # CI 的输出是管道、默认 cp1252，日志里有中文
    except (AttributeError, ValueError):
        pass
    args = sys.argv[1:]
    cloud_only = "--cloud-only" in args
    args = [arg for arg in args if arg != "--cloud-only"]
    if len(args) != 1:
        print(__doc__)
        return 2
    exe = Path(args[0])
    if not exe.is_file():
        print(f"FAIL: {exe} not found")
        return 1
    return Smoke(exe).run(cloud_only=cloud_only)


if __name__ == "__main__":
    sys.exit(main())
