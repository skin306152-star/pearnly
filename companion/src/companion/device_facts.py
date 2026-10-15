# -*- coding: utf-8 -*-
"""这台电脑与它够得到的账套目录的身份（多台电脑，ADR-021，方案 2.3；CC 桌面版 1.7.0 起上报）。

服务端用它们认「几台电脑看到的是不是同一份物理账套」（`targets.identity.resolve`），全部只读、不写任何文件：

- ``uid``：账套目录的卷序列号 + 64 位文件号（``GetFileInformationByHandle``，只用 ``FILE_READ_ATTRIBUTES`` +
  ``FILE_FLAG_BACKUP_SEMANTICS`` 打开目录）。第 0 批真机核过：本机盘、``\\\\localhost``、``\\\\主机名``、``\\\\IP``、
  非持久映射看到的同一目录这一对数完全一致；NAS 与 Windows 服务器共享上非零且稳定。**不用** ``FileIdInfo``（128 位）：
  NAS（错误 87）与服务器共享（错误 124）上都取不到。文件号为 0 当取不到（报空，服务端按位置 / 路径认）。
- ``share``：网络路径。映射盘用 ``WNetGetConnectionW`` 换算（持久、非持久映射都认）；本机已共享的目录按
  ``HKLM\\SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Shares``（普通用户可读）换成 ``\\\\本机名\\共享名\\…``；
  本来就是网络路径的原样；其余（只在本机）报空。
- ``host_names``：本机名（第一个，服务端把它当这台电脑的规范主机名）、NetBIOS 名、DNS 全名，加上**有默认网关**的网卡的
  IPv4 地址。WSL / Hyper-V 的 vEthernet、Tailscale 这类虚拟网卡没有默认网关，不报（它们的网段在各台电脑上会重复）；
  回环、隧道、拨号连接也不报。

Windows 接口都在 ``WinApi`` 里（只在 Windows 上建），别的系统上一律报空；测试换成假的。
"""
from __future__ import annotations

import ctypes
import ntpath
import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

from .logger import get_logger

log = get_logger(__name__)

MAX_HOST_NAMES = 32   # 与服务端 device_contracts.MAX_HOST_NAMES 相同
MAX_HOST_NAME = 255
# 虚拟网卡的名字里常见的几个词：有默认网关也不报（VPN 全隧道会带网关；WSL 镜像网络模式下的虚拟网卡同理）。
VIRTUAL_WORDS = ("wsl", "tailscale", "zerotier", "wireguard")
_IF_LOOPBACK, _IF_PPP, _IF_TUNNEL = 24, 23, 131
_OPER_UP = 1


@dataclass(frozen=True)
class Adapter:
    name: str
    description: str
    if_type: int
    up: bool
    ipv4: tuple = ()
    gateways: tuple = ()


class WinApi(Protocol):
    def file_index(self, path: str) -> Optional[tuple]: ...          # (卷序列号, 64 位文件号)；打不开 None
    def net_connection(self, drive: str) -> str: ...                # "S:" → "\\\\server\\share"；不是映射盘 ""
    def local_shares(self) -> dict: ...                             # {共享名: 本机路径}
    def adapters(self) -> list: ...                                 # [Adapter]
    def computer_names(self) -> list: ...                           # [NetBIOS 名, DNS 全名]


# ── 纯规则（不碰系统，测试直接调） ────────────────────────────────────────────────

def uid_of(index: Optional[tuple]) -> str:
    """(卷序列号, 64 位文件号) → ``"卷序列号-文件号"``（十六进制小写，与服务端 ``identity.clean_uid`` 同一字符集）；
    文件号为 0 或取不到 → 空串。"""
    if not index:
        return ""
    serial, number = index
    if not number:
        return ""
    return f"{int(serial) & 0xFFFFFFFF:08x}-{int(number) & 0xFFFFFFFFFFFFFFFF:016x}"


def _windows_path(path: str) -> str:
    text = str(path or "").strip().replace("/", "\\")
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return ntpath.normpath(text) if text else ""


def _inside(path: str, folder: str) -> Optional[str]:
    """path 在 folder 里（含本身）就回剩下那段（开头带反斜杠或为空），否则 None。不分大小写。"""
    left, right = ntpath.normcase(path).rstrip("\\"), ntpath.normcase(_windows_path(folder)).rstrip("\\")
    if not right:
        return None
    if left == right:
        return ""
    if left.startswith(right + "\\"):
        return path.rstrip("\\")[len(right):]
    return None


def share_of(path: str, *, drive_remote: Optional[str], shares: dict, host: str) -> str:
    """一个本机路径的网络写法（规则见文件头）。``drive_remote``：它的盘符映射到的网络路径（不是映射盘为空 / None）；
    ``shares``：本机共享表 ``{共享名: 本机路径}``；``host``：本机名。"""
    text = _windows_path(path)
    if not text:
        return ""
    if text.startswith("\\\\"):
        return text
    if len(text) < 2 or text[1] != ":":
        return ""
    if drive_remote and str(drive_remote).startswith("\\\\"):
        return ntpath.normpath(str(drive_remote).rstrip("\\") + text[2:])
    best = None
    for name, local in (shares or {}).items():
        rest = _inside(text, str(local or ""))
        if rest is None or not str(name or "").strip():
            continue
        depth = len(_windows_path(local).rstrip("\\"))
        if best is None or depth > best[0] or (depth == best[0] and str(name).lower() < best[1].lower()):
            best = (depth, str(name), rest)
    if best is None or not host:
        return ""
    return "\\\\" + host + "\\" + best[1] + best[2]


def pick_host_names(hostname: str, others: Iterable[str], adapters: Iterable[Adapter]) -> list:
    """本机名在前，其余名字、有默认网关的物理网卡 IPv4 依次接上；去重（不分大小写）、限条数与长度。"""
    names: list = []

    def add(value) -> None:
        text = str(value or "").strip()
        if not text or len(text) > MAX_HOST_NAME or "\x00" in text or len(names) >= MAX_HOST_NAMES:
            return
        if text.lower() in {item.lower() for item in names}:
            return
        names.append(text)

    add(hostname)
    for other in others or ():
        add(other)
    for adapter in adapters or ():
        label = f"{adapter.name} {adapter.description}".lower()
        if (not adapter.up or adapter.if_type in (_IF_LOOPBACK, _IF_PPP, _IF_TUNNEL)
                or any(word in label for word in VIRTUAL_WORDS)):
            continue
        if not any(gateway and gateway != "0.0.0.0" for gateway in adapter.gateways):
            continue
        for address in adapter.ipv4:
            if address and not address.startswith(("127.", "169.254.")) and address != "0.0.0.0":
                add(address)
    return names


# ── 用起来的几样（带缓存） ────────────────────────────────────────────────────────

def hostname() -> str:
    try:
        return (socket.gethostname() or "").strip()
    except Exception:
        return ""


_api_lock = threading.Lock()
_api: Optional[WinApi] = None
_api_tried = False


def default_api() -> Optional[WinApi]:
    """本机的 Windows 接口；不是 Windows（或建不起来）回 None。"""
    global _api, _api_tried
    with _api_lock:
        if not _api_tried:
            _api_tried = True
            if sys.platform == "win32":
                try:
                    _api = _Win32()
                except Exception:
                    log.exception("Windows 接口初始化失败：不报文件号与网络路径")
                    _api = None
        return _api


def file_uid(path: str, api: Optional[WinApi] = None) -> str:
    """账套目录的文件号（规则见文件头）；取不到回空串，从不抛。"""
    api = api if api is not None else default_api()
    if api is None or not str(path or "").strip():
        return ""
    try:
        return uid_of(api.file_index(_windows_path(path)))
    except Exception:
        log.debug("读目录文件号失败: %s", path, exc_info=True)
        return ""


class Locator:
    """一次扫描里换算网络路径用：本机共享表读一次，每个盘符的映射问一次。"""

    def __init__(self, api: Optional[WinApi] = None, host: Optional[str] = None) -> None:
        self._api = api if api is not None else default_api()
        self._host = hostname() if host is None else host
        self._shares: Optional[dict] = None
        self._drives: dict = {}

    def share(self, path: str) -> str:
        if self._api is None:
            text = _windows_path(path)
            return text if text.startswith("\\\\") else ""
        try:
            text = _windows_path(path)
            drive = text[:2].upper() if len(text) >= 2 and text[1] == ":" else ""
            remote = ""
            if drive:
                if drive not in self._drives:
                    self._drives[drive] = str(self._api.net_connection(drive) or "")
                remote = self._drives[drive]
            if self._shares is None and drive and not remote:
                self._shares = dict(self._api.local_shares() or {})
            return share_of(text, drive_remote=remote, shares=self._shares or {}, host=self._host)
        except Exception:
            log.debug("换算网络路径失败: %s", path, exc_info=True)
            return ""

    def uid(self, path: str) -> str:
        return file_uid(path, self._api) if self._api is not None else ""


def share_path(path: str, api: Optional[WinApi] = None) -> str:
    return Locator(api).share(path)


_names_lock = threading.Lock()
_names_cache: dict = {}
NAMES_SECONDS = 600.0   # 网卡地址最多这么久重查一次（DHCP 换了地址，下一次认电脑时报新的）


def host_names(api: Optional[WinApi] = None, *, now: Optional[float] = None) -> list:
    """这台电脑的名字与有默认网关的网卡地址（规则见文件头），十分钟内重用上一次的结果。"""
    api = api if api is not None else default_api()
    moment = time.monotonic() if now is None else now
    with _names_lock:
        cached = _names_cache.get(id(api))
        if cached and moment - cached[0] < NAMES_SECONDS:
            return list(cached[1])
    others, adapters = [], []
    if api is not None:
        try:
            others = list(api.computer_names() or [])
        except Exception:
            log.debug("读电脑名失败", exc_info=True)
        try:
            adapters = list(api.adapters() or [])
        except Exception:
            log.debug("读网卡失败", exc_info=True)
    names = pick_host_names(hostname(), others, adapters)
    with _names_lock:
        _names_cache[id(api)] = (moment, list(names))
    return names


_uid_lock = threading.Lock()
_uid_cache: dict = {}
UID_SECONDS = 600.0


def cached_uid(path: str, api: Optional[WinApi] = None, *, now: Optional[float] = None) -> str:
    """领取请求的 ``index_pending`` 用：同一个目录十分钟内不重复打开。"""
    key = ntpath.normcase(_windows_path(path))
    moment = time.monotonic() if now is None else now
    with _uid_lock:
        cached = _uid_cache.get(key)
        if cached and moment - cached[0] < UID_SECONDS:
            return cached[1]
    value = file_uid(path, api)
    with _uid_lock:
        _uid_cache[key] = (moment, value)
    return value


def _reset_for_tests() -> None:
    global _api, _api_tried
    with _api_lock:
        _api, _api_tried = None, False
    with _names_lock:
        _names_cache.clear()
    with _uid_lock:
        _uid_cache.clear()


# ── Windows 接口（ctypes；结构体按 x64 布局，ULONG / DWORD 一律 32 位） ─────────────────

_u32 = ctypes.c_uint32


class FILETIME(ctypes.Structure):
    _fields_ = [("low", _u32), ("high", _u32)]


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [("dwFileAttributes", _u32), ("ftCreationTime", FILETIME), ("ftLastAccessTime", FILETIME),
                ("ftLastWriteTime", FILETIME), ("dwVolumeSerialNumber", _u32), ("nFileSizeHigh", _u32),
                ("nFileSizeLow", _u32), ("nNumberOfLinks", _u32), ("nFileIndexHigh", _u32), ("nFileIndexLow", _u32)]


def index_of(info: BY_HANDLE_FILE_INFORMATION) -> tuple:
    return int(info.dwVolumeSerialNumber), (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)


class SOCKADDR(ctypes.Structure):
    _fields_ = [("sa_family", ctypes.c_ushort), ("sa_data", ctypes.c_ubyte * 14)]


class SOCKADDR_IN(ctypes.Structure):
    _fields_ = [("sin_family", ctypes.c_ushort), ("sin_port", ctypes.c_ushort), ("sin_addr", ctypes.c_ubyte * 4),
                ("sin_zero", ctypes.c_ubyte * 8)]


class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.POINTER(SOCKADDR)), ("iSockaddrLength", ctypes.c_int)]


class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_UNICAST_ADDRESS._fields_ = [("Length", _u32), ("Flags", _u32),
                                       ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
                                       ("Address", SOCKET_ADDRESS)]


class IP_ADAPTER_GATEWAY_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_GATEWAY_ADDRESS._fields_ = [("Length", _u32), ("Reserved", _u32),
                                       ("Next", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
                                       ("Address", SOCKET_ADDRESS)]


class IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


# 只到 FirstGatewayAddress（后面的字段不读；结构体由系统分配，这里只按指针读前面这一段）
IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", _u32), ("IfIndex", _u32),
    ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", _u32),
    ("Flags", _u32),
    ("Mtu", _u32),
    ("IfType", _u32),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", _u32),
    ("ZoneIndices", _u32 * 16),
    ("FirstPrefix", ctypes.c_void_p),
    ("TransmitLinkSpeed", ctypes.c_uint64),
    ("ReceiveLinkSpeed", ctypes.c_uint64),
    ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.POINTER(IP_ADAPTER_GATEWAY_ADDRESS)),
]

_AF_INET = 2


def _ipv4(address: SOCKET_ADDRESS) -> str:
    if not address.lpSockaddr:
        return ""
    if address.lpSockaddr.contents.sa_family != _AF_INET:
        return ""
    raw = ctypes.cast(address.lpSockaddr, ctypes.POINTER(SOCKADDR_IN)).contents.sin_addr
    return ".".join(str(part) for part in raw)


def _chain(first) -> list:
    found, item, guard = [], first, 0
    while item and guard < 64:
        found.append(item.contents)
        item = item.contents.Next
        guard += 1
    return found


def parse_adapters(first) -> list:
    """``GetAdaptersAddresses`` 的链表 → [Adapter]（测试里用自己拼的链表调）。"""
    adapters = []
    for row in _chain(first):
        adapters.append(Adapter(
            name=str(row.FriendlyName or ""), description=str(row.Description or ""), if_type=int(row.IfType),
            up=int(row.OperStatus) == _OPER_UP,
            ipv4=tuple(value for value in (_ipv4(item.Address) for item in _chain(row.FirstUnicastAddress)) if value),
            gateways=tuple(value for value in (_ipv4(item.Address) for item in _chain(row.FirstGatewayAddress))
                           if value)))
    return adapters


class _Win32:
    """真的 Windows 接口（只在 Windows 上建）。"""

    _FILE_READ_ATTRIBUTES = 0x0080
    _SHARE_ALL = 0x1 | 0x2 | 0x4
    _OPEN_EXISTING = 3
    _BACKUP_SEMANTICS = 0x02000000
    _GAA_FLAGS = 0x2 | 0x4 | 0x8 | 0x80   # 不要任播 / 组播 / DNS，要网关
    _ERROR_BUFFER_OVERFLOW = 111
    _ERROR_MORE_DATA = 234

    def __init__(self) -> None:
        from ctypes import wintypes

        self._w = wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._create = kernel32.CreateFileW
        self._create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self._create.restype = wintypes.HANDLE
        self._info = kernel32.GetFileInformationByHandle
        self._info.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
        self._info.restype = wintypes.BOOL
        self._close = kernel32.CloseHandle
        self._close.argtypes = [wintypes.HANDLE]
        self._close.restype = wintypes.BOOL
        self._name_ex = kernel32.GetComputerNameExW
        self._name_ex.argtypes = [ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self._name_ex.restype = wintypes.BOOL
        mpr = ctypes.WinDLL("mpr", use_last_error=True)
        self._wnet = mpr.WNetGetConnectionW
        self._wnet.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self._wnet.restype = wintypes.DWORD
        iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self._gaa = iphlpapi.GetAdaptersAddresses
        self._gaa.argtypes = [wintypes.ULONG, wintypes.ULONG, wintypes.LPVOID, ctypes.c_void_p,
                              ctypes.POINTER(wintypes.ULONG)]
        self._gaa.restype = wintypes.ULONG

    def file_index(self, path: str) -> Optional[tuple]:
        invalid = ctypes.cast(-1, self._w.HANDLE).value
        handle = self._create(path, self._FILE_READ_ATTRIBUTES, self._SHARE_ALL, None, self._OPEN_EXISTING,
                              self._BACKUP_SEMANTICS, None)
        if handle in (None, invalid):
            return None
        try:
            info = BY_HANDLE_FILE_INFORMATION()
            if not self._info(handle, ctypes.byref(info)):
                return None
            return index_of(info)
        finally:
            self._close(handle)

    def net_connection(self, drive: str) -> str:
        size = self._w.DWORD(1024)
        for _ in range(3):
            buffer = ctypes.create_unicode_buffer(size.value)
            code = self._wnet(drive, buffer, ctypes.byref(size))
            if code == 0:
                return buffer.value
            if code != self._ERROR_MORE_DATA:
                return ""   # 不是映射盘（ERROR_NOT_CONNECTED 等）
        return ""

    def local_shares(self) -> dict:
        import winreg

        found = {}
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Services\LanmanServer\Shares") as key:
                index = 0
                while True:
                    try:
                        name, data, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    lines = data if isinstance(data, list) else str(data or "").split("\x00")
                    path = next((line[5:] for line in lines if str(line).lower().startswith("path=")), "")
                    if name and path:
                        found[str(name)] = str(path)
        except OSError:
            return {}
        return found

    def adapters(self) -> list:
        size = self._w.ULONG(16 * 1024)
        for _ in range(4):
            buffer = ctypes.create_string_buffer(size.value)
            code = self._gaa(_AF_INET, self._GAA_FLAGS, None, buffer, ctypes.byref(size))
            if code == 0:
                return parse_adapters(ctypes.cast(buffer, ctypes.POINTER(IP_ADAPTER_ADDRESSES)))
            if code != self._ERROR_BUFFER_OVERFLOW:
                return []
        return []

    def computer_names(self) -> list:
        names = []
        for kind in (0, 3):   # ComputerNameNetBIOS、ComputerNameDnsFullyQualified
            size = self._w.DWORD(0)
            self._name_ex(kind, None, ctypes.byref(size))
            if not size.value:
                continue
            buffer = ctypes.create_unicode_buffer(size.value + 1)
            if self._name_ex(kind, buffer, ctypes.byref(size)) and buffer.value:
                names.append(buffer.value)
        return names


__all__ = ["Adapter", "Locator", "WinApi", "cached_uid", "default_api", "file_uid", "host_names", "hostname",
           "parse_adapters", "pick_host_names", "share_of", "share_path", "uid_of"]
