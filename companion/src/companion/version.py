# -*- coding: utf-8 -*-
"""小助手版本号(单一真相源)。

自动更新比对、打包(installer.iss AppVersion)、release 脚本都读这里。
升级=只改这一处,跑 packaging/release.ps1 一键发布。
"""

from __future__ import annotations

VERSION = "1.3.1"


def version_tuple(v: str) -> tuple:
    """'1.2.0' → (1,2,0)。非法→(0,)。供版本大小比较。"""
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0,)
