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
