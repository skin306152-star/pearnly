# ERP 直连 CC 桌面版、写进 Express（不经 Cowork）施工记录（2026-10-23）

- 状态：进行中。
- 分支 `pearnly/erp-cc-direct-26da5a`，工作树 `.claude/worktrees/erp-cc-direct-26da5a`，基线 `2dca095a`（= origin/main；生产 `d92337b2`、
  镜像 `0ccdb2a0`、CC 1.10.0 正式版，见 [发布记录](2026-10-22-release-d92337b2.md)）。集成目标 `main`。
- 架构决定：[ADR-025](../adr/0025-cc-desktop-direct-product-sites.md)。

## 目标与完成条件

用户 10-23：ERP 员工用 ERP 账号登录 CC 和 ERP，ERP 开的采购 / 销售单写进 Express，单号回到 ERP；主档、库存回读进 ERP。完全不出现
Cowork；事务所现有 Cowork 用法不受影响。由哪台电脑写照 Cowork「谁的活谁的电脑做」（ADR-021 第 50–55 条）。

完成条件：上面这条在测试机真实写入跑通；Cowork 中转（`erp_gateway`）已删；Cowork 现有推送回归通过；本记录分层写清实现、本地测试、
部署、外部结果与没验证的部分。

本次不做：领料（OU）、ERP 作废同步到 Express、部门 / 2–5 档售价 / 付款条件 / 业务员映射、Cowork 迁到共用通道（ADR-025 登记的债）。

## 施工顺序（按依赖）

1. 机械搬移：Express 写账规则与 CC 线上约定的纯函数进共用层（`pearnly_core.express_rules`、`pearnly_core.cc_desktop.wire`），Cowork
   改导入。单独提交，Cowork 全量测试 + 载荷逐字节回归。
2. 共用机器通道 `pearnly_core.cc_desktop`：DocType、登记、机器认证、心跳、写账队列、按需请求、产品钩子。
3. ERP `express` 域改直连：选账套、准备载荷、交队列、回执写回、要人选的项在 ERP 里处理、主档经按需请求。
4. CC 桌面版 1.11.0：每成员站点与通道、同时连 Cowork + ERP、账套锁文件、`express_masters` 带科目表等；对话页 ERP 站点自动登记。
5. 删除中转（Cowork `erp_gateway`、`erp_portal` 来源、ERP `gateway` / `setup`、两个站点配置键），文档与架构策略同步。
6. 验证：本地全闸门、分支 CI、CC 测试包、测试机真实写入（只写 C 盘 `70EXP` 的 T 账套）、Cowork 推送回归。
7. 上线：用户同意后照生产部署步骤，先服务端再发 CC 正式版；不替用户决定接哪个租户。

## 记录

（施工中补充）
