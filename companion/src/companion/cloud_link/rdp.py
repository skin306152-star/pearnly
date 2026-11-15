# -*- coding: utf-8 -*-
"""桥用的 .rdp 文本（评估 docs/work/2026-11-11-express-cloud-assessment.md 第 4.1 节；11-15 实测写法，探针 ``prep.ps1``）。纯函数，不做 IO
（``read_session_rdp`` 只读一个文件）。

实测定下的写法：
- 主机、端口分开写（``full address:s:<主机>`` + ``server port:i:<端口>``）；
- 用户名、域分开写（``username:s:`` + ``domain:s:``）：只写用户名不写域，云端认不出（原因码 2055）；
- ``authentication level:i:0``（云端证书不校验，同 TSplus）、``enablecredsspsupport:i:1``（网络级身份验证，用 ``TERMSRV/<主机>`` 域凭据）；
- 只映射隧道盘（``drivestoredirect:s:<盘>:\\;``），剪贴板、打印机、串口、智能卡、WebAuthn、摄像头、录音一概不映射；
- mstsc 认 UTF-16LE 带 BOM、CRLF 换行。

不写 ``alternate shell``（云端不认，实测 23:16），不写密码（密码只在凭据管理器，见 ``credentials``）。

TSplus 留在用户目录的 ``RDP6\\Session.rdp``：只取主机、端口、域三项；其余各行（包括 password / credential 一类）一律不解析、不存。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

DEFAULT_WIDTH, DEFAULT_HEIGHT = 1280, 800
DEFAULT_PORT = 3389
MIN_SIZE, MAX_WIDTH, MAX_HEIGHT = 640, 3840, 2160
# Session.rdp 是几十行的小文件；更大的不读
MAX_SESSION_RDP = 64 * 1024

_HOST = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
_NAME = re.compile(r"^[^\x00-\x1f\x7f\\/:;@\"]{1,104}$")


class RdpValueError(ValueError):
    """参数不合规。文本只说是哪一项，不带值（用户名、域不是秘密，但也不往日志里抄）。"""


@dataclass(frozen=True)
class CloudTarget:
    """从 ``Session.rdp`` 读到的云端地址（不含用户名、不含任何凭据）。"""

    host: str
    port: int
    domain: str


def host(value: str) -> str:
    value = str(value or "").strip().rstrip(".")
    if not _HOST.match(value) or value.startswith("-") or ".." in value:
        raise RdpValueError("host")
    return value.lower()


def port(value) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise RdpValueError("port") from None
    if not 1 <= number <= 65535:
        raise RdpValueError("port")
    return number


def _name(value: str, what: str, *, allow_empty: bool = False) -> str:
    value = str(value or "").strip()
    if not value and allow_empty:
        return ""
    if not _NAME.match(value):
        raise RdpValueError(what)
    return value


def drive(value: str) -> str:
    letter = str(value or "").strip().rstrip(":\\").upper()
    if len(letter) != 1 or not "D" <= letter <= "Z":
        raise RdpValueError("drive")
    return letter


def split_account(account: str, default_domain: str = "") -> tuple:
    """用户输的账号拆成（用户名，域）：``域\\用户名``、``用户名@域`` 两种写法照拆；只给用户名时用 ``default_domain``
    （``Session.rdp`` 里读到的域；11-15 实测：TSplus 送的是用户名 ``W10E061734-01`` + 域 ``expressoncloud``）。"""
    account = str(account or "").strip()
    if "\\" in account:
        domain, user = account.split("\\", 1)
    elif "@" in account:
        user, domain = account.rsplit("@", 1)
    else:
        user, domain = account, default_domain
    return _name(user, "user"), _name(domain, "domain", allow_empty=True)


def lines(*, host_name: str, port_number: int, user: str, domain: str, tunnel_drive: str,
          width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> List[str]:
    """.rdp 的各行（顺序固定，同探针 ``prep.ps1``）。"""
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        raise RdpValueError("size") from None
    if not (MIN_SIZE <= width <= MAX_WIDTH and MIN_SIZE // 2 <= height <= MAX_HEIGHT):
        raise RdpValueError("size")
    letter = drive(tunnel_drive)
    return [
        "full address:s:" + host(host_name),
        "server port:i:%d" % port(port_number),
        "username:s:" + _name(user, "user"),
        "domain:s:" + _name(domain, "domain", allow_empty=True),
        "drivestoredirect:s:%s:\\;" % letter,
        "redirectclipboard:i:0",
        "redirectprinters:i:0",
        "redirectcomports:i:0",
        "redirectsmartcards:i:0",
        "redirectwebauthn:i:0",
        "devicestoredirect:s:",
        "camerastoredirect:s:",
        "audiomode:i:2",
        "audiocapturemode:i:0",
        "keyboardhook:i:1",
        "screen mode id:i:1",
        "desktopwidth:i:%d" % width,
        "desktopheight:i:%d" % height,
        "session bpp:i:16",
        "smart sizing:i:0",
        "autoreconnection enabled:i:1",
        "prompt for credentials:i:0",
        "promptcredentialonce:i:1",
        "authentication level:i:0",
        "enablecredsspsupport:i:1",
        "displayconnectionbar:i:1",
    ]


def encode(rows: List[str]) -> bytes:
    """mstsc 认的编码：UTF-16LE 带 BOM，CRLF，末行也带换行（同 .NET ``WriteAllLines``）。"""
    return b"\xff\xfe" + "".join(row + "\r\n" for row in rows).encode("utf-16-le")


def build(*, host_name: str, port_number: int, user: str, domain: str, tunnel_drive: str,
          width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> bytes:
    return encode(lines(host_name=host_name, port_number=port_number, user=user, domain=domain,
                        tunnel_drive=tunnel_drive, width=width, height=height))


def decode(data: bytes) -> str:
    """.rdp 文件字节 → 文本：带 BOM 的 UTF-16（LE / BE）、UTF-8（带不带 BOM）都认。"""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if len(data) >= 2 and data[1:2] == b"\x00":
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


# 只认这三个键；其余各行跳过，值不进任何变量
_WANTED = {"full address": "s", "server port": "i", "domain": "s"}


def parse_session_rdp(data: bytes) -> CloudTarget:
    """从 TSplus 的 ``Session.rdp`` 取主机、端口、域。``full address`` 带了 ``:端口`` 也认；``server port`` 优先。
    缺主机或主机不合规 → :class:`RdpValueError`。"""
    found = {}
    for raw in decode(data).splitlines():
        key, sep, rest = raw.partition(":")
        key = key.strip().lower()
        if not sep or key not in _WANTED or key in found:
            continue
        kind, sep, value = rest.partition(":")
        if not sep or kind.strip().lower() != _WANTED[key]:
            continue
        found[key] = value.strip()
    address = found.get("full address", "")
    if not address:
        raise RdpValueError("host")
    embedded: Optional[str] = None
    if address.count(":") == 1:
        address, embedded = address.split(":", 1)
    number = found.get("server port") or embedded or DEFAULT_PORT
    return CloudTarget(host=host(address), port=port(number),
                       domain=_name(found.get("domain", ""), "domain", allow_empty=True))


def read_session_rdp(path: Union[str, Path]) -> CloudTarget:
    """读一个 ``Session.rdp``（只读；超过 64 KB 不读）。"""
    path = Path(path)
    if path.stat().st_size > MAX_SESSION_RDP:
        raise RdpValueError("file")
    return parse_session_rdp(path.read_bytes())


def find_session_rdp(home: Union[str, Path]) -> Optional[Path]:
    """TSplus 便携客户端解到用户目录下的 ``RDP6`` 文件夹，``Session.rdp`` 在里面（11-15 测试机1 实测
    ``C:\\Users\\<用户>\\RDP6\\Session.rdp``）。找不到回 ``None``。"""
    path = Path(home) / "RDP6" / "Session.rdp"
    return path if path.is_file() else None
