# 盘点差异看板：空菜单与冻结列文案

- 用户要求：右上角三个点不要弹空菜单；说明并翻译列菜单的 Freeze。
- 基线：本地 `main` / `8e599ca538f23a05f0b3e1127ed80b3524566a93`；任务分支 `codex/erp-variance-menu`，工作树 `/Users/skin/.codex/worktrees/erp-variance-menu/pearnly`。
- 范围：ERP 的盘点差异 Script Report、术语表及其生成语言包；不改通用框架、权限、库存或业务数据。
- 原因：裁剪管理菜单后仅剩原生 `visible-xs` 刷新项，桌面隐藏了操作，却没有隐藏菜单按钮。使用原生 Page 的 show_menu/hide_menu，根据操作自身的响应式显示状态控制菜单；加载、刷新、重新进入和窗口缩放均同步，限定当前差异报表。
- Freeze/Unfreeze 来自安装版本的 `frappe-datatable/src/defaults.js`，分别调用原生 `setColumnSticky(..., true/false)`。含义为横向滚动时固定/解除固定该列。Frappe 的 DataTable 翻译映射未包含两项，故显示英文。
- 通过官方 `get_datatable_options` 扩展该报表的翻译映射，保留原生列操作；文案为“冻结列 / 取消冻结列”，从 `messages.tsv` 生成中/英/泰/日四份语言包。
- 静态检查：架构、JS 语法、diff、术语表检查通过；两条新键的四种语言与生成 CSV 逐项一致。
- 两个全仓严格语言检查原本失败：`check-i18n.py --strict` 113 条，`check-frontend-i18n.py --strict` 127 条。修改前主目录与候选工作树的退出码、完整输出完全一致；未放宽检查或修改忽略清单。
- 本地浏览器验证通过：919px 桌面不显示空菜单，刷新按钮保留；列菜单显示“冻结列”，实际点击后切换为“取消冻结列”，解除后恢复；390px 窄屏菜单保留“刷新”，点击后报表重新生成；恢复桌面宽度后空菜单再次隐藏。测试对象为 `STC-00019`，未修改业务记录。
- 实现提交 `f6390dea9a972d998a0527e9fffe9bb53a5f91e5` 已快进合入本地 `main`，清缓存并重启本地 ERP Web 后验证。运行沿用 Docker context `colima-pearnly`、Compose `pearnly-sample`、Site `qerp.localhost`，源码挂载 `/Users/skin/pearnly/apps`；不切换共享挂载。
- 收尾核对：任务工作树无未保存内容、ignored 文件、符号链接、进程或容器挂载，独有提交已被本地 main 包含；记录提交合回后删除任务工作树与本地分支并回读。远端任务分支不存在，远端 main 核验为 `ac33e0c7362915ae314295cb26f7748cb5589de0`。
- 本地 ERP 服务和浏览器页面保留供用户查看；另两份迁移工作树属于其他任务，不处理。本任务未推送、未部署生产、未写外部系统；用户验收待确认。
