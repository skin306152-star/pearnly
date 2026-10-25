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
# 6:库存单(``direction = stock``,单别 OU 领用 / JU 调整 / TK 盘点;出入库第一期 B 段,dbf_stock)。旧版不认这个方向,会把它当
# 进项写,所以盖 6,旧版诚实拒收、派单只交给报过支持 6 的电脑。
# 7:明细行可带来源（ERP）自己的包装单位换算率 ``factor``,且包装单位按 Express 主档的系数照原生记法写(TQUCOD / TFACTOR /
# XTRNQTY = 数量 × 系数,stock_units)。旧版按 1 写,所以带 factor 的载荷盖 7,旧版诚实拒收。
# 8:作废 ERP 写进去的库存单(``direction = stock``、``action = void``,照 Express 原生作废原单;dbf_void)。旧版会把它当一张新的
# 库存单写,所以盖 8,旧版诚实拒收。
# 9:销项表头可带 ``vat_fields``(ISVAT 金额栏 → 金额,来源按自己的规则算好,例如混有免税商品的现销日汇总 AMT01 只记应税部分;
# ERP 收银日汇总,主会话 10-26)。旧版按整张税前写 AMT01,所以带它的载荷盖 9,旧版诚实拒收。
# 10:收银退货单 SR(销项 ``doctype = SR`` 带 ``refund`` 块:退现金 CNTYP 3、货回仓 RETSTK Y、挂原 HS;ERP B3b,dbf_refund)。
# 旧版不认 SR(单别校验拒收或当现销写成销售),所以盖 10,旧版诚实拒收、派单只交给报过支持 10 的电脑。
SUPPORTED_PAYLOAD_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10})

ERR_PAYLOAD_VERSION = "ERR_PAYLOAD_VERSION"


def check_payload_version(payload: dict) -> Optional[str]:
    """payload_version 是否在本机支持集内。支持 → None;不支持 → 错误码字符串。"""
    got = (payload or {}).get("payload_version", 1)
    if got in SUPPORTED_PAYLOAD_VERSIONS:
        return None
    return ERR_PAYLOAD_VERSION
