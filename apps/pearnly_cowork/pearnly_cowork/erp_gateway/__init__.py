"""Pearnly ERP 前台 → 事务所 Express 账本的网关（2026-10-22）。

ERP 的采购 / 销售单经这里进现有推送（准备 → 确认 → 办公室电脑写 Express → 回读核对），ERP 按单据查结果，
并按公司取 Express 整套主档与各仓结存回去自动建档。契约在 `contracts`（纯函数），身份在 `auth`，流程在 `service`，
三个机器入口在 `api`。不改推送规则、不新增 DocType；ERP 交来的票来源记 ``erp_portal``。
"""
