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
- 本次范围状态：已完成（已上线）
- 收尾状态：已合并、已部署，工作树与分支在收尾时删除
- 分支 / 基线 SHA / 工作树 / 集成目标：`codex/cowork-work-boards` / 开工 `43dcf12f`，已变基到 `413bb5bb`（对账修复，无冲突）/ `.claude/worktrees/eager-williamson-e6a07a` / `main`
- 合并 / 推送 / 远端回读状态：main 快进到 `0937d68d` 并推送，远端回读一致（见「合并与部署」）
- 允许修改的路径：`apps/pearnly_cowork/pearnly_cowork/work/`、新 DocType `cowork_work_board` / `cowork_work_board_member`、
  已有 `cowork_work_*` DocType 加 `board` 字段、`patches/`、`patches.txt`、`hooks.py`（权限登记）、`public/desk/work.js` / `work.css`、
  `i18n/messages.tsv` 与生成的翻译包、`scripts/test-cowork-work-native.py`、新脚本 `scripts/test-cowork-work-boards-native.py`、`tests/`、
  `docs/development/architecture.md`（`work` 域一行）、本记录。不动共用层（`pearnly_core`），所以不需要与 DMS 窗口约共用层改动。
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
| 团队可见看板上的非成员（有任务管理权限） | ✓ | ✗ | 评论设置=成员和观察者 | ✗ | ✗ |
| 默认看板上的非成员（有任务管理权限） | ✓ | ✓ | 评论设置≠关闭 | ✗ | ✗ |
| 私有看板上的非成员 | ✗ | ✗ | ✗ | ✗ | ✗ |
| 没有任务管理权限 / 停用 | ✗（进不了页面） | | | | |

决定（用户 2026-09-29 看过 Trello 社区抱怨后定 **B**）：团队可见 = 有任务管理权限的人**都能看，只有看板成员能改**，
没加进来的人按观察者算（评论看「谁能评论」）；**默认看板例外**，有任务管理权限的人照常能看能改，现有用户与以后新加的人都无感。
起因：Trello 社区反复抱怨团队可见的板"不相干的人加入后乱改"，Trello 后来加了"团队成员能否直接加入编辑"的开关；我们不加开关，直接取只读。
负责人（含自动化「分派给成员」）只能是能改这块板的人；能看的人可以被 @、被加关注。
成员管理「所有成员」时普通成员只能加 / 减成员和观察者，改角色、动管理员只有看板管理员；任何人都可以把自己移出看板（离开）；
已有管理员的看板不能被减到一个管理员都没有。评论：自己的能删，看板管理员与 Cowork 管理员也能删别人的（照 Trello，管不当评论；同一天用户要求补上）。

### 每条读写路径

| 路径 | 需要的权利 | 说明 |
| --- | --- | --- |
| `boards` 看板列表 | 登录且有任务管理权限 | 只列能看的、未归档的 |
| `board` / `card` / `summary` / `archived` / `search` / `export` | view | 看板级接口带 `board`；`card` 按卡片所在看板 |
| 我的任务 / 日历 / 表格视图 | view | 前端用同一份 `board` 数据，不另外取 |
| 镜像显示 | 镜像所在看板 view + 原卡所在看板 view | 看不到原卡的人看不到这条镜像 |
| 新建 / 改 / 拖 / 完成 / 复制目标 / 模板建卡 / 归档 / 恢复卡片；列表全部写操作；附件增删；被分派为负责人 | edit | 复制：源卡 view + 目标 edit；跨看板移动：两边 edit |
| 镜像新建 | 原卡 view + 目标列表 edit | 移动 / 移除镜像：镜像所在看板 edit |
| 评论 | comment | 删自己的评论：view；删别人的评论：manage |
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
  （`is_default=1`、团队可见、评论 = 成员、成员管理 = 所有成员、不存名字——按各人界面语言显示「团队看板」，有人改名后才存），
  把 `board` 为空的列表 / 卡片 / 标签 / 字段 / 规则 / 镜像挂上去；重跑不重复建、不改已挂好的行。
- 默认看板没有显式成员：现有有任务管理权限的人照常可看可改（默认看板例外）；标签 / 字段 / 自动化从此只有 Cowork 管理员
  （和以后被设为该板管理员的人）能改——这是口径 4、5 要求的变化。
- `after_migrate` 的 `ensure_on_migrate` 改为确保默认看板 + 默认三列。

## 变更与决定

- 纯规则 `contracts.board_rights` / `member_changes`：权限矩阵与成员名单改动的限制（普通成员只能加减成员 / 观察者、只有看板管理员改角色、
  任何人可离开、已有管理员的板不能减到零个管理员）。
- `access`：`rights(board)`（一次请求内缓存，看板设置 / 成员改了清掉）、`viewers(board)`、原生查询条件按可见看板（活动 / 提醒经卡片所在看板；
  自动化只给看板管理员）；已归档的看板只读（只剩看与管理）。
- 新模块 `boards`：默认看板（兜底建完当场提交，同默认列表）、看板列表（默认看板在前）、新建（建的人是管理员，带默认三列与六个颜色标签）、
  设置、归档 / 恢复（默认看板不能归档）、成员名单读 / 存、可分派的人。
- `service` 每个入口改为先取对象、再按所在看板核对权利；看板级接口加 `board`（缺省 = 默认看板）；页面 `board(fallback=1)` 在记着的看板
  没了 / 看不了 / 归档时退回默认看板，不弹错。跨看板移动：标签、字段值只留目标看板有的，负责人 / 关注人只留能看目标看板的人；
  复制：源卡能看 + 目标能改；镜像：源卡能看 + 目标能改，看不到源卡看板的人在目标看板上看不到这条镜像（`foreign` 只给能看的人）。
- 彻底删除已归档卡片 / 列表从「只给 Cowork 管理员」放宽为「能管这块板的人」（看板管理员 + Cowork 管理员）。
- `notify`：实时刷新 `cowork_work_changed` 逐人推给能看该看板的人（不再全站广播，带看板号）；站内提醒收件人按看板过滤；
  提醒列表 / 未读数只算还能看的看板上的卡。`reminders` 跳过已归档看板上的卡。
- 页面：筛选区第一格「看板」切换（每人记在浏览器本地）；右上菜单「新建看板」「看板设置」「看板成员」，标签 / 自定义字段 / 自动化 /
  看板设置只对能管的人出现；只读（观察者、已归档的看板）时没有任何写入口（加卡、加列表、列表菜单、卡片悬停按钮、完成圆圈、拖动、日历拖动），
  卡片详情字段只读、没有保存 / 上传 / 归档，评论框按「谁能评论」；快捷菜单的移动 / 复制 / 镜像先选看板（只列能改的）再选列表；
  从提醒、卡片链接打开别的看板上的卡时先切到那块板；已归档里多一节「已归档的看板」（只列自己能管的，可恢复）。
- **附件的原生读路径**（自查发现）：Frappe 自带的 File 列表条件对"能读卡片 DocType 的人"列出所有卡片的附件，链接附件在原生里又算公开文件，
  私有看板外的人能看到文件名和链接地址。hooks 给 File 加一条本域的查询条件与行权限（`work.access.file_query` / `file_permission`，
  与 `files.access` 的并列生效）：卡片附件只给能看那块看板的人。已确认只有框架原生判据时陌生人读得到、加上后被拒。
- 四语词条 32 条（`cowork.work.board.*`、`cowork.work.archived.boards`、5 条错误），不加说明文字。
- `features.json`、侧栏菜单与角色不变（任务管理不单独售卖；能建看板 = 已有的「任务管理」角色）。

## 合并与部署（2026-09-29，用户："合并到 main 然后部署生产"）

- 合入：任务分支变基到 `origin/main` `b069f31f`（只多两个 Cowork 文档提交，无冲突）→ 本地 CI 同名闸门全过 → 分支 Development checks
  `36469905863` 通过 → 主目录 `main` 快进到 `0937d68d` 并推送，远端回读 `0937d68d`。
- 镜像：build-production `36470114834`（`0937d68d`）→ `ghcr.io/skin306152-star/pearnly/frappe@sha256:3c7be35695c8993958492dafe807c5b634780d13c4b53267e06c4882effe5722`；
  stage-production `36470848088` 成功（第一次 `36470787300` 是我把整段镜像名当成 digest 传入，被输入校验拒绝，未改服务器）。
- 激活前：`testcowork` 先 `lab-stop cowork` 切回线上（线上那份的定时任务恢复）；推送记录无排队 / 发送中（manual 12、prepared 1、success 175），
  识别任务无 queued / running / deferred；`check-deploy-image.py` 一致（本次没改部署工具目录）；可用内存 26 GB；
  已通知 Express 压测窗口（回复无进行中的批次）。
- 激活：`activate-product.sh … cowork` 208 秒，`Product cowork activated with verified image.`，日志 `/opt/pearnly/state/activate-cowork-0937d68d.log`；
  激活前备份 `20260929_021545-testcowork_pearnly_com-*`。上一版 `93264218`（main `413bb5bb`），回退用它与该备份。只动 Cowork，ERP / DMS 未动。
- 线上回读：7 个 Cowork 容器均为 `3c7be356`；补丁 `v0_0_9_work_boards` 已记；默认看板 1 块（团队可见、名字为空 = 按界面语言显示）；
  卡片 12、列表 9、标签 7 全部挂上（空值 0）、规则 0；切换后 10 分钟 Error Log 0；调度开着、维护模式关；`work.js` / `boards.py` 与 main 字节一致；
  公网 ping 200。
- 录制用的演示数据与 somchai / nida 两个账号只在测试机副本里，线上没有。

## 验证

| 时间 | 代码 | 环境与数据归属 | 命令或场景 | 结果 |
| --- | --- | --- | --- | --- |
| 2026-09-29 | 未提交差异（基于 `43dcf12f`） | 本机离线 | `tests/test_cowork_work_boards.py`（12 条：矩阵各身份、成员改动限制、最后一个管理员、坏输入）、`tests/test_cowork_work_contracts.py`（17 条） | 通过 |
| 2026-09-29 | 同上 | 本机一次性容器 `pearnly-boards-test`（样板同一镜像 `pearnly/frappe-sample:16.34.1`，自己的站点卷；连样板 MariaDB 新建独立库 `boards-test.localhost`；不碰共享样板站点） | **升级**：先挂 `43dcf12f` 旧代码建站并造旧数据（3 列表、2 卡、7 标签、1 字段、1 规则、1 镜像、1 评论）→ 换本工作树 `migrate` | 补丁执行；1 块默认看板（团队可见、评论=成员、成员管理=所有成员、无显式成员）；六类记录 `board` 空值 0；再 `migrate` 与直接重跑补丁均不多建 |
| 2026-09-29 | 同上 | 同上 + 新建站 `boards-fresh.localhost`（本工作树直接装） | **新装**：`after_install` 建默认看板、三列、六标签 | 通过 |
| 2026-09-29 | 同上 | 两个临时站点 | `scripts/test-cowork-work-boards-native.py`：每个接口按「成功 / 没有任务管理权限 / 观察者 / 私有看板非成员 / Cowork 管理员」走；另测评论三档、成员加减限制与离开、规则只给管理员（原生列表也读不到）、原生列表与行权限、附件下载、实时刷新接收人、离开看板后提醒消失、到期提醒不发给看不到的人、跨看板移动 / 复制 / 镜像、归档看板只读、默认看板照常可改；整段一个事务，过程中禁止提交，最后回滚 | 通过；回滚后看板 1、临时用户 0、附件 0 |
| 2026-09-29 | 同上 | 两个临时站点 | `scripts/test-cowork-work-native.py`（原有全套；标签 / 字段 / 自动化改由 Cowork 管理员设置，并加测成员保存标签、读规则被拒） | 通过 |
| 2026-09-29 | 同上 | 临时站点 + 内置浏览器（`bench serve` 只绑本机 127.0.0.1:8199，测试账号为本次生成） | 默认看板原样显示旧数据；切换看板；看板管理员的菜单、成员弹窗添加成员、看板设置；观察者：菜单只有提醒 / 新建看板 / 成员 / 已归档，看板上没有写入口，卡片详情只读、无评论框；新建看板后切到新板可编辑；快捷菜单「移动」选另一块看板后卡片移过去 | 通过；过程中修掉：观察者悬停仍出按钮（CSS 优先级）、只读详情报错（给不存在的字段设属性）、只读下拉显示代码、成员弹窗加人后选择框没清空、成员行按钮不齐 |
| 2026-09-29 | `4e44a941` | 临时站点 | 附件：陌生人读私有看板的链接附件、原生 File 列表列私有看板附件；框架原生判据单独判为"可读"（漏洞确认），加钩子后拒绝；两个脚本复跑 | 通过 |
| 2026-09-29 | `1ba042ad`（变基到 `413bb5bb` 后） | 本机离线（CI 同名闸门，Python 3.12 / Node） | architecture guard、native desk contract、feature catalog、LIFF、queue targets、DMS call/identity、`check-architecture`、`check-dms-offline`、provision agent、capacity、activate product、companion contract/receiver、welcome email、purchase amounts、tax documents、Node 各 Desk 测试、`i18n-build --check`、`git diff --check` | 通过（本机默认 python3 是 3.9，三项因 `X \| None` 语法报错，用 3.12 复跑通过；CI 用 3.12） |
| 2026-09-29 | 同上 | 临时站点 | 变基后 `migrate` + 两个脚本在两个站点复跑 | 通过 |

| 2026-09-29 | `4e44a941`（实时源码） | **测试机**：`lab-start cowork`，底座 = 线上 Cowork 同一镜像 `sha256:93264218…`（main `413bb5bb`，digest 由部署窗口告知）；`testcowork.pearnly.com` 已切到测试机 | 起栈时两站 `migrate` ok（补丁执行）；`testcowork` 原有 9 列表 / 12 卡 / 7 标签全部挂到默认看板「团队看板」（团队可见），空值 0；两个脚本在 `testcowork` 与 `cowork.test.localhost` 各跑一遍 | 通过；回滚后两站都只剩默认看板；`work.js` / `work.css` 带版本号地址经 testcowork 取回与源码字节一致 |

本机临时站点 `boards-test.localhost` / `boards-fresh.localhost` 已 `drop-site`，一次性容器 `pearnly-boards-test`、站点卷、`~/.cache/pearnly-boards-test` 已删。

| 2026-09-29 | B 与删评论的未提交差异（基于 `f32900f0`） | 本机离线 + 测试机（`live cowork` 后两站） | `test_cowork_work_boards.py`（14 条，加团队可见非成员只读、默认看板例外）；`test-cowork-work-boards-native.py` 加测：团队板非成员不能建卡 / 建列表 / 移动 / 归档 / 评论（评论设为「成员和观察者」后能评论）、不能被分派、规则不能分派给他但能加关注、加为成员后能改、`can_edit` 标记、默认看板照常可改、观察者不能被分派、看板管理员与 Cowork 管理员删别人的评论；跨看板复制 / 镜像移除按新口径 | 通过（两站） |

**录制（2026-09-29，第 6 集）**：录制窗口在测试机 testcowork 上用主管账号 + somchai / nida 录完（员工账号由用户本人在本地按钮页点一次性链接登录；
按钮页与链接文件录完已删）。演示数据留在测试机副本：私有看板「ปิดบัญชี · นอร์ทสตาร์」、3 张卡、1 条自动化、团队看板与列表改名、主管界面改泰语。
录制实测反馈两处，已修：① 观察者的卡片详情仍显示「添加清单」按钮（只读时清单区的按钮全部去掉）；② 默认看板名按站点语言存成中文，
泰语用户看到中文（默认看板改为不存名字、按各人界面语言显示；看板设置只交改过的项，改别的设置不会把名字存成某种语言）。

**录制用账号（用户 2026-09-29 要求"模拟两个角色进去"）**：只在测试机那份 `testcowork` 库里建 `somchai@example.com`、`nida@example.com`
（`Desk User` + `Cowork Work`，泰语）。站点关着密码登录（`disable_user_pass_login=1`）、库里 Administrator 也有本地密码，所以不打开密码登录，
改用框架自带的一次性登录链接（`frappe.www.login._generate_temporary_login_link`，单次、12 小时），链接存在本机
`~/.config/pearnly-testenv/cowork-recording-login-links.txt`（600）。线上账号中心与统一身份都没写。这两个账号只存在于测试机副本，
下次 `PEARNLY_TESTENV_REFRESH=1` 导入时会被覆盖。链接文件与按钮页已删。

| 2026-09-29 | 录制反馈两处修正（基于 `33cb1eaf`） | 测试机 `live cowork`（DocType 变了，两站自动 migrate ok） | 两个脚本两站复跑；新加断言：默认看板不存名字时按界面语言显示、改其它设置后名字仍为空 | 通过；观察者清单按钮去掉这一处只改了页面，未在浏览器复看 |

`python3 scripts/check-i18n.py --strict` 的失败是存量（其他模块硬编码文案），本任务文件不在报告里。

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 完成 | |
| 本地验证 | 通过 | 见上表；实时刷新只验了接收人名单（替身），没在两个真会话里看 |
| 测试机 | 通过（脚本）；第 6 集已在测试机录完 | 已 `lab-stop cowork` 切回线上，测试栈停、卷保留（内有录制演示数据与两个录制账号） |
| CI | 通过 | 分支 Development checks `36469905863`、build `36470114834`、stage `36470848088` |
| 部署 | 已执行 | 生产 Cowork `0937d68d`（`sha256:3c7be356…`），见「合并与部署」 |
| 外部业务结果 | 不适用 | 无外部写入 |
| 用户 / 真机验收 | 未执行 | 录制窗口在测试机上按主管 + 员工视角实操过（非用户验收）；线上尚无用户试用 |
