# 工作包：Cowork 任务管理——多看板与看板权限

## 目标和范围

- 目标 / 完成条件：Cowork「任务管理」（`pearnly-work`）从"一个团队一块板"变成照 Trello 的多块看板：每块看板有自己的成员、
  角色和可见范围；现有那块板成为默认看板（团队可见），卡片 / 列 / 标签 / 字段 / 规则原样保留、用户无感。
  所有读写路径在服务端按看板权限过滤；测试机上由用户试用。
- 用户口径（2026-09-29）：
  1. 参照 Trello 做多块看板；现有板变默认看板、可见性「团队可见」，现有数据原样保留。
  2. 可见性两档：私有（只有看板成员）、团队可见（有「任务管理」权限的人）。不做公开链接。
  3. 看板角色：看板管理员、成员（增改卡片 / 列）、只读观察者（只能看，评论按看板设置）。
  4. 标签、自定义字段只有看板管理员能新建 / 改名 / 删除；成员照常贴已有标签、填字段值。
  5. 自动化（规则）保留，只给看板管理员：其他人看不到入口、服务端拒绝读写规则；已有规则照常按所在看板生效。
  6. 「任务管理」权限（`Cowork Work`）= 能进任务管理、能自己建看板（建的人是该看板管理员）；Cowork 管理员（`Cowork Admin`）
     能看到、管理所有看板（含私有）。
  7. 卡片不单独设权限。
  8. 看板管理员可设：谁能评论（成员 / 成员和观察者 / 关闭）、谁能加减成员（仅管理员 / 所有成员）。其余 Trello 细项不做。
- 本次不做：公开链接、卡片级权限、跨看板的「我的任务」汇总、看板背景 / 星标 / 模板看板 / 看板彻底删除、列表跨看板移动。
- 本次范围状态：进行中
- 收尾状态：施工中
- 分支 / 基线 SHA / 工作树 / 集成目标：`codex/cowork-work-boards` / `43dcf12f` / `.claude/worktrees/eager-williamson-e6a07a` / `main`
- 合并 / 推送 / 远端回读状态：未合并、未推送
- 允许修改的路径：`apps/pearnly_cowork/pearnly_cowork/work/`、新 DocType `cowork_work_board` / `cowork_work_board_member`、
  已有 `cowork_work_*` DocType 加 `board` 字段、`patches/`、`patches.txt`、`hooks.py`（权限登记）、`public/desk/work.js` / `work.css`、
  `i18n/messages.tsv` 与生成的翻译包、`scripts/test-cowork-work-native.py`、`tests/`、本记录。不动共用层（`pearnly_core`）。
- 运行资源：测试机 Cowork 栈（`testcowork.pearnly.com`，需与占用窗口约时间）；本机只用一次性容器 + 临时站点，不接管共享样板。
- 必要外部操作：无外部写入。合并、生产部署各需用户同意。

## 设计

### 入口与依赖方向

| 项 | 落点 | 说明 |
| --- | --- | --- |
| 纯规则 | `work/contracts.py` 新增 `board_rights()` 与可见性 / 角色 / 评论 / 成员管理的取值 | 不依赖框架与 IO；权限矩阵只在这里算一次 |
| 判据 | `work/access.py`：`rights(board)`、`require_right`、`viewers(board)`、原生查询条件与行权限 | 原生资源与专用接口共用 |
| 看板服务 | 新模块 `work/boards.py`：默认看板、看板列表、新建、设置、成员、归档 / 恢复、默认列表与标签 | 只依赖 `access` / `contracts` / `documents` / `notify`，不反向导入 `service` |
| 卡片 / 列表服务 | `work/service.py` 每个入口改为先取对象所在看板、再按所需权限核对 | 读写都不再用"整站一块板" |
| 接口 | `work/api.py` 新增看板接口；看板级接口加 `board` 参数（缺省 = 默认看板） | 读 GET、写 POST |
| 页面 | `public/desk/work.js`：页面原生筛选区第一个字段「看板」切换；右上菜单「新建看板」「看板设置」「成员」 | 原生 Dialog / FieldGroup / DataTable；观察者的页面不出现写入口 |
| 依赖方向 | 不新增域依赖（仍只依赖 `access`） | `architecture-policy.json` 不变 |

### 数据

- 新 DocType `Cowork Work Board`：`workspace`（站点内部归属，仍以 `scope.default_workspace()` 为锚）、`title`、`visibility`（team / private）、
  `commenting`（members / observers / disabled）、`member_management`（admins / members）、`is_default`、`archived`、子表 `members`。
- 新子表 `Cowork Work Board Member`：`user`、`role`（admin / member / observer）。
- `Cowork Work Column` / `Card` / `Label` / `Field` / `Rule` / `Mirror` 加 `board`（带索引）。活动与提醒不加：按卡片所在看板过滤，
  卡片跨看板移动时不需要改历史行。
- 写入仍只走 `documents.persist`（原生接口只读、DocShare 拒绝），`Cowork Work Board` 同一道写入闸。

### 权限矩阵（`contracts.board_rights`）

权利：`view` 看、`edit` 增改卡片 / 列表、`comment` 评论、`members` 加减成员、`manage` 看板设置 / 标签 / 字段 / 自动化 / 归档 / 彻底删除。

| 身份 | view | edit | comment | members | manage |
| --- | --- | --- | --- | --- | --- |
| Cowork 管理员（任何看板，含私有） | ✓ | ✓ | ✓ | ✓ | ✓ |
| 看板管理员 | ✓ | ✓ | ✓ | ✓ | ✓ |
| 看板成员 | ✓ | ✓ | 评论设置≠关闭 | 成员管理=所有成员 | ✗ |
| 看板观察者 | ✓ | ✗ | 评论设置=成员和观察者 | ✗ | ✗ |
| 团队可见看板上的非成员（有任务管理权限） | ✓ | ✓ | 同成员 | ✗ | ✗ |
| 私有看板上的非成员 | ✗ | ✗ | ✗ | ✗ | ✗ |
| 没有任务管理权限 / 停用 | ✗（进不了页面） | | | | |

决定：团队可见 = 有任务管理权限的人**都能看、能改**（Trello 工作区可见的原文口径），这样默认看板对现有用户和以后新加的人都无感；
显式设成观察者的人按观察者算（管理员可以把某人限成只读）。成员管理「所有成员」时普通成员只能加 / 减成员和观察者，
改角色、动管理员只有看板管理员；任何人都可以把自己移出看板（离开）；已有管理员的看板不能被减到一个管理员都没有。

### 每条读写路径

| 路径 | 需要的权利 | 说明 |
| --- | --- | --- |
| `boards` 看板列表 | 登录且有任务管理权限 | 只列能看的、未归档的 |
| `board` / `card` / `summary` / `archived` / `search` / `export` | view | 看板级接口带 `board`；`card` 按卡片所在看板 |
| 我的任务 / 日历 / 表格视图 | view | 前端用同一份 `board` 数据，不另外取 |
| 镜像显示 | 镜像所在看板 view + 原卡所在看板 view | 看不到原卡的人看不到这条镜像 |
| 新建 / 改 / 拖 / 完成 / 复制目标 / 模板建卡 / 归档 / 恢复卡片；列表全部写操作；附件增删 | edit | 复制：源卡 view + 目标 edit；跨看板移动：两边 edit |
| 镜像新建 | 原卡 view + 目标列表 edit | 移动 / 移除镜像：镜像所在看板 edit |
| 评论 | comment | 删自己的评论：view |
| 关注 / 取消关注（只改自己） | view | |
| 提醒列表 / 已读 | 本人 | 只列还能看的看板上的卡 |
| 标签、自定义字段的增删改 | manage | 贴标签、填字段值 = edit |
| 自动化读 / 写 | manage | 页面入口只给 manage；规则执行不看操作者权限，按规则所在看板生效 |
| 彻底删除已归档卡片 / 列表 | manage | 原来只给 Cowork 管理员，现在看板管理员也可以 |
| 看板设置、归档 / 恢复看板 | manage | 默认看板不能归档 |
| 看板成员增删改 | members（改角色 / 动管理员需 manage） | 自己离开不受限 |
| 新建看板 | 有任务管理权限 | 建的人成为看板管理员 |
| 附件下载（原生私有 File） | 卡片的原生行权限 → view | `access.has_permission` 按看板判 |
| 原生 REST / 列表 | view（查询条件按可见看板） | 只读；写仍被写入闸拦 |
| 实时刷新 `cowork_work_changed` | 只推给能看该看板的人 | 不再全站广播 |
| 站内提醒（分派、@、评论、换列、完成、到期 / 过期） | 收件人必须能看该看板 | `notify.send` 按看板过滤；可 @ / 可分派的人 = 能看该看板的人 |
| 规则「分派 / 关注」的人 | 能看该看板的人 | 保存时校验；执行时再过滤一次 |

跨看板移动卡片：标签、字段值只保留目标看板有的；负责人、关注人只保留能看目标看板的人。

### 迁移

- 补丁 `v0_0_9_work_boards`（post_model_sync，幂等）：每个有看板数据的归属（以及站点归属）确保一块默认看板
  （`is_default=1`、团队可见、评论 = 成员、成员管理 = 所有成员、名字按站点语言），
  把 `board` 为空的列表 / 卡片 / 标签 / 字段 / 规则 / 镜像挂上去；重跑不重复建、不改已挂好的行。
- 默认看板没有显式成员：现有有任务管理权限的人按团队可见照常可看可改；标签 / 字段 / 自动化从此只有 Cowork 管理员
  （和以后被设为该板管理员的人）能改——这是口径 4、5 要求的变化。
- `after_migrate` 的 `ensure_on_migrate` 改为确保默认看板 + 默认三列。

## 变更与决定

（施工中）

## 验证

（施工中）

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 施工中 | |
| 本地验证 | 未执行 | |
| 测试机 | 未执行 | 测试机当前被「Take over Express stress test findings」窗口占用，需约时间 |
| CI | 未执行 | |
| 部署 | 未执行 | 需用户同意 |
| 外部业务结果 | 不适用 | 无外部写入 |
| 用户 / 真机验收 | 未执行 | |
