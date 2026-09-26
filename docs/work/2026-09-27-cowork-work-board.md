# 工作包：Cowork 工作协调（团队看板）

## 目标和范围

- 目标 / 完成条件：打开 Cowork 侧栏「工作协调」（`pearnly-work`），做成 Trello 式团队看板：一个团队（站点）一块板，
  固定三列 **待办 / 进行中 / 已完成**；卡片可建、拖动换列与排序、打开详情编辑（负责人、到期日、标签、清单、描述、
  附件、评论与活动）、归档 / 恢复。桌面与手机都能用，测试机上由用户试用通过。
- 用户口径（2026-09-27）：一个团队一块板；固定状态（三列）；**不要审核**（拖到「已完成」就算完成）；先只做网页；
  成员与权限用 Cowork 自己的团队管理（勾「工作协调」= 角色 `Cowork Work`，管理员 `Cowork Admin`）；不计费、
  不单独售卖（`features.json` 不加功能码）；LINE 先不接；和录入**不打通**。参考对象是用户试用的 Trello（本工作包先实际点过其核心功能）。
- 本次不做：LINE 通知 / 富菜单解锁；与录入、对账、盘点的关联；多块面板、自定义列、自动化规则、日历 / 规划器 / 其他视图；
  公开分享；站内通知铃铛（产品站按 ADR-007 关着）。
- 本次范围状态：进行中
- 收尾状态：施工中
- 分支 / 基线 SHA / 工作树 / 集成目标：`codex/cowork-work-board` / `5b8af27`（开工时 `af9aa34`，2026-09-27 按 ERP 窗口通知变基到
  `5b8af27`，无冲突）/ `/Users/skin/pearnly/.claude/worktrees/loving-gagarin-82ed99` / `main`
- 合并 / 推送 / 远端回读状态：未合并、未推送；已变基到 `origin/main` `c11e7a2`（ERP 发布记录，无冲突）；测试机实时源码挂本工作树
- 允许修改的路径：`apps/pearnly_cowork/pearnly_cowork/`（新域 `work/`、新 DocType、新页面、hooks、术语表、翻译包）、
  `docs/development/architecture-policy.json`（新域依赖登记）、`docs/development/architecture.md`（Cowork 域表）、`tests/`、本记录。
- 运行资源：测试机 Cowork 栈（`testcowork.pearnly.com`，实时源码挂本工作树）；本机样板 `pearnly-sample` 的 Cowork 容器（挂本工作树）。
- 必要外部操作：无外部写入。生产部署每一步另需用户同意。

## 设计

| 项 | 落点 | 说明 |
| --- | --- | --- |
| 业务域 | `pearnly_cowork/work/`：`contracts`（纯规则）、`access`（判据与原生钩子）、`documents`（控制器写入闸）、`service`、`api` | 依赖只有 `access`（站点内部归属）；登记在 `cowork_domain_dependencies` |
| 数据 | `Cowork Work Card`（卡片）+ 子表 `Cowork Work Card Assignee` / `Cowork Work Card Label` / `Cowork Work Checklist Item`；`Cowork Work Label`（全板标签）；`Cowork Work Activity`（评论与活动） | 全部挂站点内部归属 `workspace`；原生只给读，写只走专用接口（同盘点的写入闸），DocShare 拒绝 |
| 附件 | 原生私有 `File`，挂在卡片上 | 专用上传接口建 File；下载走原生私有文件，原生按卡片读权限放行 |
| 权限 | 能进页面 = 勾了「工作协调」或管理员（`Cowork Work` / `Cowork Admin`），账号启用、站点是 Cowork | 板上人人可建、改、拖、归档；彻底删除只给管理员（仅限已归档的卡）；评论只能删自己的 |
| 顺序 | 卡片 `position`（浮点），拖到两张卡之间取中值，间距太小时整列重排 | 服务端按邻居算，不信前端给的值 |
| 页面 | `page/pearnly_work` + `public/desk/work.js` / `work.css` | 借 Frappe 原生看板样式（`.kanban*`）与原生 `Sortable`；卡片详情用原生 Dialog / FieldGroup；筛选用页面原生筛选区 |
| 手机 | 三列纵排 + 顶部分区导航（照 ERP / 录入二级页 `form-tabs-list` 那条） | |
| 多人 | 保存带版本号（`modified`），别人先改过就提示重新打开；改动后原生 `publish_realtime` 通知在看板上的人刷新 | socket 不通时手动刷新照常可用 |

## 变更与决定

- 新域 `work/`：`contracts`（三列、位置中值 / 整列重排、字段清洗）、`access`（`Cowork Work` / `Cowork Admin`，不看开通；原生只读、
  拒绝 DocShare）、`documents`（写入闸：写只能由本域服务发起；删除按本请求的"正在删"名单放行，因为原生 `delete_doc` 会重取文档）、
  `service` / `api`（读 GET、写 POST；输入错误按术语键报给页面）。
- 新 DocType：`Cowork Work Card`（+ 三张子表）、`Cowork Work Label`、`Cowork Work Activity`；hooks 登记权限查询、行权限、DocShare 拒绝；
  `pearnly-work` 进 `available`（侧栏出现「工作协调」），`features.json` 不变（不单独售卖）。
- 页面 `page/pearnly_work` + `public/desk/work.js` / `work.css`：原生看板样式 + 原生 Sortable 拖放；列尾快速新建（回车连加、Esc 收起）；
  卡片详情为原生 Dialog（标题、状态、到期日、成员、标签、描述、清单、附件、评论与活动；「保存」只在有改动时出现；「归档」在左下角）；
  页面原生筛选区（关键词、成员 / 分配给我 / 没有成员、标签、到期：过期 / 今明两天 / 无到期日）；右上菜单「标签」「已归档的卡片」；
  已归档里可恢复，管理员可彻底删除（原生确认框，因为不可恢复）。
- 活动只记：建卡、换列、加人、改到期日、加 / 删附件、归档、恢复；评论只能删自己的。
- 保存带版本号，别人先改过就提示关掉重开；改动后 `publish_realtime("cowork_work_changed")` 让在看板上的人刷新。
- 时间：`completed_at` / `archived_at` 存 UTC，已归档列表转站点时区显示；活动时间用原生 `creation` + `comment_when`。
- 登记：`architecture-policy.json` 加 `work → access`；`architecture.md` Cowork 域表加一行；`test_native_desk_contract.py` 的可打开页面清单加 `pearnly-work`。

### 第二批（用户 2026-09-27 试用第一版后）

用户："卡片 ui 还是希望做成它（Trello）这样"；外观选"完全照 Trello"，但"底色不变，底色还是用我们自己的"；下一批要卡片交互、列功能、
卡片封面、@提醒与到期提醒。落地：

- **卡片样子照 Trello、底色用自己的**：圆角白卡轻阴影，悬停描边；悬停滑出完成圆圈、右上角出归档 / 编辑；标签条、封面（颜色条或图片）、
  徽标行（关注、到期 过期红 / 今明黄 / 完成绿、描述、评论数、附件数、清单进度）、头像。页面白底、列用原生看板灰，不做面板背景和彩色列；
  颜色全部取 Desk 原生变量（深色模式跟着走），不改 ADR-007。
- **卡片交互**：完成圆圈（未完成 → 移到「已完成」；已完成 → 回到完成前那一列）；编辑按钮弹出快捷菜单（打开、改标签、改成员、改封面、
  改日期、移动、复制卡片、复制链接、关注 / 取消关注、归档）。快捷菜单与列菜单挂在 body 上浮出（列表会滚动裁切）。
  卡片链接 `/desk/pearnly-work/<卡片>` 直接打开那张卡。
- **列功能**：列头收起成竖条（每人自己的偏好，存浏览器本地；手机上不收）；「⋯」列菜单：添加卡片、排序依据（创建时间新 / 旧、到期日、
  名称）——写回位置，所有人看到一样；把整列卡片移到另一列。三列固定，不做增删列。
- **封面**：颜色，或这张卡上的图片附件（服务端只认本卡的图片附件）；删掉作封面的附件时封面一起清掉；详情弹窗顶部整宽显示。
- **@提醒与到期提醒**：评论框敲 @ 出成员名单（原生 Awesomplete）；新 DocType `Cowork Work Notification`（只给收件人本人读）+
  子表 `Cowork Work Card Watcher`（关注）。提醒种类：被加入卡片、被 @、负责 / 关注的卡有新评论、换列、快到期（今天 / 明天）、已过期；
  自己做的不提醒自己，收件人必须仍能用看板。到期提醒是每小时一次的定时任务 `work.reminders.run`，同一卡同一到期日每人一次。
  只在看板里提示：右上角铃铛带未读数（手机上在右上菜单里）、在线时弹一条可点的提示；**不开全站原生通知**（ADR-007 产品站关着），
  LINE 以后再接。

## 验证

| 时间 | 代码 | 环境与数据归属 | 命令或场景 | 结果 |
| --- | --- | --- | --- | --- |
| 2026-09-27 | 基线 `5b8af27` + 本任务未提交差异 | 本机离线（python3.12） | CI 同名闸门：architecture guard、native desk contract、feature catalog、LIFF、queue targets、DMS call/identity、`check-architecture`、`check-dms-offline`、provision agent、capacity、activate product、welcome email、purchase amounts、tax documents、`i18n-build --check`、`git diff --check`；`node tests/test-native-desk.cjs` | 全部通过 |
| 2026-09-27 | 同上 | 本机离线 | `python3 tests/test_cowork_work_contracts.py`（11 条） | 通过 |
| 2026-09-27 | 同上 | 本机样板 `cowork-a.localhost`（`dev-reload --migrate` 建表） | `scripts/test-cowork-work-native.py`：无权限拒绝、原生写被拦、建 / 拖 / 换列 / 完成时间、标签增改删、更新 + 版本冲突 + 非成员拒绝 + 富文本清洗、评论只能删自己的、附件私有且无权者读不到、归档 / 恢复 / 管理员删除、接口报错；整段一个事务后回滚 | 通过；回滚后表与临时用户均为 0 |
| 2026-09-27 | 同上 | 本机样板，内置浏览器（桌面 1400 宽 + 手机 375 宽） | 列尾连加、跨列拖动、打开详情改成员 / 到期 / 清单并保存（保存键只在改动后出现）、标签管理与选用、评论、附件上传与私有下载、筛选、归档 / 恢复、手机分区导航 | 通过；发现并修掉：加卡按钮图标居中、时间显示成 HTML、标签颜色下拉被表格裁切、已归档表格在弹窗显示前绘制、已归档时间显示成 UTC |
| 2026-09-27 | 同上 | 本机样板 | 实时刷新（socket.io） | 未验：本机样板代理不通 socket.io（`connected: false`），留到测试机 |
| 2026-09-27 | 第二批未提交差异（基于 `45a7566`） | 本机离线（python3.12）+ 本机样板 `cowork-a.localhost`（`--migrate`） | CI 同名闸门、`test_cowork_work_contracts.py`；`test-cowork-work-native.py` 加测完成圆圈往返、复制（带清单 / 标签 / 成员）、按名称排序、整列移动、封面（颜色 / 本卡图片 / 拒绝文本 / 删附件清封面）、关注、@ 提醒（非成员不发、不发给自己、不串到别人）、提醒只读本人、全部已读、到期提醒重跑不重复 | 通过；回滚后为 0 |
| 2026-09-27 | 同上 | 本机样板，内置浏览器 | 卡片外观、悬停圆圈与按钮、快捷菜单（复制卡片）、列收起 / 展开、列菜单按到期日排序、铃铛未读数与列表、点提醒打开卡片、卡片链接直开、封面整宽、评论里敲 @ 选人发出后对方收到、手机布局 | 通过；发现并修掉：快捷菜单图标文字错位、封面挤在左栏、手机上铃铛看不到（加进菜单）、触屏长标题压到编辑按钮 |
| 2026-09-27 | `2d00c60`（变基到 `c11e7a2` 后） | 测试机 Cowork 栈（线上同一底座镜像 + 本工作树实时源码），`testcowork.pearnly.com` | `testenv.sh live cowork`（自动 migrate）；核对表、`Cowork Work` 角色、`pearnly-work` Page、测试账号 `pearnlyai@gmail.com`（Cowork Admin）可进；`work.js` / `work.css` 线上地址与本地字节一致；在测试租户上跑 `test-cowork-work-native.py` | 通过；事务回滚后卡片 / 标签 / 活动 / 临时用户 / 附件均为 0 |
| 2026-09-27 | `d784b549` | 测试机（实时源码，`testcowork.pearnly.com`） | `live cowork`（自动 migrate 两张新表）；`test-cowork-work-native.py` 全套 | 通过；回滚后临时用户 / 标签 / 提醒 / 附件为 0，用户自己试用建的 4 张卡与 11 条活动未动；`work.js` / `work.css` 带版本号地址与源码字节一致（不带版本号的地址被 Cloudflare 缓存着旧版，页面用的是带版本号的） |

`python3 scripts/check-i18n.py --strict` 失败是存量（其他模块硬编码文案），本任务新增文件不在报告里；CI 不跑这一项。
本机样板的测试数据（卡片、标签、附件、临时用户与会话）测完已删。

| 层级 | 状态 | 依据与未覆盖项 |
| --- | --- | --- |
| 实现 | 完成（待用户试用） | |
| 本地验证 | 通过 | 见上表；实时刷新（两人同时开看板）未验 |
| 测试机 | 已上 | `testcowork.pearnly.com` 侧栏出现「工作协调」，等用户试用 |
| CI | 未执行 | |
| 部署 | 未执行 | 生产仍是 `15dea26` |
| 外部业务结果 | 不适用 | 无外部写入 |
| 用户 / 真机验收 | 未执行 | |
