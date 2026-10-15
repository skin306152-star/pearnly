# -*- coding: utf-8 -*-
"""这台电脑与账套目录的身份（多台电脑，ADR-021，CC 桌面版 1.7.0）：文件号、网络路径、主机名。

规则在别的系统上用假的 Windows 接口测；ctypes 结构体按 x64 布局核偏移，在 macOS 上拼一条假的网卡链表交给解析；
真的 Windows 接口只在 Windows（CI 的 Companion checks）上跑。"""
import ctypes
import os
import socket
import sys

import pytest

from src.companion import device_facts as df


@pytest.fixture(autouse=True)
def _fresh():
    df._reset_for_tests()
    yield
    df._reset_for_tests()


class FakeApi:
    def __init__(self, *, index=None, drives=None, shares=None, adapters=(), names=()):
        self.index = index or {}
        self.drives = drives or {}
        self.shares = shares or {}
        self._adapters = list(adapters)
        self._names = list(names)
        self.calls = []

    def file_index(self, path):
        self.calls.append(("index", path))
        value = self.index.get(path)
        if isinstance(value, Exception):
            raise value
        return value

    def net_connection(self, drive):
        self.calls.append(("wnet", drive))
        return self.drives.get(drive, "")

    def local_shares(self):
        self.calls.append(("shares",))
        return dict(self.shares)

    def adapters(self):
        self.calls.append(("adapters",))
        return list(self._adapters)

    def computer_names(self):
        return list(self._names)


# ── 文件号 ──

def test_the_uid_is_the_volume_serial_and_the_64_bit_index_in_lowercase_hex():
    assert df.uid_of((0xC8D5876C, 0x0001_0000_0000_2A3B)) == "c8d5876c-0001000000002a3b"
    assert df.uid_of((0x0C9A53F6, 0x5)) == "0c9a53f6-0000000000000005"   # NAS：高 32 位为 0 照常
    assert df.uid_of((0x1, 0)) == ""       # 文件号为 0：取不到
    assert df.uid_of(None) == ""


def test_the_uid_fits_the_servers_cleaning_rule():
    value = df.uid_of((0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF))
    assert value == value.lower() and len(value) <= 80 and all(ch.isalnum() or ch in ":-_." for ch in value)


def test_the_file_information_is_read_from_the_by_handle_structure():
    assert ctypes.sizeof(df.BY_HANDLE_FILE_INFORMATION) == 52
    info = df.BY_HANDLE_FILE_INFORMATION()
    info.dwVolumeSerialNumber, info.nFileIndexHigh, info.nFileIndexLow = 0xC8D5876C, 0x10, 0x2A3B
    assert df.index_of(info) == (0xC8D5876C, (0x10 << 32) | 0x2A3B)


def test_file_uid_opens_the_folder_read_only_and_never_raises():
    api = FakeApi(index={"S:\\2569\\EXP69\\ACME": (0xAB, 0x12), "S:\\BROKEN": OSError("denied")})
    assert df.file_uid("S:/2569/EXP69/ACME/", api) == "000000ab-0000000000000012"
    assert df.file_uid("S:\\BROKEN", api) == ""
    assert df.file_uid("", api) == ""
    assert df.file_uid("S:\\GONE", api) == ""


def test_off_windows_there_is_no_uid_or_local_share(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert df.default_api() is None
    assert df.file_uid("C:\\EXP\\ACME") == ""
    assert df.Locator().share("C:\\EXP\\ACME") == ""
    assert df.Locator().share("\\\\nas\\pas212\\EXP\\ACME") == "\\\\nas\\pas212\\EXP\\ACME"


def test_cached_uid_opens_a_folder_once_in_ten_minutes():
    api = FakeApi(index={"C:\\EXP\\A": (1, 2)})
    assert df.cached_uid("C:\\EXP\\A", api, now=0) == df.cached_uid("c:/exp/a", api, now=599)
    assert len([c for c in api.calls if c[0] == "index"]) == 1
    df.cached_uid("C:\\EXP\\A", api, now=601)
    assert len([c for c in api.calls if c[0] == "index"]) == 2


# ── 网络路径 ──

def test_a_mapped_drive_becomes_its_network_path_persistent_or_not():
    # 第 0 批：非持久映射 Q: → \\localhost\PearnlyExpressRO 与持久映射 S: → \\192.168.0.212\pas212 都由 WNetGetConnectionW 给出
    assert df.share_of("Q:\\2569\\ACME", drive_remote="\\\\localhost\\PearnlyExpressRO", shares={}, host="PC1") \
        == "\\\\localhost\\PearnlyExpressRO\\2569\\ACME"
    assert df.share_of("s:/2558/EXP58/", drive_remote="\\\\192.168.0.212\\pas212\\", shares={}, host="PC1") \
        == "\\\\192.168.0.212\\pas212\\2558\\EXP58"


def test_a_local_folder_under_a_share_becomes_host_share_path():
    shares = {"PearnlyExpressRO": "C:\\PearnlyExpress", "Data": "C:\\", "Deep": "C:\\PearnlyExpress\\2569"}
    assert df.share_of("C:\\PearnlyExpress\\2569\\ACME", drive_remote="", shares=shares, host="DESKTOP-MN2BRKR") \
        == "\\\\DESKTOP-MN2BRKR\\Deep\\ACME"   # 最深的那个共享
    assert df.share_of("c:\\pearnlyexpress\\2568", drive_remote="", shares=shares, host="H") == "\\\\H\\PearnlyExpressRO\\2568"
    assert df.share_of("C:\\Other\\X", drive_remote="", shares=shares, host="H") == "\\\\H\\Data\\Other\\X"
    assert df.share_of("C:\\PearnlyExpress", drive_remote="", shares={"E": "C:\\PearnlyExpress"}, host="H") == "\\\\H\\E"


def test_a_local_folder_nobody_shares_has_no_network_path():
    assert df.share_of("D:\\EXP\\ACME", drive_remote="", shares={"Data": "C:\\Data"}, host="H") == ""
    assert df.share_of("C:\\Data2\\X", drive_remote="", shares={"Data": "C:\\Data"}, host="H") == ""
    assert df.share_of("relative\\path", drive_remote="", shares={}, host="H") == ""
    assert df.share_of("", drive_remote="", shares={}, host="H") == ""


def test_unc_paths_stay_as_they_are_including_long_path_prefixes():
    assert df.share_of("\\\\Accserver\\d$\\EXP\\ACME", drive_remote=None, shares={}, host="H") == \
        "\\\\Accserver\\d$\\EXP\\ACME"
    assert df.share_of("\\\\?\\UNC\\nas\\pas212\\X", drive_remote=None, shares={}, host="H") == "\\\\nas\\pas212\\X"
    assert df.share_of("\\\\?\\C:\\Shared\\X", drive_remote="", shares={"S": "C:\\Shared"}, host="H") == "\\\\H\\S\\X"


def test_the_locator_asks_each_drive_once_and_reads_the_share_table_only_for_local_drives():
    api = FakeApi(drives={"S:": "\\\\nas\\pas212"}, shares={"Exp": "C:\\EXP"})
    finder = df.Locator(api, host="PC1")
    assert finder.share("S:\\A") == "\\\\nas\\pas212\\A"
    assert finder.share("s:\\B") == "\\\\nas\\pas212\\B"
    assert ("shares",) not in api.calls
    assert finder.share("C:\\EXP\\A") == "\\\\PC1\\Exp\\A"
    assert finder.share("C:\\EXP\\B") == "\\\\PC1\\Exp\\B"
    assert api.calls.count(("wnet", "S:")) == 1 and api.calls.count(("wnet", "C:")) == 1
    assert api.calls.count(("shares",)) == 1


def test_the_folder_plan_share_path_uses_the_same_conversion(monkeypatch):
    from src.companion import folder_plan

    api = FakeApi(drives={"Z:": "\\\\srv\\docs"}, shares={"Scan": "D:\\Scan"})
    monkeypatch.setattr(df, "default_api", lambda: api)
    monkeypatch.setattr(df, "hostname", lambda: "ACC-01")
    assert folder_plan.share_path("Z:\\Inbox") == "\\\\srv\\docs\\Inbox"   # 非持久映射也认（以前只认注册表里的持久映射）
    assert folder_plan.share_path("D:\\Scan\\ABC") == "\\\\ACC-01\\Scan\\ABC"
    assert folder_plan.share_path("E:\\Local") == ""


# ── 主机名 ──

def _adapter(name, ipv4, gateways, *, description="", if_type=6, up=True):
    return df.Adapter(name=name, description=description, if_type=if_type, up=up, ipv4=tuple(ipv4),
                      gateways=tuple(gateways))


def test_host_names_are_the_computer_names_then_addresses_of_adapters_with_a_default_gateway():
    adapters = [
        _adapter("Ethernet", ["192.168.0.78"], ["192.168.0.1"]),
        _adapter("vEthernet (WSL)", ["172.27.224.1"], []),                          # 第 0 批：WSL 没有默认网关
        _adapter("Tailscale", ["100.67.246.0"], ["100.100.100.100"], if_type=53),     # VPN：按名字排除
        _adapter("vEthernet (Default Switch)", ["172.20.0.1"], []),
        _adapter("Wi-Fi", ["10.0.0.5", "169.254.3.4"], ["10.0.0.1"]),                 # 自动私有地址不报
        _adapter("Loopback", ["127.0.0.1"], ["127.0.0.1"], if_type=24),
        _adapter("VPN", ["10.8.0.2"], ["10.8.0.1"], if_type=131),                     # 隧道
        _adapter("Dial", ["10.9.0.2"], ["10.9.0.1"], if_type=23),                     # 拨号
        _adapter("Down", ["192.168.5.5"], ["192.168.5.1"], up=False),
        _adapter("NoGw", ["192.168.9.9"], ["0.0.0.0"]),
        _adapter("Corp Link", ["10.66.0.4"], ["10.66.0.1"], if_type=53),             # wintun 一类：按类型排除
    ]
    names = df.pick_host_names("DESKTOP-MN2BRKR", ["DESKTOP-MN2BRKR", "desktop-mn2brkr.office.local"], adapters)
    assert names == ["DESKTOP-MN2BRKR", "desktop-mn2brkr.office.local", "192.168.0.78", "10.0.0.5"]


def test_long_folder_paths_are_opened_with_the_long_path_prefix():
    short = "S:\\2569\\ACME"
    assert df.extended_path(short) == short
    deep = "S:\\" + "\\".join(["บริษัท ตัวอย่าง จำกัด"] * 12)
    assert len(deep) >= 248 and df.extended_path(deep) == "\\\\?\\" + deep
    unc = "\\\\nas\\pas212\\" + "\\".join(["x" * 30] * 9)
    assert df.extended_path(unc) == "\\\\?\\UNC\\" + unc[2:]
    assert df.extended_path("\\\\?\\" + deep) == "\\\\?\\" + deep   # 已带前缀的不重复加


def test_host_names_are_capped_like_the_server():
    adapters = [_adapter(f"Eth{n}", [f"192.168.{n}.2"], [f"192.168.{n}.1"]) for n in range(50)]
    names = df.pick_host_names("PC", ["x" * 300], adapters)
    assert len(names) == df.MAX_HOST_NAMES == 32 and names[0] == "PC" and all(len(n) <= 255 for n in names)


def test_host_names_are_cached_for_ten_minutes(monkeypatch):
    monkeypatch.setattr(df, "hostname", lambda: "PC1")
    api = FakeApi(adapters=[_adapter("Ethernet", ["192.168.1.2"], ["192.168.1.1"])], names=["PC1"])
    assert df.host_names(api, now=0) == ["PC1", "192.168.1.2"]
    api._adapters = [_adapter("Ethernet", ["192.168.1.3"], ["192.168.1.1"])]
    assert df.host_names(api, now=500) == ["PC1", "192.168.1.2"]
    assert df.host_names(api, now=601) == ["PC1", "192.168.1.3"]


def test_a_failing_adapter_query_still_reports_the_computer_name(monkeypatch):
    class Broken(FakeApi):
        def adapters(self):
            raise OSError("iphlpapi")

    monkeypatch.setattr(df, "hostname", lambda: "PC9")
    assert df.host_names(Broken(names=["PC9"]), now=0) == ["PC9"]


# ── ctypes 布局与解析（不调系统，只核我们读的那一段） ──

def test_the_adapter_structure_has_the_x64_offsets():
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("64 位布局")
    assert df.IP_ADAPTER_ADDRESSES.FirstUnicastAddress.offset == 24
    assert df.IP_ADAPTER_ADDRESSES.FriendlyName.offset == 72
    assert df.IP_ADAPTER_ADDRESSES.IfType.offset == 100
    assert df.IP_ADAPTER_ADDRESSES.OperStatus.offset == 104
    assert df.IP_ADAPTER_ADDRESSES.FirstGatewayAddress.offset == 208
    assert df.IP_ADAPTER_UNICAST_ADDRESS.Address.offset == 16
    assert df.IP_ADAPTER_GATEWAY_ADDRESS.Address.offset == 16


def _sockaddr(ip, keep):
    raw = df.SOCKADDR_IN()
    raw.sin_family = 2
    for index, part in enumerate(ip.split(".")):
        raw.sin_addr[index] = int(part)
    keep.append(raw)
    return df.SOCKET_ADDRESS(ctypes.cast(ctypes.pointer(raw), ctypes.POINTER(df.SOCKADDR)), ctypes.sizeof(raw))


def test_the_adapter_chain_is_parsed_into_names_addresses_and_gateways():
    keep = []
    gateway = df.IP_ADAPTER_GATEWAY_ADDRESS()
    gateway.Address = _sockaddr("192.168.0.1", keep)
    second_ip = df.IP_ADAPTER_UNICAST_ADDRESS()
    second_ip.Address = _sockaddr("192.168.0.79", keep)
    first_ip = df.IP_ADAPTER_UNICAST_ADDRESS()
    first_ip.Address = _sockaddr("192.168.0.78", keep)
    first_ip.Next = ctypes.pointer(second_ip)
    wsl_ip = df.IP_ADAPTER_UNICAST_ADDRESS()
    wsl_ip.Address = _sockaddr("172.27.224.1", keep)
    wsl = df.IP_ADAPTER_ADDRESSES()
    wsl.FriendlyName, wsl.Description, wsl.IfType, wsl.OperStatus = "vEthernet (WSL)", "Hyper-V", 6, 1
    wsl.FirstUnicastAddress = ctypes.pointer(wsl_ip)
    lan = df.IP_ADAPTER_ADDRESSES()
    lan.FriendlyName, lan.Description, lan.IfType, lan.OperStatus = "Ethernet", "Intel(R) Ethernet", 6, 1
    lan.FirstUnicastAddress = ctypes.pointer(first_ip)
    lan.FirstGatewayAddress = ctypes.pointer(gateway)
    lan.Next = ctypes.pointer(wsl)
    keep += [gateway, first_ip, second_ip, wsl_ip, wsl]

    adapters = df.parse_adapters(ctypes.pointer(lan))

    assert adapters == [
        df.Adapter("Ethernet", "Intel(R) Ethernet", 6, True, ("192.168.0.78", "192.168.0.79"), ("192.168.0.1",)),
        df.Adapter("vEthernet (WSL)", "Hyper-V", 6, True, ("172.27.224.1",), ()),
    ]
    assert df.pick_host_names("PC", [], adapters) == ["PC", "192.168.0.78", "192.168.0.79"]


# ── 真的 Windows 接口（CI 的 Companion checks） ──

@pytest.mark.skipif(sys.platform != "win32", reason="Windows 系统接口")
def test_windows_gives_a_stable_nonzero_uid_for_a_folder(tmp_path):
    first, second = tmp_path / "A", tmp_path / "B"
    first.mkdir()
    second.mkdir()
    uid = df.file_uid(str(first))
    assert uid and uid == df.file_uid(str(first)) and uid != df.file_uid(str(second))
    assert df.file_uid(str(first / "missing")) == ""
    # 只用 FILE_READ_ATTRIBUTES 打开：没改目录里的东西
    assert os.listdir(first) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 系统接口")
def test_windows_reports_its_own_name_first_and_queries_never_raise(tmp_path):
    api = df.default_api()
    assert api is not None
    names = df.host_names()
    assert names and names[0] == socket.gethostname()
    assert isinstance(api.adapters(), list) and isinstance(api.computer_names(), list)
    assert isinstance(api.local_shares(), dict)
    assert api.net_connection("Q:") in ("",) or api.net_connection("Q:").startswith("\\\\")
    assert df.Locator().share(str(tmp_path)) in ("",) or df.Locator().share(str(tmp_path)).startswith("\\\\")
