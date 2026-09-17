# infrastructure/

本地与目标机的运行定义。当前为空，等待 P1。

## 已知基线（本机验证过的本地 Frappe 样板，仅作输入）

来自 `~/pearnly-erp/scripts/business-sample` 的镜像与卷，2026-09-17 只读核对：

- Frappe `16.33.0`；ERPNext `v16.34.1`（commit `0b50853985312bc64977f9324c55b5d8c1ab2e59`）pinned
- 镜像 `pearnly-ci:sample-2f779849c014-d986389078ad`，架构 **arm64**，创建于 2026-09-13
- 该样板内含 apps：`frappe`、`erpnext`、`erpnext_thailand`、`pearnly_branding`、`pearnly_business`、`pearnly_tenant_manager`、`pearnly_thailand`
- 数据：MariaDB `11.8`（卷 377MB）、Redis `6.2`

这不是本仓的版本决定，只是待核对的输入。P1 需要重新锁定矩阵。

## 尚未具备

- P1.07 要求的完整样板：6 业务 Site + 控制 Site + Keycloak + 2 WeKan
- 12GB 资源可行性报告（P1.08）
- Keycloak 与各产品运行组的网络、卷、凭据分离定义

本机开发环境与目标机差异必须记入结论：本机是 arm64，目标 Contabo VPS 是 x86_64，两者资源数字不能互相代替。
