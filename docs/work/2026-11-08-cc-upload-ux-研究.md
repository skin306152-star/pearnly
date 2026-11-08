# CC 对话放文件「一放就出现、看得懂在等什么」研究与标准方案（2026-11-08）

- 来由：11-08 晚岳父在 erpcowork 代录对话里放 24 页 10.6MB PDF，界面转圈约 2 分半、看不懂在等什么。用户问：「那为什么 claude 的上传文件，
  马上就能上传到对话框？这类问题你们能不能去研究下 dsh 的开源怎么处理的啊，能不能做标准点」。
- 范围：只研究 + 出方案，不改产品代码；不迁 dsh、不重搭架构（11-07 终定），只借做法。审定后由 CC 一套窗口（local_fd1ef94e）施工。
- 基线：main `ff7db152b`；生产当晚 `2ead204d5`。证据：生产 nginx 计时日志（压测窗口只读排查「erpcowork-1108晚-日志排查.md」）、
  甲骨文 claude-ocr 容器访问日志（本窗口只读 1 次 SSH）、本仓源码。

## 大白话结论（给用户）

**Claude 的方块也不是「瞬间传完」**，它是：文件一放先在本机显示出来（不等上传）、后台传、传的时候能继续打字；传上去只是「存起来」，
读文件是发出消息以后模型自己去读，所以放文件那一刻没什么要等。dsh（DeepSeek 开源的那个）也是这样：一放就出方块、下面一条**真实进度条**、
传完才能发送、失败点方块重试。

**我们慢、看不懂，问题不在「方块出不出来」（我们的方块也是一放就出现），而在三处**：
1. **进度是假的**：方块上只有一个转圈，代码里写死「30%」，10.6MB 传了 16.9 秒，人看不出是在动还是卡死。
2. **服务器收到后串着做三件事才回话**：先把整份文件传给 Anthropic、再把原件存甲骨文、再交识别，三件依次做完才告诉页面「好了」——
   字节传到服务器约 3.5 秒，服务器自己又花了 13.4 秒。
3. **代录把「识别」也挂在方块上转**：传完以后方块接着转「排队中 / 识别中」，识别排队约 44 秒 + 24 页分 4 段一段一段读约 2 分钟。
   人只看到方块一直转，还能点发送，不知道该等还是该发。之后点「开始」又等电脑 25 秒（这段 CC 一套窗口在修）。

**标准做法（一套，直写 / 代录、ERP / Cowork、电脑 / 手机都一样）**：
- 方块一放就出来（已有），方块底部换成**真实上传进度条**（按实际传了多少字节，不估算），字节传完到服务器回话之间显示转圈；
- 服务器**只等最慢的那一件**：传 Anthropic 和存原件 + 交识别同时做，不再排队做，预计 16.9 秒 → 约 9–10 秒（要先加计时日志核实）；
- **输入框方块只管「传」**，传完就是正常方块（不再转）；「识别到哪」放到发出后对话里那张上传卡片上（卡片已有：排队中 → 识别中 n/24 页 → 已识别 N 张）；
- **上传中能打字、能点发送**：点了发送，消息气泡立刻出现、附件卡片在气泡里接着显示进度，传完自动交出去（我们已经这样做了一半，补上进度显示）；
- 失败：方块标红，**点方块重试**；服务器拒收（类型 / 大小不对）不给重试，原因写在输入框上方那一行（手机上也看得见）。
- 不加说明文字：进度条、转圈、红色、已有的「上传中 / 排队中 / 识别中 n/m 页 / 已识别 N 张 / 失败」就够。

估时：写码约 7–9 小时 + 机器跑约 4 小时（单测、实验栈 24 页 PDF 计时、无头 Chrome 限速复现、闸门）；三条里有两条和 CC 一套窗口
正在改的文件交叠，建议并进它的施工队列、排在代录事故修复之后。

## 1. 现状全链路与每段耗时

### 1.1 链路（代码）

| 段 | 做什么 | 代码 |
|---|---|---|
| 选 / 拖 / 粘贴 | 组件库 `ComposerPrimitive` 收文件 → 适配器 `add()`；同一个适配器管「+」、拖入、粘贴截图 | `frontend/assistant/src/lib/managed-agents/attachments.ts` `createAttachmentAdapter` |
| 乐观显示 | `add()` 第一下就 yield `running`，方块立刻出现；图片用本地 `URL.createObjectURL` 当缩略图，PDF / 其他是文件名 + 类型（10-16 定：前端不加 PDF 渲染库） | `attachments.ts` `add`、`components/assistant-ui/attachment.tsx` `ComposerAttachment` / `AttachmentThumb` |
| 前端上传 | 先 `getSessionId()`（新对话这一步建会话），再 `fetch` POST multipart 整份；**`progress: 0.3` 写死，没有真实进度**（fetch 拿不到上传字节） | `attachments.ts` `store`、`lib/frappe.ts` `postForm` |
| 服务端收件 | 整份读进内存 → 类型 / 大小检查 → HEIC 转 JPEG → **串行**：① `engine.attach`：`files.upload` 整份传 Anthropic（美国）+ `sessions.resources.add` 挂进会话；② `_operator_take`（只代录）：`uploads.take` → 产品 `intake`（Cowork：`files.create` 存原件层 → 甲骨文 `/v1/originals/put`；`ocr.submit` 交识别）；③ `_keep` 本机留存一份；④ `_remember_meta` 读页数；代录整段在一把按会话的 redis 锁里（11-06 防并发写撞） | `apps/pearnly_core/pearnly_core/assistant/files.py` `upload` / `_upload` / `_operator_take`；`engines/anthropic_managed_agents.py` `attach`；`express_operator/uploads.py` `take`；`apps/pearnly_cowork/pearnly_cowork/assistant/operator_source.py` `intake` |
| 识别状态回前端（代录） | 上传回包带 `task / upload`，方块接着每 3 秒读 `task_uploads`，方块上转圈 + 「排队中 / 识别中 n/m 页 / 已识别 N 张」；发出后停读，改由对话里的 `operator_upload` 上传卡片显示 | `lib/managed-agents/attachment-recognition.ts`、`components/tool-uis/operator-steps.tsx`、`components/run/upload-card.tsx` |
| 发送 | 组件库锁定版本（`@assistant-ui/react` 0.14.27 / core 0.2.21）**不因附件在传就禁用发送**、也不替我们等：我们在 `send()` 里等上传完成，期间先画「发送中」临时气泡（`beginPending`），没传上就整条放回输入框并在上方写原因 | `attachments.ts` `send` / `filesToSend`、`lib/managed-agents/pending-send.ts`、`send-failure.ts` |

### 1.2 11-08 晚那一次的时间线（泰国时间）

| 时刻 | 段 | 耗时 | 界面此刻显示 | 证据 |
|---|---|---|---|---|
| 19:29:24 | 建代录会话、开始上传 | — | 方块出现，转圈 | nginx |
| 19:29:24 → ~27.6 | 浏览器把 10.6MB 传到 nginx | 约 3.5 秒（16.9 总 − 13.4 后端） | 转圈（写死 30%，看不出在动） | nginx request_time vs upstream |
| ~27.6 → 39.3 | 服务端：传 Anthropic + 挂会话 + 存甲骨文原件（含页数 / sha256 核对） | 约 11.7 秒（两段合计，日志拆不开） | 转圈 | 甲骨文 `POST /v1/originals/put` 12:29:39Z 完成 |
| 39.3 → 41 | 交识别 + 本机留存 + 读页数 + 提交 | 约 1.7 秒 | 转圈 | nginx 19:29:41 回 200 |
| 19:29:41 → 19:30:24 | 识别排队 | 约 44 秒 | 方块转圈 +「排队中」（字只在悬停提示里，方块上是很小一行） | 19:29:44–19:30:14 每 3 秒 `task_uploads`；OCR Work 19:30:24 开始 |
| 19:30:18 | 用户点发送（等不及了） | — | 气泡出现；方块离开输入框，对话里上传卡片接着显示 | nginx |
| 19:30:24 → 19:32:31 | 识别：24 页分 4 段**依次**读 | 约 2 分 7 秒（4 段各约 25–44 秒） | 对话里上传卡片「识别中 n/24 页」+ CC 工具步骤在转 | 甲骨文 `/v1/read` 12:30:35 / 12:31:16 / 12:32:00 / 12:32:25 |
| 19:32:57 → 19:34:59 | precheck 三次 14–28 秒、start 25.6 秒、24 个 precheck 同时发 | 约 2 分钟 | 卡片按钮转圈 | nginx（CC 一套窗口在修，见第 5 节） |

**用户眼里**：从放下文件到能看懂结果约 3 分钟，全程只有转圈；其中「上传」只占 16.9 秒，其余是识别和等电脑。

## 2. 参考做法

### 2.1 Claude（claude.ai / Claude 桌面版）——只写有依据的

| 项 | 观察 | 依据 |
|---|---|---|
| 放进去的样子 | 输入框里一个方块：图片整张放进方块，其他文件是文件名 + 类型；128 见方、圆角约 8、右上角圆形移除 | 本仓 10-16 照 Claude 桌面版截图实量（`docs/work/2026-10-16-cc-shell-design.md`「输入框里的附件方块」，7.webp） |
| 上传进度长什么样、有没有百分比 | **不确定**：本窗口内置浏览器里 claude.ai 没登录，没有实测；帮助中心只写上限（单个聊天文件、每次最多 20 个），不写进度样子 | support.claude.com 8241126 |
| 上传中能不能打字 / 发送 | **不确定**（同上，没实测） | — |
| 发送后「在读」怎么显示 | **不确定**；可以肯定的只有：读文件发生在发出消息之后，放文件那一刻没有识别要等 | 产品形态（上传只存，模型回答时读） |

> 要补实测：用户在内置浏览器登录 claude.ai 后，用一份合成的 24 页 PDF、开发者工具限速，放进输入框不发送、录屏看方块与发送键（不发消息、不建对话）。主会话定要不要做。

### 2.2 dsh（`deepseek-ai/deepseek-harness`，读的提交 `fcc0ad46`，前缀 `packages/client/` 记作 `pc/`）

| 项 | 做法 | 文件 |
|---|---|---|
| 乐观显示 | 选 / 拖 / 粘贴都先建草稿：图片 `createObjectURL` 当缩略图；其他文件卡片 = 类型图标 + 文件名 + 「扩展名 大小」；不解析 PDF 页数 | `ui-attachment/src/client/drop-events.ts`、`ui-conversation/.../editor/keymap.ts` `PASTE_COMMAND`、`service.ts` `createDrafts` |
| 上传 | 选完马上后台传（不等发送），在 Web Worker 里用 **XMLHttpRequest，`xhr.upload.onprogress` 真实进度**；整份一次 POST 到后端 `api/session/uploadFileBinary`（octet-stream，不分片、不续传、不直传对象存储）；同时最多传 2 个，多的排队；后端边收边写盘、超限立刻断 | `pc/file-upload/src/client/runtime.ts` 88–105 行、`apply.ts` 73 行、`http-route.ts` |
| 进度显示 | 卡片底部一条进度条（宽 = loaded/total）+ 转圈 +「上传中…」；图片缩略图下同样一条 | `FileCard.tsx`、`ComposerAttachments.tsx` 135–143 行 |
| 上传中 | **能打字、不能发送**：有没传完或失败的附件就禁用发送，按回车提示「文件还在上传」；没有「传完自动发」 | `InputBar.tsx` 102、371 行，`view-binding.ts` 149 行 |
| 失败 | 卡片标红「上传失败，点击重试」，点卡片重试；后端拒收（带原因）不给重试，悬停看原因，只能删 | `retryFileUpload` |
| 发出后 / 在读 | 消息里同样的文件卡；模型用普通读文件工具读，界面复用工具过程显示，不另做「正在读文件」 | `ui-chat/.../UserContent.tsx`、`process-activity.ts` |

### 2.3 assistant-ui（我们用的，锁定 `@assistant-ui/react` 0.14.27 / core 0.2.21）与 Anthropic 官方示例

- 适配器接口：`add()` 可以是 AsyncGenerator，**每 yield 一次组件库就刷新一次**（`base-composer-runtime-core.ts` `addAttachment` 用 `for await`），
  `running` 状态带 `progress` 数字——真实进度只要我们多次 yield 就能显示，组件库不用换。
- 锁定版本发送按钮**不看附件状态**（`canSend = !isEmpty && !isSendDisabled`），发送时也不等上传（我们自己在 `send()` 里等，必须保留）。
  新版 core 0.3.21 起（#8027 / #8030）才有「发送时等上传」「已发出消息里继续显示上传状态」——**不升级**（底座版本以锁定为准），我们已有的做法与它同义。
- 附件原语（`AttachmentPrimitive` Root / Thumb / Name / Remove）不带进度，官方 UI 组件上传中只画转圈遮罩；官方文档「Progress Updates」示例用定时器模拟，
  `CloudFileAttachmentAdapter` 预签名 PUT 用 fetch 也没进度。**组件库没给真进度的现成写法，要我们用 XHR 自己接。**
- Anthropic 官方示例（`anthropics/claude-quickstarts` 的 `managed-agents/assistant-ui`，我们的出处见 NOTICE.md）：`add()` yield 0.1、0.5 两个写死的数；
  服务端 `beta.files.upload` + `sessions.resources.add`（同我们）；发送不等上传。我们比示例多做了「发送时等完 / 没传上不静默丢」。
- **浏览器不能直传 Anthropic**：Files API 只认 API 密钥，没有预签名 / 短期令牌（platform.claude.com Files 文档；SDK 的 `dangerouslyAllowBrowser` 等于把密钥给前端）。
  所以「传给 Anthropic」这一跳只能由服务端做，能省的是**别让它挡住回话**。

### 2.4 LibreChat / Open WebUI

（草稿：调研代理 11-08 晚还没回，明早补对照：乐观显示 / 真实进度 / 直传 / 上传中能否发送 / 处理中状态。结论不依赖这一节。）

## 3. 标准方案

原则：**方块一放就出来；只显示真数；输入框方块只管「传」，识别与等电脑在对话里显示；任何时候能打字、能点发送；失败点方块重试。**
样子用现有组件（方块、对话里上传卡片、步骤行、`TypingDots`、`Loader2Icon`），进度条用已装的 `radix-ui`（1.6.5）里的 Progress，按 shadcn 写法新增 `components/ui/progress.tsx`（不加依赖；尺寸色走 `globals.css` 标准定义），
不加说明性文案；新增的只有读屏用的数值（「上传中 45%」沿用已有 `uploading` 词条 + 数字）。

### U0 服务端分段计时（先做，核实 U2 的收益）
- 内容：`files._upload` 每段记耗时（读入 / HEIC / 传 Anthropic / 挂会话 / 存原件 / 交识别 / 留存 / 页数）进现有日志（不带文件名、票面）。
- 文件：`apps/pearnly_core/pearnly_core/assistant/files.py`。
- 估时：写码 0.5 小时 + 机器 0.3 小时（单测、闸门）。和 CC 一套窗口不交叠（它不改 files.py 上传段）。

### U1 真实上传进度（前端）
- 做法：`lib/frappe.ts` 加带进度的上传（XMLHttpRequest，`xhr.upload.onprogress`，同样的 CSRF 头与错误解析，`parse` 共用）；`attachments.ts` `add()`
  把进度回调推进队列、逐个 yield `{type: "running", progress: loaded/total}`（同一个 id；单位 0–1，同现在的 0.3）；字节到 100% 后保持 running
  （服务端还在处理），回包后 yield `requires-action`。删写死的 0.3。
- 方块：`attachment.tsx` `ComposerAttachment` 读 `status.progress`：<1 时方块底部一条细进度条（不盖缩略图），=1 等回包时方块中间转圈（同现在）。
  读屏名「上传中 45%」。手机同一组件，只是方块 72 见方。
- 发出后在气泡里继续显示：`send()` 等上传的那段，临时气泡（`pending-send.ts`）里那张附件卡也显示同一进度（`beginPending` 带上附件编号，订阅同一份进度）。
- 文件：`lib/frappe.ts`、`lib/managed-agents/attachments.ts`、`lib/managed-agents/pending-send.ts`、`components/assistant-ui/attachment.tsx`、新增 `components/ui/progress.tsx`、`globals.css`（进度条尺寸色令牌）、对应单测。
- 估时：写码 2.5 小时 + 机器 1 小时（单测；无头 Chrome 开限速放 10MB PDF，录进度从 0 到 100 的帧；电脑 + 手机视口）。
- 交叠：`attachment.tsx` 与 CC 一套「转圈写明在等什么」同一处 → 交它一起改。

### U2 服务端只等最慢的一件
- 做法：`_upload` 里「传 Anthropic + 挂会话」（纯网络调用，不碰数据库）放到线程里，与「存原件 + 交识别 + 留存 + 页数」同时做，两边都完再回；
  任一边失败照现在回可重试的错（不留半份：Anthropic 那份挂不上时，原件层那份按幂等键再传算存过，识别按请求键不重复交）。
  预计 13.4 秒 → 约 max(传 Anthropic, 存原件) ≈ 8–10 秒（U0 数据出来再定；如果传 Anthropic 本身占大头，见 U2b）。
- U2b（只在 U0 显示「传 Anthropic」占大头时考虑）：先回话、Anthropic 那份在后台挂，发送时 `send()` 等它挂好（同现在等上传）。
  代价是「发送时可能再等几秒」，换「放文件后几秒就能看到方块好了」。
- U2c（不建议这批做）：浏览器直传原件层（ADR-024 `begin_upload → PUT originals.pearnly.com → finish_upload`，Cowork 录入台已有 `post-file.js upload_original`），
  服务端再从甲骨文取字节传 Anthropic。省掉「浏览器 → 生产」那 3.5 秒、生产少经手字节，但多一跳「甲骨文 → 生产」、改动面大（CC 上传口、原件引用、两产品 intake 收引用），
  收益只在用户上行慢时明显。等 U0 的数据和真实用户上行再定。
- 文件：`apps/pearnly_core/pearnly_core/assistant/files.py`（必要时 `engines/anthropic_managed_agents.py` 不动接口）、`tests/test_assistant*.py`。
- 估时：写码 2 小时 + 机器 1.5 小时（单测；实验栈接真引擎放 24 页 PDF 前后各计时一次；闸门 + 候选离线，因改 pearnly_core 共用层）。
- 交叠：不改 CC 一套正在改的文件；但它在同一共用层，合入顺序要排（建议它的事故修复先合）。

### U3 输入框方块只管「传」，识别到哪放对话里
- 做法：代录会话里上传完成后方块就是正常方块（不再转「排队中 / 识别中」）；识别状态在发出后对话里的上传卡片（`operator_upload`，已有：排队中 → 识别中 n/m 页 → 已识别 N 张 / 失败）显示。
  理由：方块上的字太小、手机悬停看不到，而且转圈让人以为「还没传完、不能发」。识别照旧在放进来那一刻就交（不推迟，省时间）。
- 没发出前想看识别到哪：不做（Claude、dsh 都没有；发出就看得到）。
- 文件：`lib/managed-agents/attachment-recognition.ts`（删输入框那段读取，或只留给对话卡片用）、`components/assistant-ui/attachment.tsx`、`attachments.ts` `store` 里 `watchRecognition` 的调用、单测、流程断言（cc.operator 里方块不再显示识别字）。
- 估时：写码 1.5 小时 + 机器 1 小时（单测 + 实验栈 cc.operator 流程电脑 / 手机）。
- 交叠：**正是 CC 一套「转圈写明在等什么：上传中 / 识别中 · 共 N 页 / 等电脑」那一项** → 由它定稿、一起改，避免两种口径。

### U4 上传中照样能发送（保留现有做法，补齐显示）
- 口径：**能发**（与 dsh「禁用发送」不同，取我们已有、也是 assistant-ui 新版的做法）：点发送 → 临时气泡立刻出现、附件卡在气泡里显示进度（U1）→ 传完自动交出 → 正式气泡接替；
  没传上 → 整条放回输入框，原因写在输入框上方（已有 `send-failure.ts`）。理由：用户说「Claude 马上就能上传到对话框」，要的是不被挡；我们已做到不丢附件。
- 文件：只 U1 那几处，无另外改动。估时并在 U1。

### U5 失败呈现与重试
- 可重试（断网、超时、存放处一时忙 `upload.deferred`）：方块标红，**点方块重试**（同 dsh；`attachment.tsx` 方块 onClick 在 error 时重新 `store`），不加字；
  不可重试（类型 / 大小 / 页数不收，服务端带原因）：方块标红，原因写在输入框上方那一行（同发送失败那一行，手机也看得见——现在只在悬停提示里，手机上看不到），只能移除。
- 文件：`attachment.tsx`、`attachments.ts`（区分可重试：看服务端异常类型 / 状态码）、`send-failure.ts`（原因行复用）、单测。
- 估时：写码 1.5 小时 + 机器 0.5 小时。
- 交叠：同 U1 / U3 的 `attachment.tsx` → CC 一套。

### U6 一套覆盖面（不另做分支）
- 直写 / 代录：同一个 `createAttachmentAdapter`、同一个服务端 `files.upload`；代录多出的只是「交识别」，U2 并进同一线程并发里。
- ERP / Cowork：上传口在共用层 `pearnly_core.assistant.files`，产品只声明 `intake`（Cowork 已有；ERP 由 CC 一套 A 批 1 加 `receive_chat`）——两产品自动一致。
- 电脑 / 手机：同一组件，手机只有方块尺寸不同；iOS / 安卓浏览器 XHR 上传进度都可用；手机锁屏或切走时上传会被系统暂停，回来接着传或报失败按 U5 走（不另做续传）。
- 四语：不新增词条（进度用数字；「上传中」「失败」「重试」已有）；如 U5 要新的原因句，按 interface-copy 走 th/zh/en/ja 四语。

### 不做
- 分片 / 断点续传（单份上限 `contracts.UPLOAD_MAX_BYTES` = 30MB，一次传完；dsh 也不做）。
- 前端渲染 PDF 缩略图 / 读页数（10-16 定不加 PDF 库；页数随回包到，方块不显示页数，消息里的卡片照旧「PDF · 24 页」）。
- 升级 assistant-ui 底座拿新版「发送时等上传」（行为我们已有）。
- 没发出前预览识别结果、估算剩余时间（只用真数）。

## 4. 估时汇总

| 项 | 写码 | 机器 | 谁做 | 与 CC 一套交叠 |
|---|---|---|---|---|
| U0 分段计时 | 0.5 h | 0.3 h | CC 一套（共用层上传口） | 否 |
| U1 真实进度 | 2.5 h | 1 h | CC 一套 | 是（`attachment.tsx`） |
| U2 服务端并发 | 2 h | 1.5 h | CC 一套 | 同层不同文件，排合入顺序 |
| U3 方块只管传 | 1.5 h | 1 h | CC 一套 | **是（同一项「转圈写明在等什么」）** |
| U4 上传中可发 | 并在 U1 | — | — | — |
| U5 失败与重试 | 1.5 h | 0.5 h | CC 一套 | 是（`attachment.tsx`） |
| 合计 | 约 8 h | 约 4.3 h | | |

建议顺序：U0（可以随时先合）→ CC 一套代录事故修复合入 → U3 + U1 + U5 一批（同一组件）→ U2（看 U0 数据）。上线随下一批部署；
验收：实验栈同一份 24 页 PDF，电脑 + 手机录屏：放下即出方块、进度条从 0 走到 100、上传中能打字能发、发出后对话卡片显示识别 n/24 页；限速断网时点方块重试。

## 5. 不归本方案、顺手记下的（信息）

- **识别 2 分钟**：24 页分 4 段依次读（`pearnly_core.claude_ocr.segments`，甲骨文 `/v1/read` 4 次、各 25–44 秒）。段与段能否并行属识别线（ocr-haiku-split 窗口 / 识别负责人），不在上传方案里。
- **识别排队 44 秒**：19:29:41 交识别到 19:30:24 才开始（OCR Work）。当时队列里还有什么待查（只读日志可查），归识别线。
- **开始 / 预检 12–28 秒、24 个预检同时发**：CC 一套事故修复 B3 与「同一连接快照只读一次」在改。
