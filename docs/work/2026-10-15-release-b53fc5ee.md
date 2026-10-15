# 生产部署 b53fc5ee（库存盘点：多人分区、手机看差异、相机权限三步）

- 用户 2026-10-15 在真机测完后说「合并上线」；部署由本窗口做（开发窗口当天没有下一批），激活时间与验收窗口约好，避开其 V7 / F16 重验。
- 内容：`pearnly/stocktake-collab`（记录见 [2026-10-15-stocktake-collab.md](2026-10-15-stocktake-collab.md)），基于 `eca4e249`。
- 合入：全闸门 83 项通过；分支 CI run 38721835130 通过；共用层候选离线检查（生产镜像 2bc4aad1 挂 `b53fc5ee` 源码）71/71、LANE EXIT 0；
  远端 main 快进到 `b53fc5ee`（之后开发窗口在其上快进了两个文档提交 `b318fbeb`）。
- 构建 `build-production.yml` run 38722112765 → `sha256:104eb028f56c699f83cbbc281ee925c0ccbcbf85325e6742faac76030224b20a`；
  `stage-production.yml` run 38722858990 成功。
- 激活（泰国时间 17:49–17:55，`activate.sh`）：部署工具与镜像一致；ERP 10:49Z、Cowork 10:50Z、DMS 10:52Z 均 "activated with verified image"；
  基础层 10:54Z "Foundation activated"（Keycloak 未重启）。
- 回读（`readback-stocktake.sh`）：生产容器全部新镜像；三个识别进程 NOFORK=1；六站 maintenance=0、部署后 Error Log 0；
  erpcowork / testcowork 的 `tabCowork Stocktake Item.category` 已建；session_expiry 在 erpcowork、testcowork、mrerp、testerp 均为 720:00
  （开发窗口那批的 after_migrate 未被改回）；mariadb 3.61 / 8 GiB；mrerp、erpcowork、ris ping 200，fatcc.com 首页 200（其 ping 403 为既有放行规则）。
  外部读回：mrerp、testerp 的 LINE 盘点页引用 `scan/reader.js?v=239001d182b7ffef`（= `b53fc5ee` 文件 sha256 前 16 位）；
  testerp、erpcowork 的 `/?liff.state=…` 直接 302 到目标盘点页。
- 测试机：`testerp` 已 `lab-stop` 切回线上，线上 testerp 调度已恢复（`Scheduler is enabled`）；测试机无运行中的栈；
  ERP 测试副本里留有测试盘点 STC-00061 / STC-00069 与改过条码 / 类别的商品（只在测试机卷里）。
