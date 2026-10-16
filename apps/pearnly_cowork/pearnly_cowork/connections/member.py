"""池里一台电脑在连接域眼里的样子（ADR-021）：不导入框架、不做 IO。

电脑记录归 companion 域；连接域只把设备编号当不透明字符串（`architecture-policy.json`：connections 不依赖 companion）。
心跳与领取由 companion 读好这台电脑的事实、装进 :class:`Member` 再交进来。
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping, NamedTuple


class Member(NamedTuple):
    # 设备编号（`Cowork CC Desktop Device` 的 name，不透明）
    device: str
    # 还没被新版 CC 桌面版认领过（1.7.0 之前的版本不报文件号 / 网络路径）：账套只按路径认（方案 2.4 第 4 条）
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


def serves(users: Iterable[str] | None, actor: str) -> bool:
    """这台电脑写不写 ``actor`` 的活（ADR-021 第 50 条，用户 10-16「谁的活谁的电脑做」）：``users`` 是在这台电脑上登录过
    CC 的人，``actor`` 在里面才算他的电脑。一个登录记录都没有的电脑（补丁建的旧电脑、1.6 及更早、只认过电脑还没打开过
    对话页的）在过渡期照旧谁的活都写；一旦有人在上面登录 CC，就只归登录过的人。"""
    users = {user for user in users or () if user}
    return not users or bool(actor) and actor in users


__all__ = ["Member", "serves"]
