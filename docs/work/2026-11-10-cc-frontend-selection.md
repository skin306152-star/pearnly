# CC 聊天前端换成大厂成品：选型调研（2026-11-10）

- 来由：主会话 local_e395dace 11-10 派活。用户嫌 CC 前端「像 80 年代」，想像当初迁 Frappe 一样「通用部分用成品、我们只写业务」。只调研、做小样，不改产品代码、不合入 main。
- 基线：分支 `pearnly/frontend-selection-3b576d`，起点 `c9e9eedf9`；现有前端读 `frontend/assistant/`（`@assistant-ui/react` 0.14.27）与 `docs/work/2026-11-09-cc-vs-claude-ux-audit.md`。
- 截图、小样源码：共享笔记 `side-session-notes/local_e395dace…/前端选型-11-10/`（`shadcn-aisdk截图/`、`antx截图/`、`小样源码/`）。小样本身在会话临时目录，没进仓库；本机服务已全部关掉（8790 / 8791 端口回读为空）。
- 花费：0。没调用任何付费模型；演示只看只点、没登录；小样全部接假后端。

## 结论

**推荐：Vercel AI SDK（`ai` + `@ai-sdk/react`）管消息状态与流式，界面用 shadcn/ui 的 AI 组件（原 AI Elements）抄进我们仓库。** ChatKit 不满足第 1 条，排除；Ant Design X 只给界面零件、不管续传，换过去出毛病的那一层还是我们自己写，不推荐。

要先说清的三件事（核实后对主会话原有了解的修正）：
1. **ChatKit 自建后端时，界面仍由 OpenAI 托管**。官方文档对照表原文「Who hosts the iframe that renders Chat UI? → Self-hosted backend: OpenAI」；用户看过的 chatkit.world 实测：后端在 onrender.com，界面是 `cdn.platform.openai.com/deployments/chatkit/index-….html` 的 iframe，脚本 `cdn.platform.openai.com/deployments/chatkit/chatkit.js`，带 `domain_pk_…` 域名密钥（每个租户域名都要去 OpenAI 后台登记白名单）。npm 包 `@openai/chatkit` 只有类型定义（54 KB），界面代码不开源。
2. **Vercel 的 AI Elements 已宣布停用**：elements.ai-sdk.dev 顶部横幅「将于 2026 年 11 月底停用」，并入 shadcn/ui（`ui.shadcn.com/docs/ai-elements`）；新的做法是把组件代码复制进自己的项目，滚动、提问卡两块逻辑放在无样式包 `@shadcn/react`（MessageScroller、Questionnaire）。已复制进项目的旧代码继续能用。所以第 3 家实际评的是「AI SDK + shadcn/ui AI 组件」。
3. **没有哪家能免掉「我们的事件 → 界面状态」这一层**。我们的后端是 Managed Agents 事件记录 + Frappe socket.io 推送，不是任何一家的协议。区别只在：AI SDK 把「流式、停止、续传、工具卡片状态、断网回来自动续」这套状态机做成了成品（周下载 4500 万），我们只写一个翻译器（小样约 100 行）；Ant Design X 不管这些，现在 3000 行手写的那层基本原样保留。

## 逐条对照

「满足 / 不满足 / 没测到（原因）」；依据列里「小样」= 本机小样 + 无头 Chrome 点测，「源码」= 克隆的仓库或 npm 包，「演示」= 用户 Chrome 打开官方演示。

| 条件 | ChatKit（OpenAI） | Ant Design X（蚂蚁） | AI SDK + shadcn/ui（Vercel） |
|---|---|---|---|
| 1 全部放自己服务器 / 许可证 | **不满足**：界面 iframe 与脚本都在 OpenAI CDN，域名要在 OpenAI 登记（演示、官方文档）；界面代码闭源，npm 只有类型（源码）。 | 满足：npm 包，MIT（`@ant-design/x` 2.10.0、`x-sdk` 2.10.0），可打包进我们自己的静态文件，无密钥、无白名单（源码）。 | 满足：`ai` 7.0.166 Apache-2.0、`@shadcn/react` 0.3.1 MIT；组件代码用 shadcn 命令复制进项目（小样实测复制了 10 个文件），之后与 shadcn 网站无运行关系；无密钥、不依赖 Vercel 托管（源码、小样）。 |
| 2 接我们后端（长任务、挂起几分钟再继续、断线重连、刷新恢复） | 没做小样（第 1 条已排除）。另：只认 ChatKit 自己的服务端协议（要用 chatkit-python 重写成它的接口）。 | 满足但要自己写：`useXChat` 是一问一答一次请求，源码里没有续传 / 重连 / 服务端推送（`x-sdk/src` 搜不到 resume、reconnect）。小样里事件折叠、补拉、重连都是自己写的约 60 行；挂起 30 秒中途刷新 → 进度接着走、提问卡照常出（小样）。 | 满足：`ChatTransport` 有 `sendMessages` / `reconnectToStream`，`useChat({ resume: true })` 打开即续传，`@ai-sdk/react` 在标签页回到前台时自动续断网的流（源码 `use-chat.ts` 的 visibilitychange）。小样写了一个翻译器（约 100 行）把我们的事件转成它的标准消息块；挂起 30 秒中途刷新 → 接着出进度、提问卡（小样 04、05）。 |
| 3 业务卡片（提问卡、开始前卡、复核表格、右侧公司栏） | 卡片只能用它的 JSON 小部件语言（有 Table / Input / Checkbox），画在它的 iframe 里，不能放我们的 React 组件（源码 `widgets.d.ts`）。 | 满足：卡片就是 antd 组件（Radio / Checkbox / Table 可编辑格、标红、勾选），右栏是我们自己的布局（小样 05–08）。 | 满足：提问卡用 shadcn Questionnaire（单选、多选、「其他」输入框、上一题 / 跳过 / 下一步、翻页），复核表用 Table + Checkbox + 可编辑格、空值标红，点「开始写入」才交回；右栏自己的布局（小样 05–08）。开始前卡片两家都没单独组件，用同样的零件拼。 |
| 4 泰中英日四语、换成我们的词条 | 自带 th-TH / ja-JP / zh-CN（源码 `SupportedLocale`），但界面文字是它的，换不成我们的词条。 | 满足但要补：Ant Design X 自己的词条只有 zh_CN / en_US（源码 `locale/`，约 20 条，泰日要我们补）；antd 有 th_TH / ja_JP。小样四语切换正常（12、13）。 | 满足：复制进来的组件就是我们的代码，默认英文（Next / Submit / Skip、「Question 1 of 2」）全部能换成我们的词条；小样已换（05、12、13）。 |
| 5 手机、LINE 内置浏览器、iPhone 主屏幕、Qt WebEngine、轻量 | 没测（已排除）；另每次都要连 OpenAI CDN。 | 部分：390 宽能用但没有手机外壳（抽屉、底部安全区要自己做）（小样 14）。包较重：小样 963 KB（压缩后 308 KB），要带整个 antd 6。**真手机、LINE、iPhone 主屏、Qt 都没测到**（没上真机 / 测试机）。 | 部分：390 宽表格横向滚动、输入框贴底（小样 14）。小样 497 KB（压缩后 151 KB）。依赖与现在 CC 同一档（React 19 + Tailwind 4，现版已在 Qt 6.11 里跑），推断可跑；MessageScroller 用到的 `content-visibility` 在 iOS 18 以下不生效（只少一个优化，不坏）。**真手机、LINE、iPhone 主屏、Qt 都没测到**。 |
| 6 多租户、Keycloak、前端不拿密钥 | 不满足：要 OpenAI 域名密钥，租户域名都要登记。 | 满足：同源请求带 Frappe 会话 cookie，同现在。 | 满足：同上；AI SDK 的模型调用部分前端不用（小样 transport 只打我们自己的接口）。 |
| 7 成熟度 | OpenAI 维护；chatkit-js 1968 星、周下载 16 万；**最后一次推送 2026-07-31**，chatkit-python 最后推送 05-19；1.x 稳定。 | 蚂蚁集团 Ant Design 团队；4845 星、周下载 10 万；2.x（2025-11 起），近 6 个月只发 3 版（06、07、10 月）；开着 179 个问题。 | Vercel；`ai` 2.78 万星、**周下载 4529 万**，月发几十版；**每年一个大版本**（5→6→7 分别在 2025-07、2025-12、2026-06），升级有改动风险，要锁版本。shadcn/ui 12.8 万星；`@shadcn/react` 是 2026-06 才出的 0.x，最年轻的一块。 |
| 8 像不像 Claude | 演示里样子很好（它就是 ChatGPT 那一套）；但改不了。 | 不像：antd 蓝色主按钮、气泡、卡片味很重（演示截图、小样 07）。 | 接近：shadcn 默认黑白灰、无气泡回复、发出的那句钉在顶部、回复往下流、「回到最新」按钮（同 Claude 电脑网页）；停止后半段保留（小样 10）；重试有现成 `regenerate()`。编辑后从那句重答：引擎事件删不掉，两家都只能做「改了再发一条」。 |

### 小样做了什么（两家同一个假后端、同一份点测脚本）

假后端模仿 CC 现在的数据通路：服务端存会话事件记录（事件名同 Managed Agents：`event_start / event_delta / agent.message / agent.tool_use / pearnly.progress / agent.custom_tool_use / session.status_idle …`），浏览器「整段回放 + 增量推送」。剧本：发一句 → 逐字回复 → 识别工具挂起 30 秒（每 3 秒一条进度）→ 提问卡（单选 + 多选 + 其他）→ 复核卡（3 张票据表，1 张缺税号标红）→ 写入。点测（无头精简版 Chrome，1360×820，最后改 390×844）：

| 步骤 | AI SDK + shadcn | Ant Design X |
|---|---|---|
| 发出到第一个字 | 约 0.3 秒 | 约 0.3 秒 |
| 挂起中刷新，回来接着出进度、提问卡 | 通过 | 通过 |
| 提问卡答完 → 复核卡 → 改值、去勾 → 写入 | 通过 | 通过 |
| 写完刷新，对话与卡片都在 | 通过 | 通过 |
| 回复中停止，半段保留 | 通过 | 通过 |
| 泰文、日文切换 | 通过（全部界面字是我们的词条） | 通过（Ant Design X 泰日词条是小样自己补的） |
| 390 宽 | 能用 | 能用（侧栏要自己收） |

小样没做、留给正式施工的：别的设备发来的消息并进同一段对话、刷新后复核卡显示「改后的值」（假后端只回放原值）、附件、侧栏分组与拖动、草稿、开始前卡片。

### AI SDK 接我们后端时踩到的三个点（小样里都已解决，正式施工要写进测试）

1. **先订阅、再发送**：逐字预览（`event_start` / `event_delta`）不落库、只推一次，先发后订阅就会漏，表现是「整段一次出现」——和 CC 11-09 对照表 #3「第一句不逐字」是同一类原因（小样第一版复现，改成先订阅再发后 0.3 秒出字）。
2. **续传不整轮重放**：AI SDK 的「整轮重放」模式会把上一条回复（带提问卡）重置，卡片被重新挂载、已选的丢掉。改成「刷新时把已有部分折进界面、续传只接着推」，服务端执行的工具标 `providerExecuted`，它就接着写进同一条回复。
3. **停止不能挂在请求取消上**：AI SDK 会因为内部原因取消请求（新请求顶掉旧的、组件卸载），若把「通知服务端停止」挂在取消信号上会误停后台任务；停止按钮单独发一次中断。

## 换过去的工作量（按机器耗时估）

现有前端 `frontend/assistant/src` 共约 3.2 万行；直接用到 assistant-ui 的 24 个文件约 9500 行，消息状态层（`lib/managed-agents/` 里 events / reducer / realtime / runtime-provider / session-controller / turn）约 3000 行。

| 块 | 现成（拿来就用） | 我们写 | 写代码 | 机器跑 |
|---|---|---|---|---|
| 状态与流式 | AI SDK Chat：流式、停止、重试、续传、断网回前台自动续、工具卡片状态 | 事件翻译器 + 刷新折叠 + socket.io 接入（多设备、本站提醒、`pearnly.tool_run / answer`、识别进度） | 8–12 小时 | — |
| 对话区与输入框 | shadcn MessageScroller（跟随 / 钉住 / 回到最新）、Message、Prompt Input、附件、Markdown | 套 globals.css 令牌、手机贴键盘、桌面版快捷键 | 8–12 小时 | — |
| 业务卡片 | Questionnaire、Table、Checkbox、Confirmation | 提问卡、开始前卡、复核卡、代录 6 张卡、步骤卡、待处理框改接新数据形状 | 8–12 小时 | — |
| 侧栏 | shadcn Sidebar（无业务） | 现有公司分组、拖动、置顶、列表同步从 assistant-ui 的列表运行时摘下来 | 6–10 小时 | — |
| 四语、Qt、手机外壳 | — | 词条搬运、桌面版 12 处判断、抽屉 | 4–6 小时 | — |
| 测试与流程 | — | `tests/flows`、`test_assistant_ui`、`cc_desktop_fake` 等改选择器与断言 | 6–10 小时 | 流程 + 截图：Cowork / ERP × 电脑 / 手机约 4–6 小时；测试机 CC 桌面版 + 三星 LINE 约 2–4 小时 |
| 合计 | | | **约 40–60 小时** | **约 6–10 小时** |

更省的一条路（只写给主会话参考）：毛病集中在状态层，assistant-ui 官方就有接 AI SDK 的适配（`@assistant-ui/react-ai-sdk`）。只换状态层、界面和卡片不动，约 15–25 小时写码 + 4–6 小时机器；但界面还是 assistant-ui，不满足用户「换成大厂成品」的意思。另：assistant-ui 周下载 317 万、1.3 万星、近半年每月 5–16 版，并不冷门；CC 现在「旧」的样子主要是我们自己的样式，换库不会自动变好看，shadcn 的默认样式只是更接近 Claude 的素净。

## 最大的风险

1. **不能一次全换**：对话区、卡片、侧栏都在其他窗口近期改动范围里（11-09 对照表的交互批 A / B、乱用兜底批、上传 U 系列）。建议先只换状态层（把 AI SDK 接到现有界面后面，最痛的卡住、丢进度、刷新丢都在这一层），再分批换界面；期间其他窗口停改这几个文件。
2. **AI SDK 每年一个大版本**：锁版本、只在专门批次升级；我们只用它的 Chat / transport 部分，改动面小。
3. **`@shadcn/react` 才 0.x（2026-06 起）**：MessageScroller、Questionnaire 两块逻辑在这个包里；组件外壳已复制进仓库可自己改，包本身要锁版本。
4. **真机没测**：Qt WebEngine（办公室老电脑）、LINE 内置浏览器、iPhone 主屏幕本次都没测到，正式施工第一步先在测试机和三星上跑小样。

## 11-10 用户最新想法（待定）

主会话转达，用户看过上面的结论后说先放着、待定：
- **不重构**：不整体换掉现在的前端。
- **倾向**：把 shadcn/ui 整套标准组件复制进 CC，当作 CC 的标准组件库；界面统一引用这些组件，按类拦截手写的通用组件（同 Frappe 原生优先、按类修的做法）。
- **状态层暂不换**：AI SDK 先不接。
- 以上待定，没有开工；小样代码不进仓库，截图与小样源码留在共享笔记 `前端选型-11-10/`。

## 没测到

- 真手机（触屏、软键盘）、LINE 内置浏览器、iPhone 主屏幕网页、CC 桌面版 Qt WebEngine：没上真机和测试机。
- 断网真实样子：小样只测了刷新（等同断开重连）；网络时断时续没模拟。
- 录屏：本机 Playwright 缺对应版本的 ffmpeg，没装（不改用户缓存目录），只有分步截图。
- ChatKit、Ant Design X 的在线演示只截了首屏，没在演示里发消息。
