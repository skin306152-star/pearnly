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
- 浏览器检查发现原生窄屏样式会隐藏除最后一级之外的全部节点。使用框架 `menu_items` 在末级提供首页、盘点和任务的原生下拉菜单；阻止菜单点击冒泡给路由器，避免菜单刚打开就被当前页重绘关闭。未改框架 CSS。
- 数据读取前先提供返回路径；失效任务、空记录、异步结果晚于离开页面都不能破坏当前导航。记录读取使用现有受权 API，并核对盘点行属于任务。

## 验证与交付

- 开工架构检查：0 违规。
- 修改后架构检查、两份 JS 的 `node --check`、`git diff --check`、`i18n-build.py --check` 均通过；ERP 权限契约测试运行 106 项，6 项跳过，其余通过。
- `tests/test_native_desk_contract.py` 15 项中有 3 项失败；在未修改的 `5d1c067` 主目录复跑，失败完全相同：公司主档缺少 Page 元数据、ERP 菜单期望 14 实际 16、可用页面集合仍按早期 6 页断言。属于既有缺口，本次未修改测试或扩大忽略。
- 本地定向浏览器验证通过（代码 `8148885`，2026-09-22）：
  - 从 `STC-00019` 进入 `STC-ITEM-00020` 的空记录，显示“无数据”，能按面包屑回任务再回盘点列表；列表不残留记录指示器。
  - 有记录的 `STC-ITEM-00021` 正常显示数量 6，回任务正常；未点撤销、结束或过账。
  - 空记录深层地址硬刷新后保留任务和行；浏览器后退/前进恢复对应层级。
  - 从任务进入独立差异报表，路径包含任务与看板；报表硬刷新后仍保留父级，能返回原任务。
  - 390×844 浏览器视口中，报表末级菜单可返回任务，空记录末级菜单可直接返回盘点列表；验证后已取消视口覆盖。这是响应式浏览器验证，不是真机或 LINE 验收。
  - 构造不存在的记录行地址，错误状态仍能回到有效任务；没有创建或删除业务数据。
- 无生产部署、外部写入或业务数据变更；用户验收待确认。

## 本地集成与收尾

- 实现提交：`cc0d5e5`、`8148885`，均已 fast-forward 到本地 `main`；本文件及交接入口更新随最后的文档提交一起集成。
- 本地 ERP 沿用主目录挂载；清除 `qerp.localhost` 缓存并重启 `erp-web` 后，浏览器实测使用新代码。未做 schema migrate，未重启其他产品服务。
- 远端回读：`origin/main` 实际为 `ac33e0c7362915ae314295cb26f7748cb5589de0`；远端 `codex/erp-stock-count-breadcrumbs` 不存在。本次不推送。
- 本任务工作树 tracked/untracked/ignored 清单为空，无符号链接、相关进程、运行或停止容器挂载。成果已纳入本地主线，最后文档提交集成后按非强制删除移除本任务工作树和本地分支。
- 运行中的 `pearnly-sample` 与演示数据继续供用户查看，源码来自长期主目录；不属于待删除资源。此前 `erp-pos-migration`、`cowork-backend-migration` 两工作树属于原迁移任务，不在本次删除范围。
