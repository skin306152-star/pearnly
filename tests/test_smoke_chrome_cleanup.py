"""冒烟开的本机无头 Chrome 一律收干净（主会话 10-26：本机留下 9 个跑了 1 天半的孤儿 Chrome、102 个临时 profile）。

用一个假 Chrome（记下自己的 pid、一直睡）代替真的跑 ``scripts/smoke/drive.mjs``：
- 驱动超时：run_driver 整组收掉，假 Chrome 不在、临时 profile 删掉；
- 驱动中途被 SIGTERM 打断：它自己关掉 Chrome、删 profile；
- 启动时的孤儿清理只清冒烟自己这一类（--headless + 临时目录下的 smoke-* profile、父进程已是 1），删 1 小时以前的 smoke-* 目录。
没有 node 时跳过。
"""
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chrome_reaper", ROOT / "scripts/smoke/chrome_reaper.py")
reaper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reaper)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # 已经结束、等着被收的僵尸不算活着
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(out) and not out.startswith("Z")
    except OSError:
        return True


def wait_for(path: Path, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


@unittest.skipUnless(shutil.which("node"), "needs node")
class SmokeChromeCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pidfile = self.tmp / "chrome.pid"
        fake = self.tmp / "fake-chrome"
        fake.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import os, time
            open({str(self.pidfile)!r}, "w").write(str(os.getpid()))
            time.sleep(120)
            """))
        fake.chmod(0o755)
        self.env = {**os.environ, "SMOKE_CHROME": str(fake), "TMPDIR": str(self.tmp)}
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def profiles(self):
        return sorted(path.name for path in self.tmp.glob("smoke-*"))

    def test_a_driver_that_times_out_leaves_no_chrome_and_no_profile(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            reaper.run_driver(["node", str(ROOT / "scripts/smoke/drive.mjs"), '{"url": "about:blank"}'], timeout=2, env=self.env)
        pid = wait_for(self.pidfile)
        time.sleep(0.5)
        self.assertFalse(alive(pid), "fake Chrome still running after the driver timed out")
        self.assertEqual(self.profiles(), [])

    def test_a_driver_interrupted_midway_closes_chrome_and_removes_its_profile(self):
        proc = subprocess.Popen(["node", str(ROOT / "scripts/smoke/drive.mjs"), '{"url": "about:blank"}'], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        pid = wait_for(self.pidfile)
        self.assertEqual(len(self.profiles()), 1)
        proc.send_signal(signal.SIGTERM)   # 只打断驱动本身，看它自己收不收
        proc.wait(10)
        time.sleep(0.5)
        self.assertFalse(alive(pid), "fake Chrome left running after the driver was interrupted")
        self.assertEqual(self.profiles(), [])

    def test_the_startup_sweep_only_takes_smoke_orphans_and_old_profiles(self):
        orphan_dir = self.tmp / "smoke-orphan"
        orphan_dir.mkdir()
        other_pidfile = self.tmp / "other.pid"
        # 孤儿：外壳退出后父进程变成 1，命令行带 --headless 与 smoke-* profile；对照：不是 smoke-* profile 的不碰
        subprocess.run(["sh", "-c", f"{sys.executable} -c 'import time; time.sleep(120)' --headless=new "
                                    f"--user-data-dir={orphan_dir} & echo $! > {self.pidfile}"], check=True)
        subprocess.run(["sh", "-c", f"{sys.executable} -c 'import time; time.sleep(120)' --headless=new "
                                    f"--user-data-dir={self.tmp / 'profile-other'} & echo $! > {other_pidfile}"], check=True)
        orphan, other = wait_for(self.pidfile), wait_for(other_pidfile)
        self.addCleanup(lambda: alive(other) and os.kill(other, signal.SIGKILL))
        old, new = self.tmp / "smoke-old", self.tmp / "smoke-new"
        old.mkdir(), new.mkdir()
        past = time.time() - 2 * 3600
        os.utime(old, (past, past))
        time.sleep(0.3)
        swept = reaper.reap_orphans(self.tmp)
        time.sleep(0.3)
        self.assertIn(orphan, swept["killed"])
        self.assertFalse(alive(orphan))
        self.assertTrue(alive(other))
        self.assertIn("smoke-old", swept["removed"])
        self.assertTrue(new.exists())


if __name__ == "__main__":
    unittest.main()
