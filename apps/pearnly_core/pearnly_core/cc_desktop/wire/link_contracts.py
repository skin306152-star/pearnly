"""CC 桌面版「经哪台电脑连上来」的纯约定（多产品共用，纯函数，不导入框架、不做 IO）：云端 Express 的来源标记。

云端主机上跑的 CC 经客户自己那台 CC 电脑（桥）连到站点，心跳带一项可选的 ``link``::

    {"kind": "cloud", "via": "<桥电脑显示名>", "host": "<云端主机名>"}

站点把它记在电脑行上（``link_kind`` / ``link_via`` / ``link_host``），给界面显示「云端」标记用；选目标账本的逻辑不看它。
账本的来源（:func:`source_of`，云端 Express 第二阶段 4.8）：一本账报它的电脑全是云端那份才算「云端 Express」，ERP 与 Cowork 同一条。

- 只有明确的 ``kind == "cloud"`` 才写；``via`` / ``host`` 去控制字符、去空白、限 :data:`MAX_TEXT` 字，不是字符串的当空；
- ``{"kind": "none"}`` 清空（以后「断开云端」用）；
- 缺省、``null``、``{"kind": "local"}``、认不出的形状一律「不改上次存的」（旧版 CC 不报），也绝不因为它拒收心跳。
"""
from __future__ import annotations

from typing import Mapping, Optional

CLOUD = "cloud"
NONE = "none"
MAX_TEXT = 80
FIELDS = ("link_kind", "link_via", "link_host")


def _text(value) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch >= " " and ch != "\x7f").strip()[:MAX_TEXT].strip()


def stored(value) -> Optional[dict]:
    """心跳的 ``link`` → 要写进电脑行的三个字段；回 None 表示不改上次存的。"""
    if not isinstance(value, Mapping):
        return None
    kind = value.get("kind")
    if kind == NONE:
        return {key: "" for key in FIELDS}
    if kind != CLOUD:
        return None
    return {"link_kind": CLOUD, "link_via": _text(value.get("via")), "link_host": _text(value.get("host"))}


SOURCE_LOCAL = "local"
SOURCE_CLOUD = "cloud"
# 账本标签（CC 选公司、ERP「从连接添加」、Cowork 添加公司）：本地 Express / 云端 Express
ADAPTER_LOCAL = "express"
ADAPTER_CLOUD = "express_cloud"


def source_of(kinds) -> str:
    """报一本账（或一组同身份的账）的那几台电脑的 ``link_kind`` → 账本来源：有、且全是 :data:`CLOUD` 才是 ``cloud``；有一台本地的、
    或一台都不知道，都算 ``local``（同一身份只出一行，本地优先）。"""
    kinds = [str(kind or "") for kind in (kinds or ())]
    return SOURCE_CLOUD if kinds and all(kind == CLOUD for kind in kinds) else SOURCE_LOCAL


def adapter_of(source: str) -> str:
    """账本来源 → 标签。"""
    return ADAPTER_CLOUD if source == SOURCE_CLOUD else ADAPTER_LOCAL


def changed(update: Optional[Mapping], row) -> bool:
    """``update``（:func:`stored` 的结果）与电脑行上现存的三个字段不一样才需要写。``row`` 是 dict 或带属性的行。"""
    if update is None:
        return False
    get = row.get if isinstance(row, Mapping) else (lambda key: getattr(row, key, None))
    return any((get(key) or "") != update[key] for key in FIELDS)


def view(kind, via, host) -> Optional[dict]:
    """电脑行上存的三个字段 → 接口返回的 ``link``：云端回 ``{kind, via, host}``，其余回 None。"""
    if kind != CLOUD:
        return None
    return {"kind": CLOUD, "via": str(via or ""), "host": str(host or "")}


__all__ = ["ADAPTER_CLOUD", "ADAPTER_LOCAL", "CLOUD", "FIELDS", "MAX_TEXT", "NONE", "SOURCE_CLOUD", "SOURCE_LOCAL", "adapter_of",
           "changed", "source_of", "stored", "view"]
