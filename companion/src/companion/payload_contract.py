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
# 11:明细可带免税行标记 ``vat_exempt``(VATCOD '0'、单头 AMTRAT0,整张免税照样写销项税记录;实录第 10 条),退货块可带 ``cross_year``
# (原单在上一年度的账套,明细挂 AVGPR 按平均成本回冲;B3b 实录点 ②)。旧版免税行照应税写、跨年的报找不到原单,所以盖 11,旧版诚实拒收。
# 12:整单免税的进货单(全是免税行、带 `vat_fields` AMTRAT0 全额):照原生主流价格类型 1 / 2、单头记免税额、不写进项税记录(主会话 10-29)。
# 旧版进货按「税额 > 0」才算有税,写成 FLGVAT 0,所以盖 12,旧版诚实拒收。
# 13:进货单可带人选的服务品进项税口径 ``srv_vattyp``(1 / 2;这本账套的历史定不了时用,`service_vat`,10-29)。旧版不认这个键、
# 恒写 1,所以盖 13,旧版诚实拒收。
# 14:仓间调拨 RL(``direction = stock``、``doctype = RL``,单头 ``loccod`` 调出 / ``to_loccod`` 调入;作废 RL 同号,dbf_transfer / dbf_void;
# ERP 多仓闭环 P2)。旧版把 RL 当未知单别拒收或当领用写,所以盖 14,旧版诚实拒收、派单只交给报过支持 14 的电脑。
# 15:库存单 / 调拨的明细可带 ``new_ok``(Express 里没有、人在 ERP 里选了新建的商品;多仓闭环 P3x):写前备份里建 0 余额存货再写
# (dbf_stock.create_new_items,与采购 / 销售同一个建档)。旧版认不出就整单停 STOCK_ITEM_NOT_FOUND,所以盖 15,旧版诚实拒收。
# 16：代录 Express（``entry_mode = operator``，评估 docs/work/2026-10-26-express-operator-product-评估.md）：同一份载荷不直写 DBF，
# 交给代录在 Express 界面里一张张录，带 ``operator.batch``（批次号、第几张 / 共几张、开始时刻）与 ``operator.vat_included``。
# 旧版会把它当直写写进 DBF（入口分开的口径就破了），所以盖 16，旧版诚实拒收、派单只交给报过支持 16 的电脑。
# 17：明细行可带按台号码 ``remarks``（来源拼好的说明，每条 ≤ 50 字节）：写进 Express 单据附加说明 ARTRNRM，挂在那一行下面
# （dbf_remarks，评估 docs/work/2026-11-02-vehicle-artrnrm-评估.md）。旧版不认这个键、税票上没有车号，所以盖 17，旧版诚实拒收。
# 18：代录到 Express 以外的目标（``operator.target``，ADR-026；评估 docs/work/2026-11-02-mrerp-operator-and-erp-company-评估.md），
# 第一个是 MR.ERP 网页（``mrerp``）。旧版会把它当代录 Express 去开 Express、找账套，所以盖 18，旧版诚实拒收；派单只交给心跳
# ``operator_targets`` 报了这个目标的电脑。本版认得这个载荷，宿主按目标装配，还不会录的目标按「不支持」回执、不碰任何账本。
SUPPORTED_PAYLOAD_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18})
OPERATOR_PAYLOAD_VERSION = 16
REMARKS_PAYLOAD_VERSION = 17
MRERP_OPERATOR_PAYLOAD_VERSION = 18
ENTRY_OPERATOR = "operator"
# 这一版 CC 桌面版能代录到哪些目标（心跳 ``operator_targets``，与宿主 ``express_operator.host.ASSEMBLERS`` 一致）。
# 服务端只把 Express 以外目标的代录单派给报了它的电脑；旧版不报 = 只有 Express。
OPERATOR_TARGETS = ("express",)

ERR_PAYLOAD_VERSION = "ERR_PAYLOAD_VERSION"


def check_payload_version(payload: dict) -> Optional[str]:
    """payload_version 是否在本机支持集内。支持 → None;不支持 → 错误码字符串。"""
    got = (payload or {}).get("payload_version", 1)
    if got in SUPPORTED_PAYLOAD_VERSIONS:
        return None
    return ERR_PAYLOAD_VERSION
