# 需求影响评估：Code 版 CC（对话、代录都做成 Claude Code 式会话）

- 状态：**评估待主会话审定**。这一步只写评估，没有改产品代码，没跑回归实验室，没碰测试机和生产。
- 分支 `pearnly/cc-code-style`，基线 `63b1a379`（= 2026-11-04 读取时的 `origin/main`）。
- 产品地图 F 块（给 `frontend/assistant` 加 `// map:` 注释）在基线里**还没有**；它先合入的话，本任务施工前先变基。施工期间 `frontend/assistant` 只能有本任务一个写者。
- 主会话：local_3d36d798-00b6-49f2-aed5-e453cc591ce7。要定的事都在第 12 节。

## 0. 需求原文与理解

### 用户原话（11-04，按时间顺序）

1. 「代录界面，把右侧的工作区全部融入到对话框去，做成像code一样的工作原理，不需要单独在右侧操作，都在对话框，cowork和erp同步，做完和本批次一起部署」
2. 「左侧待处理也丢掉吧，一起融入到对话框，cowork和erp都不要了，都在对话框，就像我们现在做开发一样，都在对话框工作，状态显示一定要做好，参考code任务中的状态即可，就相当于我要的是一个code版的cc」
3. 「对话界面要做成code版的cc」
4. **范围更正，以这条为准**（主会话转达）：「直写那边右侧内容区不要动，直写和代录左侧待处理不要，代录右侧不要，cowork.erp同步」

### 理解

| 部位 | 做什么 |
|---|---|
| 消息流与状态显示 | 对话（直写）、代录两种模式**用同一套 Code 式会话组件**：消息、工具步骤折叠行、消息下的任务清单、工作状态行、结束框、出错、停止和被打断的显示，以及侧栏行状态。 |
| 代录右栏 | 五块（资料、开始前确认、需要处理、电脑与 Express、报告）连同「开始」按钮**全部删掉**，内容并进对话。 |
| 左侧「待处理」 | 两种模式、两个产品都删。待处理的事并进对话：照 Code 的做法，需要人处理的事落在一个会话里，侧栏那一行显示「等你处理」。 |
| 直写右侧内容区 | **不动**。公司、本月、待处理卡、知识库、单据文件夹、定时任务、记住的选择、申报草稿、输出文件，包括「账套」显示，都照旧。 |
| 公司页、公司列表 | 不动。只改「待处理数」点了以后去哪（见 2.3）。 |
| 产品 | Cowork 和 ERP 同步，同一套代码，不按产品分支。 |

### 不在本需求范围

- 直写、代录的业务逻辑和管钱底线：怎么判、怎么推、怎么录、预检规则、扣费口径都不改。
- 直写的右侧内容区和公司页的内容。
- CC 桌面版（`companion/`）：不改，不发版（深链接兼容放在网页端，见 7.5）。
- 照搬 Code 里 CC 没有对应能力的东西，例如模型选择、权限模式、用量环、分享、分叉、章节目录、Verbose 视图、后台任务窗格（同外壳设计文档第 6 节「不照搬的」）。

## 1. 照 Code 的交互与状态清单

### 1.1 依据从哪来

| 代号 | 依据 | 说明 |
|---|---|---|
| E1 | `docs/work/2026-10-15-claude-code-ui-study.md` | 10-15 在 claude.ai/code 读的结构与样式 |
| E2 | `docs/work/2026-10-16-cc-shell-design.md` | 10-15 夜开发窗口亲自实测，用户已确认。第 3 节是组件规格 |
| E3 | 官方文档 https://code.claude.com/docs/en/desktop | 2026-11-04 读 |
| E4 | 本评估所在的 Claude Code 桌面版会话，App 交给智能体的工具说明 | 都是 App 对自己界面的文字描述，**不是截图**：`update_plan`（消息下的任务清单）、`wrap_up`（结束框）、`mark_chapter`（章节）、`ccd_sidebar` 的 `mark_completed` / `set_unread` |
| E5 | 用户 11-04 的描述 | 「15 分钟 53 秒 · 即将完成思考」 |

仓库和 `~/pearnly-devtools` 里**都没有** Code「任务状态」的截图。这一轮是子代理，没有申请电脑控制权限去截 Claude 桌面版，避免给用户弹授权窗口。凡是只有文字依据、看不到样子的项，下表都写「样子不知道」，并在第 12 节建议施工第 0 块用 30 分钟截图核对一遍。

### 1.2 清单

| # | 项 | Code 是什么样（依据） | CC 现状 | CC 要做成（两种模式同一个组件） |
|---|---|---|---|---|
| S1 | 进行中 | 转圈，加已用时间和当前在做什么，例如「15 分钟 53 秒 · 即将完成思考」（E5）。E1/E2 记作「工作指示」 | 已有：`thread.tsx` 的 `RunLine` / `RunIndicator` 和 `turn.ts`，显示「计时 · 在做什么」，想完说「思考了 N 秒」，断线说「连接中断，正在重连」 | 对话：照旧。代录：同一个 `RunLine` 改成「计时 · 第 n/N 张 · 单号 · 对方」；排队或暂停中写「计时 · 等电脑空闲」（`batchStatusText` 现在没有 n/N）。Code 那句「即将完成思考」的**确切说法不知道**，不照抄，沿用 CC 已有的词 |
| S2 | 步骤折叠行 | 连续工具步骤合成一行摘要，例如「已运行 2 个命令（1 个失败）›」。点开是细框列表，每步一句话，失败的行尾红字「失败」，再点开看命令和输出（E1 第 4 节，E2 3.11）。E3 的 Normal 视图也是「Tool calls collapsed into summaries」 | 已有：`tool-uis/steps.tsx`、`lib/step-summary.ts` | 对话：照旧。代录：把单据进度画成同样的步骤行，例如「收到资料 22 张」「录好 18 张」「没录成 2 张（2 个失败）」，点开是每张一行，失败的写原因，再点开看单号、Express 单号、金额和税 |
| S3 | 消息下的任务清单 | 列在最新一条消息下面，三种状态：待办、进行中、完成，同时只有一项进行中（E4 `update_plan`）。**样子不知道**：图标、颜色、长清单怎么收、全部完成后收不收 | 对话：没有。代录：主栏每张单一行（`TaskLog` 的 `docRow`），不是清单形式 | 代录：本次要录的单就是任务清单，「排队」= 待办，`entering` = 进行中，「已入账」「已跳过」「没录成」= 完成（没录成的带红字）。对话：要 CC 自己列计划，得加一个工具，见待定 Q5 |
| S4 | 结束框 | 一轮做完，最后有一个框：2～3 句回顾；有要人定的事时，加一个问题（≤100 字）和 1～2 个按钮，第一个是推荐项、画成主按钮；没有要定的事只留回顾，**不画框**；回顾的第一句就是侧栏这一行的状态；用户被卡住时 App 把这个会话标成「等你处理」（E4 `wrap_up`）。**样子不知道** | 对话：没有。代录：完成时一行 `finishLine`，「需要处理」在右栏 | 代录：需要人处理时，框里写问题和按钮（例：「2 张缺供应商，选一次？」〔去选〕）；整批停了（例如 Express 登录）时（例：「Express 登录未设置」〔设置〕）；准备中时是「开始前」框（见 2.1）；做完、没有要定的事时只留回顾行，加一张报告文件卡片。对话：要 CC 输出结束框，见待定 Q5 |
| S5 | 侧栏行状态 | 行尾 24 的槽：运行中是 6px 呼吸点（1.2 秒一次明暗），另有未读点；鼠标移上去时换成「···」（E2 3.5）。讨论串按「进行中 / 等你处理 / 已完成」跟踪（E2 3.15）。卡住的会话被标成「等你处理」，可以「标为完成」「标为未读」（E4）。会话做完而你没在看时，系统弹通知（E3）。**「等你处理」的标记长什么样不知道**；回顾的第一句在侧栏怎么显示不知道 | 对话行：运行中、未读（蓝点）、提醒圆点（`activity.ts`）；公司页的对话列表已有「等你处理」（`chatState` 的 `waiting`）。任务行：只有运行中和未读。**以前定过「行上没有状态字、彩色点」**（check-impact 登记的 `cc.operator.page` 预期） | 两种行用同一个 `ActivityMark`，四种状态：运行中（照旧）、**等你处理（新）**、未读（照旧）、完成（不标）。数据来源见 1.3。新标记的样子先照 `RunLine` 的「需要您的输入」小橙点，等截图核对。这一条和「行上没有彩色点」的旧口径冲突，见待定 Q2 |
| S6 | 出错 | 断线有一行说明，例如「Some output produced while you were disconnected may be missing here」（E3）。一轮本身出错时是什么样**不知道** | 已有：发送失败框（`send-failure.ts`），顶上「正在重新连接…」，状态行「连接中断」。代录：主栏顶上一个错误位（`shownError`） | 两种模式都用对话现有的错误框，代录的错误位不再单放在顶上；代录整批停（必须人工的原因）走 S4 的结束框 |
| S7 | 停止 / 被打断 | 停止按钮马上打断，`Esc` 也停。运行中照样能发消息：当前动作完了 Claude 才读到（E3） | 对话：发送键在运行中变成「■ 停止」（`stop-run.ts`）。代录：顶栏一个「停止」（`OperatorTools`） | 代录的「停止」挪进输入框，同对话：批次在录、没有对话轮在跑时，发送键变成 ■，点了停这一批，`Esc` 同效；对话轮在跑时，■ 停的是这一轮。停下以后在对话里留一行「已停止 · 录好 N 张」，结束框问「继续剩下的 M 张？」〔继续〕。被打断后的显示 Code **不知道**，CC 沿用「已停止」 |
| S8 | 附件 | 点输入框旁的「+」附加文件，也可以直接把文件拖进输入框（E3） | 对话：有「+」和附件卡片（E2 3.10）。代录：右栏上传框，加上「输入框附件发送时进资料」 | 两种模式同一个输入框。代录里的附件就是资料，进对话后立刻出状态卡片（见第 5 节） |
| S9 | 模式选择 | 在输入框下面那一行，`Cmd+Shift+M` 打开；按文件夹记住（E3） | 左上角分段切换，`Ctrl/⌘+Shift+1/2` | 见第 3 节 |
| S10 | 回复中的操作 | 鼠标移上去出操作；回复中输入框可以排队（E1） | 对话已有 | 照旧 |
| S11 | 电脑和手机 | 手机能看云端会话的进度（E3）；Dispatch 会话做完或要你批准时，手机收到推送（E3）。手机端各状态的样子**不知道** | 手机：侧栏是抽屉，行高 44，正文 16/24（E2 3.16）；等回答时由输入框上方的浮层代替状态行（`approval-sheet.tsx`）；代录右栏在手机上盖满整屏 | 手机上结束框和「开始前」框贴在输入框上面，用同一个浮层，同 `approval-sheet`；任务清单默认收起，只露「第 n/N 张」；不再有盖满整屏的右栏 |

### 1.3 侧栏状态从哪来（现有数据，不新开通道）

| 行 | 运行中 | 等你处理 | 未读 | 完成 |
|---|---|---|---|---|
| 对话 | `activity.ts` 的 running，加服务端运行状态校正 | 运行状态是 `requires_action`（`contracts.py` 已经给出 `parked`），**或**这段对话所属的「公司 · 月份」下还有待处理（数据见第 4 节 B3） | 跑完你没看（照旧）；提醒圆点照旧 | 其余 |
| 代录任务 | `queued`、`running` | `attention`；`stopped` 并且还能继续 | 收尾时你没在看（`tasks.ts` 的 `seenAt`，照旧） | `finished`；`preparing` 不标 |

## 2. 现有每块 → 对话里的样子

### 2.1 代录右栏五块

| 现在（`components/operator/panel.tsx`） | 对话里 |
|---|---|
| ① 资料：上传框、按组（采购、销售、费用、收付款、不入账）列单、拖到别的组改分组 | 上传：输入框「+」或拖进输入框，每份文件一张状态卡片（第 5 节）。已收的单：对话里一行步骤摘要「资料 22 张 · 采购 20 · 费用 2 ›」，点开就是现在的分组列表。拖动改分组照旧，用共用拖动 `ui/drag.tsx`，代码搬过来，不重写 |
| ② 开始前确认：要人定的按类一次问完；结果不明的单标「已入账 / 没有入账」 | 对话最后一个「开始前」框（S4 的样子），从上到下：<br>• 写入目标一行（第 6 节）<br>• 要定的项，按类分组，下拉选，和现在一样<br>• 结果不明的单，每张两个按钮<br>• 主按钮「开始」：一直能点，还差什么就在框里说，和现在一样<br>答完一项，侧栏不再是「等你处理」 |
| ③ 需要处理：没录成的单（重试、跳过），整批停（登录、选公司…） | 录到一半停下：结束框里写问题和按钮。现有的 `TaskChoices`（中途要选的单）已经画在对话里，搬进结束框。每张单的「重试 / 跳过」放在 S2 步骤行展开后的那一行 |
| ④ 电脑与 Express：哪台电脑、在不在线、Express 登录设了没有 | 输入框下面那一行（E2 3.12）多两个小钮：「电脑名 ●在线」「账套名」。点电脑钮出菜单换电脑；没设 Express 登录时，「开始前」框里那一行写「Express 登录 未设置〔设置〕」 |
| ⑤ 报告：导出 CSV | 做完时对话里一张文件卡片，样子同直写回复下的输出文件卡片（`MessageFiles`），点了下载。CSV 在浏览器生成，同现在的 `reportCsv` |
| 「开始」按钮 | 在「开始前」框里 |
| 右栏开关、拖宽、窄屏抽屉、手机盖满屏 | 删掉。代录的会话栏宽 768，居中，同对话 |

### 2.2 代录主栏和顶栏

| 现在（`workspace.tsx`） | 改成 |
|---|---|
| 新建任务页：大输入框加公司、月份胶囊，选了公司就建任务 | 保留：这就是新会话页，同对话的新建页 |
| 任务记录：你的话一个气泡，「时刻 · 电脑 · N 张」，每张单一行，最后完成行 | 一条会话：你的话（气泡加附件卡片）→ 资料步骤行 →「开始前」框 → 开始后的任务清单（S3）、单据步骤行（S2）、状态行（S1）→ 结束框（S4）。任务里的对话（对话框甲，`task-thread.tsx`）的回复按时间插进同一条流里，不再「画在任务记录下面」 |
| 顶栏：标题、「停止」 | 标题，点了就地改名，同对话；「停止」挪进输入框（S7） |
| 准备中在输入框打的字：只存在这台设备，作顶上的气泡，不交给 CC | 见待定 Q6：建议照 Code，交给这个任务的对话 |

### 2.3 左侧「待处理」

现在的入口和去向：

| 入口（代码） | 做什么 | 改成 |
|---|---|---|
| 侧栏「待处理 N」（`assistant.tsx` 的 `PendingNav`，两种模式共用） | 打开待处理页 `?view=pending` | **删** |
| 待处理页（`components/pending.tsx` 的 `PendingView`） | 按公司列待处理；当场选、推送、选账套、MR.ERP、交给 CC、打开单据页（ERP） | 页**删**。行组件 `PendingList` / 行逻辑**保留**：右侧内容区的待处理卡还在用，对话里的「需要处理」框也用它（一套代码、两处摆放） |
| 待处理页的「任务」一组（`operator/pending-tasks.tsx`）、侧栏数里的代录任务（`attention.ts`） | 列「需要你处理」的代录任务 | 组**删**。这些任务本来就是代录侧栏的一行，行尾亮「等你处理」 |
| 公司组头的待处理数（`projects.tsx`，点了到这家的待处理页） | | 数和点击都删。组里那几行自己亮「等你处理」（见待定 Q3） |
| 公司列表卡片上的待处理数（`companies.tsx`） | 只显示 | 保留数字。点卡片照旧进公司页 |
| 早上 08:30 的待处理汇总提醒（`companies/pending.py` 的 `morning`，提醒的 `view="pending"`） | 点开到待处理页 | 服务端不改。网页把 `?view=pending` 当兼容入口（7.5） |
| CC 桌面版托盘通知（`companion/tomo_links.py` 把 `pending` 变成 `?view=pending`） | | 不改桌面版，同上，由网页兼容 |

**待处理落到哪个会话**（照 Code：需要人处理的事就是一个会话，侧栏那一行亮「等你处理」，点进去就是问题和按钮）：

1. **能归到公司和月份的单**（Cowork 绝大多数，ERP 全部）→ 落到「这家公司 · 单据月份」的对话。Cowork、ERP 的对话本来就按公司和月份分。
   - 这家这个月**有**本人的对话（取最近更新、没归档的那段）：那一行亮「等你处理」。点进去，对话**末尾**是一个「需要处理」框，样子同 S4 结束框：标题写「N 张单要处理」，下面每张一行，就是现在待处理页的行，当场选、确认、推送、选账套、MR.ERP、打开单据页（ERP）照旧，处理完这一行当场消失。原来的「交给 CC」改成在**这段对话**的输入框里预填「处理这几张：…」，不另开对话，不自动发送。
   - **没有**对话：侧栏这家公司组里出一行**占位行**「{月份}」，亮「等你处理」。点了才建对话（`sessions.create(公司, 月份)`，和现在「交给 CC」开新对话是同一条路），建完显示上面那个框。不在后台自动建对话，不开模型轮，零成本。
   - 这个框是页面按服务端的待处理列表**现画**的，不写进引擎会话（同提醒卡片的原则：写进去会开一轮、要花钱）。
2. **归不上公司的单**（只有 Cowork：推送去向和本方税号都对不上）：见待定 Q4。建议在侧栏「最近」组里放一行占位行「未归属公司」，亮「等你处理」，点进去同上；建对话时不挂公司。
3. **代录任务需要你处理**：就是代录侧栏那一行（1.3）。在对话模式里时，左上角模式切换的「代录」图标上加一个小点，提示另一边有等你处理的任务（见待定 Q7；Code 没有两种模式，这一条没有依据）。
4. **LINE 上传识别失败**：现在由 LINE 当场回复「无法识别此单据，请重新发送」（`line/intake.py` 的 `ocr_failed`），不在待处理范围。**不变**。
5. **定时任务出错、简报**：现在挂在建这个定时任务的那段对话上（`folder_intake/notify.py` 的 `conversation`）；那段对话没了，就只走手机推送和电脑通知，点开到公司页或定时任务页。**不变**。这类提醒在侧栏是提醒圆点。
6. **早上汇总**：见 7.5 的兼容规则。

### 2.4 直写右侧内容区、公司页

**都不动**（用户更正）。右侧「待处理」卡现在只列**对话月份以外**的待处理（`otherMonths`）；对话月份的那些改由对话末尾的「需要处理」框显示，两处正好不重复。卡片的行组件和框是同一个，见待定 Q8。

## 3. 两种模式还要不要分开

界面统一以后，两种模式的差别只剩执行方式：直写（CC 交写账队列，CC 桌面版直接写 DBF）和代录（CC 桌面版在 Express 窗口里逐张填）。

| | A. 保留两种模式（建议） | B. 模式选择挪到输入框下面，侧栏合成一个列表 | C. 合成一个，CC 自己选直写还是代录 |
|---|---|---|---|
| 做法 | 左上角切换照旧（`Ctrl/⌘+Shift+1/2`），两种模式各有自己的侧栏列表，会话组件统一 | 照 E3：新会话在输入框下面那一行选模式，`Cmd/Ctrl+Shift+M` 打开菜单；侧栏一个列表，混排对话和代录任务，行首图标区分 | 只有对话。CC 按单据和公司判断：能直写就直写，否则交代录 |
| 和「代录右侧不要」 | 没冲突：代录会话没有右栏，直写会话照旧有 | 没冲突：打开哪种会话就是哪种样子（直写有右栏，代录没有）。同一个列表里点不同的行，右栏时有时无 | **冲突**：合成以后只有一种会话。要么都有右栏（违背「代录右侧不要」），要么都没有（违背「直写右侧不要动」） |
| 数据 | 不动 | 对话（Pearnly Assistant Session）和任务（Pearnly Express Operator Task）两张表，服务端各自分页，侧栏要合并两路分页（按公司分组时每组两路）。两份手排顺序（偏好 `order/` 和 `operator` 份）要合成一份，要迁移；任务号要进对话的手排顺序，推翻 11-03「任务号不进对话的手排顺序」 | 任务记录、代录任务号（= CC 桌面版批次号）要并进对话；走哪条路变成模型决定，**动到管钱底线**（判断落到模型），超出「只改呈现」 |
| 流程、测试 | 改呈现相关的断言 | 加上侧栏合并、偏好迁移 | 重做代录、直写两条路的入口和测试 |
| 照 Code 的程度 | 消息流、状态和 Code 一样；模式位置不一样 | 最像 Code | 不像 Code（Code 的模式只管权限，不管执行方式） |
| 机器耗时（另加） | 0 | 约 8 小时写代码，加侧栏和偏好迁移的回归 | 不估：超出范围 |

**建议 A**：这次只做呈现，和本批一起部署；B 留给以后，主会话要的话另开评估（不算过渡版：A 就是现状，不另写一版）。C 和用户的更正冲突，又动管钱底线，不建议。见待定 Q1。

## 4. 后端影响

原则：直写、代录的业务逻辑和管钱底线不动。只加**只读**接口或给现有回包**加字段**；只有一处小的存储，要主会话批（B1）。

| # | 补什么 | 为什么 | 怎么做 | 改到 |
|---|---|---|---|---|
| B1 | 任务的上传记录 | 上传后在对话里要看得见每份文件的状态，刷新、换设备也要在（第 5 节）。现在的上传幂等键在 `files.service` 里**哈希后存**，不能按任务反查 | 任务加一张子表「上传」，记：原件编号、文件名、大小、识别任务号、上传时间。在 `express_operator.api.intake` 里登记，这一步是**新写入**，见待定 Q9。DocType JSON 随源码，用原生迁移；不改识别和扣费 | `pearnly_core` 的 DocType、`express_operator/api.py`、`tasks.py` |
| B2 | `task_uploads(name)`（只读） | 每份文件：上传时间、识别状态（排队中 / 识别中 / 已识别 / 失败）、`pages_done / page_count`、识别出的单（编号、日期、在不在任务月份、在不在资料里）、失败码 | 产品钩子取识别状态：Cowork 用现有的 `ocr.execution.service.detail` / `store.project`（已有 `state`、`reading`、`pages_done`、`history_records`、`last_code`）。ERP 不声明 `intake`，没有这一项 | `pearnly_core/express_operator`，Cowork 的 `operator_source.py` 加钩子 |
| B3 | 待处理按「公司 · 月份」计数，带对话编号 | 侧栏的「等你处理」和占位行 | 现有的 `pending_counts`（Cowork `companies/pending.py`、ERP `express/pending.py`）回包加 `months: [{company, month, count, conversation}]`。`conversation` 是本人这家这个月最近更新、没归档的对话，没有就空（查会话表，同 `notices.owned_conversation` 的判据）。只读 | 两个产品的 `pending.py`；共用层查会话的函数 |
| B4 | 预检回包加写入目标 | 第 6 节：开始前在对话里说清写哪份账套 | `precheck` 回包加 `target: {account_set, account_set_title, connection_title, computer}`，取值必须和 `submit` 实际用的同一个函数（施工时核对 Cowork 的 `seed_account_set` / 现用账套和 ERP 的 `express/target.py`）。只读 | 共用层 `api.precheck`，两个产品的 `precheck` |
| B5 | 识别完成的实时事件 | BJC 缺陷的根因之一：上传后只 `reload()` 一次 | 现有 `pearnly_ocr_work` 事件已推「开始识别」和「读到第几页」；**识别完**有没有推，施工时核对（`push_state.py` 有 `cowork_intake_changed`）。没有就在识别落成票那一处补推一条（只推给提交人，只作展示，`after_commit`）。兜底：页面看得见、有在途上传时，每 10 秒读一次 B2，最多同时盯 5 份 | Cowork `ocr/execution/store.py`（只加推送） |
| B6 | 待处理的会话占位：建对话 | 点占位行才建 | 用现有 `assistant.api.create(project, period)`，不新增接口 | 无 |
| B7 | 代录停止：从输入框停 | S7 | 现有的 `stop`（桥）和 `task_*`，不新增 | 无 |
| B8（可选） | 对话模式的任务清单和结束框 | S3、S4 对话部分 | 共用层 `CORE_TOOLS` 加两个不碰账的工具：`cc_plan`（列计划，三种状态）、`cc_next_step`（回顾 + 一个问题 + 按钮）。前端画成清单和结束框。见待定 Q5 | `pearnly_core/assistant/tools.py`、系统提示；产品地图加 `tool:erp:*` / `tool:cowork:*` 各 2 行 |

**不改的**：`start / submit / regroup / resolve / task_answer / task_resume / task_settle`、`products.py` 的钩子语义、预检规则、扣费、推送、CC 桌面版的桥和载荷（不升载荷版本）、提醒（`notices.py`）、早上汇总。

改到 `pearnly_core` 的部分合入前，要在生产镜像上跑候选离线检查（用仓库当前的 `scripts/ci-candidate-checks.sh`）。

## 5. 必须根治：上传后看不到状态，导致重复上传、重复扣费

**根因**（BJC 报告第二节，代码已核对）：
- `controller.tsx` 的 `intake`：上传返回后 `reload()` 一次资料，之后不再刷新；
- 资料列表只列「发票日期在任务月份内、并且 `push_state=waiting`」的单（`operator_source.sources`）；12 页的 PDF 识别约 3 分钟；
- 整个过程没有任何显示，错误位也是空的。

**改成**（只有 Cowork；ERP 的代录不收上传，见待定 Q10）：

1. 文件放进输入框（「+」或拖入）、点发送的那一刻，对话里出现「你的消息」，消息里每份文件一张卡片，同直写附件卡片的样子（E2 3.10），卡片下面一行状态：
   - **上传中**：按已传字节显示百分比。现在用 `fetch`，拿不到上传进度，要换成带进度的上传。
   - **排队中** → **识别中 3/12 页**：来自 B5 的事件或 B2 的读数。
   - **已识别 11 张**：只陈述事实。有不在任务月份的单时加「· 2 张不在 10 月」，这是 BJC 里「资料是 0」的另一个原因。
   - **失败**：显示识别失败码对应的文字。上传失败就地有「重试」（同一份重传）；识别失败照产品现在的口径，不另做重试。
2. 刚传过的同一份又传：**照常**上传、照常识别、照常扣费（产品口径「上传识别都扣费」）。界面上就是多一张自己的卡片和自己的状态，**不加**「已传过」之类的提示，不拦（守「录入口不加识别后提示」和「不提复用免扣」）。会让人重复上传的原因是看不到状态，卡片一直在，原因就没了。
3. 资料步骤行跟着刷新：任一上传变成「已识别」时重取资料，不再只在上传返回时取一次。
4. 状态来自 B1/B2，所以刷新页面、换设备、手机上都看得到同一张卡片和同一个状态。
5. 卡片在对话里的位置就是上传那一刻，不会被后面的消息挤掉。侧栏这一行在有上传在途时显示「运行中」。

## 6. 在对话里说清「这次写进哪份账套」

- 输入框下面那一行常显「账套名」小钮（第 2.1 节 ④）。来源是 B4，没开始前就是 `submit` 要用的那一份；开始后用批次快照的 `account_set`（`BatchSnapshot.account_set`，现有）。
- 「开始前」框第一行：「写入 {公司} · {账套} · {电脑} 的 Express」。
- BJC 那种「单据上的本方税号与所选账套不一致」，预检照旧挡住；框里那张单的原因后面接着显示账套名，人一眼看出是哪一份。
- 顺带的已有文案问题：预检原因「…请核对账套后重新发送」是 Cowork 直写、代录共用的服务端文案。代录这时其实还没发送，改不改见待定 Q11，不在本任务默认范围。

## 7. 键盘、拖放、手机、后退和前进、深链接

| 项 | 做法 |
|---|---|
| 7.1 键盘 | `Esc`：停这一轮或这一批（S7，同 E3）。`Ctrl/⌘+N`：新建（对话 / 任务，照旧）。`Ctrl/⌘+Shift+1/2`：切模式（照旧）。侧栏方向键、`⌘1–9`、多选：照旧。结束框和「开始前」框的按钮能用 Tab 走到，主按钮用 `Enter`（Code 有没有快捷键**不知道**）。删掉右栏分区的折叠键 |
| 7.2 拖放 | 文件拖进输入框（同 E3），拖到代录会话栏任何地方也接住，同直写现在的行为（施工时核对直写的落点范围，两边一致）。资料分组的拖动留在资料步骤行展开后的列表里。删掉右栏上传框 |
| 7.3 手机 | 代录不再有盖满整屏的右栏。输入框下面那一行放不下时，公司、月份、电脑、账套四个小钮折成两行。结束框和「开始前」框贴在输入框上面，同 `approval-sheet` 的浮层。任务清单默认收起。侧栏抽屉行 44 高，状态标记同电脑 |
| 7.4 后退和前进 | `lib/nav.ts` 去掉 `pending` 视图；历史里旧的 `pending` 记录跳过。代录的历史照旧记模式和任务号。返回键栈（`lib/back-stack.ts`）删掉右栏和抽屉的那几层 |
| 7.5 深链接 | `?c=<对话>`、`?view=tasks`、`?view=companies`、`?company=<公司>`（托盘提醒、公司页）**照旧**。`?view=pending` 保留作兼容入口，不再是视图：带 `&company=X` 时打开 X 待处理最多那个月的对话或占位行；不带时，只有一家有待处理就打开那家，多家就打开公司列表（列表上有数）。新增 `?task=<任务号>`：打开代录任务（给以后的提醒用，可选）。CC 桌面版不改 |

## 8. 要删的旧代码，产品地图，流程断言

### 8.1 删除清单（不留两套）

| 文件 | 删什么 |
|---|---|
| `components/operator/panel.tsx` | 整个文件。要复用的问答、处理行、`downloadReport` 搬进新的会话卡片文件，原文件删掉 |
| `components/operator/workspace.tsx` | `OperatorDock`、`PanelToggle`、`usePanelPlace`、`useNarrow`、`NARROW_PX`、`OperatorTools`（停止挪走）、`TaskLog` 里画单据行的那部分（换成 S2、S3） |
| `components/operator/store.ts` | 右栏开关、抽屉、分区开合（`folds`、`setFold`、`setPanel`、`setDrawer`、`FOLDS_MAX`） |
| `components/operator/prefs.ts` | `cards/folds` 同步那一份；服务端偏好里存过的旧键按「认不出就忽略」处理，不迁移 |
| `components/operator/pending-tasks.tsx` | 整个文件 |
| `components/operator/attention.ts`、`attention-store.ts` | 只给 Q7 那个小点用的话留一个计数，否则删 |
| `components/pending.tsx` | `PendingView`、搜索框和按公司筛的那层；`PendingList` 和行留着 |
| `assistant.tsx` | `PendingNav`、`pending` 视图分支、`watchPendingCounts` 里只为侧栏的部分 |
| `lib/nav.ts` | `View` 去掉 `pending`，`ownMode` 和深链接改兼容入口 |
| `lib/companies.ts` | `useCompanyNav.pendingCompany` |
| `components/assistant-ui/projects.tsx` | 组头待处理数和点击 |
| `components/split-handle.tsx` | `operatorPanelSize`（拖宽分隔条本身留着，左侧栏和文件预览还在用） |
| `globals.css` | `.cc-op-panel*`、`.cc-op-section*`、`.cc-op-scrim`、`.cc-op-drop`、`--cc-op-narrow` 等约 27 处；`.cc-op-log` / `.cc-op-row` 换成会话组件的类 |
| `i18n.ts` | `op.section.*`、右栏专用词、待处理页标题和搜索占位（右侧卡仍用的「待处理」保留） |
| 共用件登记 `scripts/impact/shared-parts.toml` | `cc-split-handle` 的说明去掉「实操右侧栏」，入口照旧 |

### 8.2 共用件影响（`check-impact.py --files` 演练结果）

命中 `cc-sidebar-list`、`cc-composer`、`cc-split-handle`、`assistant` 四个共用件；要实测的入口 15 个：

`cc.assistant`、`cc.operator.page`、`control.assistant`、`shared.assistant`、`cowork.assistant`、`cc.operator.thread`、`erp.cc.companies`、`erp.cc.chat-push`、`cowork.cc-chat.push`、`cowork.cc.troubleshooting`、`cowork.companies`、`cowork.cc.mrerp-setup`、`shared.login`、`cc.settings.connection`、`cc.desktop.bridge`。

合入前逐个填「通过 / 不通过 / 没测到（原因）」。`cc.operator.page` 登记的「预期」里有「行上没有状态字、彩色点」「没收的单在『需要处理』」「『开始』在报告下面」等说法，要随本任务改写。

### 8.3 产品地图（同一提交登记）

| 动作 | 行 |
|---|---|
| 删 | `erp.cc.view.pending`、`cowork.cc.view.pending`（`View` 去掉 `pending` 后锚点 `ccview:*:pending` 消失） |
| 改 `where` / `tests` | `erp.cc.view.operator`、`cowork.cc.view.operator`：「CC 代录（实操任务页）」改成「CC 代录（任务会话）」，测试加上新断言。`erp.cc.action.sidebar-modes`、`cowork.cc.action.sidebar-modes`：说明去掉「待处理」。`erp.cc.view.companies`、`cowork.cc.view.companies`：测试里的待处理那步改掉 |
| 新增 | `task_uploads` 接口那一行（两产品，按接口分诊规则登记调用方）。B8 批了的话加 `tool:erp:cc_plan`、`tool:erp:cc_next_step`、`tool:cowork:cc_plan`、`tool:cowork:cc_next_step`。行动入口「待处理落到会话（占位行 / 需要处理框）」两产品各一行，`group = "cc.action.pending-in-chat"` |
| 不动 | `*.cc.operator.pending`（代录 `pending` 钩子，服务端去重还在用） |

`untested_baseline.txt`、`sibling_baseline.txt` 只许减不许加：新行必须带测试。

### 8.4 受影响流程的断言怎么改

| 流程 | 现在断言 | 改成 |
|---|---|---|
| `cc.operator`（`scripts/flows/cc_operator.py`，ERP） | 选公司后出现 `cc-op-title` 和 `cc-op-panel`；右栏 `[data-section=files]`；`.cc-op-foot` 输入框；`.cc-op-log` 里的状态行和回复 | 选公司后**没有** `cc-op-panel`；会话里资料步骤行的张数 = 站点 `sources` 条数；「开始前」框里写入目标一行的账套和 B4 回包一致；输入框里说一句，状态行出现又消失，回复在同一条流里；侧栏行在跑时有运行中标记，结束后消失；删除任务照旧 |
| `cc.sidebar-modes` / `cowork.cc.sidebar-modes` | 两种模式里「待处理 N」位置一致；点进待处理页；「任务」一组点了打开任务；数 = 产品待处理 + 去重后的代录任务 | 两种模式里**都没有** `cc-nav-pending`；新建、公司、定时任务的位置不挪；准备好的「需要你处理」任务在代录侧栏那一行是「等你处理」，点了打开任务，结束框里有问题和按钮；对话模式里切换钮上的小点（Q7 批了才加）；ERP 那张标成写账失败的单，所在公司和月份的对话行（或占位行）是「等你处理」 |
| `cowork.pending.operator`（`cowork_pending_operator.py`） | `READY` 等 `cc-pending` 页；列出这张；展开选付款方式；「确认」后显示单据在代录中 | 用 `?view=pending&company=X` 兼容入口打开，落到这家这个月的对话或占位行；末尾「需要处理」框列出这张；展开、选、「确认」、文字和服务端断言**照旧**（不多推送记录、不多领单） |
| `cowork.operator.unknown`（`cowork_operator_unknown.py`） | 右栏「开始前确认」列出结果不明的单，点「没有入账」 | 同一张单出现在对话的「开始前」框里，`data-slot="cc-op-unknown"` / `cc-op-not-entered` 沿用；服务端断言照旧 |
| `cc.companies`（`cc_companies.py`，ERP） | 「待处理」那步：侧栏点「待处理」，点一行到单据页 | 改成：这家这个月对话（或占位行）的「需要处理」框里点那一行，打开单据页（`url` 照旧）；公司列表上的数照旧 |
| `cc.chat`（`cc_chat.py`） | 两轮收发、刷新、侧栏找回、搜索 | 加：回复期间侧栏行有运行中标记，回完消失；对话末尾没有多余的框。B8 批了的话，实验室假智能体要支持回 `cc_plan` / `cc_next_step`，加一步断言清单和结束框 |
| 新增 `cowork.cc.operator.upload` | — | Cowork 代录会话里放一份文件：卡片依次出现「上传中 → 排队中或识别中 → 已识别 N 张」；刷新后卡片和状态还在；同一份再放一次，出现第二张卡片、照常识别（断言库里两条识别记录）。实验栈的识别要用实验室的替身，施工时核对 `cowork_intake_review` 用的是什么；没有替身就把这一项降成离线测试，并在报告里写明 |

单元测试：`tests/test_cc_operator.py` 里约 19 处引用右栏（`SourceRules` 的 `test_hands_on_panel_is_a_full_height_column_like_claude`、`test_panel_has_its_own_classes` 等），改成「没有右栏、卡片在会话里、停止在输入框」；`test_assistant_ui.py`（8 处）、`test_cc_session_panel.py`（10 处，右侧待处理卡保留，只改引用路径）、`test_cc_ccs.py`、`test_cc_month_ledger.py`、`test_cc_idle_motion.py`、`test_cc_mobile_content.py` 的待处理页引用跟着改。按「不靠删断言」：每条删掉的断言都要换成对新位置的断言。

## 9. 文案

- **新增**（按官方简短写，三种语言同批）：上传中、排队中、识别中 {n}/{m} 页、已识别 {n} 张、{n} 张不在 {月}、写入 {公司} · {账套} · {电脑}、{n} 张单要处理、资料 {n} 张、录好 {n} 张、没录成 {n} 张、已停止、继续剩下的 {n} 张、未归属公司、开始前（框标题）、第 {n}/{m} 张。
- **改已有**（要用户批，施工前把清单贴进本记录）：
  1. 「需要你处理」（`op.state.attention`）、「需要您的输入」（`needsInput` / `chatWaiting`）、「有待办」：同一个状态三种说法。建议侧栏和状态统一成 Code 的「等你处理」（E2 的新增文案里已有这个词），对话里的状态行保留「需要您的输入」。
  2. 「交给 CC」：不再开新对话，而是在本对话预填。字不变，只是行为变，列出来让用户知道。
- 删掉的词（右栏分区名、待处理页标题和搜索占位）不算改文案，跑一次 `check-copy.py --unused-keys` 清掉。
- 交付时附 `python3 scripts/check-copy.py --report`。

## 10. 模板其余各栏

| 栏 | 结论 |
|---|---|
| 1. 同类复用 | 状态行 `RunLine`、步骤行 `steps.tsx`、附件卡片、`approval-sheet`、侧栏 `ActivityMark`、`PendingList` 的行、`TaskChoices`、共用拖动全部复用；新写的只有任务清单、结束框（两种模式一个组件）、上传状态卡片、占位行 |
| 2. Frappe 原生 | CC 是 ADR-005/007 登记的例外前端（`frontend/assistant`，不是 Desk）。后端用原生 DocType 子表（B1）、`publish_realtime`（B5）、whitelist 只读接口；不手工改库 |
| 3. 按类通用 | 这一类是「CC 里所有需要人处理的事」：代录任务、产品待处理（Cowork 六种原因、ERP 写账失败）、对话里的「允许」卡片，都走同一个「等你处理」状态和同一个框。不覆盖：LINE 识别失败（LINE 当场回复）、定时任务提醒（已挂对话，用提醒圆点） |
| 4. 上下游 | 单据流、Express 映射、写账队列、CC 桌面版载荷都不变。Cowork、ERP 同一套前端，后端各补 B3/B4 |
| 5. 隔离与权限 | B2 按任务的创建人校验（同 `tasks.read`）；B3 沿用 `pending_counts` 现有的人和公司范围，`conversation` 只回本人的对话；占位行建对话走现有 `create` 的权限。都由服务端拦 |
| 6. 入口 | 网页（电脑、窄屏）、CC 桌面版窗口、手机三端同一套。相机：代录的「拍照」走输入框「+」，同直写附件，按「相机权限三步」，相机共用件不改 |
| 7. 共用件 | 8.2 |
| 8. 存储与成本 | B1 每份上传一行子表，约 200 字节，可以不计。不多识别、不多推送。占位行点了才建对话，不开模型轮。B5 推送只给提交人。B8 批了：每轮多一两次工具调用，几百个 token 级别，按现单价每轮不到 ฿0.1（施工时按实测补）。Q6 批了：准备中每句话都要交给 CC，一句一轮。没有新的固定月费 |
| 9. 删除与追溯 | 删任务时连子表一起删（同任务单据子表）。不动已写进 Express 的数据 |
| 10. 官方 UI | 只走 `globals.css` 的变量和 `.cc-*` 类，过写死数值闸门；等待界面不用主题色、进度只用真数（「等待界面全用官方原生」） |
| 11. 测试与验收 | 8.4 加离线单测，三语，浅色和深色，网页、桌面窗口、手机三种变体。实验栈（`pearnly-regression-lab` 现在被主会话占着，通知后再用）跑受影响流程。用户亲自看：BJC 那份 12 页 PDF 在 Cowork 代录里传一次、再传一次，两张卡片的状态；侧栏「等你处理」；「开始前」框里的账套 |

## 11. 分块施工计划（按机器耗时）

同一批文件只能有一个写者。`frontend/assistant` 拆成三块各自持有不同的文件，`assistant.tsx` / `nav.ts` / `globals.css` 只归 W3。

| 块 | 内容 | 写者和文件 | 依赖 | 写代码 | 机器跑 |
|---|---|---|---|---|---|
| 0 | 截图核对 Code 状态（S3～S7、S11 里「不知道」的项），补进本记录 | 只读，要主会话准许电脑控制 | 无 | 0.5 h | — |
| 1 | 后端 B1～B5（B8 批了一起做）、离线测试 | W1：`apps/**`、`tests/test_express_operator_*.py`、`tests/test_cowork_*`、`tests/test_erp_*` | 无 | 4 h | 候选离线检查约 0.7 h |
| 2 | 会话组件：任务清单、结束框、侧栏「等你处理」标记、上传状态卡片、错误和停止统一（两种模式共用） | W2：`components/assistant-ui/thread.tsx`、`tool-uis/*`、`lib/managed-agents/activity.ts`、`turn.ts`、新的 `components/run/*` | 无（先用假数据） | 4 h | — |
| 3 | 代录对话化：删右栏，会话流、输入框下面那一行的电脑和账套、「开始前」框、停止挪进输入框、报告卡片 | W3：`components/operator/*`、`split-handle.tsx`、`globals.css` | 1 的接口形状，2 的组件 | 7 h | — |
| 4 | 待处理并入对话：删入口、占位行、「需要处理」框、深链接兼容、模式小点 | W3（接着 3）：`assistant.tsx`、`nav.ts`、`pending.tsx`、`projects.tsx`、`companies.tsx`、`lib/companies.ts` | 1 的 B3 | 4 h | — |
| 5 | 文案三语、产品地图行、共用件登记、单测和流程改写（8.4） | W1 做完 1 以后接手：`scripts/flows/*`、`scripts/impact/*`、`tests/test_cc_*.py`、`i18n.ts` 交 W3 改 | 3、4 | 4 h | — |
| 6 | 合并、全闸门、受影响流程、15 个入口实测、浅深 × 三变体自查、CI | 一个人 | 全部 | 1 h | 全闸门约 0.5 h；`npm run build` 两处；受影响流程（6 条加新增 1 条）约 1 h；全套约 0.5 h；CI 15 分钟以上查一次，约 0.5 h |

- 能并行：1 ∥ 2 ∥ 0；3 等 2 的组件接口定下来（先约定 props，约 0.5 小时后就能开工）；5 的流程部分可以在 3、4 写到一半时开始写断言。
- 合计：写代码约 28 小时；三个写者并行时，墙钟约 13～15 小时，加机器跑约 3.5 小时。实验栈要等主会话放出来。
- 交付：全闸门、受影响流程、CI 绿 → 合入 main → 报主会话。不部署、不发版（随本批部署由主会话排）。CC 桌面版不改，不发版。

## 12. 待定（交主会话）

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| Q1 | 两种模式：A 保留切换 / B 模式选择挪到输入框下面、侧栏合并 / C 合成一个 | A。这次只改呈现；B 另开评估；C 和「代录右侧不要、直写右侧不动」冲突，又动管钱底线 | 第 3、11 节的工作量 |
| Q2 | 侧栏行加「等你处理」标记，推翻以前「行上没有状态字、彩色点」的口径 | 加。用户这次要求照 Code 的任务状态；样子等块 0 截图定 | 没有这个标记，待处理并进对话以后就没有地方提醒 |
| Q3 | 公司组头的待处理数：删，还是保留数字（点了打开这家待处理最多的那个月的对话） | 删。Code 的组头没有数，行自己亮 | 组头的样子 |
| Q4 | 归不上公司的单（只有 Cowork）落哪里 | 侧栏「最近」组里一行占位行「未归属公司」，点了建一段不挂公司的对话，末尾是「需要处理」框 | 这类单没有入口 |
| Q5 | 对话模式的任务清单和结束框要 CC 自己输出：加 `cc_plan` / `cc_next_step` 两个工具（B8） | 加。照 Code 本来就是模型列清单、模型出结束框；成本是每轮几百 token。不加的话，对话模式只有状态行和步骤行像 Code | 对话模式像不像 Code；产品地图多 4 行 |
| Q6 | 代录准备中在输入框打的字交不交给 CC（现在只存在这台设备） | 交。照 Code，「都在对话框工作」；每句一轮，BJC 那次正是 CC 在对话里先看出买方不符 | 成本；准备中的气泡要不要跨设备 |
| Q7 | 对话模式时，模式切换的「代录」图标上加小点，表示有等你处理的任务（反过来也一样） | 加。删了待处理以后，这是跨模式唯一的提醒；Code 没有两种模式，**没有依据** | 在对话模式里可能看不到代录在等人 |
| Q8 | 直写右侧的「待处理」卡（只列对话月份以外的）保留不动 | 保留（用户说右侧不动），和对话末尾的框共用一个行组件 | 无 |
| Q9 | B1 任务上传子表：这是本任务唯一的新写入 | 批。不记的话，刷新或换设备以后看不到上传状态，根治不了 | 第 5 节 |
| Q10 | ERP 代录要不要也能放资料（现在 ERP 没声明 `intake`，单据在 ERP 里开好） | 不加。ERP 代录的输入框「+」隐藏；要加的话是 ERP 的业务改动，另开评估 | 「Cowork 和 ERP 一模一样」在这一点上做不到完全一样 |
| Q11 | 预检原因「…请核对账套后重新发送」在代录里不准确（还没发送） | 本任务不改（直写和代录共用的服务端文案）；要改的话列进文案清单，给用户批 | 小 |
| Q12 | 块 0：用电脑控制截 Claude 桌面版 Code 页，核对「不知道」的项（等你处理标记、任务清单、结束框、出错、手机） | 做，30 分钟，要主会话准许，用户会看到一次授权窗口 | 不截的话，S3～S7 的样子按 CC 现有组件推，可能和 Code 不一致 |

## 13. 主会话审定（11-04）

范围以用户澄清为准：直写右侧内容区不动；直写和代录左侧「待处理」都删、并进对话；代录右栏删、并进对话；对话流与状态照 Code；Cowork / ERP 同步。

| # | 审定 |
|---|---|
| Q1 | A：保留左上角模式切换。B、C 不做 |
| Q2 | 加「等你处理」标记，样子以块 0 截图为准 |
| Q3 | 删组头待处理数 |
| Q4 | 同意：「最近」组一行「未归属公司」 |
| Q5 | 加 `cc_plan` / `cc_next_step`（每轮几百 token 可接受） |
| Q6 | 交给 CC，每句一轮 |
| Q7 | 加：删了待处理后这是跨模式唯一提醒，属功能不是装饰；样式用 CC 已有的未读点，不新造 |
| Q8 | 保留 |
| Q9 | 批 |
| Q10 | **不按建议**：用户要求 Cowork / ERP 同步。ERP 代录也能放资料，复用 ERP 现有的上传识别入口（与 LINE 录入同一条路），不新造识别链；若 ERP 没有可复用的入口，停下报主会话 |
| Q11 | 本任务不改 |
| Q12 | 做；等用户在场授权电脑控制时做。块 2 先按评估与已有研究文档做，块 0 截图后再校样式 |

施工分工：W1 = 块 1 → 块 5；W2 = 块 2；W3 = 块 3 → 块 4（等 W2 组件接口）。块 6 由最后一个写者做。每个写者独立分支与工作树，只改自己名下文件。

## 14. 统一对话框架（方案，主会话 11-04 追加，待审定，未施工）

用户 11-04：「对话框状态其实直写和代录应该都共用的吧，这东西应该有个标准框架的，做 Harness 的不应该都是一套框架吗」。

用户 11-04 补充：「这些都是现成的，代录没必要重新这一套」，指直写现成的新建页、公司 / 月份 / 分店胶囊、侧栏公司分组和对话列表、新建会话。

目标：只留一套框架。代录任务就是一段会话，进直写同一个会话列表接口、同一个侧栏按公司分组，执行中 / 结束标识走同一套活动状态；两种模式唯一的区别是这段会话的消息由谁执行（直写：CC 交写账队列；代录：代录引擎）。页面只认统一对话事件（Managed Agents 格式加 `pearnly.*` 扩展），代录的状态由服务端的**代录事件源**翻译过去。对话页的 thread、runtime、活动状态、停止键、侧栏标记两种模式共用，`components/operator/*` 里自成一套的状态模型删掉。

### 14.0 现状与关键约束（代码已核对）

| 项 | 现状 | 对方案的影响 |
|---|---|---|
| 统一对话事件 | `assistant/engines/base.py`：`user.message` / `agent.message` / `agent.tool_use` / `agent.tool_result` / `agent.custom_tool_use` / `user.tool_confirmation` / `user.interrupt` / `session.status_*`。站点自己的事件 `pearnly.notice`、`pearnly.answer`、`pearnly.tool_run` 不进引擎，由 `sessions.events` 用 `contracts.merge_by_time` 并进引擎事件，实时都走同一个通道 `contracts.REALTIME_EVENT`（`{session_id, event}`，只推会话主人） | 有现成的「站点事件并进对话」做法（提醒、执行记录），代录照这条路，不另开通道 |
| 代录的 AI 部分 | 已经是一段真的 Managed Agents 会话：`tasks.thread` 建、`Pearnly Assistant Session.operator_task` 指回任务，不进对话侧栏 | 代录会话 = 这一段会话。它的引擎事件照旧来自 Managed Agents，代录事件是并进去的另一路 |
| 每张单的结论 | CC 桌面版录完一张按写账回执报回站点（`worker._ack_operator`），产品写到推送记录（Cowork）/ 单据 `ledger_*`（ERP）；任务子表要等页面调 `task_settle` 才写回。产品回执写回时已有站点钩子：Cowork `cowork_push_state_changed`（`history/push_state._announce`，提交后调用）、ERP `express.pending.notify` | 服务端**知道**每张「录成 / 没录成 + 原因」；接上这两个钩子就能当场出事件，不必等页面收尾。「这一张开始录」（写账件被领走）在写账通道 `cc_desktop` 里，本方案不碰它，见 E8 |
| 每张单的细步与暂停 | 「正在填第几栏」、暂停原因（锁屏、有人在用、等网络）只在执行那台电脑的 CC 本机（桥 `operator_state`，页面 3 秒读一次） | 服务端**不知道**。CC 桌面版本批不改（评估第 0 节），所以这部分只能由执行电脑上的页面经桥补上，见 14.1 的 E10 与 14.6 风险 R1 |
| 行动 | `start` / `task_answer` / `task_resume` / `resolve` / `regroup` / `intake` / `task_delete` 都是站点白名单接口；停止只在执行电脑的桥上 | 行动接口一律不改，卡片上的按钮照旧调它们；新事件只管「显示」 |

所以「代录引擎适配器」落成**站点上的代录事件源**（和提醒、执行记录同一层），而不是在 `engines.ENGINES` 里再加一家引擎：一个站点只配一家引擎，代录会话里打字问 CC 仍要走 Managed Agents；代录事件源只做翻译，把任务记录投影成统一事件，并进同一段会话。

### 14.1 逐项映射：代录状态与动作 → 标准事件

事件编号一律由记录推出、固定不变（同一状态重放得到同一编号，页面按编号去重，跨设备、刷新一致）：`pop_<任务号>_<对象>_<状态>`。工具名是站点内部的显示用名，不交给模型、不进工具表。

| # | 代录的状态 / 动作（现在在哪） | 标准事件 | 页面怎么画（已有组件） |
|---|---|---|---|
| E1 | 新建（现在是代录自己的新建页 + `task_create`） | 用直写的新建页、公司 / 月份 / 分店胶囊和 `sessions.create(project, period, scope)`，多带 `mode=operator`；服务端在同一事务里建任务记录并互相指上（见 14.8） | 直写的新建页，代录专用的新建页删掉 |
| E2 | 你说的话、附件（准备中打字，Q6 已批交给 CC） | `user.message`（引擎原生），附件照对话附件 | 气泡、附件卡片 |
| E3 | 上传一份资料：上传中 → 排队 → 识别中 n/m → 已识别 N 张 / 失败（B1/B2，已合入） | 每份一对 `agent.tool_use`（`operator_upload`，input：文件名、大小）+ `agent.tool_result`（已识别：单号列表、几张不在本月；失败：`is_error` + 失败码）；进行中的页数用**新扩展** `pearnly.progress`（见下） | 步骤折叠行 `steps.tsx`：「识别了 3 份（1 个失败）›」，点开每份一行；进行中行尾「识别中 3/12」 |
| E4 | 资料清单（`documents`，按组） | 一条 `agent.tool_use`（`operator_sources`）+ `tool_result`（按组的张数、每张一行）；改分组仍调 `regroup`，之后重出一条结果（编号带资料版本） | 步骤行「资料 22 张 · 采购 20 · 费用 2 ›」，展开后的分组列表与拖动沿用 W3 的组件 |
| E5 | 开始前确认（`precheck`：要人选的项、结果不明的单、写入目标 B4） | `agent.custom_tool_use`（`operator_start`，input：写入目标、按类的要选项、结果不明的单）+ `pearnly.tool_run`（`awaiting_approval`）；会话状态 `session.status_idle`（`stop_reason: requires_action`，停着等这张卡）→ 侧栏「等你处理」 | 同对话「允许」卡片的位置与状态；卡片里的下拉、两个按钮和「开始」是**新的工具卡片渲染**（tool-uis 按工具名注册），点「开始」照旧调 `start`（带 answers），然后补一条 `user.tool_confirmation`（allow） |
| E6 | 交单结果、没收的单（`start` 回包、`attach`） | `operator_start` 的 `agent.tool_result`（交了几张、没收几张及原因）；`pearnly.tool_run` → `done` | 卡片变成点完的样子（同允许卡片） |
| E7 | 等电脑空闲（`queued`） | `session.status_running` + 状态行文字用**新扩展** `pearnly.progress`（`label: waiting_computer`） | 状态行「计时 · 等电脑空闲」 |
| E8 | 每张交进队列、开始录 | 交单时每张出一条 `agent.tool_use`（`operator_document`，input：单号、对方、金额；时间 = `queued_at`），之后没有结果的就是「还在录 / 排着」。「正在录的是哪一张」只有执行电脑的桥知道，由 E10 补 | 步骤行里没有结果的几张显示进行中 |
| E9 | 每张录成 / 没录成 / 跳过 / 作废（产品回执、`settle`） | 同一张的 `agent.tool_result`：录成带 Express 单号；没录成 `is_error` + 原因码（页面按代码出文案）；结果不明的单另带 `result_unknown` | 步骤行「录好 18 张 · 没录成 2 张（2 个失败）›」，展开每张一行、失败红字（评估 S2） |
| E10 | 进行中的细节与暂停（桥：锁屏、有人在用、等网络、第 n/N 张） | `pearnly.progress`（`done`、`total`、`label`：暂停原因码）。**只有执行电脑上的页面**能从桥读到，由页面本地合成同形状的事件喂进同一个 reducer，不落库、不推送 | 状态行「计时 · 第 7/22 张 · 锁屏暂停」 |
| E11 | 中途停下等人选的那张（`waiting`，现在的 `TaskChoices`） | `agent.custom_tool_use`（`operator_choose`，input：这张单、要选的项与可选值）+ `pearnly.tool_run` `awaiting_approval`；`session.status_idle` `requires_action` | 结束框位置的问题卡片；点了照旧调 `task_answer`，再补 `user.tool_confirmation` |
| E12 | 整批停（登录、选公司、授权……，`attention` + `BATCH_STOPPERS`） | `agent.message`（一句事实：停在哪张、原因码对应文案）+ `agent.custom_tool_use`（`operator_resume`）卡片；`requires_action` | 结束框：「Express 登录未设置」〔设置〕〔继续〕；继续照旧调 `task_resume` |
| E13 | 你点停止（S7） | `user.interrupt`（页面照对话发）→ 代录事件源翻译成：执行电脑上 = 桥 `operator_control stop`；别的设备 = **新增**一种任务控制 `stop`（同现有的 `skip` / `retry` 记在任务上，执行电脑的页面取走去停，`tasks.push_control` 的白名单多一项） | 输入框 ■ 与 `Esc`，同对话 |
| E14 | 已停止 / 已完成（`stopped` / `finished`） | `agent.message`（「已停止 · 录好 N 张」或完成回顾）+ `session.status_idle`（`end_turn`）；`stopped` 且还能继续时加 `operator_resume` 卡片 | 结束框、回顾行 |
| E15 | 报告 CSV（右栏 ⑤，现在在浏览器里由 `model.reportCsv` 生成） | 收尾那条 `agent.message` 带一个会话文件（`kind: output`），服务端按任务记录现生成，列与现在的 `reportCsv` 一致（搬到服务端，同一份列定义） | 回复下面的文件卡片 `MessageFiles`，同直写输出文件 |
| E16 | 任务里问 CC（对话框甲，`task_thread`） | 引擎原生事件，不用翻译 | 同一条流里，不再单独「画在任务记录下面」 |
| E17 | 侧栏行状态（运行中 / 等你处理 / 未读 / 完成） | 从上面的 `session.status_*` 自动得出，同对话行；`sessions.run_state` 对代录会话改读任务状态（`queued` / `running` → running，`attention` 或停着的卡片 → requires_action） | 同一个 `ActivityMark` |

**要新加的扩展事件**（只在站点与页面之间，不进引擎，同 `pearnly.notice`）：

| 事件 | 字段 | 用在 |
|---|---|---|
| `pearnly.progress` | `id`、`processed_at`、`tool_use_id`（对哪一步，可空 = 整个会话）、`done`、`total`、`label`（代码：`reading_pages` / `waiting_computer` / `entering` / 暂停原因码） | E3 识别页数、E7 等电脑、E10 第 n/N 张与暂停。同一步只留最新一条（编号带 `done`，页面只认最大的） |

不新增别的扩展：卡片复用 `agent.custom_tool_use` + `pearnly.tool_run`，结果复用 `agent.tool_result`，文件复用会话文件。

### 14.2 会话怎么存、怎么回放和推送

- **一一对应**：会话是主体，任务记录是挂在会话上的代录业务记录（单据、上传、控制、批次号）。新建代录会话时同一事务建任务记录，`Pearnly Assistant Session.operator_task` ↔ `Pearnly Express Operator Task.session_id`（两个字段都已存在，不加列）。旧任务没有会话的，由一个原生补丁一次补建并指上（不调模型，只建会话行；远端会话在第一次打开时再开，同现在的懒建）。会话**进**直写同一个会话列表（14.8），代录自己的任务列表不再给侧栏用。
- **代录事件不另存一张事件表**：每次读都由任务记录投影出来（任务状态、`documents` 子表、`uploads` 子表、预检结果），编号固定，所以跨设备、刷新回放得到同一串事件。要补的只有时间：`documents` 子表加 `queued_at`、`done_at` 两列（Datetime，产品回执写回子表时一并记；这是唯一的新列），事件按它排序；`uploads` 已有 `uploaded_at`。
- **读**：`sessions.events` 对 `operator_task` 不空的会话，在已有的 `merge_by_time(引擎事件, 提醒)` 里再并一路 `operator_feed.events(task)`；增量补拉按锚点时间取，同提醒的 `since` 做法。
- **推**：三个时刻，代录事件源算出新增的那几条，经 `contracts.REALTIME_EVENT` 推给会话主人，形状同 `notices`：任务记录保存后（`tasks._announce` 那一处）；产品回执写回后（在 Cowork `cowork_push_state_changed`、ERP `express.pending.notify` 旁各登记一个只读入口：这张单属于哪个在跑的任务，就对那个任务做一次 `settle` 同样的只读刷新并推事件；只读产品状态、写任务子表，不碰推送记录）；上传状态变了（识别队列已推 `pearnly_ocr_work`，B5，页面收到即补拉）。推不出去不影响业务，页面下一次补拉照样拿到（投影是确定的）。
- **运行状态**：`sessions.run_state` 对代录会话：引擎在跑 → running（CC 在回话）；否则看任务：`queued` / `running` → running，有 `awaiting_approval` 的卡片或 `attention` → requires_action，其余 idle。侧栏「等你处理」因此和对话完全同一条路（评估 1.3 不用再分两种行）。
- **删除**：删任务照旧连会话一起删（`tasks.delete` 已经这么做）。

### 14.3 管钱底线、代录业务逻辑不动：哪些文件不碰

事件源只**读**任务记录与产品回执、只**转调**现有接口；不新增任何写账、推送、扣费、判单的路径。

| 不碰 | 内容 |
|---|---|
| `apps/pearnly_cowork/pearnly_cowork/push/**`、`targets/**`、`connections/**`、`companies/rules.py` | Cowork 推送准备、预检规则、账套选择、写者锁、结果不明核对 |
| `apps/pearnly_cowork/pearnly_cowork/assistant/operator_source.py` 的 `precheck / submit / status / resolve / regroup / intake` | Cowork 代录钩子（B2 加的只读 `uploads` 已合入） |
| `apps/pearnly_erp_portal/pearnly_erp_portal/express/` 下的 `push.py`、`prepare.py`、`choices.py`、`target.py`、`facts.py`、`contracts.py`、`operator.py`（整个文件） | ERP 写账准备、账套选择、代录交单与回执（`pending.py` 只在 `notify` 末尾加一行调用共用层的只读入口） |
| `apps/pearnly_core/pearnly_core/express_operator/contracts.py`、`products.py`、`api.py` 的行动接口、`tasks.py` 的 `attach / answer / resume / _resubmit / _conclude / settle` | 共用层状态机与交单 |
| `apps/pearnly_core/pearnly_core/cc_desktop/**`、`companion/**` | 写账通道、CC 桌面版（不发版） |
| 识别与扣费：`ocr/**`、`execution/**`、`billing*` | — |

会改的只有：新文件 `express_operator/feed.py`（投影与推送，纯读）；`assistant/sessions.py`（`events` / `run_state` 认代录会话）；`assistant/contracts.py`（`pearnly.progress` 形状）；`tasks.py` 的 `create`（建任务即建会话）、`push_control` 的白名单加 `stop`、`attach` / `_refresh` 写子表时顺带记 `queued_at` / `done_at`（只加时间，不改判断）；`documents` 子表加两列；两个产品的 `hooks.py` 各登记一个回执后的只读入口（Cowork 挂在已有的 `cowork_push_state_changed` 列表里，ERP 在 `express.pending.notify` 末尾调用共用层同一个入口）。证明办法：施工提交里 `git diff --stat` 只出现上面这几个文件；加一条离线测试，对「一个任务从建到收尾」前后比对 `products.submit / status` 的调用次数与参数和改造前逐字相同。

### 14.4 前端能删什么，还剩什么

| 文件（行数） | 去留 |
|---|---|
| `live-source.ts`（706）、`mock-source.ts`（466）、`model.ts`（290）、`types.ts`（302 的大部分） | **删**：进度拼装、桥与站点合并、批次快照模型都由事件取代。原因码 → 级别表 `REASONS`、`BATCH_STOPPERS` 移到出文案的那一处（或直接用服务端给的代码） |
| `controller.tsx`（399）、`workspace.tsx`（446）、`store.ts`（98）、`context.ts`（19） | **删**：代录工作区换成对话页同一个 thread / runtime，打开代录会话就是打开一段会话 |
| `task-thread.tsx`（168） | **删**：任务里的对话就是这段会话本身 |
| `panel.tsx`（534）、`pending-tasks.tsx`（46）、`attention*.ts`（72） | **删**（评估 8.1 本来就删；跨模式小点 Q7 改读会话的 requires_action） |
| `operator-sidebar.tsx`（440）、`tasks.ts`（161）、`prefs.ts`（43）、`task-title.ts`（11） | **删**：侧栏、列表分页、改名、置顶、手排顺序、搜索、多选都用直写那一套（`thread-list.tsx`、`sidebar-order.ts`、`session-list-adapter.ts`），见 14.8 |
| 代录的新建任务页（`workspace.tsx` 里那一段） | **删**：用直写的新建页与胶囊 |
| `mode-switch.tsx`（66）、`index.ts`（12） | **留**：左上角切换（Q1 定 A），切换只改侧栏的模式筛选与新建时带的 `mode` |
| **新增** `tool-uis/operator-*.tsx` | 4 张工具卡片：`operator_start`（开始前）、`operator_choose`（中途选）、`operator_resume`（继续 / 设置登录）、上传与单据的步骤行展开内容；另有执行电脑上把桥的进度合成 `pearnly.progress` 的一个小钩子（约 60 行） |

估计 `components/operator/` 从约 4300 行降到约 400 行（模式切换、4 张卡片和桥钩子）。

### 14.5 和 W3 正在做的「代录对话化」怎么衔接

W3 现在做的块 3、块 4 是在现有 `operator/*` 模型上把右栏挪进会话流。改成统一框架后：

| W3 的产出 | 去留 |
|---|---|
| W2 已合入的 `components/run/*`（上传状态卡片 `upload-card`、`upload-state`、输入框 `ComposerStop`、`thread.tsx` 的 `Tail` 与每条消息的 `before` 钩子） | **保留**。上传卡片就是 E3 那一步展开后的样子；`ComposerStop` 在统一后可以不用（停止走对话自己的 `user.interrupt`），留着不碍事；`Tail` / `before` 钩子在统一后不再需要往对话流里另插代录内容，审定后可删 |
| 删右栏、抽屉、拖宽、分区折叠（评估 8.1） | **保留**，两种方案都要删 |
| 停止挪进输入框、`Esc` | **保留**；只是 ■ 发的从「代录停止」变成 `user.interrupt`，由服务端翻译 |
| 「开始前」框、「需要处理」框、报告卡片、资料分组列表的界面 | **保留界面**，改成按工具名注册的工具卡片，数据从事件的 input / result 来，不再从 `controller` 的状态来 |
| 输入框下面那一行的电脑钮、账套钮 | **保留**（账套来自 E5 卡片里的写入目标，也就是 B4） |
| 会话流里自己拼的单据行、状态行（照 `TaskLog` 改的） | **不保留**：换成对话页的步骤行和状态行（W2 的块 2），这正是要统一的部分 |
| 代录侧栏（`operator-sidebar.tsx`）、代录新建任务页上的改动（如行尾「等你处理」标记） | **不保留**：侧栏和新建页都换成直写那一套（14.8），标记由同一个 `ActivityMark` 出 |
| 块 4（待处理并进对话、占位行、深链接） | **保留**；占位行、「等你处理」都在直写侧栏里做，代录会话进了同一个列表后自然一起显示 |

建议：W3 现在**停在块 3 里与状态模型相关的部分**（会话流拼装、进度显示），先做与本方案不冲突的删除、输入框、卡片界面和块 4；本方案审定后由一个写者接着把卡片接到事件上。W2 的会话组件（任务清单、结束框、状态行、侧栏标记）原样复用，不受影响。

### 14.6 工作量（按机器耗时）与风险

| 块 | 内容 | 写代码 | 机器跑 |
|---|---|---|---|
| U1 服务端 | `feed.py` 投影 E1–E17、编号、时间排序；`sessions.events` / `run_state` 接上；推送；`documents` 两列；`stop` 控制；`sessions.listing` 的 `mode`、`create(mode=operator)` 连建任务、改名置顶删除转到任务；旧任务补建会话、手排顺序换编号两个补丁；离线测试（每种状态的事件、重放一致、增量、权限、业务调用不变、列表按模式筛、补丁幂等） | 7 h | 候选离线检查 0.7 h |
| U2 前端 | 代录会话改用对话 thread / runtime、直写的新建页与侧栏（多一个 `mode` 筛选）；4 张工具卡片；桥 → `pearnly.progress` 小钩子；删 14.4 的文件；单测改写（`test_cc_operator.py` 约 19 处、侧栏两模式的测试） | 7 h | `npm run build` 两处 0.2 h |
| U3 流程 | 改 `cc.operator`、`cowork.pending.operator`、`cowork.operator.unknown`、`cc.sidebar-modes` 两条，新增「代录会话回放一致」（换设备、刷新事件逐条相同） | 3 h | 受影响流程约 1 h，全套 0.5 h |
| 合并与闸门 | — | 0.5 h | 全闸门 0.5 h、CI 15 分钟以上查一次 |

合计写代码约 17.5 小时，机器约 3.5 小时；和 W3 当前块 3 的剩余部分有重叠，实际增量约少 4 小时（14.5 不保留的那两项不用再写）。

| 风险 | 说明 | 处置 |
|---|---|---|
| R1 细步和暂停只在执行电脑 | CC 桌面版不改的前提下，网页、手机、别的电脑上只看得到「第几张开始 / 录完」，看不到「锁屏暂停」 | 执行电脑上页面经桥补（E10）；别的设备状态行写「进行中 · 第 n/N 张」（n 来自 E8/E9）。要全设备都看到暂停原因，需要 CC 桌面版心跳带上批次状态，属于发版改动，另批 |
| R2 两路事件合并的顺序 | 引擎事件时间来自 Anthropic，代录事件来自站点，钟差几秒 | 沿用提醒的 `merge_by_time` 与 5 分钟前推的增量窗口（`notices.FEED_SLACK`）；编号去重 |
| R3 投影要和旧状态一一对应 | 漏一种状态，页面就少一张卡 | 测试按 `TASK_STATES × 单据状态 × 原因级别` 全排列出事件，对照 14.1 断言 |
| R4 推送量 | 一批 200 张，每张两条事件 | 只推会话主人；页面合并同一步的进度事件；量级同对话的工具步骤 |
| R5 旧任务 | 没有会话、子表没有时间 | 打开时补建会话；没有时间的按 `started_at` 加序号排，显示不受影响 |
| R7 侧栏数据迁移 | 旧任务补建会话、手排顺序从任务号换成会话编号，漏了就有任务在侧栏里「消失」或顺序乱 | 两个补丁都是原生 patch、幂等；测试比对补丁前后「任务数 = 代录会话数」「手排先后不变」 |
| R6 和 B1–B5 的关系 | 已合入的接口照用：上传记录（B1/B2）是 E3 的数据来源，`months`（B3）给侧栏，写入目标（B4）进 E5 卡片，识别推送（B5）触发 E3 的增量 | 不用回滚 |

### 14.8 同一个会话列表、同一个侧栏

用户 11-04：直写现成的新建页、胶囊、侧栏公司分组、对话列表、新建会话，代录不再另做一套。

| 项 | 做法 |
|---|---|
| 会话列表接口 | 只有 `sessions.listing`（分页、星标、按公司分组 `grouped` / `group`、`check` 运行状态校正、未读）。现在它用 `operator_task: not set` 把代录会话排除在外；改成带 `mode` 参数：`chat` = `operator_task` 为空，`operator` = 不为空，不带 = 全部。`_grouped_first` / `_page` / `_starred` / `_filed_counts` 都只是多一个筛选条件，组头的数与「更多」游标按同一筛选算 |
| 模式从哪来 | 不加列：`operator_task` 不为空就是代录会话。新建时 `sessions.create(..., mode="operator")` 在同一事务里调 `tasks.create(project, period, ...)` 并写回 `operator_task`；产品不允许代录（`products.allowed`）就拒，不悄悄建成直写 |
| 新建页与胶囊 | 直写的新建页原样用：公司、月份、分店胶囊，`Ctrl/⌘+N`。代录要求有公司和月份（任务必填），没选时「发送」照直写现有的「先选公司」口径挡住。分店照记在会话的 `project_scope` 上；代录的资料来源现在不按分店筛，本方案不改 |
| 侧栏分组、排序、搜索、多选、改名、置顶、归档、删除 | 全用直写那一套。改名、置顶走 `sessions.rename / star`，同时写回任务标题、星标（任务记录只是镜像，读以会话为准）；删除走 `sessions.delete`，它对代录会话转调 `tasks.delete`（交出去还没收尾的照旧不能删） |
| 手排顺序 | 只留直写那一份（偏好 `pearnly_cc_layout.order`，按会话编号）。代录那一份（`operator` 项，按任务号）由一次性补丁按 `任务号 → 会话编号` 换过去并入 `order`，再删 `operator` 项；推翻 11-03「任务号不进对话的手排顺序」——现在进的是会话编号，不再是任务号 |
| 活动状态 | 同一个 `ActivityMark`，数据都来自 `sessions.listing` 的 `check` / `runs` 与实时事件，代录会话的运行状态按 14.2 的 `run_state` 规则算 |
| 深链接 | 统一成 `?c=<会话>`；旧的代录任务链接（`?view=tasks` 带任务号）按任务号查会话后跳转，兼容入口不删 |

**两种模式下侧栏怎么区分**：

| 做法 | 说明 | 利弊 |
|---|---|---|
| 甲：同一个列表，按模式筛（建议） | 左上角切到直写，侧栏只列直写会话；切到代录，只列代录会话。两边是同一个接口、同一个组件，只差 `mode` 参数 | 和 Q1 审定的 A（保留左上角切换）一致；公司组里不会混着两种行，代录的「等你处理」不会被一堆对话淹没；跨模式提醒照 Q7 用切换钮上的未读点（数据也是同一个列表接口按 `mode=operator` 取 requires_action 的数）。代价：在直写里看不到代录会话，要切一下 |
| 乙：一起显示 | 一个列表混排，行首图标分直写 / 代录，新建时在输入框下面选模式 | 最像 Code（模式只是会话的属性）；但这就是第 3 节的方案 B，Q1 已定不做；左上角切换会变得多余 |

建议甲。界面上两种模式完全同一套，只有侧栏多一个筛选；以后要改成乙，只是前端不再传 `mode`，服务端不用再改。

### 14.7 要主会话定的

| # | 问题 | 建议 |
|---|---|---|
| U-Q1 | 采用本方案（代录事件源并进同一段会话，而不是新加一家引擎） | 采用。理由见 14.0 末段 |
| U-Q2 | W3 的块 3 先停在状态模型相关部分，等本方案审定 | 停，见 14.5 |
| U-Q3 | 别的设备上停止：新增任务控制 `stop`（执行电脑的页面取走去停） | 加。这是「停止」在两种模式一样的前提，不碰交单与回执 |
| U-Q4 | 暂停原因全设备可见要等 CC 桌面版改心跳 | 本批不做（R1），记入下一批 |
| U-Q5 | 两种模式在侧栏：同一个列表按模式筛（甲），还是一起显示（乙） | 甲，见 14.8 |
| U-Q6 | 手排顺序合成一份（代录的按任务号换成会话编号），推翻 11-03「任务号不进对话的手排顺序」 | 合。进的是会话编号，不是任务号 |

### 14.9 主会话审定（11-04）

| # | 审定 |
|---|---|
| U-Q1 | 采用：代录事件源并进同一段会话，不新加引擎 |
| U-Q2 | 已停 |
| U-Q3 | 加任务控制 `stop` |
| U-Q4 | 本批不做（不发 CC 桌面版），记入下一批 |
| U-Q5 | 甲：同一个列表按 `mode` 筛 |
| U-Q6 | 合成一份，按会话编号 |
| 补充 1 | 用户更正后**没有**单独的任务清单、结束框、侧栏新标记、模式切换小点（Q2、Q7 撤销）。E11、E12、E14 里写的「结束框位置」一律改为：对话流里的 `agent.message` 加工具卡片，样子同直写现有的「允许」卡片；侧栏只用现有的 `ActivityMark` |
| 补充 2 | W3 分支 `pearnly/cc-code-operator`（724a24013，工作树 `.claude/worktrees/agent-a453b5b8da64d959c`）里已做的删右栏、块 4、新建页与侧栏复用直写，是 U2 的起点 |

### 14.10 U1 交给 U2 的事件清单（U1 施工中定稿，字段以本节为准）

服务端 `pearnly_core.express_operator.feed` 把任务记录投影成下面这些事件，`sessions.events` 按时间并进同一段会话（整段、增量同一套），
`contracts.REALTIME_EVENT`（`{session_id, event}`，只推会话主人）实时推。投影是确定的：同一份任务记录两次投影逐条相同，编号由记录推出。

**通用**

| 项 | 约定 |
|---|---|
| 编号 | 代录事件一律以 `pop_<任务号>_` 开头。**页面把 `pop_` 开头的当本站事件**：不拿它当补拉锚点（同 `pearnly.*`，`isLocalEvent` 加这个前缀）；`operator_*` 卡片**不走**自动答复 / `run_tool` / `confirm`（服务端 `confirm` 也会把 `pop_` 的编号原样丢掉） |
| 时间 | `processed_at` 为 RFC3339 UTC（微秒）。同一步的事件先后：调用 < 进度 < 结果 |
| 一步的状态 | 同一个 `tool_use_id` 下，**`processed_at` 最新的那条**（`agent.tool_result` / `user.custom_tool_result` 或 `pearnly.progress`）说了算：重交的单先出一条更新的 `pearnly.progress`（排队），之后再出新的结果。整段重放只有当前一轮，增量的设备上旧结果会被更新的进度盖过 |
| 卡片能不能点 | 卡片（`agent.custom_tool_use`）能点 = 有 `pearnly.tool_run` `awaiting_approval`、还没有 `user.custom_tool_result`。不靠 `pendingCustomTools`（用户在代录会话里和 CC 说话后，引擎的 `status_idle(end_turn)` 会清掉它） |
| 运行状态 | 投影里带 `session.status_*`（编号 `pop_<任务号>_st_<…>`）；以服务端 `run`（`events?status=1`、`listing?check=`）为准：引擎在回话 → running；否则按任务：`queued` / `running` → running；准备中、有等着的卡片 → requires_action（`parked` / `awaiting_approval` = 卡片编号）；其余 idle |
| 会话 | 代录会话 = `Pearnly Assistant Session.operator_task` 不为空。补丁补建的和新建的代录会话编号是 `op_<任务号>`，远端会话第一次发话时才开（之前读事件不碰引擎）。`listing` / `get` / `create` 回的每行多 `mode`（`chat` / `operator`）与 `task`（任务号，直写为空） |

**工具名与形状**（`agent.tool_use` = 步骤行；`agent.custom_tool_use` = 卡片）

| 工具名 | 事件 | 编号 | `input` | 结果 / 进度 |
|---|---|---|---|---|
| `operator_sources` | `agent.tool_use`，时间 = 任务创建 | `pop_<T>_sources` | `{task, company, company_title, period}` | 交单后 `agent.tool_result`（`pop_<T>_sources_r`）：`{"total": n, "groups": {"purchase": n, "sales": n, "expense": n, "payment": n, "skip": n, "": 未分组}}`。准备中没有结果，展开时页面照旧调 `documents(company, period, offset, limit)`（服务端分页） |
| `operator_upload` | `agent.tool_use`，每份一条，时间 = `uploaded_at` | `pop_<T>_u<子表行名>` | `{task, request_key, filename, size, batch, source}` | 排队 / 识别中：`pearnly.progress`（`label` = `queued` / `reading_pages`，`done` / `total` = 页）；已识别：`agent.tool_result` `{"state": "done", "page_count", "documents": [{doc_id, ref, date, party, in_period, listed}]}`；失败：同上 `is_error: true`，`{"state": "failed", "code"}`（`unavailable` = 读不到）。结果编号 `…_rdone` / `…_rfailed`。**只有读事件时带识别状态**（实时推送只推调用本身），收到 `pearnly_ocr_work` 就补拉一次 |
| `operator_start` | `agent.custom_tool_use`，时间 = 任务创建；准备中带 `pearnly.tool_run` `awaiting_approval` | `pop_<T>_start` | `{task, company, company_title, period}` | 卡片自己调现有接口：`precheck`、`resolve`、`regroup`、`intake(task=…)`，点「开始」调 `start(company, doc_ids, computer, start_at, answers, task, groups)`；**不调 confirm**。交单后出 `user.custom_tool_result`（`pop_<T>_start_r`）：`{"sent": n, "not_sent": [{doc_id, ref, party, reason}], "computer", "start_at"}` + `pearnly.tool_run` `done` |
| `operator_document` | `agent.tool_use`，每张一条（交单后），时间 = 这张交进队列的时刻 `queued_at` | `pop_<T>_d<子表行名>` | `{task, n, doc_id, ref, party, group}`（`n` 从 1 数，同 `express_operator_status`） | 排着：`pearnly.progress`（`label` = `queued`，被结果不明的单挡着时 `blocked`，`detail` = 「单号 · 对方」）；有结论：`agent.tool_result`（编号 `…_r<state>_<毫秒>`）`{"state": saved / not_done / cancelled / skipped, "docnum", "voided_docnum", "reason", "level", "result_unknown", "ref", "party"}`，`not_done` 时 `is_error: true`。`reason` 是代码：CC 回执的 `[OPERATOR_X] …` 已拆成 `x`，`level` 按 `contracts.REASONS`（认不出 = human），结果不明 = `push.result_unknown` 且 `result_unknown: true`；页面按代码出文案 |
| `operator_choose` | `agent.custom_tool_use`，每张没录成、要人处理的单一张（整批停的不在这里），时间 = 这张出结论的时刻；带 `awaiting_approval` | `pop_<T>_c<子表行名>_<毫秒>` | `{task, company, doc_id, ref, party, reason, level, result_unknown}` | 卡片照旧调：选项 `precheck(company, [doc_id])`，点了 `task_answer(name, doc_id, answers)`（单张重试 = 空列表）；结果不明 `resolve(company, doc_id, entered, docnum)` 后 `task_settle(name)`。这张交回队列后出 `user.custom_tool_result`（`…_r`）`{"answered": true}` + `done` |
| `operator_resume` | `agent.custom_tool_use`，任务因整批停（`contracts.BATCH_STOPPERS` 或停止）停下时一张；带 `awaiting_approval` | `pop_<T>_resume_<毫秒>` | `{task, company, reason, doc_id, ref, party, doc_ids}`（`reason` = 卡住那张的原因代码，只因停止跳过的是 `stopped`；`doc_ids` = 会被继续的几张） | 点「继续」调 `task_resume(name)`；「设置」照旧开 CC 的 Express 登录。交回后出 `user.custom_tool_result`（`…_r`）`{"resumed": true}` + `done` |

**收尾消息（E14 / E15）**：任务到 `finished` / `stopped` / `attention` 且没有在排的单时，出一条 `agent.message`（`pop_<T>_end_<状态>_<毫秒>`），`content` 是一个空文字块，另带 `operator` 字段：
`{"kind": "finished" | "stopped" | "attention", "task", "counts": {"saved", "not_done", "cancelled", "skipped"}, "report": {"filename", "url"}}`。页面按 `operator` 画「已停止 · 录好 N 张」或完成回顾，`report.url`（`GET /api/method/pearnly_core.express_operator.feed.report?name=<T>`，只给任务主人，CSV 列同原来的 `reportCsv`）画成回复下面的文件卡片。

**扩展事件 `pearnly.progress`**（形状在 `assistant/contracts.py` 的 `progress_event`，只在站点与页面之间）：

```json
{"type": "pearnly.progress", "id": "pop_<T>_…", "processed_at": "2026-11-04T08:00:00.000000Z",
 "tool_use_id": "pop_<T>_u<行>" , "done": 3, "total": 12, "label": "reading_pages", "detail": ""}
```

`tool_use_id` 为空串 = 整个会话的状态行。`label`：`queued`、`reading_pages`、`blocked`（单据步骤），`waiting_computer`（任务等电脑空闲，`done/total` = 已有结论 / 交出张数）、`entering`（CC 已开始录，同上）；执行电脑上页面从桥合成的同形状事件（E10，不落库不推送）用暂停原因码（`screen_locked`、`user_active`、`network_wait`、`account_busy`、`waiting_start`）或 `entering`，编号用 `local-` 开头。同一步只认 `processed_at` 最新的一条，相同时刻认 `done` 大的。

**停止（E13）**：代录会话里点 ■ 照对话发 `interrupt`。服务端：远端会话在回话就照旧打断引擎；任务在 `queued` / `running` 时记一条任务控制 `{"action": "stop", "doc_id": "", "at"}`（`push_control` 白名单多了 `stop`）。执行电脑上的页面照旧取 `task_controls(name)`，取到 `stop` 就走桥 `operator_control stop`（执行电脑本机点停止也可以直接走桥，两条路都安全）。

**会话列表与新建（14.8）**：`listing(..., mode="chat" | "operator")`（不带 = 全部），`grouped` / `group` / 星标 / `check` 都按同一筛选；`create(project, period, scope, mode="operator")` 同一事务建任务并互相指上（产品不允许代录时报「没有权限」，不悄悄建成直写；没有公司或月份报请求无效）。改名、星标走 `rename` / `star`，同时写回任务；`delete` 转调任务删除（交出去还没收尾的照旧不能删）。旧链接 `?view=tasks` 带任务号：`task_get(name).session_id` 就是会话编号。

### 14.11 U1 施工记录（分支 `pearnly/cc-unified-feed`，基线 `e0a720a11` = `origin/pearnly/cc-code-backend`，B1～B5 已在其上，还没进 main）

| 项 | 落点 |
|---|---|
| 代录事件源 | 新文件 `express_operator/feed.py`：`project`（纯函数，E1～E17 → 事件，编号见 14.10）、`since`（增量）、`run_state`、`stamp`（单据子表时间）、`publish`（保存后推新出现的事件）、`receipt` / `refresh`（产品回执后的只读入口，提交后以任务主人身份 `settle`）、`report`（E15 CSV） |
| 会话 | `assistant/sessions.py`：`listing(mode)`（`_page` / `_starred` / `_grouped_first` / `_filed_counts` 同一筛选）、`create(mode="operator")`（`_create_operator` → `tasks.create`）、`open_operator`（本站先建 `op_<任务号>`）、`_remote_ready`（第一次发话才开远端）、`events` 并进事件源（`_operator_events`）、`run_state` 认任务（`_operator_run`）、`get` / `rename` / `star` / `move` / `delete` 转到任务、`confirm` 丢掉 `pop_` 编号、`interrupt` 记任务控制 stop、`run_tool` 拒 `pop_`；`mode` 由 `sessions.requested_mode` 从同一个请求的参数取（`api.listing` / `api.create` 照旧原样转交，**接口模块 `assistant/api.py` 不改**：改它会让产品地图把所有调它别的接口的页面都算作被改到） |
| 契约 | `assistant/contracts.py`：`PROGRESS_EVENT` / `progress_event` |
| 任务 | `express_operator/tasks.py`：`create` 同一事务建会话并指上；`push_control` 认 `STOP`（不进给模型的 `CONTROL_ACTIONS`，工具表不变）；`_save` 保存前 `feed.stamp`（只记时间，`attach` / `_refresh` / `_resubmit` 本身一行没改）；`_announce` 末尾 `feed.publish` |
| 表结构 | `Pearnly Express Operator Task Document` 加 `queued_at`、`done_at`（Datetime，只读），`doc_id` 加索引（回执入口按单据找任务）；原生 DocType 改动，`bench migrate` 落库 |
| 补丁 | `v0_0_9_operator_task_sessions`（旧任务补建会话，幂等）、`v0_0_10_operator_order_into_sessions`（`operator` 的手排顺序按会话编号并进 `order`，删 `operator` 项，幂等） |
| 产品回执入口 | Cowork `hooks.py` 的 `cowork_push_state_changed` 加 `pearnly_core.express_operator.feed.receipt`；ERP `express/pending.py` 的 `notify` 末尾（`finally`，原逻辑提前返回时也走）调 `_operator_receipt` |
| 文案 | 术语表 `pearnly_core/i18n/messages.tsv` 加报告用的 47 条键式词条（中英泰照搬 CC 网页原有的 `reportCsv` 文案，日文新译）与两个字段标签；`scripts/i18n-build.py` 重生成翻译包 |
| 测试 | 新 `tests/test_express_operator_feed.py`（R3 全排列、重放一致、增量、上传、重交收口、旧任务无时间、记时间、业务调用逐字对照改造前、推送只给主人、停止、回执入口、报告、权限、两个补丁幂等）；`tests/test_assistant.py` 加列表按 mode 筛、代录会话（无远端不碰引擎、卡片不经引擎、停止、第一次发话开远端、删除与新建、改月份转任务）；`tests/test_express_operator_tasks.py` 接上新的会话与事件源桩；Development checks 登记新测试 |

**不碰的文件**：14.3 列的一个都没改（`products.py`、`express_operator/contracts.py`、`express_operator/api.py`、`push/**`、`cc_desktop/**`、`companion/**`、`operator_source.py`、ERP `operator.py` 等）。业务调用逐字不变由 `LifecycleTests.test_a_task_from_start_to_finish_calls_the_product_exactly_as_before` 守住（对照值是用 `3d46f2ae0` 的 `tasks.py` 跑同一过程记下的）。

**和 14.1 的差别（施工时定的）**

- E5 `operator_start` 的 `input` 只放任务、公司、月份；写入目标、要人选的项、结果不明的单由卡片照旧调 `precheck` 现取（预检要按产品的写账准备逐张判，每次读事件都跑太重，结果也会随人作答变化，放进投影就不再「同一记录投影相同」）。
- E8 的「金额」与报告里的「金额」「用时」：任务记录里没有（只在执行电脑上），服务端不估不补，留空。
- E11 每张要人处理的单一张卡（`operator_choose`），不是整批一张：编号才能由记录推出、作答一张收口一张。
- E12 / E14 的一句话不在服务端写文字：`agent.message` 带 `operator` 字段，页面按代码出文案（服务端写字要四种语言，且和页面口径会分叉）。
- 卡片的结果用 `user.custom_tool_result`：页面 reducer 把自定义工具收到的 `agent.tool_result` 当成「停止时引擎代发的中断」。
- 整批停的「继续」卡片只在任务「需要你处理 / 已停止」时投影；别的单重交、任务回到等电脑空闲期间它不在投影里，录完回来同一个编号会再推一次（页面按编号去重）。

**剩余项**

- 页面调用方式：`sessions.listing` / `sessions.create` 照旧调 `pearnly_core.assistant.api.listing` / `.create`，请求里多带 `mode=chat|operator`。
- `feed.report` 暂登记在 `scripts/impact/product-map/not_entries.toml`（还没有页面调用方），U2 画报告文件卡片的同一提交删掉那一条。
- 前端（U2）没接之前**不能单独合入**：`listing` 不带 `mode` 时现在列全部，旧代录界面的任务列表与新会话列表会重复。由主会话安排与 U2 一起合。
- `prefs.PARTS` 里的 `operator` 项留着（旧页面还在写）；U2 合入后可删。
- 暂停原因全设备可见（U-Q4）本批不做。
## 15. W3 施工记录（分支 `pearnly/cc-code-operator`，基线 W2 的 `cf1a16eb2`）

用户 11-04 的更正都按主会话转达执行：侧栏不加「等你处理」标记、模式图标不加点；代录不再有自己的对话界面、侧栏和新建页（「这些都是现成的」）；只有一套对话框，代录状态由 W1 的适配器翻译成对话事件。

| 项 | 状态 | 提交 |
|---|---|---|
| 删代录右栏和分隔条（panel.tsx、右栏开合与宽度、样式、词） | 完成 | `454a0624e` |
| 代录状态并进会话（资料步骤行、「开始前」框、单据行按钮、报告卡片、停止进输入框、上传卡片） | 已写，**暂停**，待统一框架方案定后重做 | `f02d63124`、`454a0624e` |
| 左侧「待处理」入口和页面删除（两种模式） | 完成 | `b11c0bc70` |
| 「公司 · 月份」对话末尾的「需要处理」框（thread 的 Tail）、占位行、「未归属公司」、删掉组头的数、`?view=pending` 兼容 | 完成，等 W1 给出 `pending_counts.months`（没有时退回到这家这个月的任一段对话） | `b11c0bc70` |
| 代录新建页改用直写新建页（同一个 Thread / Welcome / 下巴），侧栏改用直写侧栏；operator-sidebar.tsx、prefs.ts、任务列表删除 | 完成 | `482e6750e` |

剩余：
- `tests/test_cc_operator.py` 还没改：它主要断言旧右栏、任务侧栏和暂停的那部分。适配器方案定下来之前重写会白做，等主会话决定。
- `scripts/flows` 的断言改写和实验室流程归块 5、块 6。
- 截图需要实验栈，没有拍。
- i18n 的 `op.section` 暂时保留没删：删掉以后，文案闸门会把新增的 beforeStart、sources、writeTo 当成「改已有文案」拦下。重做时一起处理。
- 共用层接口 `express_operator.api.task_attention` 已经没有前端调用方，登记在 not_entries，留给 W1 删。

## 15. U2 施工记录（统一对话框架的前端，分支 `pearnly/cc-unified-ui`，起点 W3 的 `724a24013` 变基到 `origin/main` `1a09e0e73`）

方案见主会话的统一框架方案（14.1–14.9）与 U1 交来的事件清单（U1 分支 `pearnly/cc-unified-feed` `139695b6d` 的 14.10，字段以它为准）。
本节编号与 U1 的 14.10 不冲突；合并文档时 W3 那一节已经叫「W3 施工记录」。

| 项 | 做法 | 位置 |
|---|---|---|
| 打开代录会话 = 打开一段会话 | 代录模式的主栏就是对话页（同一个 Thread、runtime、停止键、Esc），`OperatorMain` / `OperatorNew` / 任务标题全删 | `assistant.tsx` |
| 侧栏按模式筛（U-Q5 甲） | `listing` 带 `mode`（跟着左上角切换），换模式整表重拉；`create` 在代录模式带 `mode=operator`；每行的 `task` 记下 | `lib/managed-agents/list-mode.ts`、`api.ts`、`list-sync.ts`、`session-list-adapter.ts`、`lib/nav.ts` |
| 新建挡住 | 代录模式的新对话没选公司（有月份的产品还要月份）发送键不可用，同直写的下巴选法 | `runtime-provider.tsx` 的 `isSendDisabled` |
| 资料 | 代录会话里放的资料留在本页，发送时交给任务（站点 `intake`，之后由事件源出上传那一步），说的话交给这段会话的 CC | `runtime-provider.tsx` |
| 4 张工具卡片 | `operator_start`（开始前：自己调 documents / precheck / resolve / start）、`operator_choose`（中途：precheck + task_answer，或 resolve + task_settle）、`operator_resume`（task_resume，「设置」开 CC 的 Express 登录）、步骤行展开（`operator_upload` 用 W2 的 UploadCard、`operator_document`、`operator_sources` 分组与拖动）。卡片样子同「允许」卡片，不进步骤组；能点 = `pearnly.tool_run` `awaiting_approval` 且还没有结果；不走 confirm / run_tool | `components/tool-uis/operator-*.tsx` |
| 事件 | `pearnly.progress`（同一步认 `processed_at` 最新的，比结果新时这一步又在排）；`pop_` / `local-` 开头的当本站事件（不当补拉锚点）；代录卡片停着不进 `pendingCustomTools`、答完不算引擎接着跑；收尾 `agent.message` 的 `operator` 字段写一句、报告画成文件卡片 | `lib/managed-agents/events.ts`、`reducer.ts`、`progress.ts`、`thread.tsx` |
| 执行电脑的桥（E10 / E13） | 页面看得见时 3 秒读一次 `operator_state`：合成 `pearnly.progress`（`local-` 编号、暂停原因码）只并进本页；`task_running`、做完与 CC 重启后的 `task_settle` + 桥 `settle`；取 `task_controls`（stop / skip / retry）照桥做 | `components/operator/bridge.ts` |
| 停止 | ■ / Esc 同对话发 `interrupt`，服务端记任务控制 `stop`，执行电脑取走去停 | 不另写 |
| 深链接 | `?c=<会话>`；旧链接 `?task=<任务号>`（或 `?view=tasks&task=`）按 `task_get(name).session_id` 查到会话，在代录模式打开 | `lib/nav.ts` |
| 删除 | `components/operator/` 只留 `mode-switch.tsx`、`bridge.ts`、`index.ts`；`live-source`、`mock-source`、`model`、`types`、`controller`、`workspace`、`store`、`context`、`task-thread`、`tasks`、`task-title`、W3 的 `conversation`、`uploads` 删除；原因表 `REASONS` / `BATCH_STOPPERS` 移到 `tool-uis/operator-shared.ts`；W3 两个 wip 提交里的会话流拼装不保留（方案 14.5） | — |

新增文案（官方简短）：`op.nth`（第 n/N 张）、`op.resume`（继续）、`op.stopped`（已停止）、`op.end.*`（完成 N 张 / 已停止 · 已入账 N 张 / N 张需要处理）、`op.steps`（代录步骤名，站点没声明时用）。

待对齐 / 剩余：
- 代录会话的「开始前」卡片只交第一页起已取到的资料（同旧页面，服务端分页 + 显示更多）。
- 两套运行状态都以服务端 `run` 为准；本页只在点完卡片时先定下卡片样子。
