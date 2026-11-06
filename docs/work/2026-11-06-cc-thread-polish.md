# CC 对话页：「思考中」改「执行中」+ 对话滚动区上沿（2026-11-06）

用户 11-06 晚要求，随当晚部署一起上线。范围只在 CC 对话页 `frontend/assistant`（直写、代录、Cowork、ERP 两种站点、电脑 / 手机 / CC 桌面版共用同一个对话框）。

## 1. 工作状态行「思考中」改「执行中」（改已有文案，用户已明确要求）

出现位置：`frontend/assistant/src/i18n.ts` 的 `STEPS.<语言>.thinkingNow`（状态行「1分19秒 · 思考中…」）与 `STEPS.<语言>.thinking`（同一行给读屏报的字）。全仓搜过：只有这一处给用户看；CC 桌面版（`companion/`）、各产品术语表里没有这个词；`reducer.ts` 的 `THINKING_LABEL` 只是内部占位、界面不显示。

| 键 | 语言 | 原文 | 新文案 |
| --- | --- | --- | --- |
| `assistant:STEPS.thinkingNow`（zh） | 中文 | 思考中 | 执行中 |
| `assistant:STEPS.thinking`（zh） | 中文 | 正在思考… | 正在执行… |
| `assistant:STEPS.thinkingNow`（th） | 泰文 | กำลังคิด | กำลังดำเนินการ |
| `assistant:STEPS.thinking`（th） | 泰文 | กำลังคิด… | กำลังดำเนินการ… |
| `assistant:STEPS.thinkingNow`（en） | 英文 | Thinking | Working |
| `assistant:STEPS.thinking`（en） | 英文 | Thinking… | Working… |
| — | 日文 | — | CC 网页没有日文界面（`i18n.ts` 只有 th / zh / en），不加 |

未改：想完开始出正文时的「思考了 M秒」（`thoughtFor`）不在这次要求里，保持原样。

## 2. 对话滚动区上沿（照 Claude 桌面端）

现象（用户原话）：「当我在对话框界面滑动时，这个界面的最高限制太高了」——内容一直滚到紧贴顶栏（公司名 › 对话标题 …）才被裁掉。

依据：只有用户给的 Claude 桌面版截图（滚动中的内容停在标题栏下面、留出一段距离）。Claude 内部具体是上内边距还是渐隐遮罩、多高，没有实测依据；按截图观感做成「顶栏下面 24 的一段里内容淡出」，不另加别的东西。

做法（只用 globals.css 令牌）：

- `--cc-thread-fade`（24，`--cc-space-24`）：滚动区顶上一层吸顶的淡出（页面底色 → 透明），不占版面、不接指针；
- `--cc-thread-top`（24）：内容离滚动区上沿（原来 16），停在最上面时第一条不在淡出区里；
- 发出后组件库把这一轮的提问顶到滚动区最上沿（按消息外框算，`turnAnchor="top"`，打开在跑的对话时 `lib/follow-scroll.ts` 同一落点）：`.cc-user-row` 外框往上多出淡出高（负外边距 + 同高上内边距，版面不变），气泡停在淡出区下面；多出的一截不接指针，上一条回复的操作行照常能点；
- 手机变体两个值是 0 / 16：手机顶栏本来就盖在内容上、自己往下淡出（同 Claude 手机端镜像实测），不再加一层，手机画面与改前逐像素相同。CC 桌面版窗口宽 ≥ 768 时同电脑，窄窗口走手机变体。

## 验证

- 本机画面比对（`scripts/cc-visual`，假服务端）：web / desktop / phone × 浅 / 深；场景 chat-running、chat-rich、chat-rich-bottom、chat-rich-mid（滚到中间，临时场景，没提交）、chat-answered、send-pending、chat-reply-hover。前后对比图在共享笔记「CC对话页-执行中与上沿-11-06/」。
- 手机 chat-rich / chat-rich-mid 改前改后截图文件 md5 相同。
- 没碰测试机。

## 共用件连带回归（`scripts/check-impact.py`）

| 入口 | 结果 |
| --- | --- |
| `cc.assistant` CC 对话页（命中 cc-composer，因 `thread.tsx`） | 通过：画面比对打开对话、在跑、发送中气泡、允许卡片答完、回复悬停（web / desktop / phone × 浅深），输入框与 ↓ 位置不变；`tests/test_cc_ccs.py`、`tests/test_cc_run_indicator.py`、`tests/test_assistant_ui.py` 通过 |
| `cc.operator.page` 代录会话（同上） | 通过（离线）：`tests/test_cc_operator.py`；代录与直写同一个对话框，状态行同一个 `statusText` |
| `shared.team.members` 团队页（因 `i18n.ts`） | 没测到：这次只改 `STEPS` 的状态行两个键，团队页不读这两个键 |

产品地图：`scripts/check-product-map.py` 一致，没有增删入口。

## 3. 切模式时离开不属于新模式的对话（用户 11-06 补充）

现象（用户原话）：「当我在不同模式点开的对话框，切换到另一个模式时，不属于这个模式的对话框还一直在」——侧栏已按模式筛，右边正在看的那段没跟着换。

做法（直写、代录同一套，`frontend/assistant/src/lib/nav.ts`）：

- 每段会话是哪种由服务端每行的 `mode` 定（侧栏取列表、打开、新建时都带回），`lib/managed-agents/list-mode.ts` 记下（`sessionMode`）；
- 换了模式——左上角两个图标、Ctrl / ⌘ + Shift + 1 / 2（`switchMode`），或直接进了另一个模式自己的视图（`noteViewMode`，如代录下进定时任务）——都调同一个 `leaveForeignThread`：正在看的对话已知不属于新模式，就清草稿、开新对话（同「新建」落到的新建页）；属于新模式的、还没发出的新对话、还没见过的照旧；
- 外壳 `Layout` 用 `useThreadExit()` 登记「正在看的对话 / 去新建页」，电脑、手机、CC 桌面版同一个外壳；
- 后退 / 前进：换了模式、还没离开旧对话那一下（模式已换、对话还是旧的）不进记录，后退回到「原模式 + 原对话」，不会回到「代录模式开着直写对话」；
- 深链接（`?c=`、旧的 `?task=`）本来就按会话的 mode 先定模式再打开，刷新后地址是干净的 `/assistant`（新建页），都不经这里。

测试：`tests/test_cc_operator.py`（直写→代录、代录→直写离开；属于新模式、新对话、没见过的不动；进共有视图不换模式不离开；进对话模式自己的视图换模式并离开；外壳登记、两处调用、后退记录跳过中间态）。把 `nav.ts` 还原成改前，这条测试失败。

画面（`scripts/cc-visual` 新增场景 mode-before / mode-leave / mode-op-open / mode-op-leave / mode-leave-back，假服务端的列表按 mode 筛、多一段代录会话 op_1）：web / desktop / phone 改前改后对比在共享笔记「CC对话页-执行中与上沿-11-06/切模式/」。改前：切到代录后右边还是直写那段；改后：落到新建页；代录会话在代录模式下照旧打开；切模式后点 ← 回到直写模式 + 原对话。

连带：`check-impact` 另列出 `shared.login`、`cc.settings.connection`、`cc.desktop.bridge`（因 `assistant.tsx`）——这次只在 `Layout` 加了一行登记，登录、设置、桌面桥的代码没动，没单测；流程用例覆盖通过（cc.sidebar-modes / cowork.cc.sidebar-modes 等，步骤只断言模式与侧栏，切回对话后落到新建页不影响断言），流程整套随今晚冻结后的全套跑。

## 4. 滚动条不常显（用户 11-06 补充）

用户原话：「cc 左侧的滚动条不要常显」。照 Claude：平时不显示，鼠标在这块滚动区上（含正在用滚轮滚）才出现，移开就收。

做法：`globals.css` 原来的全局细滚动条规则改成滑块平时透明（`scrollbar-color: transparent transparent`，位置照留、出现时版面不跳），`*:hover` 时滑块是 `--cc-fill-muted`。侧栏、对话区、右侧面板、菜单、弹窗共用这一条，不另起类。CC 桌面版是 PySide6 6.11（QtWebEngine 约 Chromium 134），支持标准 `scrollbar-color`；手机是系统浮层滚动条，滚动时才出现，未改。

验证：本机是 mac（浮层滚动条，截图里本来就看不到滚动条，没有改系统设置去强制显示，也没碰测试机），改用内置浏览器读计算后的样式：鼠标在右侧空白时对话区与侧栏的滑块都透明；移到对话区只有对话区出现；移到侧栏只有侧栏出现（记录在共享笔记「…/滚动条/说明.md」）。`tests/test_cc_ccs.py` 断言这条规则。Windows 上的真实观感待部署后看。
