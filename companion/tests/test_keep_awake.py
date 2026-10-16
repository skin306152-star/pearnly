"""CC 开着时保持电脑唤醒（keep_awake.py，用户 10-16「改成和 claude 一样的功能」，ADR-021 第 58 条）：
有成员连着才拿住、成员都断开 / 退出 / 关掉开关马上放开；用电池时按开关放开、插上电再拿起；现代待机另保持屏幕；从不锁屏。"""
import sys
import threading
import time
import types
from pathlib import Path

from src.companion import keep_awake
from src.companion.keep_awake import ES_CONTINUOUS, ES_DISPLAY_REQUIRED, ES_SYSTEM_REQUIRED, KeepAwake

SRC = Path(__file__).resolve().parents[1] / "src/companion"


class _Power:
    def __init__(self, *, battery=False, aoac=False):
        self.battery = battery
        self.aoac = aoac
        self.calls = []

    def set_state(self, flags):
        self.calls.append((threading.current_thread().name, flags))
        return True

    def on_battery(self):
        return self.battery

    def modern_standby(self):
        return self.aoac


def _until(check, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.005)
    return False


def test_holds_system_only_while_a_member_is_connected_and_lets_go_when_none_is():
    power = _Power()
    awake = KeepAwake(power, poll=0.01)
    awake.apply(enabled=True, on_battery=True, members=True)
    assert _until(lambda: awake.flags == ES_SYSTEM_REQUIRED)
    assert power.calls[0] == ("keep-awake", ES_CONTINUOUS | ES_SYSTEM_REQUIRED)   # 一般电脑：屏幕照常关
    awake.apply(enabled=True, on_battery=True, members=True)   # 再来一次不多起线程
    assert awake.active and len([c for c in power.calls if c[1] != ES_CONTINUOUS]) == 1
    awake.apply(enabled=True, on_battery=True, members=False)   # 成员都断开 / 都暂停
    assert not awake.active and awake.flags == 0
    assert power.calls[-1] == ("keep-awake", ES_CONTINUOUS)     # 同一条线程上放开：系统按原电源设置睡眠


def test_the_switch_off_and_quitting_let_go_at_once():
    power = _Power()
    awake = KeepAwake(power, poll=0.01)
    awake.apply(enabled=False, on_battery=True, members=True)
    assert not awake.active and power.calls == []
    awake.apply(enabled=True, on_battery=True, members=True)
    assert _until(lambda: awake.flags)
    awake.apply(enabled=False, on_battery=True, members=True)
    assert not awake.active and power.calls[-1][1] == ES_CONTINUOUS
    awake.apply(enabled=True, on_battery=True, members=True)
    assert _until(lambda: awake.flags)
    awake.stop()   # 退出 CC
    assert not awake.active and awake.flags == 0 and power.calls[-1][1] == ES_CONTINUOUS
    awake.stop()   # 再退一次无事


def test_on_battery_it_follows_the_battery_switch_and_picks_up_again_on_mains():
    power = _Power(battery=True)
    awake = KeepAwake(power, poll=0.01)
    awake.apply(enabled=True, on_battery=True, members=True)   # 「用电池时也保持」开着（默认）：用电池也拿住
    assert _until(lambda: awake.flags == ES_SYSTEM_REQUIRED)
    awake.apply(enabled=True, on_battery=False, members=True)  # 关掉：用电池就放开
    assert _until(lambda: awake.flags == 0)
    assert awake.active   # 线程还在，盯着电源
    assert power.calls[-1] == ("keep-awake", ES_CONTINUOUS)
    power.battery = False   # 插上电
    assert _until(lambda: awake.flags == ES_SYSTEM_REQUIRED)
    power.battery = True    # 又拔掉
    assert _until(lambda: awake.flags == 0)
    awake.stop()
    assert [flags for _name, flags in power.calls] == [
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS, ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS]
    assert {name for name, _flags in power.calls} == {"keep-awake"}


def test_modern_standby_also_keeps_the_display_as_verified_on_the_office_pc():
    # 2026-10-12 办公室测试机（现代待机）实测：只保持系统，关屏立即进待机；系统 + 屏幕才一直在线
    power = _Power(aoac=True)
    awake = KeepAwake(power, poll=0.01)
    awake.apply(enabled=True, on_battery=True, members=True)
    assert _until(lambda: awake.flags == ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    assert power.calls[0][1] == ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    awake.stop()
    assert power.calls[-1][1] == ES_CONTINUOUS


def test_nothing_happens_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    awake = KeepAwake()
    awake.apply(enabled=True, on_battery=True, members=True)
    assert not awake.active and awake.flags == 0


def test_windows_power_calls_the_verified_api_and_reads_aoac(monkeypatch):
    calls = []

    def set_state(flags):
        calls.append(("state", flags.value))
        return 1

    def power_status(ref):
        ref._obj.ACLineStatus = 0
        return 1

    def capabilities(ref):
        ref._obj[keep_awake._AOAC_OFFSET] = 1
        return 1

    import ctypes

    windll = types.SimpleNamespace(kernel32=types.SimpleNamespace(SetThreadExecutionState=set_state,
                                                                  GetSystemPowerStatus=power_status),
                                   powrprof=types.SimpleNamespace(GetPwrCapabilities=capabilities))
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    power = keep_awake.WindowsPower()
    assert power.set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) is True
    assert calls == [("state", ES_CONTINUOUS | ES_SYSTEM_REQUIRED)]
    assert power.on_battery() is True and power.modern_standby() is True
    # GetPwrCapabilities 返回 BOOLEAN（1 字节）：按 c_ubyte 读，不把 EAX 高位残值当成功
    assert windll.powrprof.GetPwrCapabilities.restype is ctypes.c_ubyte
    assert keep_awake._AOAC_OFFSET == 20   # winnt.h：15 个 BOOLEAN + 两个 BYTE + FastSystemS4、Hiberboot、WakeAlarmPresent 之后

    def broken(ref):
        raise OSError("no powrprof")

    windll.powrprof.GetPwrCapabilities = broken
    assert keep_awake.WindowsPower().modern_standby() is True   # 查不出按现代待机（验证过不睡的那一种）


def test_no_lock_screen_anywhere_and_the_tray_holds_and_lets_go_with_the_members():
    sources = {path.name: path.read_text(encoding="utf-8") for path in SRC.glob("*.py")}
    assert not any("LockWorkStation" in text or "lock_screen" in text for text in sources.values())
    assert "always_on.py" not in sources
    tray = sources["gui_tray.py"]
    apply = tray.split("def _apply_keep_awake(", 1)[1].split("\n    def ", 1)[0]
    assert "members=any(member_active(cfg) for cfg in self.configs)" in apply
    assert 'getattr(self, "_awake_settings", (True, True))' in apply
    reload = tray.split("def _reload_profiles(", 1)[1].split("\n    def ", 1)[0]
    assert "self._apply_keep_awake()" in reload   # 成员断开 / 暂停 / 新连上（配对、断开、被顶掉都走这里）
    assert "store.global_settings.keep_awake" in reload   # 重读配置时开关按文件里的值
    # 设置里改开关只记在托盘上、不改各成员配置（取任务线程的旧副本存盘不会把它盖回去，见 test_profiles）
    setter = tray.split("def _set_keep_awake(", 1)[1].split("\n    def ", 1)[0]
    assert "self._awake_settings = (bool(enabled), bool(on_battery))" in setter and ".update(" not in setter
    quit_ = tray.split("def _quit(", 1)[1].split("\n    def ", 1)[0]
    assert "self._keep_awake.stop()" in quit_
    assert "on_keep_awake=self._set_keep_awake" in tray
    bridge = sources["tomo_bridge.py"]
    assert 'method == "keep_awake"' in bridge and 'method == "keep_awake_on_battery"' in bridge
