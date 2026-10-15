# 交接：CC 夜间批之后的待验证与未完成（2026-10-15）

写给接手的新窗口。先读本文，再按需读：[夜间批记录](2026-10-15-cc-night-batch.md)、[公司主档记录](2026-10-15-cc-company-master.md)、
[ADR-020](../adr/0020-cc-company-master.md)、[发布记录](2026-10-15-release-2bc4aad1.md)、[教程问题清单](2026-10-13-tutorial-issues.md)、
[多电脑方案](2026-10-13-cc-multi-desktop-plan.md)、[科目建议方案](2026-10-13-account-suggest-plan.md)。

## 0. 现状（2026-10-15 06:00 曼谷）

| 项 | 状态 |
|---|---|
| 远端 main | `6aa3ed2a`（代码同 `2bc4aad1`，之后只多了文档） |
| 生产 | 镜像 `sha256:232bf6ea…a37f`（revision `2bc4aad1`），ERP / Cowork / DMS / 基础层 05:13–05:19 激活，回读全过，半小时后六站点 Error Log 仍为 0、Max_used_connections 37 |
| CC 桌面版 | 正式版 1.6.0（`latest.json` source_sha `2bc4aad1`、sha256 `a2ff49e7…539b`） |
| testcowork | 已 `lab-stop` 切回生产；测试机 Cowork 测试栈已停、数据卷保留（`lab-start cowork` 可复用） |
| 分支 / 工作树 | 本批全部已合入并删除（本地、远端）；只剩开发窗口自己的会话工作树 |
| 用户 | 已睡，醒来验收；未验收的不得代签（AGENTS「证据分层」） |

## 1. 待验证（按优先级；每条写了怎么验、期望、服务端怎么核）

用户真机项只能请用户点，接手窗口负责：先从外部读回确认生产是新版本 → 告诉用户怎么点 → 用户点完从服务端核对。

| # | 项 | 怎么验 | 期望 | 服务端怎么核 |
|---|---|---|---|---|
| V1 | 工作状态行（第 6、8 条） | iPhone 主屏幕 App 拍照加一句「ลงบิลนี้เข้า Express ของ… เป็นค่าเครื่องเขียน」 | 气泡不闪没；立刻出现橘点 +「0 วินาที · กำลังคิด…」每秒走；识别时「กำลังอ่านเอกสาร」；两段工具之间不出现复制按钮、发送键一直是停止键、无空步骤点；结束时状态行、光标同时收起 | 只能看录屏；有异常让用户录屏，对照 `frontend/assistant/src/lib/managed-agents/turn.ts`、`reducer.ts` |
| V2 | 断网与卡死区分 | 回复中途开飞行模式约 30 秒，再关 | 显示「การเชื่อมต่อขาดหาย กำลังเชื่อมต่อใหม่…」；恢复后几秒内回正常、补齐 | 锁屏回来不应误报（`STALL_MS=30000`，socket.io 心跳 25 秒） |
| V3 | 往上翻不拉回、Safari 选字回车 | 长回复时往上翻；Mac / iPhone Safari 用拼音选字按回车 | 保持位置，回底部附近再跟；选字回车不发送 | `lib/follow-scroll.ts`、`lib/ime.ts` |
| V4 | 多设备侧栏同步（第 7 条）与标题（第 11 条） | 同账号电脑 + iPhone：一边新建 / 改名 / 置顶 / 删除 | 另一边约 2 秒内跟上；第一轮结束后标题成主题短语、无省略号；手动改名不被覆盖 | `Pearnly Assistant Session.title_source`（message / summarizing / summary / user） |
| V5 | 公司主档全流程 | 生产 testcowork：侧栏「บริษัท」→ 添加公司（从账套）→ 主档页 → 开 9 月对话 → 知识库手写一条 → 对话里说一条长期口径 | 公司名是法定名（不带「T.8」前缀）；顶栏「公司 › 标题 + 月份」；侧栏出现公司组（不先闪在「最近」）；CC 先问要不要记，同意后允许卡片上写着公司名，点了才写；CC 引用知识库 | `tabCowork Company`、`tabCowork Company Note`；会话表 `project / period / project_noted` |
| V6 | 公司对话的查询范围 | 在公司对话问「เดือนนี้มีบิลอะไรเข้า Express ไปแล้วบ้าง」，再问「ทุกเจ้าเลย」 | 前者只答这家（含各年度、各分店账套）；后者逐家列全所 | 工具结果里有 `scope` 字段；注意：测试机 lab 里修好的查询在新对话才准确，旧对话历史里有错答案 |
| V7 | 「待处理」与早上汇总 | 侧栏「รอดำเนินการ N」→ 点开要人选科目的那行 → 选 → 确认；另一行「交给 CC」 | 选完 CC 桌面版写入后这张从列表消失、计数减一；交给 CC 开在对应公司、月份取单据月份、输入框预填不自动发送；每天 08:30（曼谷）起两小时内推一条汇总给有待处理单的负责人，同一天不重复，0 张不推，不发 LINE | `apps/pearnly_cowork/pearnly_cowork/companies/pending.py`（`morning`、`MORNING_WINDOW`）；`tabPearnly Assistant Notice` 有 `kind` 与 `view` |
| V8 | 在电脑上为公司选文件夹（1.6.0） | 电脑 CC 桌面版升到 1.6.0（下次心跳自动更新）→ 主档页「单据文件夹 · เลือกบนคอมพิวเตอร์」 | 弹 Windows 原生选文件夹；选整盘 / 系统目录被拒；选已授权根目录里的子文件夹复用、不新增授权；几秒内手机上状态变正常；设置页不再有「网页可读取的文件夹」，只有存在没人用的旧授权时才出现「以前授权的文件夹」 | `tabCowork Company Folder`；CC 桌面版日志 `C:\Users\<用户>\AppData\Roaming\Pearnly\logs\companion.log`（快速同步 `roots_only`） |
| V9 | 历史一致直接用、科目建议 | 某账套同一供应商近 12 个月 ≥3 张都记同一科目时推一张新的 | 不再问科目、直接用；主档页「记住的选择」有「来自 Express 历史 · N 次」，删掉后再推又问 | 历史文件 `sites/<站点>/private/cowork_account_history/`；CC 桌面版需 ≥1.6.0（`account_history` 请求） |
| V10 | 分店路由 | 一家有总公司与分店两份账套：选总公司账套推一张买方分店 00012 的进项；在指定分店的对话里推无分店收据 | 前者被拦（分店不符）；后者按对话分店核对 | `targets/account_match.py`（`BRANCH_MISMATCH`，只在同法人 ≥2 个分店账套时才拦） |
| V11 | 定时任务挂公司 | 侧栏公司组「⏱ งานตามกำหนดเวลา N」；总览按公司分组；「＋」先选公司；任务简报点开 | N 正确、有失败显示点；简报（任务都属同一家）点开到主档页 | `companies.api.task_overview` |
| V12 | CC 桌面版更新被拦（第 1 条） | 开着「智能应用控制」的电脑（录制笔记本）自动更新到 1.6.0 时 | 托盘 / 设置给「下载新版」，不反复重试；网页 CC 桌面版状态处如实显示一句 | 心跳 `update_blocked` 字段；`companion/src/companion/update_block.py` |
| V13 | ccs 视觉 | 浅色 / 深色 × 电脑 / 手机看欢迎页、对话（步骤、允许卡片、文件卡片）、侧栏、设置、定时任务、结果面板 | 颜色与官方一致；注意 Windows 100% 缩放下 0.5px 细线显得深一点；lg 以上字号整体大一档 | `frontend/assistant/src/globals.css`（`--ccs-*`）、`docs/development/ccs.md` |
| V14 | 第 5、3、10 条 | 一句话改几个任务的提醒；打开 CC 产出的 docx；侧栏 | 一张允许卡片（最多 5 行，多了「还有 N 项」）；右侧面板直接预览；侧栏无气泡图标 | 第 3、10 条已在测试机通过；第 5 条只过了离线测试 |

测试机已实测通过（lab，见夜间批记录）：第 2、3、4、9、10、11 条，公司添加 / 对话 / 知识库 / 允许卡片 / 待处理列表 / 查询范围。
测试机写入只有一张：`C:\PearnlyExpress\70EXP\T9SINCER` 的 HP690908-001（快递费 285.00）。

## 2. 未完成的任务（计划 + 怎么做）

### T1 定时任务跨年度自动换账套（用户已同意放下一批，单独测透）
- **问题**：定时任务（`Cowork Folder Intake Rule`）绑死一份年度账套 `account_set_key`，跨年要手工改。
- **做法**：
  1. 任务加「跟随公司当年账套」：建任务时（主档页「＋」）记下公司（主档编号）与分店（可空），保留现有 `account_set_key` 作兜底。
     任务表加字段要走原生 DocType 迁移，旧任务不受影响（不填就照旧）。
  2. 执行时（`folder_intake/push.py` 一带，推送前选账套）：按单据日期在公司 `companies.service.books()` 里取覆盖那天的账套
     （`companies/identity.py: covering`），有分店按分店筛；取不到或多于一份就停下等人（不猜），原因写进结果。
  3. 推送仍过 `account_match` 的期间与税号硬闸，所以选错会被拦而不是写错。
  4. 测试：跨年两份账套各推一张；只有旧年度账套时新年度的单停下；两份同期账套停下；旧任务不变。
  5. 测试机：用 70EXP 里 T7/T8/T9 同公司三年度造跨年单据验证（测试账套写入要先列给用户确认）。
- **风险**：会改执行逻辑，写错账套代价大 → 测透再上；Express 测试数据里 T8、T9 期间相同（2025 年），验证时注意。

### T2 MR.ERP 推送方向同类问题
- **问题**：`push/application/service.py` 的 `_prepare_mrerp` 和 `push/mrerp/routing.py` 只用工作空间税号（`Cowork Workspace.tax_id`）判方向。
  事务所替客户记账时判不出，跟 Express 修好前一样（Express 已改为 `contracts/push.py: resolve_account_direction`，按所选账套税号）。
- **做法**：MR.ERP 目标的公司税号（MR.ERP 目录行或连接配置里的公司税号）作为 `account_tax_id` 传入同一个
  `resolve_account_direction`；卖方税号必须有效且与账套税号差 ≥3 位（同 Express 的审查修复）。补测试（同 `tests/test_cowork_push_direction.py` 的写法），
  在 MR.ERP 测试租户（testerp）实测无买方票。

### T3 CC 桌面版托盘提醒按公司打开
- 服务端提醒已带 `view="company:<编号>"`（`pearnly_core/assistant/contracts.py` 的 `NOTICE_PROJECT_VIEW`），网页点开到主档页。
- 改 `companion/src/companion/tomo_links.py`：把 `company:<编号>` 映射成对话页 `?company=<编号>`；补 `companion/tests` 与
  `tests/test_companion_contract.py`；升版本（1.6.1），合入 main 后先部署服务端再发桌面版（记忆：下次发桌面版前先部署服务端）。

### T4 简易税票「票面印 0 不拆」的金标全量验证（要用户同意花钱）
- 拆税规则在 `pearnly_core/tax_documents/rules.py: included_vat`；`printed_zero` 认票面印了 0 就不拆。
- 零费用回放用的 `gold-gemini-rec.jsonl` 没有 `source_refs`，这条子规则没被真实数据验证。
- 做法：用户同意后（约 70–140 泰铢），在测试机 `C:\PearnlyStress\语料\金标-2026-10\` 用 `工具/gold_run.py` 跑一批带来源的识别结果，
  再用 `scripts/cowork-vat-included-replay.py <目录> --all` 看哪些票被拆 / 不拆，对照 `金标/gold-final.json`。不同意就不跑。

### T5 多电脑方案（D1–D9 用户已全部按建议拍板）
- 方案 `2026-10-13-cc-multi-desktop-plan.md`，8 批，写代码约 31 小时、机器约 11.5 小时。
- 第 0 批要第二台物理电脑（借或买，待用户定；录制笔记本不能占用）。
- **与本批的衔接**：`Cowork Company Folder` 已预留 `reader_device`，`binding_key` 已含它；方案里原计划的补丁名
  `v0_0_18_companion_devices` **已被本批占用编号**（`v0_0_18_history_push_targets`），施工时改用下一个编号；方案第 6 节「此电脑 → 网页可读取的文件夹」
  按公司主档的口径已删掉，不要再加回。

### T6 公司主档的后续（不急，按用户需要排）
合并申报（ยื่นรวม）的草稿汇总算法（目前只显示标记）、同税号 Express 与 MR.ERP 合成一家、公司级参考文件上传、批量添加公司、按公司分权限、
网络盘掉线与已移除的区分、每个子文件夹建一家公司、拖放移入公司。
性能：`pending_counts` 每次现算（最多读 500 张 + 每条连接目录一次）、`task_overview` 每个任务一次带缓存查询，公司多时在测试机看一次耗时。

### T7 运维与杂项
- 生产服务器上有 `pearnly-maintenance-cowork` 容器（部署时已运行 2 天，不是本批产生）：只读核对它的来源与是否该保留，删除前问用户。
- 生产 testcowork 里 D2 那张手写简易税票 03/0102（727.00）当时按税额 0 推进了 Express（DEMO02），新规则只对新识别的票生效；
  要不要更正这张（教程数据）问用户。
- 仓库为撑过 10 月的 Actions 额度改成了公开：**11 月 1 日额度重置后提醒用户改回私有**。用户说过自己去改 dmstest 等密码，确认改了没有。
- 盘点三项升级（另一窗口的方案，记忆 stocktake-collab-plan-parked）仍等用户说开工。

## 3. 这批是怎么做的（接手改代码前先看）

- **对话流与状态行**：`frontend/assistant/src/lib/managed-agents/turn.ts`（状态行显示什么）、`reducer.ts`（停着等工具也算在跑）、
  `session-controller.ts` / `realtime.ts`（30 秒无事件判卡死、重开尾巴）；步骤进行时名称由服务端 `pearnly_core/assistant/tools.py: tool_steps`
  汇总各产品 hooks `pearnly_assistant_step_names`（Cowork 在 `assistant/step_names.py`）。新工具必须补进行时名称，否则测试会拦。
- **侧栏同步与标题**：`pearnly_core/assistant/sidebar.py`（`pearnly.session` 事件，只推本人，提交后推）、`titles.py`（第一轮结束后概括一次，显示宽度 26）；
  前端 `lib/managed-agents/list-sync.ts`。
- **公司主档**：Cowork 新域 `companies`（`identity`：有效税号 → `tax:{税号}`，分店不进身份；`service.books()` 现算账套；`context.py` 注入 `<company>`
  说明，转义、9000 字上限、哈希判断重带；`pending.py`；`provider.py` 经 hooks `pearnly_assistant_projects` 交给共用层）。共用层只存会话
  `project / period / project_scope / project_noted`（`pearnly_core/assistant/projects.py`）。ERP / DMS 没有 provider，一切照旧。
- **公司对话默认范围**：`assistant/tools.py: _company_scope`——只给连接不放开，点名账套或 `all_companies` 才放开；推送成功时把连接与账套写回票上
  （`history/push_state.py: _target`，旧数据由补丁补）。
- **推送方向**：`contracts/push.py: resolve_account_direction`（工作空间税号 → 所选账套税号；无买方且卖方是有效的别家税号 → 进项）；
  `targets/account_match.py`（税号、分店、期间硬闸）。
- **科目建议**：CC 桌面版 `companion/src/companion/account_history.py`（只读 `account_history` 请求）→ 服务端 `readback/history.py`
  （按连接 + 账套存 `private/cowork_account_history/`）→ `push/express/account_suggest.py`（提示 > 历史 > 名称，最多 2 个）；
  历史一致直接用在 `push/application/service.py` 的决策处。
- **有界等待**：推送线程里最多 25 秒（`toolrun.may_wait`），网页请求与引擎回调里只读一次。
- **ccs**：`globals.css` 唯一来源；`scripts/check-frontend-tokens.py` 拦写死数值；规范 `docs/development/ccs.md`。

## 4. 工具与环境（本机）

- 常驻工具 `~/pearnly-devtools/`：
  - `bin/`（git / npm 包装、python3→3.12，用法 `export PATH=$HOME/pearnly-devtools/bin:$PATH`）。
  - `gate.sh <工作树>`：照 `development.yml` 跑全闸门，`SKIP_NPM_CI=1` 跳过 npm ci；frontend/ 依赖缺失时先 `npm ci --prefix frontend --offline`。
  - `cand-run.sh <tgz>`：在测试机 WSL 用生产镜像（f9bf6551 底座，测试机拉不到更新的镜像，依赖相同）跑 `ci-candidate-checks offline`，候选代码只读挂载。
    用法：`git archive --format=tar.gz -o /tmp/x.tgz HEAD` → scp 到 `lenovo-office-lan:C:/Users/codexops/AppData/Local/Temp/` →
    `ssh lenovo-office-lan "wsl -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/codexops/AppData/Local/Temp/cand-run.sh x.tgz"`。改了 pearnly_core 合入前必须跑。
  - `e2e/cc.sh new | say <对话> "<话>" | allow <对话> | show <对话> [n]`、`e2e/ccf.sh <对话> "<话>" <本机文件>`：在测试机 lab 的 testcowork 里驱动 CC（只在 lab 开着时用）。
  - `deploy/activate.sh`、`deploy/readback.sh`：生产统一激活与只读回读（scp 到 `/opt/pearnly/state/` 后 nohup 跑，`</dev/null`）。
- 测试环境：`PEARNLY_TESTENV_SRC=<工作树> PEARNLY_TESTENV_IMAGE=ghcr.io/skin306152-star/pearnly/frappe@sha256:f9bf655157d2dbd046e08e306ee0e6af85fbc9e8bb2551fb902f3faf74276eb3 bash scripts/testenv.sh lab-start cowork`；
  改代码后 `PEARNLY_TESTENV_LOCAL_SPA=1 … live cowork`（先在本机 `npm --prefix frontend run build` 与 `npm --prefix frontend/assistant run build`）；测完 `lab-stop cowork`。
- CC 桌面版测试版只能从 main 构建（`companion-release.yml` 对 staging 也要求提交在 main 上），所以桌面版界面只能随正式版验收。
- 生产部署原样步骤见记忆「生产部署步骤」；别循环 SSH 生产；别 exec 进 mariadb 容器。

## 5. 约束（摘要，详见 AGENTS.md 与记忆）

- 回复只用中文；用户可见处只叫「CC 桌面版」。子代理一律 high 档、同时不超过 5 路（用户 10-15 定）。
- 付费识别要用户同意（预算口径）；测试账套写入先列给用户确认；Accserver / S 盘真账不碰。
- 生产部署按「攒批、进维护、立即部署」，不新增固定月费；改已有文案先给用户看。
- 本仓提交钩子与 main 快进规则不得绕过；推分支前跑全闸门；分支 CI 红了本窗口负责。
