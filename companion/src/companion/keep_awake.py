# -*- coding: utf-8 -*-
"""CC 开着时保持电脑唤醒（用户 2026-10-16：「改成和 claude 一样的功能」，ADR-021 第 58 条）。

CC 的活大多从外面来（手机、LINE、夜里的定时任务），所以只要 CC 桌面版在运行、至少有一个成员连着（配对有效：没暂停、
密钥没被服务端说不能再用，``config.member_active``），就不让系统**空闲睡眠**；没有成员、退出 CC、关掉开关时马上放开。
两个开关都在设置「此电脑 → 系统」，同 Claude 桌面版的「Keep computer awake」「Keep awake on battery power」：

- ``keep_awake``：CC 开着时保持电脑唤醒（默认开）；
- ``keep_awake_on_battery``：用电池时也保持（默认关，用户 10-18 定，D8：测试机用电池约 3 小时 68%→21%，期间 CC 一直拿着
  DISPLAY + SYSTEM 不让睡）。关着时一换成电池就放开、插上电按插电的设置再拿起：订阅系统的交流 / 直流切换通知
  （``PowerSettingRegisterNotification(GUID_ACDC_POWER_SOURCE)``，回调只叫醒守护线程），另每 15 秒查一次
  ``GetSystemPowerStatus`` 兜底（订阅不上时就靠它）；开关与成员一变马上重算。

做法沿用 1.4.2–1.6 的常驻模式（``always_on.py``，10-16 删、提交 ecabec82 之前）在办公室测试机上验证过的那一种：
``SetThreadExecutionState(ES_CONTINUOUS | …)`` 挂在一条专用守护线程上（Windows 按调用线程记这个状态，线程在、状态就在；
放开时在同一条线程上只留 ``ES_CONTINUOUS``，进程退出时系统也自动收回）。不改电源计划、不改系统设置、不改合盖动作——
按电源键、合上笔记本盖子照样睡。**不锁屏**（Claude 没有，常驻模式的开机锁屏随之不再有）。

关屏：

- 一般电脑（S3 睡眠）：只要 ``ES_SYSTEM_REQUIRED``，屏幕照常按电源设置关掉，系统不睡；
- 现代待机（S0 低功耗待机，``GetPwrCapabilities`` 的 ``AoAc``）：屏幕一关就进待机，只保持系统拦不住。2026-10-12 在办公室
  测试机（现代待机）实测：只保持系统，关屏立即进入待机；保持「系统 + 屏幕」（``ES_DISPLAY_REQUIRED``），4 分钟以上一直在线、
  系统日志无待机记录。所以这种电脑另加 ``ES_DISPLAY_REQUIRED``——屏幕不会自动关（显示器自己的电源键照样能关）。
  查不出是哪一种时按现代待机处理（验证过不睡的那一种）。

非 Windows（开发机）什么都不做。系统调用都在 ``WindowsPower`` 里，测试换成假的。
"""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import Optional, Protocol

from .logger import get_logger

log = get_logger(__name__)

# winbase.h
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
DEVICE_NOTIFY_CALLBACK = 2   # winuser.h：Recipient 是 DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS，不要窗口

POLL_SECONDS = 15.0          # 电源（插电 / 电池）多久查一次
_AOAC_OFFSET = 20            # SYSTEM_POWER_CAPABILITIES.AoAc（winnt.h：15 个 BOOLEAN + 两个 BYTE + 3 个 BOOLEAN 之后）


class Power(Protocol):
    def set_state(self, flags: int) -> bool: ...
    def on_battery(self) -> bool: ...
    def modern_standby(self) -> bool: ...
    # 可选：电源（插电 / 电池）一变就叫 ``notify``；回一个取消订阅的函数，订阅不上回 None（只靠轮询）
    # def watch(self, notify: Callable[[], None]) -> Optional[Callable[[], None]]: ...


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


class _Guid(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


# GUID_ACDC_POWER_SOURCE {5D3E9A59-E9D5-4B00-A6BD-FF34FF516548}（winnt.h）：交流 / 直流（电池）/ 短时电源切换
GUID_ACDC_POWER_SOURCE = (0x5D3E9A59, 0xE9D5, 0x4B00, (0xA6, 0xBD, 0xFF, 0x34, 0xFF, 0x51, 0x65, 0x48))

if sys.platform == "win32":
    _NOTIFY_CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p)
else:   # 只为能 import；非 Windows 不订阅
    _NOTIFY_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p)


class _SubscribeParameters(ctypes.Structure):   # DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS
    _fields_ = [("Callback", _NOTIFY_CALLBACK), ("Context", ctypes.c_void_p)]


class WindowsPower:
    """Windows 的四样：设线程执行状态、是不是在用电池、是不是现代待机（只查一次）、订阅插电 / 电池切换。"""

    def __init__(self) -> None:
        self._aoac: Optional[bool] = None

    def set_state(self, flags: int) -> bool:
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags)))

    def on_battery(self) -> bool:
        status = _PowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return False
        return status.ACLineStatus == 0   # 0 = 用电池，1 = 插着电，255 = 不知道（按插着电）

    def modern_standby(self) -> bool:
        if self._aoac is None:
            try:
                caps = (ctypes.c_ubyte * 256)()
                query = ctypes.windll.powrprof.GetPwrCapabilities
                # 返回 BOOLEAN（1 字节）：不设 restype 时 ctypes 按 int 读整个 EAX，失败时高位残值会被当成成功、
                # 读到全零的 caps，现代待机被当成 S3，只保持系统、关屏立即待机（10-16 审查）
                query.restype = ctypes.c_ubyte
                if not query(ctypes.byref(caps)):
                    raise OSError("GetPwrCapabilities")
                self._aoac = bool(caps[_AOAC_OFFSET])
            except Exception:
                log.warning("查不出是不是现代待机，按现代待机保持（系统 + 屏幕）", exc_info=True)
                self._aoac = True
            log.info("这台电脑%s现代待机", "是" if self._aoac else "不是")
        return self._aoac

    def watch(self, notify):
        """订阅交流 / 直流切换（Windows 8 起的 ``powrprof.PowerSettingRegisterNotification``，回调形式不要窗口）。
        回调在系统线程上，只叫 ``notify``（叫醒守护线程，由它重新查电源）；订阅时系统会先按当前状态回调一次，无妨。
        订阅不上回 None（只靠 15 秒轮询）。回的函数取消订阅；回调对象与参数结构挂在它身上，取消前不会被回收。"""
        try:
            def _callback(_context, _type, _setting):
                try:
                    notify()
                except Exception:   # 不让异常穿回系统线程
                    pass
                return 0

            callback = _NOTIFY_CALLBACK(_callback)
            params = _SubscribeParameters(callback, None)
            d1, d2, d3, d4 = GUID_ACDC_POWER_SOURCE
            guid = _Guid(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))
            handle = ctypes.c_void_p()
            powrprof = ctypes.windll.powrprof
            register = powrprof.PowerSettingRegisterNotification
            register.argtypes = [ctypes.POINTER(_Guid), ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            register.restype = ctypes.c_ulong
            unregister = powrprof.PowerSettingUnregisterNotification
            unregister.argtypes = [ctypes.c_void_p]
            unregister.restype = ctypes.c_ulong
            error = register(ctypes.byref(guid), DEVICE_NOTIFY_CALLBACK, ctypes.c_void_p(ctypes.addressof(params)),
                             ctypes.byref(handle))
            if error or not handle.value:
                log.warning("订阅插电 / 电池切换失败（%s），只按 15 秒轮询", error)
                return None
        except Exception:
            log.warning("订阅插电 / 电池切换失败，只按 15 秒轮询", exc_info=True)
            return None

        def _unwatch() -> None:
            try:
                unregister(handle)
            except Exception:
                log.warning("取消订阅插电 / 电池切换失败（忽略）", exc_info=True)

        _unwatch.keep = (callback, params, guid)   # type: ignore[attr-defined]
        return _unwatch


class KeepAwake:
    """（界面线程调 ``apply`` / ``stop``）要保持时起一条专用守护线程拿住状态，不用保持时放开并让线程退出。"""

    def __init__(self, power: Optional[Power] = None, *, poll: float = POLL_SECONDS) -> None:
        if power is None and sys.platform == "win32":
            power = WindowsPower()
        self._power = power
        self._poll = poll
        self._on_battery_too = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop: Optional[threading.Event] = None
        self._wake: Optional[threading.Event] = None
        self.flags = 0   # 此刻拿着的（0 = 没拿）；日志与测试看

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def apply(self, *, enabled: bool, on_battery: bool, members: bool) -> None:
        """开关或成员变了：两样都满足才拿住（用电池时再按 ``on_battery``），否则马上放开。"""
        if self._power is None:
            return
        with self._lock:
            self._on_battery_too = bool(on_battery)
            if enabled and members:
                if self.active:
                    assert self._wake is not None
                    self._wake.set()   # 电池开关变了：马上重算
                    return
                self._stop, self._wake = threading.Event(), threading.Event()
                self._thread = threading.Thread(target=self._hold, args=(self._stop, self._wake),
                                                name="keep-awake", daemon=True)
                self._thread.start()
                return
            self._release_locked()

    def stop(self) -> None:
        """退出 CC：放开（进程退出时系统本来也会收回，这里先放）。"""
        with self._lock:
            self._release_locked()

    def _release_locked(self) -> None:
        if self._thread is None:
            return
        assert self._stop is not None and self._wake is not None
        self._stop.set()
        self._wake.set()
        self._thread.join(5)
        self._thread = self._stop = self._wake = None

    def wanted(self) -> int:
        """此刻该拿的状态：用电池且没开「用电池时也保持」→ 0；现代待机另加屏幕。"""
        assert self._power is not None
        if not self._on_battery_too and self._power.on_battery():
            return 0
        flags = ES_SYSTEM_REQUIRED
        if self._power.modern_standby():
            flags |= ES_DISPLAY_REQUIRED
        return flags

    def _hold(self, stop: threading.Event, wake: threading.Event) -> None:
        assert self._power is not None
        watch = getattr(self._power, "watch", None)
        unwatch = None
        if watch is not None:
            try:
                unwatch = watch(wake.set)   # 插电 / 电池一变马上重算（用电池且没开「用电池时也保持」就放开）
            except Exception:
                log.warning("订阅插电 / 电池切换失败，只按 15 秒轮询", exc_info=True)
        try:
            while not stop.is_set():
                try:
                    want = self.wanted()
                except Exception:
                    log.warning("查电源状态失败，照常保持", exc_info=True)
                    want = self.flags or ES_SYSTEM_REQUIRED
                if want != self.flags:
                    self._set(want)
                wake.wait(self._poll)
                wake.clear()
        finally:
            if unwatch is not None:
                unwatch()
            if self.flags:
                self._set(0)

    def _set(self, flags: int) -> None:
        assert self._power is not None
        ok = self._power.set_state(ES_CONTINUOUS | flags)
        if not ok:
            log.warning("保持电脑唤醒：SetThreadExecutionState 失败（0x%x）", flags)
            return
        self.flags = flags
        if flags:
            log.info("保持电脑唤醒：已拿住（%s）", "系统 + 屏幕" if flags & ES_DISPLAY_REQUIRED else "系统")
        else:
            log.info("保持电脑唤醒：已放开，系统按原电源设置睡眠")
