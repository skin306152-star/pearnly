# -*- coding: utf-8 -*-
"""载荷版本协商(与云端后端严格对齐,字段名一字不差)。

后端将来改 payload 结构会带 `payload_version` 递增;本机声明的支持集
`SUPPORTED_PAYLOAD_VERSIONS` 落后于云端新版号时,任务必须诚实失败
(见 worker._handle_dbf_task),绝不按旧逻辑瞎写出错账 —— 静默错账是最大罪。

`payload_version` 缺失视同 1(兼容后端尚未升级期间下发的旧载荷)。
"""

from __future__ import annotations

from typing import Optional

# 2:明细行可带 `code`(复核时选定的 Express 存货,按码落账、码不在就报错)、单头可带 `depcod`(部门码)。
# 旧版小助手会静默忽略这两个键(按品名新建存货、部门留空),所以带了它们的载荷盖 2,旧版诚实拒收。
# 3:POS 导出的日汇总票带 `pos` 块(自编单号、票号起止写进销项税摘要、每种非现金付款方式同一次写入一张日记账凭证
# 「借 收款科目 / 贷 现金」)。旧版小助手会把它当普通现销写、漏掉凭证,所以盖 3,旧版诚实拒收。
# 4:明细行 / 表头可带 `loccod`(Express 仓库码,行上的优先,表头是行的默认):库存行写进那个仓并维护各仓结存 STLOC,
# 仓库码不在这套账的仓库代码表里整单失败(ERR_LOCATION,不回落主仓)。旧版会静默写主仓,所以带了它的载荷盖 4。
# 没带 loccod 的载荷与以前逐字节相同(见 stock_location)。
SUPPORTED_PAYLOAD_VERSIONS = frozenset({1, 2, 3, 4})

ERR_PAYLOAD_VERSION = "ERR_PAYLOAD_VERSION"


def check_payload_version(payload: dict) -> Optional[str]:
    """payload_version 是否在本机支持集内。支持 → None;不支持 → 错误码字符串。"""
    got = (payload or {}).get("payload_version", 1)
    if got in SUPPORTED_PAYLOAD_VERSIONS:
        return None
    return ERR_PAYLOAD_VERSION
