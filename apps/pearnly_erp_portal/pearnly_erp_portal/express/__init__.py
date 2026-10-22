"""ERP 当前台、事务所 Express 当账本（2026-10-22）：采购 / 销售单经 Cowork 网关写进 Express，Express 的主档与库存回读进 ERP。

`contracts` 纯规则，`gateway` 内网调用，`push` 交单与查结果，`masters` 主档与库存同步，`api` 管理员手动同步。
站点配置 ``pearnly_express_gateway`` 没配的 ERP 不接 Express，这里什么都不做。
"""
