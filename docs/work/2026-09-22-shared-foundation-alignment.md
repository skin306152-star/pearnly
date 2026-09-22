# ERP / Cowork 共享底座对齐

## 当前任务与暂停范围

用户要求先暂停盘点工作台及容器调整，纠正并行开发的共享分歧，再继续产品前端。仅本地集成与验证；不推送、不部署、不启动正式开户或外部业务。

- main 基线：`4cd39e035e141b53e6b723c70b5d92184ccffadc`，上一轮 ERP 多仓位数据流已合入。
- Cowork：`c17f475198eca708671796d49f10c04ccd73bb98`，干净；后端继续保留在其任务分支。
- 盘点：`codex/stocktake-workbench` 从 main 开始，未完成修改独立保留，不能标为验收通过。
- 本次集成工作树：`/Users/skin/.codex/worktrees/shared-foundation-align/pearnly`，分支 `codex/shared-foundation-align`。

原因：ADR-009 的拆分提交 `b92cd63` 留在 Cowork 分支，未进入 main；ERP 接续时遗漏了此依赖检查，继续向旧导航、目录和总词库增加内容。两条线共同基线 `21581e4` 之后有 12 个交叉文件，不能任选一侧覆盖。

## 本批取舍

| 内容 | 对齐方式 |
| --- | --- |
| 产品功能目录 | 复用 ADR-009 文件聚合，ERP 最新盘点、商品读取、公司只读与成本字段授权保留在 ERP `features.json`。Control 只聚合，Core provisioning 原有权限消费保留。 |
| 菜单和分组 | ERP 17 项目录、10 个可用页面及原生分组移到 ERP hooks。Core 按已安装 App 聚合、权限过滤和原生分组渲染；不保存某个受限用户的小菜单到共享 Sidebar。 |
| Cowork | 只移入目录/菜单声明，不复制整个 hooks 或未合入后端。保留其已确认取消 WeKan 的决定，仅开放 home/intake；其余页面不会因后端存在就放出。 |
| 词库 | 从当前 main 实际总表按 App 分配，ERP 增量不丢；删除旧总表，使用拆分后的生成器与守卫。除目录字段说明随新路径改名，四语输出保持原文。Cowork 后端新增词条在同步时保留于 Cowork 自己的表。 |
| 通用列表 | 保留 main 的整行进入、选择、分页、原生构造逻辑；把盘点分支已实现的可选原生空态动作接回共享版本，不包含盘点页面实现。未提供动作的页面仍为只读空态。 |
| 运行依赖 | ERP 两个实验配方补齐 Cowork/DMS 的纯 JSON 声明挂载；控制面不安装或导入产品 App。正式 Dockerfile 本来复制全部 App，构建时应使用同一提交。 |
| 不合入 | Cowork 整个后端、DMS 台账下沉 `625c3d0`、Core OCR 工作流后端增量；它们不是本次目录拆分的前置依赖。 |

## 后续开发顺序与归属

1. 本批共享声明、组件和检查先进入本地主线；记录唯一基线 SHA。
2. Cowork 与 ERP 任务分支先合入同一基线，解决产品词条/声明增量；核对共享消费者一致后，才继续业务页面。不要把“某个分支里已经做过”当成主线已经具备。
3. 商品、采购、库存、盘点、销售、POS 页面与业务归 ERP App；Cowork 页面与业务归 Cowork App。新增导航、功能、词条只改各自声明。
4. 共用组件及加载契约本轮由「查看 ERP UI 交接内容」窗口统一集成。另一个窗口有共享需求，先登记具体入口/用途/接口，避免同时写共享文件；已有通用能力直接复用。
5. 盘点保持暂停，后续仍按“任务列表 → 单任务工作台”两级、实际仓位/数量、连续扫码填写保存下一项、复核确认结束只读推进。
6. 业务完成后再做候选构建：固定源码提交 → 正式 Dockerfile 的 linux/amd64 镜像 → 正式服务拓扑/站点初始化/统一登录 → 本机外部浏览器验收。当前规则实验室不属于部署等价验收，不以开发服务器通过替代镜像验收。
7. 最终业务验收、主线全部业务收尾和生产部署仍后置；本批仅宣布已实际完成的共享集成。

## 本地验证与限制

- ERP/DMS 1,088 种功能选择组合：新旧 catalog、roles_payload（含字段权限）、workspace_payload 完全一致。
- 锁定 Frappe 镜像 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`（arm64，本批规则测试，非生产候选）：ERP 106、导航 17、目录 16、架构守卫 16、开户拒绝 17、DMS 入口 4 项通过。
- DMS 零 IO 回归 207 项、原生 Desk/记录/选择 Node 回归通过。架构检查、四语生成、前端词条检查通过。
- `check-i18n.py --strict` 仍报 main 已有 113 项（95 内部文字、18 库存导入匹配词），本批未新增；不能写成全仓语言审计通过，不增加忽略来掩盖。
- 真实 Site：独立 project `pearnly-shared-foundation-lab`，自有 DB/Site/Redis，内部网络、无外部凭据。商品权限与租户隔离通过；控制面仅装 Core/Control 的目录重复同步、ERP/DMS 按已装 App 导航隔离、原生侧栏分组、成本字段授权、个人顺序保存及无权/访客拒绝全部通过。
- CI、生产部署、本轮外部浏览器与用户业务验收均未执行。未改共享 qerp 数据，也不把旧截图当本批证据。

## 同步和资源回读

- 共享代码提交 `db4c7dc3b8d01d19d5aac8395125b6ca1f9059bd` 已 fast-forward 到本地主线。
- ERP 盘点未完成快照 `e510bf3` 单独保留；同步提交 `fc134cd` 已包含该共享基线。31 条新增/修改词条按 App/msgid 迁入 ERP 表；Core、Control 和翻译工具相对 main 无差异，工作树干净。该快照不代表盘点业务已验收。
- Cowork 同步提交 `db8aa9177e5912ff72dc745d881616e969d69665` 已包含共享代码基线；工作树干净。回读 ERP App 全目录及 Core 导航/分组/Desk JS/provisioning、Control features.py 与该基线逐字节一致。Cowork 503 条业务词条及后端、DMS 台账下沉、Core execution 增量与域检查按原分支保留，不称全仓业务已合并。取消 boards 后的默认说明和 App 描述也统一回主线，避免留下纯文案分歧。
- 远端只读回查：`refs/heads/main=ac33e0c7362915ae314295cb26f7748cb5589de0`；本批集成、盘点、Cowork 任务远端分支均不存在。未推送、未部署，不称本地与远端代码一致。
- 本批 `pearnly-shared-foundation-lab` 的 3 个容器、网络、DB/Site 卷及已登记的匿名日志卷已删除，并回读无工作树容器引用。只含本次合成验证数据，无用户业务数据。
- 盘点工作树 `/Users/skin/.codex/worktrees/stocktake-workbench/pearnly` 与分支保留；3 个隔离实验容器已停止，数据卷保留。负责人为 ERP UI 任务，待用户恢复后复核，业务完成并合入主线后清理。
- Cowork 工作树/分支保留，归 Cowork 后端任务，等待下一步前端开发及最终集成；本批不删除其代码、实验环境或记录。
- 旧 `codex/erp-pos-migration` 已是 main 祖先，但 `/Users/skin/.codex/worktrees/erp-pos-migration/pearnly` 仍被 `pearnly-erp-pos-provision-lab-maintenance-1` 挂载，归原 ERP 迁移任务，本批保留；原任务解除挂载并核对独有/忽略内容后再清理，不作为新开发基线。
- 共享 `pearnly-sample` / qerp、已有 `pearnly-erp-pos-lab` 与控制面运行状态均未调整；后续实验室重新启用须按已更新的 Compose 重建缺少声明挂载的旧 bench，不能继续使用旧运行配置。

本批集成工作树及本地分支在本提交合入 main 后删除；任务远端分支不存在。整体盘点/Cowork 工作状态为交接保留，不宣布全仓任务清理完成。

### 收尾同步要求

共享功能基线为 `db4c7dc`；本记录所在的后续提交只统一取消看板的描述并记录收尾。ERP/Cowork 各再合入本收尾提交，保持共享源码与最新本地主线一致。旧总词库不存在，只有产品词库。继续开发前读本记录与当前 AGENTS/ADR-009；盘点和 Cowork 前端仍保持暂停。
