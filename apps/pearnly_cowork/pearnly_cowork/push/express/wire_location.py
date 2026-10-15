"""下发给领取电脑的 Express 载荷副本：账套位置换成这台电脑自己的写法（ADR-021，方案 2.5 第 5 步）。纯函数。

账本里冻结的载荷一字不动；交给电脑的是一份副本，账套的四个位置字段换成这台电脑可达行里的写法（它只认本机数据根或
授权根下的路径，`companion/worker._task_settings`），另加登记里的文件号 ``account_uid``（1.6.0 起写前核对：对不上就
回「等账套」，不写错份）。``account_company`` 是显示用的公司名，各台相同，不换。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .account_rules import wire_account_set

# 换成领取电脑写法的字段（与预检往载荷里写的四个同名，`preflight`）
LOCATION_KEYS = ("account_root", "account_dir", "account_set", "account_set_row")
UID_KEY = "account_uid"


def localize(payload: Mapping[str, Any], *, path: str, root: str = "", row: int | None = 0, uid: str = "") -> dict:
    """``payload`` 的副本，位置换成领取电脑可达行里的写法（``path`` 数据目录、``root`` 程序目录、``row`` 登录列表行号）。

    ``path`` 为空（不该发生：可达行总有写法）时原样返回副本，不编一个位置。"""
    result = deepcopy(dict(payload or {}))
    path = str(path or "").strip()
    if not path:
        return result
    try:
        number = max(0, int(row or 0))
    except (TypeError, ValueError):
        number = 0
    result.update({"account_root": str(root or "").strip(), "account_dir": path, "account_set_row": number})
    result["account_set"] = wire_account_set(path, path)
    if uid:
        result[UID_KEY] = str(uid)
    return result


__all__ = ["LOCATION_KEYS", "UID_KEY", "localize"]
