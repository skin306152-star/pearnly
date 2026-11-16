"""云端 Express 连接窗口的延迟加载守卫（CC 桌面版要轻量：不连云端的电脑不为它付任何导入开销）。

守住：CC 启动、空闲、托盘、对话页的主路径模块全部导入、对话页桥读一次状态与「云端 Express」一行之后，进程里**没有** comtypes、
``cloud_link.ax_host``、``cloud_link.window``、``cloud_link.nonscriptable``；只有调到 ``CloudHost.start``（人点「连接」）时
才导入窗口、控件宿主与非脚本设置。在干净的子进程里跑，不受本测试进程里别的测试已导入模块的影响。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HEAVY = ("comtypes", "src.companion.cloud_link.ax_host", "src.companion.cloud_link.window",
         "src.companion.cloud_link.nonscriptable")

_CHILD = r"""
import json, sys, tempfile
from pathlib import Path

HEAVY = %r

def loaded():
    return sorted(m for m in sys.modules if m.split(".")[0] == "comtypes" or m in HEAVY)

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

# CC 启动、托盘、空闲取任务、对话页窗口与桥
import src.companion.main
import src.companion.gui_tray
import src.companion.gui_tray_workers
import src.companion.gui_tomo
import src.companion.window_main
import src.companion.worker
from src.companion import tomo_bridge

class Settings:
    def snapshot(self):
        return {}

bridge = tomo_bridge.TomoBridge(Settings(), allowed=lambda: True, window=lambda: None, set_lang=lambda lang: None)
bridge.state()
bridge._cloud_settings("cloud_status", {}, "zh")
bridge._cloud_settings("cloud_prefill", {}, "zh")
idle = loaded()

# 人点「连接」：起宿主（隧道盘、代发、密码都用假的），窗口用真的 CloudWindow + 假的控件宿主
from src.companion.cloud_link import host as host_mod
from src.companion.cloud_link.link_store import LinkStore
after_host_import = loaded()

class Protector:
    def protect(self, value):
        return "x:v1:" + value[::-1]
    def unprotect(self, value):
        return value.split(":", 2)[2][::-1]

class Drives:
    def __init__(self):
        self.maps = {}
    def used(self):
        return {"C"} | set(self.maps)
    def target(self, letter):
        return self.maps.get(letter)
    def add(self, letter, root):
        self.maps[letter] = "\\??\\" + str(root)
    def remove(self, letter):
        self.maps.pop(letter, None)

class Client:
    def close(self):
        pass

class Control:
    def setProperty(self, name, value):
        return True
    def querySubObject(self, name):
        return Control()
    def property(self, name):
        return 0
    def call(self, name, *args):
        return None
    def unknown_address(self):
        return 0

class FakeAxHost:
    def __init__(self, hwnd, width, height, *, router):
        self.control = Control()
    def resize(self, width, height):
        pass
    def close(self):
        self.control = None

tmp = Path(tempfile.mkdtemp())
store = LinkStore.load(tmp / "cloud_link.json", protector=Protector())
store.save_link(host="198.51.100.7", port=3390, user="CLOUDUSER-01", domain="example")

def window_factory(params, *, password_provider, nonscriptable, on_change, on_closed):
    from src.companion.cloud_link import window
    window.available = lambda: True
    win = window.CloudWindow(params, password_provider=password_provider, nonscriptable=nonscriptable,
                             on_change=on_change, host_factory=FakeAxHost)
    win.session._open = lambda: None   # 不真连
    return win

host = host_mod.CloudHost("198.51.100.7", links=lambda: store, base_dir=tmp / "tunnel", drives=Drives(),
                          window_factory=window_factory, available=lambda: True,
                          forward_factory=lambda: (Client(), lambda out: None),
                          password_provider=lambda h: (lambda: "pw"), run_thread=False)
host.start()
started = loaded()
host.stop()
print("RESULT " + json.dumps({"idle": idle, "after_host_import": after_host_import, "started": started}))
""" % (HEAVY,)


def test_cloud_window_modules_load_only_when_connecting(tmp_path):
    env = dict(os.environ)
    for key in ("PEARNLY_CC_CLOUD", "PEARNLY_CLOUD_VIA", "PEARNLY_COMPANION_PORT"):
        env.pop(key, None)
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), APPDATA=str(tmp_path / "Roaming"),
               LOCALAPPDATA=str(tmp_path / "Local"), XDG_CONFIG_HOME=str(tmp_path / "config"), PYTHONUTF8="1",
               QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, "-c", _CHILD], cwd=str(ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stdout + done.stderr
    result = json.loads(line[len("RESULT "):])
    assert result["idle"] == []                              # 启动、托盘、对话页、设置页那一行：都不导入
    assert result["after_host_import"] == []                 # 宿主模块本身也不带它们
    started = set(result["started"])
    assert {"src.companion.cloud_link.window", "src.companion.cloud_link.ax_host",
            "src.companion.cloud_link.nonscriptable"} <= started   # 点「连接」才导入
    if sys.platform != "win32":
        assert not any(m.split(".")[0] == "comtypes" for m in started)   # 假宿主：真 comtypes 只在 ax_host.Com 里
