"""ERP 当前台、Express 当账本（2026-10-22；10-23 起 CC 桌面版直连 ERP，ADR-025）：采购 / 销售单经共用机器通道由办公室电脑
写进 Express，Express 的主档与库存回读进 ERP。完全不经过 Cowork。

`contracts` 纯规则，`target` 选账套，`facts` 读回的账套资料，`choices` 要人拿主意的项（按公司记住），`prepare` 备载荷，
`push` 交进写账队列与记结果，`masters` 主档与库存同步，`locks` 映射档只读，`desktop` 接共用通道的声明，`api` 页面入口。
本公司的账套没有 CC 桌面版报过就是没接 Express，这里什么都不做。
"""
