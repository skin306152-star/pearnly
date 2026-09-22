# ERP 盘点面包屑修复

## 目标与基线

- 用户要求：空盘点记录能通过面包屑返回；独立差异看板也纳入同一条路径。
- 基线：本地 `main` 的 `5d1c067fabb478ad8bca889bbe57db5a5b58adcf`。
- 分支：`codex/erp-stock-count-breadcrumbs`；工作树：`/Users/skin/.codex/worktrees/erp-stock-count-breadcrumbs/pearnly`。
- 归属：`pearnly_erp_portal` 的盘点 Desk 页面与差异 Script Report。只改前端导航，不改账务、权限、数据结构或外部接口。
- 本地演示站点：`qerp.localhost:8102`，Compose `pearnly-sample`、Docker context `colima-pearnly`；ERP 源码挂载长期主目录 `/Users/skin/pearnly/apps`，Site 卷为 `pearnly-sample_sites-erp`。不切换共享挂载。

## 原因与实现

- 旧页面的列表、任务、记录共用 `/desk/pearnly-stock-count`；任务和记录层级只临时追加到面包屑 DOM。Frappe `container.change_to()` 会调用 `breadcrumbs.update()`，按注册数据重建后只剩“盘点”，临时点击处理也消失。
- 使用原生 Desk 子路由：任务为 `/desk/pearnly-stock-count/<任务>`，记录为该任务下的 `/records/<盘点行>`；页面每次显示按路由恢复，支持刷新和浏览器前进后退。
- 原生 Custom 面包屑只支持一个节点，故在 ERP 资产中对 `set_custom_breadcrumbs` 作小范围扩展：仅消费带 `pearnly_stock_count_trail` 的节点，其他页面仍走原函数；所有节点仍由原生 `append_breadcrumb_element` 渲染。路径保存在原生注册数据中，父级使用真实地址，任务名转义。
- 差异看板通过 report 的 `onload` 和任务筛选变更更新“首页 / 盘点 / 任务 / 盘点差异看板”；直接打开报表也能恢复父级。
- 数据读取前先提供返回路径；失效任务、空记录、异步结果晚于离开页面都不能破坏当前导航。记录读取使用现有受权 API，并核对盘点行属于任务。

## 验证与交付

- 开工架构检查：0 违规。
- 修改后架构检查、两份 JS 的 `node --check`、`git diff --check`、`i18n-build.py --check` 均通过；ERP 权限契约测试运行 106 项，6 项跳过，其余通过。
- `tests/test_native_desk_contract.py` 15 项中有 3 项失败；在未修改的 `5d1c067` 主目录复跑，失败完全相同：公司主档缺少 Page 元数据、ERP 菜单期望 14 实际 16、可用页面集合仍按早期 6 页断言。属于既有缺口，本次未修改测试或扩大忽略。
- 本地定向浏览器验证待完成：空记录返回、有记录返回、硬刷新、浏览器前进后退、报表进入与返回、独立打开报表、异常路径。
- 无生产部署、外部写入或业务数据变更；用户验收待确认。
- 暂时保留本任务工作树用于验证；完成本地集成、验证且确认无占用后删除。
