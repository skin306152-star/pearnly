# 上线 8fd0fa4d：多仓闭环 P1、采购单去掉「采购订单」、撤掉采购设置 / 集成两页、扫码不重读、一条命令部署（2026-10-29）

主会话部署，第一次用 `scripts/production/deploy.sh` 一条命令跑完。线上原来是 `fcb65cca`（[上次发布](2026-10-28-release-fcb65cca.md)）。

## 版本

- main `8fd0fa4d`（相对 fcb65cca 共 27 个提交）。
- 镜像 `sha256:81dbb5a97a67f50be3da1b6e1367f549938f4379c6f8aefe89903780f6cdebf7`，构建运行 40826061340。
- 用时（脚本打印）：构建 8 分 51 秒、放镜像 44 秒、激活与回读 18 分 05 秒，合计 27 分 41 秒（补进 [一条命令部署](2026-10-28-one-command-deploy.md) 的「时间对比」）。

## 这一批包含

- **多仓闭环 P1**（[评估](2026-10-26-multi-warehouse-closed-loop-评估.md)）：采购 / 销售 / 出入库单头「仓库 + 库位」带领明细并按本人上次预填，服务端不再猜仓；
  期初行可指定仓；单头库位按库位清单核；停着的 Express 单按公司与可写状态重排；提交前 Express 预检给没写仓的行补单头仓。
- **采购单去掉「采购订单」**：类型、打印格式与入口删除，旧调用与识别结果落成采购发票（补丁 `v1_16_retire_purchase_order`）。
- **撤掉采购设置、集成两页**（从未做成）：保留采购流程读的设置值，删无人调用的接口。
- **扫码不重读**：盘点页拒掉的码（按台商品没扫台、认不到、查找失败）不再被读码器反复读。
- **Oracle claude-ocr 打包**带上自检表要的 `pearnly_core.tax_documents`。
- **一条命令部署**与检查跑自己机器（free-arm-2 / 测试机 runner）等运维改动（不进产品）。

## 回读（主会话）

- 通用：ERP / Cowork / DMS / 基础层都已激活；九个站点 maintenance = 0、部署后 Error Log 为 0；三个 ocr-worker 的 `FRAPPE_BACKGROUND_WORKERS_NOFORK=1` 都在。
- 批次：线上 ERP `stocktake-entry.js` 带新代码（stocktake-lookup）；mrerp / testerp / polymer / gize 采购订单 0 张，`Purchase Document` 的 warehouse、
  location 两列都在，两个撤掉的 Page 都不在，`v1_16` 补丁日志都有；四个域名 `/api/method/ping` 200；polymer 上 TC001167 仍命中 PRD-00010。

## 没验证（要真机 / 线上实测）

- 真机 LINE 扫码。
- Cowork 盘点扫码的线上实测。
