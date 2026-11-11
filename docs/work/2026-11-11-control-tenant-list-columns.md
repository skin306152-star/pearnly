# 需求影响评估：控制面租户列表显示用户名与产品

基线 `6275e4999`（origin/main），分支 `pearnly/tenant-product-column`。

## 0. 需求原文与理解
用户 2026-11-11 开 klm 租户后看本机控制面「租户」列表：「这个界面加一个那个租户是什么产品」「还有用户名」。
即列表上直接看到每个租户开了哪些产品（erp / cowork / dms）和开户时的登录用户名，不用点进部署或发号单。

## 1. 现有同类与复用
数据都已有：产品在「部署」（Pearnly Deployment.product），用户名在「发号单」（Pearnly Issue.account_username）。不新增数据来源。

## 2. Frappe 原生能力
用 DocType 字段 `in_list_view` + `read_only` 的原生列表列；不写列表 JS、不在浏览器里拼数据。

## 3. 按类通用
对所有租户一致：由部署记录的 on_update / after_delete 维护，补丁 `v0_0_5_tenant_list_summary` 给已有租户回填一次。

## 4. 上下游数据
只读汇总；不改开户、拆除、计费流程。租户删除时 `refresh_summary` 发现租户已不存在直接返回。

## 5. 隔离与权限
控制面只给 System Manager；字段沿用 Pearnly Tenant 的权限。

## 6. 入口
只改本机控制面「租户」列表与表单（两个只读字段）。不碰客户产品的网页、手机、LINE。

## 7. 共用件与连带回归
不改登记的共用件。生产镜像里也有 pearnly_control，只装在 fatcc.com 入口站上，那里的 Pearnly Tenant 表下次部署时多两列空字段，不影响业务。本机控制面直接挂主目录源码，合入后在本机执行 migrate 即生效。

## 8. 存储与成本
两列 Data，无新增费用。

## 9. 删除、作废、追溯
无新增删除路径；拆除流程删部署时同样触发刷新（租户已删则跳过）。

## 10. 文案与官方 UI
列名沿用已有词条 Username（用户名）、Product（产品），无新增或改动文案（check-copy：新增 0、改动 0）。

## 11. 测试与验收
- `tests/test_control_tenant_list_summary.py`：顺序去重、用户名来源、已删租户、字段与钩子接线。
- 合入后本机控制面 migrate，回读 12 个租户的两列，并打开列表页核对。

## 12. 要问用户 / 客户的点
| # | 问题 | 我的建议 | 不定的影响 |
| --- | --- | --- | --- |
| — | 无 | — | — |

## 13. 工作量与排期
代码与测试约 20 分钟，闸门约 10 分钟，本机 migrate 1 分钟。不需要生产部署。
