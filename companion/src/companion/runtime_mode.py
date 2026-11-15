# -*- coding: utf-8 -*-
"""这一份 CC 在哪种模式下跑：本地（默认，装在客户电脑上）还是云端（``companion.exe --cloud``，云端 Express 主机上那一份）。

云端那份 CC（docs/work/2026-11-11-express-cloud-assessment.md 第 4.2 节）由客户自己那台 CC 电脑（桥）放上云端主机并启动，
会话里没人看：不起托盘、不开窗口、不发系统通知、不自动更新、不写开机自启、不防睡眠；文件夹自动录入、MR.ERP 成员与 MR.ERP 代录
不起；找 Express 数据根不碰映射进来的 ``\\\\tsclient\\*``（客户电脑的盘）与局域网共享。只跑直写循环、按需请求与代录宿主。

判断只看这里（:func:`cloud`），各处不另判；值只来自启动参数或环境：``main.py`` 收到 ``--cloud`` 时调 :func:`enable`，
写进本进程环境变量 :data:`CLOUD_ENV`（子进程照样继承）。桥启动云端 CC 时把自己的显示名放进 :data:`VIA_ENV`
（或 ``--cloud-via``），心跳据此带 ``link``（:func:`link`）；没有就不带。
"""

from __future__ import annotations

import os
from typing import Optional

CLOUD_ENV = "PEARNLY_CC_CLOUD"
# 桥电脑的显示名（桥启动云端 CC 时交过来；心跳 ``link.via``）
VIA_ENV = "PEARNLY_CLOUD_VIA"
CLOUD = "cloud"
# 与心跳 ``device``（本机名）同一上限
TEXT_MAX = 60


def enable(via: Optional[str] = None) -> None:
    """本进程进入云端模式（``main.py`` 收到 ``--cloud``）；``via`` 有值时一并记下桥电脑名。"""
    os.environ[CLOUD_ENV] = "1"
    if via is not None and str(via).strip():
        os.environ[VIA_ENV] = str(via).strip()


def cloud() -> bool:
    """这一份是不是云端那份 CC。"""
    return os.environ.get(CLOUD_ENV, "").strip() == "1"


def _text(value) -> str:
    clean = "".join(ch for ch in str(value or "") if ch.isprintable()).strip()
    return clean[:TEXT_MAX]


def link() -> Optional[dict]:
    """心跳带的 ``link``：云端模式且知道桥电脑名时 ``{"kind": "cloud", "via": <桥电脑名>, "host": <本机主机名>}``，否则 None（不带）。"""
    if not cloud():
        return None
    via = _text(os.environ.get(VIA_ENV, ""))
    if not via:
        return None
    from .queue_client import _device_name

    return {"kind": CLOUD, "via": via, "host": _text(_device_name())}


def serves(cfg) -> bool:
    """云端模式下这个成员起不起：只起 Express 成员（MR.ERP 连接的成员只读文件夹 / 代录 MR.ERP，云端不起）。本地模式一律起。"""
    if not cloud():
        return True
    return (getattr(cfg, "kind", "express") or "express") != "folder"
