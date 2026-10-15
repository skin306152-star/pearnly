"""池里一台电脑在连接域眼里的样子（ADR-021）：不导入框架、不做 IO。

电脑记录归 companion 域；连接域只把设备编号当不透明字符串（`architecture-policy.json`：connections 不依赖 companion）。
心跳与领取由 companion 读好这台电脑的事实、装进 :class:`Member` 再交进来。
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Callable, Mapping, NamedTuple


class Member(NamedTuple):
    # 设备编号（`Cowork CC Desktop Device` 的 name，不透明）
    device: str
    # 还没被新版 CC 桌面版认领过（≤1.6.0 不报文件号 / 网络路径）：账套只按路径认（方案 2.4 第 4 条）
    legacy: bool = True
    # 这台电脑的主机名（新登记的、只在本机的账套键写成 ``\\主机\盘$\…``）
    host: str = ""
    # 池里各台电脑报的主机别名 → 规范主机名（``\\IP`` 与 ``\\主机名`` 是同一处）
    aliases: Mapping[str, str] = MappingProxyType({})
    # 这台电脑上 CC 桌面版的版本与最高载荷版本
    version: str = ""
    payload_version: int | None = None
    # 这台电脑上次报的所选账套（它本机的写法）；领取请求没有心跳的所选时用它
    selected: str = ""
    # 读目录刷新的分派要用：``{设备: {"user", "always_on", "online"}}``，只在要分派时才调用
    peers: Callable[[], Mapping[str, Mapping]] | None = None


__all__ = ["Member"]
